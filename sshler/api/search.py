"""Directory search API with frecency ranking.

A search answers the folders visited before (zoxide on the local box, the SQLite
frecency table for a remote box) and what a walk of the box itself finds. When the walk
of the box did not run (a box that cannot be reached, no ``find`` on it, a walk past
its limit) the answer still gives the folders visited before, and says why the walk did
not run: the failure's text in ``error`` and its word in ``reason`` (``failure_of``).
Both are absent when it ran. A root the route refuses is ``invalid``.
"""

from __future__ import annotations

import asyncio
import fnmatch
import logging
import os
import shutil
import time
from collections.abc import Callable
from dataclasses import dataclass

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from .. import state
from ..config import AppConfig, Box
from ..proc import run_bounded
from ..ssh_pool import get_pool
from ..validation import PathValidator, ValidationError
from . import helpers
from .box_files import is_local
from .box_runner import COMMAND_NOT_FOUND, LocalRunner
from .dependencies import APIDependencies
from .failure import REASON_INVALID, Failure, failure_of
from .remote import command, path_word, pipe, quiet, remote_path, require, then
from .run_failures import ProgramNotFoundError

logger = logging.getLogger(__name__)

# Directories that never contain anything worth surfacing in a name search
PRUNE_DIRS = (".git", "node_modules", "__pycache__", ".venv", ".cache", ".tox")


def _find_pattern(query: str) -> str:
    """Build a find -iname pattern: user wildcards pass through, else substring."""
    if "*" in query or "?" in query:
        return query
    return f"*{query}*"


class SearchResult(BaseModel):
    """A single search result with path and score."""

    path: str
    score: float
    source: str  # 'frecency' or 'discovery'
    is_directory: bool = True


class SearchResponse(BaseModel):
    """Response from directory search endpoint. ``error`` and ``reason`` say why the
    walk of the box did not run; absent when it ran."""

    box: str
    query: str
    results: list[SearchResult]
    error: str | None = None
    reason: str | None = None


ZOXIDE_TIMEOUT = 5.0


async def _query_zoxide(pattern: str) -> list[tuple[str, float]]:
    """Query zoxide for matching directories.

    Returns list of (path, score) tuples.
    """
    zoxide_path = shutil.which("zoxide")
    if not zoxide_path:
        logger.debug("zoxide not installed, skipping local frecency lookup")
        return []

    try:
        returncode, stdout, _ = await run_bounded(
            [
                zoxide_path,
                "query",
                "-l",  # list mode
                "-s",  # include scores
                pattern,
            ],
            ZOXIDE_TIMEOUT,
        )

        if returncode != 0:
            # zoxide returns non-zero when no results found
            return []

        results: list[tuple[str, float]] = []
        for line in stdout.decode().strip().split("\n"):
            if not line.strip():
                continue
            # zoxide output format: "score path" (space-separated)
            parts = line.split(None, 1)
            if len(parts) == 2:
                try:
                    score = float(parts[0])
                    path = parts[1]
                    results.append((path, score))
                except ValueError:
                    continue

        return results
    except TimeoutError:
        logger.warning("zoxide query timed out")
        return []
    except Exception as exc:
        logger.warning(f"zoxide query failed: {exc}")
        return []


LOCAL_DISCOVERY_TIMEOUT = 10.0
REMOTE_DISCOVERY_TIMEOUT = 10.0


class DiscoveryTimeoutError(TimeoutError):
    """A walk of a box ran past its limit."""


