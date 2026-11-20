from __future__ import annotations

import logging
import re
import time
from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends

from .. import state
from ..config import AppConfig, Box, StoredBox, rebuild_boxes, save_config
from ..ssh_pool import get_pool
from ..validation import ValidationError
from . import dependencies
from .box_runner import BoxRunner, ProgramNotFoundError, Ran, box_runner, remote_command_line
from .dependencies import APIDependencies
from .models import (
    APIBox,
    APIBoxStats,
    APIBoxStatus,
    APIFavoriteToggle,
    APIGitInfo,
    APIPinToggle,
    APIRefreshResult,
    APIThemeResult,
    APIThemeUpdate,
)

logger = logging.getLogger(__name__)

if TYPE_CHECKING:  # pragma: no cover - type checking only
    from ..webapp import ServerSettings  # noqa: F401


_VALID_THEMES = {"cyberpunk", "default", "solarized", "dracula", "nord", "monokai", "light"}


def _box_to_api(box: StoredBox | Box) -> APIBox:
    """Convert box dataclass into API schema."""

    if isinstance(box, Box):
        host = box.display_host or box.connect_host
        return APIBox(
            name=box.name,
            host=host,
            user=box.user,
            port=box.port,
            transport=box.transport,
            pinned=box.pinned,
            default_dir=box.default_dir,
            favorites=box.favorites,
            terminal_theme=box.terminal_theme,
        )

    host = box.host or box.ssh_alias or box.name
    return APIBox(
        name=box.name,
        host=host,
        user=box.user or "",
        port=box.port or 22,
        transport="ssh",
        pinned=box.pinned,
        default_dir=box.default_dir,
        favorites=box.favorites,
        terminal_theme=box.terminal_theme,
    )


def get_router(deps: APIDependencies) -> APIRouter:
    router = APIRouter()

    @router.get("/boxes", response_model=list[APIBox])
    async def api_list_boxes(
        application_config: AppConfig = Depends(deps.get_application_config),
    ) -> list[APIBox]:
        return [_box_to_api(box) for box in application_config.boxes]

    @router.get("/boxes/{name}", response_model=APIBox)
    async def api_get_box(
        name: str,
        application_config: AppConfig = Depends(deps.get_application_config),
    ) -> APIBox:
        return _box_to_api(deps.get_box_or_404(application_config, name))

    @router.get("/boxes/{name}/status", response_model=APIBoxStatus)
    async def api_box_status(
        name: str,
        application_config: AppConfig = Depends(deps.get_application_config),
    ) -> APIBoxStatus:
        box = deps.get_box_or_404(application_config, name)
        if box.transport == "local":
            return APIBoxStatus(name=box.name, status="online", latency_ms=0)

        start = time.time()
        try:
            await deps.run_pooled(
                box, application_config, lambda conn: conn.run("true", check=True)
            )
            latency = (time.time() - start) * 1000
            return APIBoxStatus(name=box.name, status="online", latency_ms=latency)
        except Exception:
            return APIBoxStatus(name=box.name, status="offline", latency_ms=None)

    @router.post("/boxes/{name}/pin", response_model=APIPinToggle)
    async def api_toggle_pin(
        name: str,
        application_config: AppConfig = Depends(deps.get_application_config),
    ) -> APIPinToggle:
        box = deps.get_box_or_404(application_config, name)
        stored = application_config.get_or_create_stored(name)
        stored.pinned = not stored.pinned
        save_config(application_config)
        rebuild_boxes(application_config)
        return APIPinToggle(name=box.name, pinned=stored.pinned)

    @router.post("/boxes/{name}/fav", response_model=APIFavoriteToggle)
    async def api_toggle_favorite(
        name: str,
        payload: APIFavoriteToggle,
        application_config: AppConfig = Depends(deps.get_application_config),
    ) -> APIFavoriteToggle:
        box = deps.get_box_or_404(application_config, name)
        stored = application_config.get_or_create_stored(name)
        # Use box.favorites (from SQLite) not stored.favorites (from YAML)
        favorites = set(box.favorites or [])
        if payload.favorite:
            favorites.add(payload.path)
        else:
            favorites.discard(payload.path)
        sorted_favorites = sorted(favorites)
        stored.favorites = sorted_favorites
        box.favorites = sorted_favorites
        await state.replace_favorites_async(name, sorted_favorites)
        save_config(application_config)
        rebuild_boxes(application_config)
        return APIFavoriteToggle(path=payload.path, favorite=payload.favorite)

    @router.post("/boxes/{name}/refresh", response_model=APIRefreshResult)
    async def api_refresh_box(
        name: str,
        application_config: AppConfig = Depends(deps.get_application_config),
    ) -> APIRefreshResult:
        """Remove connection overrides so SSH config values apply."""
        deps.get_box_or_404(application_config, name)
        stored_override = application_config.stored.get(name)
        if stored_override is not None:
            stored_override.host = None
            stored_override.user = None
            stored_override.port = None
            stored_override.keyfile = None
            stored_override.known_hosts = None
            stored_override.ssh_alias = None

            if not any(
                [
                    stored_override.host,
                    stored_override.user,
                    stored_override.port,
                    stored_override.keyfile,
                    stored_override.known_hosts,
                    stored_override.ssh_alias,
                    stored_override.default_dir,
                    stored_override.agent,
                ]
            ):
                application_config.stored.pop(name, None)

            save_config(application_config)

        rebuild_boxes(application_config)
        deps.clear_connect_failure(name)
        # Pooled connections were opened with the old settings, and the pool
        # keeps its own 60 s failure cache: drop both so the next probe
        # reconnects with what the refresh just applied.
        await get_pool().invalidate(name)
        return APIRefreshResult(name=name, refreshed=True)

    @router.get("/boxes/{name}/stats", response_model=APIBoxStats)
    async def api_box_stats(
        name: str,
        application_config: AppConfig = Depends(deps.get_application_config),
    ) -> APIBoxStats:
        """Get system statistics (CPU, memory, uptime) for a box."""
        box = deps.get_box_or_404(application_config, name)

        if box.transport == "local":
            # Local box: use psutil
            return await _get_local_stats(name)

        # Remote box: use SSH commands on a pooled connection
        try:
            return await deps.run_pooled(
                box, application_config, lambda conn: _get_remote_stats(name, conn)
            )
        except Exception as e:
            logger.warning(f"Failed to get stats for {name}: {e}")
            return APIBoxStats(name=name, error=str(e))

    @router.get("/boxes/{name}/git", response_model=APIGitInfo)
    async def api_git_info(
        name: str,
        directory: str = "/",
        application_config: AppConfig = Depends(deps.get_application_config),
    ) -> APIGitInfo:
        """Get git repository info for a directory on a box."""
        box = deps.get_box_or_404(application_config, name)
        # The UI polls this route: the request is bounded as run_pooled bounds one.
        limit = dependencies.PROBE_TIMEOUT_SECONDS
        try:
            async with box_runner(deps, box, application_config, deadline=limit) as runner:
                return await git_info(runner, directory)
        except Exception as e:
            logger.warning(f"Failed to get git info for {name}:{directory}: {e}")
            return APIGitInfo(error=str(e))

    @router.put("/boxes/{name}/theme", response_model=APIThemeResult)
    async def api_set_theme(
        name: str,
        payload: APIThemeUpdate,
        application_config: AppConfig = Depends(deps.get_application_config),
    ) -> APIThemeResult:
        """Set terminal theme preference for a box."""
        theme = payload.terminal_theme
        if theme and theme not in _VALID_THEMES:
            from fastapi import HTTPException

            raise HTTPException(
                status_code=400, detail=f"Invalid theme. Valid: {', '.join(sorted(_VALID_THEMES))}"
            )

        deps.get_box_or_404(application_config, name)
        stored = application_config.get_or_create_stored(name)
        stored.terminal_theme = theme or None
        save_config(application_config)
        rebuild_boxes(application_config)
        return APIThemeResult(status="ok", terminal_theme=theme or None)

    return router


