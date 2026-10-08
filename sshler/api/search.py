"""Directory search API with frecency ranking."""

from __future__ import annotations

import asyncio
import logging
import os
import shlex
import shutil
from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from .. import state
from ..config import AppConfig
from ..ssh import SSHError
from ..ssh_pool import get_pool
from ..validation import PathValidator, ValidationError
from .dependencies import APIDependencies

if TYPE_CHECKING:
    pass

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
    """Response from directory search endpoint."""

    box: str
    query: str
    results: list[SearchResult]


async def _query_zoxide(pattern: str) -> list[tuple[str, float]]:
    """Query zoxide for matching directories.

    Returns list of (path, score) tuples.
    """
    zoxide_path = shutil.which("zoxide")
    if not zoxide_path:
        logger.debug("zoxide not installed, skipping local frecency lookup")
        return []

    try:
        proc = await asyncio.create_subprocess_exec(
            zoxide_path,
            "query",
            "-l",  # list mode
            "-s",  # include scores
            pattern,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=5.0)

        if proc.returncode != 0:
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


async def _discover_local(
    root: str,
    pattern: str,
    max_depth: int = 6,
    limit: int = 50,
    include_files: bool = False,
) -> list[tuple[str, bool]]:
    """Discover directories (and optionally files) on the local filesystem.

    Runs GNU find directly (no shell) under ``root``, pruning VCS/build noise.
    Returns list of (path, is_directory) tuples. Permission errors on
    subtrees are tolerated — find still prints the matches it can reach.
    """
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
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        try:
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=10.0)
        except TimeoutError:
            proc.kill()
            logger.warning("Local discovery timed out")
            return []

        results: list[tuple[str, bool]] = []
        for line in stdout.decode(errors="replace").splitlines():
            if "\t" not in line:
                continue
            entry_type, path = line.split("\t", 1)
            if path == root:
                continue
            results.append((path, entry_type == "d"))
            if len(results) >= limit:
                break
        return results
    except Exception as exc:
        logger.warning(f"Local discovery failed: {exc}")
        return []


def _remote_find_command(
    root: str | None,
    pattern: str,
    entry_type: str,
    max_depth: int,
    limit: int,
) -> str:
    # "~" must stay unquoted so the remote shell expands it
    if root is None or root in ("", "~"):
        root_expr = "~"
    else:
        root_expr = shlex.quote(root)
    return (
        f"find {root_expr} -maxdepth {max_depth} -type {entry_type} "
        f"-iname {shlex.quote(pattern)} 2>/dev/null | head -n {limit}"
    )


async def _discover_remote_directories(
    connection,
    pattern: str,
    max_depth: int = 4,
    limit: int = 50,
    root: str | None = None,
    include_files: bool = False,
) -> list[tuple[str, bool]]:
    """Discover directories (and optionally files) on remote host via find.

    Returns list of (path, is_directory) tuples.
    """
    separator = "---SSHLER-FILES---"
    cmd = _remote_find_command(root, pattern, "d", max_depth, limit)
    if include_files:
        files_cmd = _remote_find_command(root, pattern, "f", max_depth, limit)
        cmd = f"{cmd}; printf '%s\\n' {shlex.quote(separator)}; {files_cmd}"

    try:
        result = await asyncio.wait_for(
            connection.run(cmd, check=False),
            timeout=10.0,
        )

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
    except TimeoutError:
        logger.warning("Remote directory discovery timed out")
        return []
    except Exception as exc:
        logger.warning(f"Remote directory discovery failed: {exc}")
        return []


def get_router(deps: APIDependencies) -> APIRouter:
    router = APIRouter()

    @router.get("/boxes/{name}/search", response_model=SearchResponse)
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

        if root is not None:
            try:
                if box.transport == "local":
                    root = PathValidator.validate_local_path(root)
                else:
                    root = PathValidator.validate_remote_path(root)
            except ValidationError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc

        pattern = _find_pattern(q)
        results: list[SearchResult] = []
        seen_paths: set[str] = set()

        def add(path: str, score: float, source: str, is_directory: bool) -> None:
            if path not in seen_paths:
                results.append(
                    SearchResult(
                        path=path, score=score, source=source, is_directory=is_directory
                    )
                )
                seen_paths.add(path)

        if box.transport == "local":
            # Frecency-ranked directories from zoxide (skip when a wildcard
            # query is used — zoxide does its own substring matching)
            if "*" not in q and "?" not in q:
                for path, score in (await _query_zoxide(q))[:limit]:
                    add(path, score, "frecency", True)

            # Filesystem discovery under root (default: home)
            local_root = os.path.expanduser(root or "~")
            if os.path.isdir(local_root):
                discovered = await _discover_local(
                    local_root, pattern, limit=limit, include_files=files
                )
                for path, is_directory in discovered:
                    add(path, 0.1, "discovery", is_directory)
        else:
            # For remote boxes: combine frecency + discovery
            # 1. Get frecency-ranked results from SQLite
            frecency_results = await state.search_directories_async(box.name, q, limit)
            for path, score in frecency_results:
                add(path, score, "frecency", True)

            # 2. Discover directories/files via SSH find
            ssh_pool = get_pool()
            try:
                async with ssh_pool.connection(
                    box, lambda: deps.connect_for_box(box, application_config)
                ) as connection:
                    discovered_remote = await _discover_remote_directories(
                        connection,
                        pattern,
                        max_depth=4,
                        limit=limit,
                        root=root,
                        include_files=files,
                    )
                    for path, is_directory in discovered_remote:
                        # Give discovered (but not yet visited) entries a lower score
                        add(path, 0.1, "discovery", is_directory)
            except SSHError as exc:
                logger.warning(f"SSH error during directory discovery: {exc}")
                # Continue with frecency-only results
            except Exception as exc:
                logger.warning(f"Error during directory discovery: {exc}")

        # Frecency first, then discovery; shallower paths win ties
        results.sort(key=lambda r: (-r.score, len(r.path), r.path))
        results = results[:limit]

        return SearchResponse(box=box.name, query=q, results=results)

    return router
