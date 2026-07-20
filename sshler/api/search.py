"""Directory search API with frecency ranking."""

from __future__ import annotations

import asyncio
import logging
import shutil
from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from .. import state
from ..config import AppConfig
from ..ssh import SSHError
from ..ssh_pool import get_pool
from .dependencies import APIDependencies

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


class SearchResult(BaseModel):
    """A single search result with path and score."""

    path: str
    score: float
    source: str  # 'frecency' or 'discovery'


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
    except asyncio.TimeoutError:
        logger.warning("zoxide query timed out")
        return []
    except Exception as exc:
        logger.warning(f"zoxide query failed: {exc}")
        return []


async def _discover_remote_directories(
    connection,
    pattern: str,
    max_depth: int = 4,
    limit: int = 50,
) -> list[str]:
    """Discover directories on remote host using find command.

    Returns list of discovered paths.
    """
    # Escape pattern for shell
    safe_pattern = pattern.replace("'", "'\"'\"'")

    # Use find with case-insensitive glob matching
    cmd = f"find ~ -maxdepth {max_depth} -type d -iname '*{safe_pattern}*' 2>/dev/null | head -n {limit}"

    try:
        result = await asyncio.wait_for(
            connection.run(cmd, check=False),
            timeout=10.0,
        )
        if result.exit_status != 0:
            return []

        paths = [
            line.strip()
            for line in result.stdout.split("\n")
            if line.strip()
        ]
        return paths
    except asyncio.TimeoutError:
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
        application_config: AppConfig = Depends(deps.get_application_config),
    ) -> SearchResponse:
        """Search directories by name with frecency ranking.

        For local box: uses zoxide if available.
        For remote boxes: combines frecency tracking with directory discovery.
        """
        box = deps.get_box_or_404(application_config, name)

        results: list[SearchResult] = []
        seen_paths: set[str] = set()

        if box.transport == "local":
            # Use zoxide for local box
            zoxide_results = await _query_zoxide(q)
            for path, score in zoxide_results[:limit]:
                if path not in seen_paths:
                    results.append(SearchResult(path=path, score=score, source="frecency"))
                    seen_paths.add(path)
        else:
            # For remote boxes: combine frecency + discovery
            # 1. Get frecency-ranked results from SQLite
            frecency_results = await state.search_directories_async(box.name, q, limit)
            for path, score in frecency_results:
                if path not in seen_paths:
                    results.append(SearchResult(path=path, score=score, source="frecency"))
                    seen_paths.add(path)

            # 2. Discover new directories via SSH find
            ssh_pool = get_pool()
            try:
                async with ssh_pool.connection(
                    box, lambda: deps.connect_for_box(box, application_config)
                ) as connection:
                    discovered = await _discover_remote_directories(
                        connection, q, max_depth=4, limit=limit
                    )
                    for path in discovered:
                        if path not in seen_paths:
                            # Give discovered (but not yet visited) directories a lower score
                            results.append(
                                SearchResult(path=path, score=0.1, source="discovery")
                            )
                            seen_paths.add(path)
            except SSHError as exc:
                logger.warning(f"SSH error during directory discovery: {exc}")
                # Continue with frecency-only results
            except Exception as exc:
                logger.warning(f"Error during directory discovery: {exc}")

        # Sort by score descending and limit
        results.sort(key=lambda r: r.score, reverse=True)
        results = results[:limit]

        return SearchResponse(box=box.name, query=q, results=results)

    return router
