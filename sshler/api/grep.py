"""File content search (grep) API."""

from __future__ import annotations

import asyncio
import logging
import os
import re
import shlex
import shutil
import time

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from ..config import AppConfig
from ..ssh import SSHError
from ..ssh_pool import get_pool
from ..validation import PathValidator, ValidationError
from . import helpers
from .dependencies import APIDependencies
from .files import _remote_path
from .helpers import _normalize_local_path
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


# POSIX character classes inside a bracket expression, as Python set members.
_BRE_CLASSES = {
    "alpha": "a-zA-Z",
    "digit": "0-9",
    "alnum": "a-zA-Z0-9",
    "upper": "A-Z",
    "lower": "a-z",
    "space": r" \t\n\r\f\v",
    "blank": r" \t",
    "punct": r"!-/:-@\[-`{-~",
    "xdigit": "0-9A-Fa-f",
    "cntrl": r"\x00-\x1f\x7f",
    "print": r" -~",
    "graph": r"!-~",
}
# GNU escapes that keep their meaning after translation.
_BRE_ESCAPES = {
    "(": "(",
    ")": ")",
    "{": "{",
    "}": "}",
    "|": "|",
    "+": "+",
    "?": "?",
    "<": r"\b(?=\w)",
    ">": r"\b(?<=\w)",
    "b": r"\b",
    "B": r"\B",
    "w": r"\w",
    "W": r"\W",
    "s": r"\s",
    "S": r"\S",
    "`": r"\A",
    "'": r"\Z",
}


def _bre_bracket(pattern: str, start: int) -> tuple[str, int]:
    """Translate the bracket expression opening at ``pattern[start]``.

    Returns the Python set and the index after its closing ``]``; an unclosed
    ``[`` raises ``re.error``, as GNU grep rejects it ("Unmatched [").
    """
    i = start + 1
    out = "["
    if i < len(pattern) and pattern[i] == "^":
        out += "^"
        i += 1
    first = True
    while i < len(pattern):
        char = pattern[i]
        if char == "]" and not first:
            return out + "]", i + 1
        first = False
        if char == "[" and pattern.startswith("[:", i):
            end = pattern.find(":]", i + 2)
            name = pattern[i + 2 : end] if end != -1 else ""
            if name in _BRE_CLASSES:
                out += _BRE_CLASSES[name]
                i = end + 2
                continue
        out += "\\" + char if char in "\\[]^" else char
        i += 1
    raise re.error("unmatched [")


def _bre_to_python(pattern: str) -> str:
    """Translate a GNU basic regular expression (grep's default) to Python ``re``.

    ``\\( \\) \\{ \\} \\| \\+ \\?`` are operators and their bare forms are
    literals; ``*`` at the start of an expression is literal; ``^`` and ``$``
    anchor only at the ends of an expression.
    """
    out: list[str] = []
    i = 0
    at_start = True  # start of the whole pattern, of a group or of an alternative
    while i < len(pattern):
        char = pattern[i]
        if char == "\\" and i + 1 == len(pattern):
            raise re.error("trailing backslash")  # GNU grep: "Trailing backslash"
        if char == "\\":
            nxt = pattern[i + 1]
            if nxt in _BRE_ESCAPES:
                out.append(_BRE_ESCAPES[nxt])
            elif nxt.isdigit() and nxt != "0":
                out.append("\\" + nxt)
            else:
                out.append(re.escape(nxt))
            at_start = nxt in "(|"
            i += 2
            continue
        if char == "[":
            translated, i = _bre_bracket(pattern, i)
            out.append(translated)
            at_start = False
            continue
        if char == "^" and at_start:
            out.append("^")
        elif char == "$" and (
            i + 1 == len(pattern) or pattern.startswith(("\\)", "\\|"), i + 1)
        ):
            out.append("$")
        elif char == "*" and not at_start:
            out.append("*")
        elif char == ".":
            out.append(".")
        else:
            out.append(re.escape(char))
        at_start = at_start and char == "^"
        i += 1
    return "".join(out)


class _GrepDeadlineError(Exception):
    """The built-in search ran past GREP_TIMEOUT."""