def _discover_local_builtin(
    root: str,
    pattern: str,
    max_depth: int,
    limit: int,
    include_files: bool,
    timeout: float,
) -> list[tuple[str, bool]]:
    """The GNU ``find`` expression of ``_discover_local``, without a find binary.

    Used for a Windows local box, whose ``find`` is ``find.exe`` (a text
    search). Same results as the GNU path: preorder walk below ``root`` down to
    ``max_depth``, any entry named in PRUNE_DIRS (case-sensitive) neither listed
    nor entered, symlinks neither listed nor entered, case-insensitive glob on
    the entry name, regular files only with ``include_files``, ``root`` itself
    never listed, at most ``limit`` results. Paths are ``root`` with ``/``
    separators joined to the entry names with ``/``, the form the local file
    routes hand to the client on Windows. Past ``timeout`` it raises
    ``DiscoveryTimeoutError``, as the GNU path does when find is killed.
    """
    root = helpers.local_path_form(root)
    if os.path.basename(root.rstrip("/")) in PRUNE_DIRS:
        return []
    folded = pattern.lower()
    deadline = time.monotonic() + timeout
    results: list[tuple[str, bool]] = []

    def walk(path: str, depth: int) -> bool:
        if time.monotonic() > deadline:
            raise DiscoveryTimeoutError(_local_timed_out(timeout))
        try:
            with os.scandir(path) as entries:
                listed = list(entries)
        except OSError:
            return False
        prefix = path if path.endswith("/") else path + "/"
        for entry in listed:
            if entry.name in PRUNE_DIRS:
                continue
            try:
                if entry.is_symlink():
                    continue
                is_dir = entry.is_dir(follow_symlinks=False)
                is_file = not is_dir and entry.is_file(follow_symlinks=False)
            except OSError:
                continue
            child = prefix + entry.name
            wanted = is_dir or (include_files and is_file)
            if wanted and fnmatch.fnmatchcase(entry.name.lower(), folded):
                results.append((child, is_dir))
                if len(results) >= limit:
                    return True
            if is_dir and depth < max_depth and walk(child, depth + 1):
                return True
        return False

    walk(root, 1)
    return results


def _local_timed_out(limit: float) -> str:
    return f"directory search timed out after {limit:g} s"


async def _discover_local(
    root: str,
    pattern: str,
    max_depth: int = 6,
    limit: int = 50,
    include_files: bool = False,
    box: str = "local",
) -> list[tuple[str, bool]]:
    """Discover directories (and optionally files) on the local filesystem.

    Runs GNU find directly (no shell) under ``root``, pruning VCS/build noise;
    on Windows the same walk runs in Python (``_discover_local_builtin``).
    Returns list of (path, is_directory) tuples. Permission errors on
    subtrees are tolerated — find still prints the matches it can reach. No
    ``find`` raises ``ProgramNotFoundError`` naming ``box``; past
    ``LOCAL_DISCOVERY_TIMEOUT`` it raises ``DiscoveryTimeoutError``.
    """
    if helpers.LOCAL_IS_WINDOWS:
        return await asyncio.to_thread(
            _discover_local_builtin,
            root,
            pattern,
            max_depth,
            limit,
            include_files,
            LOCAL_DISCOVERY_TIMEOUT,
        )

    args = ["find", root, "-maxdepth", str(max_depth), "("]
    for index, name in enumerate(PRUNE_DIRS):
        if index:
            args.append("-o")
        args.extend(["-name", name])
    args.extend([")", "-prune", "-o"])
    if include_files:
        args.extend(["(", "-type", "d", "-o", "-type", "f", ")"])
    else:
        args.extend(["-type", "d"])
    args.extend(["-iname", pattern, "-printf", "%y\t%p\n"])

    try:
        # find's status is not read: a subtree it cannot enter makes it exit 1 after
        # printing every match it reached.
        ran = await LocalRunner(box).run(args, folder=None, timeout=LOCAL_DISCOVERY_TIMEOUT)
    except TimeoutError as exc:
        raise DiscoveryTimeoutError(_local_timed_out(LOCAL_DISCOVERY_TIMEOUT)) from exc

    results: list[tuple[str, bool]] = []
    for line in ran.output.splitlines():
        if "\t" not in line:
            continue
        entry_type, path = line.split("\t", 1)
        if path == root:
            continue
        results.append((path, entry_type == "d"))
        if len(results) >= limit:
            break
    return results


DISCOVERY_SEPARATOR = "---SSHLER-FILES---"


