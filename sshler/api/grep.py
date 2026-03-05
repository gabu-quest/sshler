"""File content search (grep) API."""

from __future__ import annotations

import asyncio
import logging
import shlex
import shutil

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from ..config import AppConfig
from ..ssh import SSHError
from ..ssh_pool import get_pool
from ..validation import PathValidator, ValidationError
from .dependencies import APIDependencies
from .helpers import _normalize_local_path, _normalize_directory_path
from .rate_limiting import rate_limit_grep

logger = logging.getLogger(__name__)

MAX_PATTERN_LENGTH = 500
GREP_TIMEOUT = 15


class APIGrepMatch(BaseModel):
    file: str
    line_number: int
    line: str


class APIGrepResponse(BaseModel):
    box: str
    pattern: str
    directory: str
    matches: list[APIGrepMatch]
    truncated: bool


def _parse_grep_output(output: str, limit: int) -> tuple[list[APIGrepMatch], bool]:
    """Parse grep output in filename:line_number:line format."""
    matches: list[APIGrepMatch] = []
    for line in output.split("\n"):
        if not line.strip():
            continue
        # Split on first two colons: file:lineno:content
        parts = line.split(":", 2)
        if len(parts) < 3:
            continue
        try:
            line_number = int(parts[1])
        except ValueError:
            continue
        content = parts[2][:500]  # Truncate long lines
        matches.append(APIGrepMatch(
            file=parts[0],
            line_number=line_number,
            line=content,
        ))
        if len(matches) >= limit:
            return matches, True
    return matches, False


def get_router(deps: APIDependencies) -> APIRouter:
    router = APIRouter()

    @router.get("/boxes/{name}/grep", response_model=APIGrepResponse)
    async def api_grep(
        name: str,
        pattern: str = Query(..., min_length=1, description="Search pattern"),
        directory: str = Query("/", description="Directory to search in"),
        case_sensitive: bool = Query(False),
        limit: int = Query(100, ge=1, le=500),
        application_config: AppConfig = Depends(deps.get_application_config),
        _rate_limit: None = Depends(rate_limit_grep),
    ) -> APIGrepResponse:
        if len(pattern) > MAX_PATTERN_LENGTH:
            raise HTTPException(status_code=400, detail=f"Pattern too long (max {MAX_PATTERN_LENGTH} chars)")
        if "\0" in pattern:
            raise HTTPException(status_code=400, detail="Pattern cannot contain null bytes")

        box = deps.get_box_or_404(application_config, name)

        if box.transport == "local":
            normalized = _normalize_local_path(directory)
            grep_path = shutil.which("grep")
            if not grep_path:
                raise HTTPException(status_code=500, detail="grep not available")

            args = [grep_path, "-rn", f"--max-count={limit}", "--"]
            if not case_sensitive:
                args.insert(2, "-i")
            args.append(pattern)
            args.append(normalized)

            try:
                proc = await asyncio.create_subprocess_exec(
                    *args,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=GREP_TIMEOUT)
            except asyncio.TimeoutError:
                raise HTTPException(status_code=504, detail="Search timed out")

            output = stdout.decode("utf-8", errors="replace")
            matches, truncated = _parse_grep_output(output, limit)

            return APIGrepResponse(
                box=box.name,
                pattern=pattern,
                directory=normalized,
                matches=matches,
                truncated=truncated,
            )

        # Remote box
        try:
            validated_dir = PathValidator.validate_remote_path(directory)
        except ValidationError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

        # Build command as list, then join with shlex.quote for safety
        cmd_parts = ["grep", "-rn"]
        if not case_sensitive:
            cmd_parts.append("-i")
        cmd_parts.extend([f"-m", str(limit), "--", pattern, validated_dir])
        cmd = " ".join(shlex.quote(p) for p in cmd_parts) + " 2>/dev/null"

        ssh_pool = get_pool()
        try:
            async with ssh_pool.connection(
                box, lambda: deps.connect_for_box(box, application_config)
            ) as connection:
                try:
                    result = await asyncio.wait_for(
                        connection.run(cmd, check=False),
                        timeout=GREP_TIMEOUT,
                    )
                except asyncio.TimeoutError:
                    raise HTTPException(status_code=504, detail="Search timed out")

                output = result.stdout or ""
                matches, truncated = _parse_grep_output(output, limit)

                return APIGrepResponse(
                    box=box.name,
                    pattern=pattern,
                    directory=validated_dir,
                    matches=matches,
                    truncated=truncated,
                )
        except HTTPException:
            raise
        except SSHError as exc:
            raise HTTPException(status_code=502, detail=str(exc))

    return router