async def _get_local_stats(name: str) -> APIBoxStats:
    """Get system stats for local machine using psutil."""
    try:
        import psutil

        # CPU usage (average over 0.1 second)
        cpu_percent = psutil.cpu_percent(interval=0.1)

        # Memory info
        mem = psutil.virtual_memory()
        memory_used_mb = mem.used / (1024 * 1024)
        memory_total_mb = mem.total / (1024 * 1024)
        memory_percent = mem.percent

        # Uptime
        import time

        uptime_seconds = int(time.time() - psutil.boot_time())

        return APIBoxStats(
            name=name,
            cpu_percent=cpu_percent,
            memory_used_mb=round(memory_used_mb, 1),
            memory_total_mb=round(memory_total_mb, 1),
            memory_percent=memory_percent,
            uptime_seconds=uptime_seconds,
        )
    except Exception as e:
        logger.warning(f"Failed to get local stats: {e}")
        return APIBoxStats(name=name, error=str(e))


async def _get_remote_stats(name: str, conn) -> APIBoxStats:
    """Get system stats for remote machine via SSH commands."""
    try:
        # Get CPU usage from /proc/stat (two samples for delta)
        cpu_result = await conn.run(
            "cat /proc/stat | head -1; sleep 0.2; cat /proc/stat | head -1",
            check=True,
        )
        cpu_percent = _parse_cpu_usage(cpu_result.stdout or "")

        # Get memory from /proc/meminfo
        mem_result = await conn.run(
            "cat /proc/meminfo | grep -E '^(MemTotal|MemAvailable):'",
            check=True,
        )
        memory_info = _parse_memory_info(mem_result.stdout or "")

        # Get uptime
        uptime_result = await conn.run("cat /proc/uptime", check=True)
        uptime_seconds = _parse_uptime(uptime_result.stdout or "")

        return APIBoxStats(
            name=name,
            cpu_percent=cpu_percent,
            memory_used_mb=memory_info.get("used_mb"),
            memory_total_mb=memory_info.get("total_mb"),
            memory_percent=memory_info.get("percent"),
            uptime_seconds=uptime_seconds,
        )
    except Exception as e:
        logger.warning(f"Failed to get remote stats for {name}: {e}")
        return APIBoxStats(name=name, error=str(e))


