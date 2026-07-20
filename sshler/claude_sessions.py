"""Scanner for Claude Code session transcripts.

Claude Code streams every conversation to
``<config-dir>/projects/<encoded-cwd>/<session-uuid>.jsonl`` as it runs. Each
``.jsonl`` file is one resumable session (``claude --resume <uuid>``). This
module reads those files cheaply to power the sshler "Claude sessions"
dashboard, where each session can be resumed into its own browser terminal.

Parsing is deliberately frugal: transcripts range from a few KB to tens of MB,
so we never read a whole file. We ``stat`` for the modification time (= last
active), read the first handful of lines for ``cwd``/``version``/``gitBranch``
and the first typed user prompt, then read only the last ~64 KB to find the
most recent ``ai-title`` / ``last-prompt`` line (Claude Code rewrites these as
the session evolves, so the *last* occurrence is authoritative). Results are
cached per ``(path, mtime, size)`` — a static transcript parses exactly once.

This is local-box only: it reads the same filesystem sshler runs on.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

# How many leading lines to scan for metadata + the first typed user prompt.
# Lines longer than this (giant tool-output records) are skipped during the
# scan — titles/metadata are always small, so we never need to buffer them.
_MAX_LINE_BYTES = 256 * 1024
# Read granularity for the streaming scan.
_READ_BLOCK = 1024 * 1024
# Cap on the stored title so a pathological prompt can't bloat the payload.
_TITLE_MAX = 200

# Strict canonical UUID (Claude session ids / filenames). Used to validate
# anything that ends up in a shell command (see api/claude_sessions.py).
_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)

# path -> (mtime, size, info)
_CACHE: dict[str, tuple[float, int, "ClaudeSessionInfo"]] = {}

# cwd -> git repo root (or None). Many sessions share a cwd, so the upward
# ``.git`` walk runs once per directory rather than once per transcript.
_GIT_ROOT_CACHE: dict[str, str | None] = {}


def _git_root(cwd: str) -> str | None:
    """Return the git repo root containing *cwd*, or None if not in a repo.

    Walks up from *cwd* looking for a ``.git`` entry (a directory for a normal
    checkout, a file for submodules/worktrees). This mirrors how Claude Code's
    ``/resume`` groups sessions: a transcript run in ``repo/sub/dir`` is grouped
    under ``repo``, not its exact cwd. Cached per directory.
    """
    if not cwd:
        return None
    if cwd in _GIT_ROOT_CACHE:
        return _GIT_ROOT_CACHE[cwd]
    root: str | None = None
    try:
        current = Path(cwd)
        for candidate in (current, *current.parents):
            if (candidate / ".git").exists():
                root = str(candidate)
                break
    except OSError:
        root = None
    _GIT_ROOT_CACHE[cwd] = root
    return root


@dataclass(frozen=True)
class ClaudeSessionInfo:
    id: str  # session UUID (the .jsonl filename stem)
    cwd: str  # working directory the session ran in (read from the file)
    title: str  # human-readable title (ai-title > last-prompt > first prompt)
    last_prompt: str | None  # most recent user prompt, if recorded
    last_active: float  # epoch seconds (file mtime)
    git_branch: str | None
    version: str | None
    size_bytes: int
    project_dir: str  # encoded directory name under projects/
    repo_root: str | None  # git repo root containing cwd (for /resume-style grouping)


def is_valid_session_id(session_id: str) -> bool:
    """True iff *session_id* is a canonical UUID (safe to interpolate)."""
    return bool(_UUID_RE.match(session_id))


def _projects_dir() -> Path:
    """Resolve ``<config-dir>/projects``, honoring ``CLAUDE_CONFIG_DIR``."""
    base = os.environ.get("CLAUDE_CONFIG_DIR")
    root = Path(base) if base else Path.home() / ".claude"
    return root / "projects"


def _loads(line: str) -> dict | None:
    line = line.strip()
    if not line:
        return None
    try:
        obj = json.loads(line)
    except (json.JSONDecodeError, ValueError):
        return None
    return obj if isinstance(obj, dict) else None


def _user_text(obj: dict) -> str | None:
    """Extract plain text from a ``user`` line's ``message.content``.

    Content may be a string or a list of content blocks; we join the text of
    any ``text`` blocks. Returns None if no text is present.
    """
    message = obj.get("message")
    if not isinstance(message, dict):
        return None
    content = message.get("content")
    if isinstance(content, str):
        return content or None
    if isinstance(content, list):
        parts = [
            block["text"]
            for block in content
            if isinstance(block, dict)
            and block.get("type") == "text"
            and isinstance(block.get("text"), str)
        ]
        joined = " ".join(parts).strip()
        return joined or None
    return None


def _iter_lines(path: Path):
    """Yield newline-delimited lines (bytes) from *path*, streaming.

    Bounded memory: a single line longer than ``_MAX_LINE_BYTES`` (a giant
    tool-output record) is skipped entirely rather than buffered, and line
    alignment is preserved across the skip. Every yielded line is small.
    """
    with path.open("rb") as handle:
        buf = b""
        skipping = False
        while True:
            block = handle.read(_READ_BLOCK)
            if not block:
                break
            if skipping:
                newline = block.find(b"\n")
                if newline == -1:
                    continue  # still inside the oversized line
                block = block[newline + 1:]
                skipping = False
            buf += block
            while True:
                newline = buf.find(b"\n")
                if newline == -1:
                    break
                yield buf[:newline]
                buf = buf[newline + 1:]
            if len(buf) > _MAX_LINE_BYTES:
                buf = b""
                skipping = True
        if buf and not skipping:
            yield buf


def _clip_title(value: str) -> str:
    value = value.strip().replace("\n", " ")
    return value[:_TITLE_MAX]


# Byte markers that make a line worth JSON-parsing for a title.
_TITLE_MARKERS = (b'"custom-title"', b'"ai-title"', b'"last-prompt"')


def _parse_session_file(path: Path) -> ClaudeSessionInfo | None:
    """Parse one transcript into a ClaudeSessionInfo, or None if unreadable.

    Streams the whole file so the latest ``custom-title`` / ``ai-title`` is
    always found regardless of file size (they can sit megabytes from EOF in a
    long session). Title priority mirrors Claude Code's ``/resume`` picker:
    latest ``customTitle`` (a user ``/rename``) → latest ``aiTitle`` → latest
    ``lastPrompt`` → first typed user prompt → "(untitled session)".

    Cheap: only lines containing a title marker (or, until found, the header
    metadata / first typed prompt) are JSON-parsed; bulk assistant/tool lines
    are skipped by a byte pre-filter. Tolerant of malformed lines.
    """
    try:
        stat = path.stat()
    except OSError:
        return None

    cwd: str | None = None
    version: str | None = None
    git_branch: str | None = None
    custom_title: str | None = None
    ai_title: str | None = None
    last_prompt: str | None = None
    first_prompt: str | None = None

    def _absorb(obj: dict) -> None:
        nonlocal cwd, version, git_branch, custom_title, ai_title, last_prompt
        if cwd is None and isinstance(obj.get("cwd"), str):
            cwd = obj["cwd"]
        if version is None and isinstance(obj.get("version"), str):
            version = obj["version"]
        if git_branch is None and isinstance(obj.get("gitBranch"), str):
            git_branch = obj["gitBranch"]
        obj_type = obj.get("type")
        if obj_type == "custom-title" and isinstance(obj.get("customTitle"), str):
            custom_title = obj["customTitle"]
        elif obj_type == "ai-title" and isinstance(obj.get("aiTitle"), str):
            ai_title = obj["aiTitle"]
        elif obj_type == "last-prompt" and isinstance(obj.get("lastPrompt"), str):
            last_prompt = obj["lastPrompt"]

    try:
        for raw in _iter_lines(path):
            need_meta = (
                cwd is None
                or version is None
                or git_branch is None
                or first_prompt is None
            )
            is_title = any(marker in raw for marker in _TITLE_MARKERS)
            if not (need_meta or is_title):
                continue
            obj = _loads(raw.decode("utf-8", errors="ignore"))
            if obj is None:
                continue
            _absorb(obj)
            if (
                first_prompt is None
                and obj.get("type") == "user"
                and obj.get("promptSource") == "typed"
            ):
                first_prompt = _user_text(obj)
    except OSError:
        return None

    display = (
        custom_title or ai_title or last_prompt or first_prompt or "(untitled session)"
    )

    return ClaudeSessionInfo(
        id=path.stem,
        cwd=cwd or "",
        title=_clip_title(display),
        last_prompt=_clip_title(last_prompt) if last_prompt else None,
        last_active=stat.st_mtime,
        git_branch=git_branch,
        version=version,
        size_bytes=stat.st_size,
        project_dir=path.parent.name,
        repo_root=_git_root(cwd or ""),
    )


def _within(path: Path, resolved_base: Path) -> bool:
    """True iff *path* resolves to a location inside *resolved_base*.

    ``Path.glob`` follows symlinks, so a symlink planted under projects/ could
    otherwise point the scanner at arbitrary ``.jsonl`` files elsewhere on disk.
    """
    try:
        return path.resolve().is_relative_to(resolved_base)
    except OSError:
        return False


def _parse_cached(path: Path) -> ClaudeSessionInfo | None:
    """Parse *path*, reusing the cache when mtime and size are unchanged."""
    try:
        stat = path.stat()
    except OSError:
        return None
    key = str(path)
    cached = _CACHE.get(key)
    if cached is not None and cached[0] == stat.st_mtime and cached[1] == stat.st_size:
        return cached[2]
    info = _parse_session_file(path)
    if info is None:
        return None
    _CACHE[key] = (stat.st_mtime, stat.st_size, info)
    return info


def list_claude_sessions(
    limit: int = 100, since_days: float | None = None
) -> list[ClaudeSessionInfo]:
    """Return sessions across all projects, most-recently-active first.

    *limit* caps the result count; *since_days* drops sessions whose transcript
    hasn't been touched within that many days.
    """
    base = _projects_dir()
    if not base.is_dir():
        return []
    resolved_base = base.resolve()
    cutoff = time.time() - since_days * 86400 if since_days else None

    infos: list[ClaudeSessionInfo] = []
    for path in base.glob("*/*.jsonl"):
        if not _within(path, resolved_base):
            continue
        try:
            mtime = path.stat().st_mtime
        except OSError:
            continue
        if cutoff is not None and mtime < cutoff:
            continue
        info = _parse_cached(path)
        if info is not None:
            infos.append(info)

    infos.sort(key=lambda item: item.last_active, reverse=True)
    return infos[: max(0, limit)]


def get_claude_session(session_id: str) -> ClaudeSessionInfo | None:
    """Look up a single session by UUID, or None if not found / invalid id."""
    if not is_valid_session_id(session_id):
        return None
    base = _projects_dir()
    if not base.is_dir():
        return None
    resolved_base = base.resolve()
    for path in base.glob(f"*/{session_id}.jsonl"):
        if not _within(path, resolved_base):
            continue
        info = _parse_cached(path)
        if info is not None:
            return info
    return None
