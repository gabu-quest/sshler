from __future__ import annotations

import asyncio
import re
from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends

from .. import state
from ..config import AppConfig, Box, StoredBox, rebuild_boxes, save_config
from ..ssh_pool import get_pool
from . import dependencies
from .box_files import is_local
from .box_runner import BoxRunner, Ran, box_runner, remote_command_line
from .dependencies import APIDependencies
from .failure import (
    REASON_FAILED,
    REASON_INVALID,
    REASON_TIMEOUT,
    REASON_UNREACHABLE,
    Failure,
    failure_of,
)
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

    # "offline" is an ordinary answer of this probe: it says why in `error` and
    # `reason`, which an online answer does not carry.
    @router.get(
        "/boxes/{name}/status", response_model=APIBoxStatus, response_model_exclude_unset=True
    )
    async def api_box_status(
        name: str,
        application_config: AppConfig = Depends(deps.get_application_config),
    ) -> APIBoxStatus:
        box = deps.get_box_or_404(application_config, name)
        # Bounded as every polled route is; the local box answers at once.
        limit = dependencies.PROBE_TIMEOUT_SECONDS
        try:
            async with box_runner(deps, box, application_config, deadline=limit) as runner:
                seconds = await runner.reach(timeout=limit)
        except Exception as exc:
            return _offline(box.name, failure_of(exc))
        if seconds is None:
            return _offline(box.name, Failure(REASON_UNREACHABLE, f"{box.name} did not answer"))
        return APIBoxStatus(name=box.name, status="online", latency_ms=seconds * 1000)

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
        return await box_stats(
            deps, deps.get_box_or_404(application_config, name), application_config
        )

    @router.get("/boxes/{name}/git", response_model=APIGitInfo)
    async def api_git_info(
        name: str,
        directory: str = "/",
        application_config: AppConfig = Depends(deps.get_application_config),
    ) -> APIGitInfo:
        """Get git repository info for a directory on a box."""
        box = deps.get_box_or_404(application_config, name)
        # The UI polls this route: the connect and every command are bounded.
        limit = dependencies.PROBE_TIMEOUT_SECONDS
        async with box_runner(deps, box, application_config, deadline=limit) as runner:
            return await git_info(runner, directory)

    @router.put("/boxes/{name}/theme", response_model=APIThemeResult)
    async def api_set_theme(
        name: str,
        payload: APIThemeUpdate,
        application_config: AppConfig = Depends(deps.get_application_config),
    ) -> APIThemeResult:
        """Set terminal theme preference for a box."""
        theme = payload.terminal_theme
        if theme and theme not in _VALID_THEMES:
            raise Failure(
                REASON_INVALID, f"Invalid theme. Valid: {', '.join(sorted(_VALID_THEMES))}"
            )

        deps.get_box_or_404(application_config, name)
        stored = application_config.get_or_create_stored(name)
        stored.terminal_theme = theme or None
        save_config(application_config)
        rebuild_boxes(application_config)
        return APIThemeResult(status="ok", terminal_theme=theme or None)

    return router


def _offline(name: str, found: Failure) -> APIBoxStatus:
    return APIBoxStatus(
        name=name,
        status="offline",
        latency_ms=None,
        error=str(found.detail),
        reason=found.reason,
    )


async def _get_local_stats(name: str) -> APIBoxStats:
    """Get system stats for local machine using psutil."""
    import time

    import psutil

    # CPU usage (average over 0.1 second)
    cpu_percent = psutil.cpu_percent(interval=0.1)

    # Memory info
    mem = psutil.virtual_memory()
    memory_used_mb = mem.used / (1024 * 1024)
    memory_total_mb = mem.total / (1024 * 1024)
    memory_percent = mem.percent

    uptime_seconds = int(time.time() - psutil.boot_time())

    return APIBoxStats(
        name=name,
        cpu_percent=cpu_percent,
        memory_used_mb=round(memory_used_mb, 1),
        memory_total_mb=round(memory_total_mb, 1),
        memory_percent=memory_percent,
        uptime_seconds=uptime_seconds,
    )