def remote_find_command(
    root: str,
    pattern: str,
    entry_type: str,
    max_depth: int,
    limit: int,
) -> str:
    """The remote ``find`` for names matching ``pattern`` under ``root`` (a path with
    any ``~`` already resolved: the remote shell is never asked to expand one)."""
    find = command(
        "find",
        path_word(root),
        "-maxdepth",
        str(max_depth),
        "-type",
        entry_type,
        "-iname",
        pattern,
    )
    return pipe(quiet(find), command("head", "-n", str(limit)))


def remote_discovery_command(
    root: str, pattern: str, max_depth: int, limit: int, include_files: bool
) -> str:
    """Folders under ``root`` matching ``pattern``; with ``include_files``, then a
    ``DISCOVERY_SEPARATOR`` line and the matching files. Status 127 when the box has no
    ``find``."""
    folders = remote_find_command(root, pattern, "d", max_depth, limit)
    if include_files:
        files = remote_find_command(root, pattern, "f", max_depth, limit)
        folders = then(folders, command("printf", "%s\\n", DISCOVERY_SEPARATOR), files)
    # Each find is piped into head, which has the status of the pipeline: the line
    # begins by checking for find, so a box without it exits 127.
    return then(require("find"), folders)


async def _discover_remote_directories(
    connection,
    pattern: str,
    max_depth: int = 4,
    limit: int = 50,
    root: str | None = None,
    include_files: bool = False,
    box: str = "remote",
) -> list[tuple[str, bool]]:
    """Discover directories (and optionally files) on remote host via find.

    Returns list of (path, is_directory) tuples. A root that cannot be resolved, a
    connection that fails and a walk past ``REMOTE_DISCOVERY_TIMEOUT`` each raise; no
    ``find`` on the box (the shell's status 127) raises ``ProgramNotFoundError``
    naming ``box``, as the runner does. Any other status is not read: a subtree find
    cannot enter makes it exit 1 after printing every match it reached.
    """
    separator = DISCOVERY_SEPARATOR
    # No root means the home folder, as in a shell.
    resolved_root = await remote_path(connection, root or "~")
    cmd = remote_discovery_command(resolved_root, pattern, max_depth, limit, include_files)
    try:
        result = await asyncio.wait_for(
            connection.run(cmd, check=False), timeout=REMOTE_DISCOVERY_TIMEOUT
        )
    except TimeoutError as exc:
        raise DiscoveryTimeoutError(
            f"directory search timed out after {REMOTE_DISCOVERY_TIMEOUT:g} s"
        ) from exc
    if result.exit_status == COMMAND_NOT_FOUND:
        raise ProgramNotFoundError("find", box)

    results: list[tuple[str, bool]] = []
    in_files = False
    for line in result.stdout.split("\n"):
        line = line.strip()
        if not line:
            continue
        if line == separator:
            in_files = True
            continue
        results.append((line, not in_files))
    return results


# One result before the merge: (path, score, source, is_directory).
Found = tuple[str, float, str, bool]

# A discovered entry that was never visited ranks below every frecency result.
DISCOVERY_SCORE = 0.1


@dataclass(frozen=True)
class Searched:
    """What a search of a box found, and why the walk of the box did not run (None
    when it ran)."""

    found: list[Found]
    failure: Failure | None = None


def _checked_root(root: str | None, validate: Callable[[str], str]) -> str | None:
    """``root`` checked by ``validate``; a bad one is ``invalid`` with its text."""
    if root is None:
        return None
    try:
        return validate(root)
    except ValidationError as exc:
        raise Failure(REASON_INVALID, str(exc)) from exc


