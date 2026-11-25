"""File content search (grep) API.

The search runs ``grep`` through the runner of the box (``box_runner``), with the same
words on both kinds. One test of the kind of box stays: the local box on Windows has no
grep and searches in-process with ``_grep_builtin``.

grep's exit status 0 (matches) and 1 (none) are the ordinary answers. Status 2 is
grep's one word for trouble, both for a pattern it refuses and for a file or folder
it could not read, which a recursive search meets often (it still prints what it
found). The two are told apart by grep's status on an input with no line: 2 there is
a refused pattern, ``invalid`` with grep's own words; otherwise the search answers
what grep found. When it found nothing and said 2, the box is asked whether the folder
was there to search (``_searched``): a folder that is not there is ``not_found``, one
that cannot be read ``denied``, and a folder searched but for a file grep could not
read is the ordinary "no matches". Any other status (grep killed, say) is ``failed``.
No grep on the box is ``program_missing`` (from the runner).
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import time

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from ..config import AppConfig
from . import helpers
from .box_runner import BoxRunner, Ran, box_runner, remote_command_line
from .dependencies import APIDependencies
from .failure import (
    REASON_DENIED,
    REASON_FAILED,
    REASON_INVALID,
    REASON_NOT_FOUND,
    REASON_TIMEOUT,
    Failure,
)
from .helpers import _normalize_local_path
from .rate_limiting import rate_limit_grep

logger = logging.getLogger(__name__)

MAX_PATTERN_LENGTH = 500
GREP_TIMEOUT = 15
# grep's exit statuses: a line matched, none did, trouble.
GREP_MATCHED = 0
GREP_NO_MATCH = 1
GREP_TROUBLE = 2
# An input with no line: grep reads the pattern and nothing else.
EMPTY_INPUT = "/dev/null"


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


def grep_words(pattern: str, directory: str, case_sensitive: bool, limit: int) -> list[str]:
    """``grep`` for ``pattern`` under ``directory``, at most ``limit`` matches per file.
    ``--`` ends grep's options, so neither the pattern nor the folder is read as one."""
    words = ["grep", "-rn", *([] if case_sensitive else ["-i"])]
    return [*words, "-m", str(limit), "--", pattern, directory]


def pattern_check_words(pattern: str, case_sensitive: bool) -> list[str]:
    """``grep`` for ``pattern`` in an input with no line: status 2 only when grep
    refuses the pattern."""
    return ["grep", *([] if case_sensitive else ["-i"]), "--", pattern, EMPTY_INPUT]


def remote_grep_command(pattern: str, directory: str, case_sensitive: bool, limit: int) -> str:
    """The line the remote runner sends for the search: grep's error output kept, to say
    why it could not search."""
    return remote_command_line(
        grep_words(pattern, directory, case_sensitive, limit), keep_errors=True
    )


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
        matches.append(
            APIGrepMatch(
                file=parts[0],
                line_number=line_number,
                line=content,
            )
        )
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
        elif char == "$" and (i + 1 == len(pattern) or pattern.startswith(("\\)", "\\|"), i + 1)):
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
    # A pattern that does not compile raises its `re.error`, as GNU grep refuses it.
    regex = re.compile(
        "|".join(f"(?:{_bre_to_python(part)})" for part in pattern.split("\n")), flags
    )
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
            raise Failure(REASON_INVALID, f"Pattern too long (max {MAX_PATTERN_LENGTH} chars)")
        if "\0" in pattern:
            raise Failure(REASON_INVALID, "Pattern cannot contain null bytes")

        box = deps.get_box_or_404(application_config, name)

        if box.transport == "local" and helpers.LOCAL_IS_WINDOWS:
            normalized = _normalize_local_path(directory)
            if not os.path.exists(normalized):
                raise Failure(REASON_NOT_FOUND, f"Folder not found: {directory}")
            if not os.access(normalized, os.R_OK):
                raise Failure(REASON_DENIED, f"Permission denied: {directory}")
            try:
                matches, truncated = await asyncio.to_thread(
                    _grep_builtin, normalized, pattern, case_sensitive, limit, GREP_TIMEOUT
                )
            except _GrepDeadlineError as exc:
                raise Failure(REASON_TIMEOUT, "Search timed out") from exc
            except re.error as exc:
                raise Failure(REASON_INVALID, str(exc)) from exc
            return APIGrepResponse(
                box=box.name,
                pattern=pattern,
                directory=normalized,
                matches=matches,
                truncated=truncated,
            )

        async with box_runner(deps, box, application_config) as runner:
            # A remote `~` is the home: the quoted word would not expand it. A folder
            # that fails its check raises its ValidationError (`invalid`).
            folder = await runner.folder(directory)
            words = grep_words(pattern, folder, case_sensitive, limit)
            ran = await _grep(runner, words, keep_errors=True)
            if ran.status == GREP_TROUBLE:
                checked = await _grep(
                    runner, pattern_check_words(pattern, case_sensitive), keep_errors=True
                )
                if checked.status == GREP_TROUBLE:
                    raise Failure(REASON_INVALID, _said(checked))
                if not ran.output.strip():
                    await _searched(runner, folder, directory)
            elif ran.status not in (GREP_MATCHED, GREP_NO_MATCH):
                raise Failure(REASON_FAILED, _said(ran))

        matches, truncated = _parse_grep_output(ran.output, limit)
        return APIGrepResponse(
            box=box.name,
            pattern=pattern,
            directory=folder,
            matches=matches,
            truncated=truncated,
        )

    return router


async def _grep(runner: BoxRunner, words: list[str], *, keep_errors: bool = False) -> Ran:
    """grep on the box; past ``GREP_TIMEOUT`` it is ``timeout``."""
    try:
        return await runner.run(words, folder=None, timeout=GREP_TIMEOUT, keep_errors=keep_errors)
    except TimeoutError as exc:
        raise Failure(REASON_TIMEOUT, "Search timed out") from exc


async def _searched(runner: BoxRunner, folder: str, as_sent: str) -> None:
    """grep reported trouble, its pattern is sound and it found nothing: was ``folder``
    there to search? The box is asked (``test``), grep's words are not read. A folder
    that is not there is ``not_found`` and one that cannot be read ``denied``; otherwise
    grep searched everything in it that it could read, and nothing matched."""
    for flag, reason, text in (
        ("-e", REASON_NOT_FOUND, "Folder not found"),
        ("-r", REASON_DENIED, "Permission denied"),
    ):
        if not (await _grep(runner, ["test", flag, folder])).ok:
            raise Failure(reason, f"{text}: {as_sent}")


def _said(ran: Ran) -> str:
    """What grep wrote on its error output (when kept), else its exit status."""
    return ran.errors.strip() or f"grep exited with status {ran.status}"