def _grep_builtin(
    directory: str, pattern: str, case_sensitive: bool, limit: int, timeout: float
) -> tuple[list[APIGrepMatch], bool]:
    """``grep -rn [-i] --max-count=limit -- pattern directory`` without a grep binary.

    Used for a Windows local box, which has no GNU grep. Same results as the
    GNU path: basic-regex syntax, newline-separated alternatives, every file
    under ``directory`` (nothing pruned), symlinks below ``directory`` skipped,
    files holding a NUL byte or invalid UTF-8 treated as binary (no lines),
    paths joined with ``/`` onto ``directory``, at most ``limit`` matches per
    file and overall. The pattern is only ever compiled, never executed.
    """
    flags = 0 if case_sensitive else re.IGNORECASE
    try:
        regex = re.compile(
            "|".join(f"(?:{_bre_to_python(part)})" for part in pattern.split("\n")), flags
        )
    except re.error:
        return [], False  # GNU grep exits 2 on a bad pattern and prints no matches
    deadline = time.monotonic() + timeout
    matches: list[APIGrepMatch] = []

    def scan_file(path: str) -> bool:
        try:
            with open(path, "rb") as handle:
                data = handle.read()
        except OSError:
            return False
        if b"\0" in data:
            return False
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            return False
        lines = text.split("\n")
        if lines and lines[-1] == "":
            lines.pop()
        found = 0
        for number, line in enumerate(lines, start=1):
            if regex.search(line):
                matches.append(APIGrepMatch(file=path, line_number=number, line=line[:500]))
                if len(matches) >= limit:
                    return True
                found += 1
                if found >= limit:
                    break
        return False

    def walk(path: str) -> bool:
        if time.monotonic() > deadline:
            raise _GrepDeadlineError
        try:
            with os.scandir(path) as entries:
                listed = list(entries)
        except OSError:
            return False
        prefix = path if path.endswith("/") else path + "/"
        for entry in listed:
            child = prefix + entry.name
            try:
                if entry.is_symlink():
                    continue
                if entry.is_dir(follow_symlinks=False):
                    if walk(child):
                        return True
                elif entry.is_file(follow_symlinks=False) and scan_file(child):
                    return True
            except OSError:
                continue
        return False

    # A file operand is not searched: GNU grep prints it without a file name,
    # which _parse_grep_output drops, so the GNU path returns no matches either.
    truncated = walk(directory) if os.path.isdir(directory) else False
    return matches, truncated


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
            raise HTTPException(
                status_code=400, detail=f"Pattern too long (max {MAX_PATTERN_LENGTH} chars)"
            )
        if "\0" in pattern:
            raise HTTPException(status_code=400, detail="Pattern cannot contain null bytes")

        box = deps.get_box_or_404(application_config, name)

        if box.transport == "local":
            normalized = _normalize_local_path(directory)
            if helpers.LOCAL_IS_WINDOWS:
                try:
                    matches, truncated = await asyncio.to_thread(
                        _grep_builtin, normalized, pattern, case_sensitive, limit, GREP_TIMEOUT
                    )
                except _GrepDeadlineError as exc:
                    raise HTTPException(status_code=504, detail="Search timed out") from exc
                return APIGrepResponse(
                    box=box.name,
                    pattern=pattern,
                    directory=normalized,
                    matches=matches,
                    truncated=truncated,
                )

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
            except TimeoutError as exc:
                raise HTTPException(status_code=504, detail="Search timed out") from exc

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
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        ssh_pool = get_pool()
        try:
            async with ssh_pool.connection(
                box, lambda: deps.connect_for_box(box, application_config)
            ) as connection:
                # The quoted shell argument would not expand `~`: resolve it here.
                validated_dir = await _remote_path(connection, validated_dir)

                # Build command as list, then join with shlex.quote for safety
                cmd_parts = ["grep", "-rn"]
                if not case_sensitive:
                    cmd_parts.append("-i")
                cmd_parts.extend(["-m", str(limit), "--", pattern, validated_dir])
                cmd = " ".join(shlex.quote(p) for p in cmd_parts) + " 2>/dev/null"

                try:
                    result = await asyncio.wait_for(
                        connection.run(cmd, check=False),
                        timeout=GREP_TIMEOUT,
                    )
                except TimeoutError as exc:
                    raise HTTPException(status_code=504, detail="Search timed out") from exc

                remote_out = result.stdout or ""
                output = (
                    remote_out
                    if isinstance(remote_out, str)
                    else remote_out.decode("utf-8", errors="replace")
                )
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
        except ValidationError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except SSHError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    return router