async def box_stats(deps: APIDependencies, box: Box, application_config: AppConfig) -> APIBoxStats:
    """The CPU, memory and uptime of ``box``; the stats route and the stats stream both
    ask this. A failure is raised (the route answers it by reason, the stream puts its
    reason in the box's event).

    The local box reads them with psutil. A remote box reads ``/proc`` through its
    runner, bounded by ``PROBE_TIMEOUT_SECONDS`` for the connect and every command; past
    it the failure names the step and the connection is closed, not handed on."""
    if is_local(box):
        return await _get_local_stats(box.name)
    limit = dependencies.PROBE_TIMEOUT_SECONDS
    async with box_runner(deps, box, application_config, deadline=limit) as runner:
        return await _proc_stats(box.name, runner, limit)


# Two reads of /proc/stat this far apart give the CPU's use between them.
CPU_SAMPLE_GAP = 0.2
# The most a /proc read sends back: the uptime, the memory and the first line of
# /proc/stat fit many times over; the rest of /proc/stat (one line per CPU and the
# interrupt counts) is not needed.
PROC_READ_BYTES = 16_384


async def _proc_stats(name: str, runner: BoxRunner, timeout: float) -> APIBoxStats:
    """The stats of a box with Linux's ``/proc``, in two commands: ``/proc/stat``, then
    after ``CPU_SAMPLE_GAP`` the uptime, the memory and ``/proc/stat`` again. Each
    reading is picked from the output by the shape of its line, so a file or a line
    that is missing leaves only its own figure empty."""

    async def read(*files: str) -> str:
        ran = await runner.run(
            ["cat", *files], folder=None, timeout=timeout, output_limit=PROC_READ_BYTES
        )
        return ran.output

    first = await read("/proc/stat")
    await asyncio.sleep(CPU_SAMPLE_GAP)
    second = await read("/proc/uptime", "/proc/meminfo", "/proc/stat")

    samples = [line for line in (_cpu_line(first), _cpu_line(second)) if line is not None]
    cpu_percent = _parse_cpu_usage("\n".join(samples))
    memory_info = _parse_memory_info(second)
    uptime_seconds = _parse_uptime(_uptime_line(second))
    if cpu_percent is None and "total_mb" not in memory_info and uptime_seconds is None:
        raise Failure(REASON_FAILED, f"could not read /proc on {name}")
    return APIBoxStats(
        name=name,
        cpu_percent=cpu_percent,
        memory_used_mb=memory_info.get("used_mb"),
        memory_total_mb=memory_info.get("total_mb"),
        memory_percent=memory_info.get("percent"),
        uptime_seconds=uptime_seconds,
    )


def _cpu_line(output: str) -> str | None:
    """The first ``cpu `` line (all CPUs together) of ``/proc/stat`` output."""
    return next((line for line in output.splitlines() if line.startswith("cpu ")), None)


def _uptime_line(output: str) -> str:
    """The ``/proc/uptime`` line: the one line that starts with a digit (every line of
    ``/proc/meminfo`` and ``/proc/stat`` starts with its name)."""
    return next((line for line in output.splitlines() if line[:1].isdigit()), "")


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


async def git_info(runner: BoxRunner, directory: str) -> APIGitInfo:
    """The git badge of ``directory`` on the runner's box: the same three commands and
    the same answer on both kinds. A folder outside any repository is ``is_repo``
    False. A command past ``GIT_INFO_TIMEOUT`` raises ``timeout`` at once and no later
    command is run; a folder that fails its check, no git on the box and a box that
    cannot be reached are raised as they are."""
    folder = await runner.folder(directory)

    async def run(words: list[str], output_limit: int | None = None) -> Ran:
        try:
            return await runner.run(
                words, folder=folder, timeout=GIT_INFO_TIMEOUT, output_limit=output_limit
            )
        except TimeoutError:
            raise Failure(
                REASON_TIMEOUT, f"git in {directory} timed out after {GIT_INFO_TIMEOUT:g} s"
            ) from None

    head_words, commit_words, status_words = git_info_words()
    return await _git_info_answer(run, head_words, commit_words, status_words)


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