async def _search_local(
    box: str, q: str, pattern: str, limit: int, root: str | None, include_files: bool
) -> Searched:
    """zoxide's folders first (not for a wildcard query: zoxide matches substrings
    itself), then a walk under ``root`` (the home when none)."""
    root = _checked_root(root, PathValidator.validate_local_path)
    found: list[Found] = []
    if "*" not in q and "?" not in q:
        for path, score in (await _query_zoxide(q))[:limit]:
            found.append((path, score, "frecency", True))
    local_root = os.path.expanduser(root or "~")
    if os.path.isdir(local_root):
        try:
            discovered = await _discover_local(
                local_root, pattern, limit=limit, include_files=include_files, box=box
            )
        except Exception as exc:
            return Searched(found, failure_of(exc))
        for path, is_directory in discovered:
            found.append((path, DISCOVERY_SCORE, "discovery", is_directory))
    return Searched(found)


async def _search_remote(
    deps: APIDependencies,
    box: Box,
    application_config: AppConfig,
    q: str,
    pattern: str,
    limit: int,
    root: str | None,
    include_files: bool,
) -> Searched:
    """The folders visited before (the SQLite frecency table), then ``find`` on the
    box over one pooled connection. A box that cannot be reached gives the frecency
    results and the failure."""
    root = _checked_root(root, PathValidator.validate_remote_path)
    found: list[Found] = [
        (path, score, "frecency", True)
        for path, score in await state.search_directories_async(box.name, q, limit)
    ]
    ssh_pool = get_pool()
    try:
        async with ssh_pool.connection(
            box, lambda: deps.connect_for_box(box, application_config)
        ) as connection:
            discovered = await _discover_remote_directories(
                connection,
                pattern,
                max_depth=4,
                limit=limit,
                root=root,
                include_files=include_files,
                box=box.name,
            )
    except Exception as exc:
        return Searched(found, failure_of(exc))
    for path, is_directory in discovered:
        found.append((path, DISCOVERY_SCORE, "discovery", is_directory))
    return Searched(found)


async def search_box(
    deps: APIDependencies,
    box: Box,
    application_config: AppConfig,
    q: str,
    limit: int,
    root: str | None,
    include_files: bool,
) -> Searched:
    """The one place a search tells the kinds apart: each kind gathers its frecency
    results, then what it discovers, in the same shape, with the failure of the walk
    when it did not run; a bad ``root`` raises ``invalid``."""
    pattern = _find_pattern(q)
    if is_local(box):
        return await _search_local(box.name, q, pattern, limit, root, include_files)
    return await _search_remote(
        deps, box, application_config, q, pattern, limit, root, include_files
    )


def get_router(deps: APIDependencies) -> APIRouter:
    router = APIRouter()

    # `error` and `reason` are absent from an answer whose walk ran.
    @router.get(
        "/boxes/{name}/search", response_model=SearchResponse, response_model_exclude_none=True
    )
    async def api_search_directories(
        name: str,
        q: str = Query(..., min_length=2, description="Search query (min 2 chars)"),
        limit: int = Query(20, ge=1, le=100, description="Max results to return"),
        root: str | None = Query(None, description="Directory to search under (default: home)"),
        files: bool = Query(False, description="Include files, not just directories"),
        application_config: AppConfig = Depends(deps.get_application_config),
    ) -> SearchResponse:
        """Search directories (and optionally files) by name.

        Supports * and ? wildcards in the query; plain text matches as a
        case-insensitive substring. Frecency results (zoxide locally, SQLite
        for remote boxes) rank first; filesystem discovery via find fills in
        the rest, scoped under ``root``.
        """
        box = deps.get_box_or_404(application_config, name)
        searched = await search_box(deps, box, application_config, q, limit, root, files)

        results: list[SearchResult] = []
        seen_paths: set[str] = set()
        for path, score, source, is_directory in searched.found:
            if path not in seen_paths:
                results.append(
                    SearchResult(path=path, score=score, source=source, is_directory=is_directory)
                )
                seen_paths.add(path)

        # Frecency first, then discovery; shallower paths win ties
        results.sort(key=lambda r: (-r.score, len(r.path), r.path))
        results = results[:limit]

        failure = searched.failure
        if failure is None:
            return SearchResponse(box=box.name, query=q, results=results)
        return SearchResponse(
            box=box.name,
            query=q,
            results=results,
            error=str(failure.detail),
            reason=failure.reason,
        )

    return router