def _parse_cpu_usage(output: str) -> float | None:
    """Parse CPU usage from two /proc/stat samples."""
    lines = [line for line in output.strip().split("\n") if line.startswith("cpu ")]
    if len(lines) < 2:
        return None

    def parse_cpu_line(line: str) -> tuple[int, int]:
        parts = line.split()[1:]  # Skip 'cpu' prefix
        if len(parts) < 4:
            return (0, 0)
        user, nice, system, idle = map(int, parts[:4])
        iowait = int(parts[4]) if len(parts) > 4 else 0
        total = user + nice + system + idle + iowait
        active = user + nice + system
        return (total, active)

    total1, active1 = parse_cpu_line(lines[0])
    total2, active2 = parse_cpu_line(lines[1])

    total_delta = total2 - total1
    active_delta = active2 - active1

    if total_delta == 0:
        return 0.0

    return round((active_delta / total_delta) * 100, 1)


def _parse_memory_info(output: str) -> dict[str, float]:
    """Parse memory info from /proc/meminfo output."""
    result: dict[str, float] = {}
    for line in output.strip().split("\n"):
        if "MemTotal:" in line:
            match = re.search(r"(\d+)", line)
            if match:
                result["total_kb"] = int(match.group(1))
        elif "MemAvailable:" in line:
            match = re.search(r"(\d+)", line)
            if match:
                result["available_kb"] = int(match.group(1))

    if "total_kb" in result:
        total_mb = result["total_kb"] / 1024
        result["total_mb"] = round(total_mb, 1)

        if "available_kb" in result:
            available_mb = result["available_kb"] / 1024
            used_mb = total_mb - available_mb
            result["used_mb"] = round(used_mb, 1)
            result["percent"] = round((used_mb / total_mb) * 100, 1)

    return result


def _parse_uptime(output: str) -> int | None:
    """Parse uptime from /proc/uptime output."""
    try:
        # Format: "123456.78 654321.12" (uptime_seconds idle_seconds)
        parts = output.strip().split()
        if parts:
            return int(float(parts[0]))
    except (ValueError, IndexError):
        pass
    return None


GIT_INFO_TIMEOUT = 5.0


# One byte of `git status --porcelain` shows that it printed a line. The first byte of
# a line is ASCII, so the cut never splits a character.
GIT_STATUS_BYTES = 1


def git_info_words() -> tuple[list[str], list[str], list[str]]:
    """The three commands of the git badge: the git folder then the branch (the folder
    is printed even when the branch fails, in a repository with no commit), the short
    commit, and the changes of the tree."""
    return (
        ["git", "rev-parse", "--git-dir", "--abbrev-ref", "HEAD"],
        ["git", "rev-parse", "--short", "HEAD"],
        ["git", "status", "--porcelain"],
    )


def remote_git_info_commands(directory: str) -> tuple[str, str, str]:
    """The three lines the remote runner sends for the git badge of ``directory``
    (``~`` already resolved)."""
    head, commit, status = git_info_words()
    return (
        remote_command_line(head, directory),
        remote_command_line(commit, directory),
        remote_command_line(status, directory, GIT_STATUS_BYTES),
    )


class GitTimedOutError(Exception):
    """A command of the git badge ran past its limit."""


async def git_info(runner: BoxRunner, directory: str) -> APIGitInfo:
    """The git badge of ``directory`` on the runner's box: the same three commands and
    the same answer on both kinds. A command past ``GIT_INFO_TIMEOUT`` ends the badge at
    once with an error that says so; no later command is run."""
    try:
        folder = await runner.folder(directory)
    except ValidationError:
        return APIGitInfo(is_repo=False)

    async def run(words: list[str], output_limit: int | None = None) -> Ran:
        try:
            return await runner.run(
                words, folder=folder, timeout=GIT_INFO_TIMEOUT, output_limit=output_limit
            )
        except TimeoutError:
            raise GitTimedOutError(
                f"git in {directory} timed out after {GIT_INFO_TIMEOUT:g} s"
            ) from None
        except ProgramNotFoundError:
            return Ran(1, "")

    head_words, commit_words, status_words = git_info_words()
    try:
        return await _git_info_answer(run, head_words, commit_words, status_words)
    except GitTimedOutError as exc:
        return APIGitInfo(error=str(exc))


async def _git_info_answer(run, head_words, commit_words, status_words) -> APIGitInfo:
    head = await run(head_words)
    lines = head.output.strip().splitlines()
    if not lines:
        return APIGitInfo(is_repo=False)
    branch = lines[-1] if head.ok and len(lines) >= 2 else None

    commit_ran = await run(commit_words)
    commit = commit_ran.output.strip() if commit_ran.ok and commit_ran.output.strip() else None

    status_ran = await run(status_words, GIT_STATUS_BYTES)
    dirty = bool(status_ran.output)

    return APIGitInfo(is_repo=True, branch=branch, commit=commit, dirty=dirty)
