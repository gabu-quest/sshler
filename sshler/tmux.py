"""ts-style per-session tmux server utilities.

The ``ts`` CLI tool runs each tmux session on its own server via
``-L ts-<name>``, creating sockets at ``/tmp/tmux-<UID>/ts-<name>``.
This module provides the same convention so sshler and ts can see each
other's sessions.

Remote (SSH) tmux operations are unaffected — they use the remote host's
default tmux server.
"""

from __future__ import annotations

import asyncio
import logging
import os
import platform
import shutil
from pathlib import Path

logger = logging.getLogger(__name__)

_IS_WINDOWS = platform.system().lower().startswith("windows")

SOCKET_PREFIX = "ts-"
SOCKET_TIMEOUT = 2  # seconds — matches ts


def local_tmux_command(session: str) -> list[str]:
    """Build a tmux command targeting the per-session server for *session*.

    Returns ``["tmux", "-L", "ts-<session>"]`` (or the WSL variant on Windows).
    Callers append subcommand args, e.g.::

        local_tmux_command("myproj") + ["new", "-As", "myproj", "-c", "/tmp"]
    """
    if _IS_WINDOWS:
        return ["wsl", "--", "tmux", "-L", f"{SOCKET_PREFIX}{session}"]
    return ["tmux", "-L", f"{SOCKET_PREFIX}{session}"]


def ts_session_name(directory: str) -> str:
    """Tmux session name for a LOCAL directory, matching the ``ts`` CLI exactly.

    ``ts`` names a session after the directory basename with only ``.`` and
    ``:`` replaced by ``_`` (hyphens and everything else preserved) — no hash,
    no parent path. Matching it byte-for-byte lets sshler and ``ts`` share one
    session per directory, so a session opened in sshler can be attached from a
    plain terminal via ``ts`` (and vice-versa) if sshler is down.

    Same-basename directories collide onto one session — this is ``ts``'s own
    behavior and is intentional for parity. Remote boxes do not use this.
    """
    parts = [segment for segment in (directory or "").split("/") if segment]
    base = parts[-1] if parts else "home"
    if base in (".", "..", "~"):
        base = "home"
    # ts rule (`.`/`:` -> `_`), then the same tmux-safe filter the /ws/term
    # handler applies (PathValidator.sanitize_session_name) so this equals the
    # session the terminal opens for the same dir. Kept byte-identical to the
    # `box === "local"` branch of generateSessionName in sessionName.ts.
    # `.`/`:` are already gone here, so the session name never contains them —
    # important because tmux target syntax is `session:window.pane`.
    stepped = base.replace(".", "_").replace(":", "_")
    safe = "".join(ch if (ch.isalnum() or ch in "-_") else "_" for ch in stepped)
    return safe or "home"


def _socket_dir() -> Path:
    """Return the tmux socket directory for the current user.

    On Windows, tmux runs inside WSL so the socket directory is not
    accessible from the host. Return a non-existent path so callers skip
    socket scanning and fall through to the WSL-based default-server query.
    """
    if _IS_WINDOWS:
        return Path("C:/nonexistent/tmux-sockets")
    return Path(f"/tmp/tmux-{os.getuid()}")


async def _query_server(server_name: str) -> set[str]:
    """Query a single tmux server for session names, with timeout."""
    cmd = ["tmux", "-L", server_name, "list-sessions", "-F", "#{session_name}"]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(
            proc.communicate(), timeout=SOCKET_TIMEOUT
        )
        if proc.returncode == 0 and stdout:
            return {
                line.strip()
                for line in stdout.decode().strip().split("\n")
                if line.strip()
            }
    except (asyncio.TimeoutError, Exception) as exc:
        logger.debug("Failed to query tmux server %s: %s", server_name, exc)
    return set()


async def _query_default_server() -> set[str]:
    """Query the default tmux server (backward compat for pre-ts sessions)."""
    cmd = ["tmux", "list-sessions", "-F", "#{session_name}"]
    if _IS_WINDOWS:
        cmd = ["wsl", "--", *cmd]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(
            proc.communicate(), timeout=SOCKET_TIMEOUT
        )
        if proc.returncode == 0 and stdout:
            return {
                line.strip()
                for line in stdout.decode().strip().split("\n")
                if line.strip()
            }
    except (asyncio.TimeoutError, Exception) as exc:
        logger.debug("Failed to query default tmux server: %s", exc)
    return set()


async def discover_local_sessions() -> set[str]:
    """Discover live local tmux sessions across all ts-* servers.

    Scans ``/tmp/tmux-<UID>/ts-*`` sockets and queries each for session
    names.  Also checks the default tmux server as a backward-compat
    fallback for sessions created before the ts convention was adopted.

    Queries run concurrently.  Stale sockets are logged but NOT deleted
    (sshler is a daemon — deleting sockets could race with the user's
    ``ts`` commands).
    """
    sock_dir = _socket_dir()
    tasks: list[asyncio.Task] = []

    if sock_dir.is_dir():
        for entry in sock_dir.iterdir():
            if entry.name.startswith(SOCKET_PREFIX) and entry.is_socket():
                tasks.append(
                    asyncio.ensure_future(_query_server(entry.name))
                )

    # Also check default server for legacy sessions
    tasks.append(asyncio.ensure_future(_query_default_server()))

    results = await asyncio.gather(*tasks, return_exceptions=True)
    sessions: set[str] = set()
    for result in results:
        if isinstance(result, set):
            sessions |= result
    return sessions


async def list_local_window_names(session: str) -> set[str]:
    """Return the set of window names in a local ``ts`` session (empty if none)."""
    cmd = local_tmux_command(session) + [
        "list-windows", "-t", session, "-F", "#{window_name}",
    ]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=SOCKET_TIMEOUT)
        if proc.returncode == 0 and stdout:
            return {
                line.strip()
                for line in stdout.decode("utf-8", errors="ignore").splitlines()
                if line.strip()
            }
    except (asyncio.TimeoutError, Exception) as exc:
        logger.debug("list_local_window_names(%s) failed: %s", session, exc)
    return set()


async def record_ts_history(session: str, directory: str) -> None:
    """Record a session in ts history so it appears in ``ts``'s fzf picker.

    Calls ``ts-add <session> <directory>`` if the binary is available.
    Fire-and-forget — failures are silently ignored.
    """
    ts_add = shutil.which("ts-add")
    if ts_add is None:
        return
    try:
        proc = await asyncio.create_subprocess_exec(
            ts_add, session, directory,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await asyncio.wait_for(proc.communicate(), timeout=5.0)
    except Exception:
        pass


async def _run_local_tmux_command(session_name: str, args: list[str]) -> None:
    """Run ``tmux -L ts-<session> <args>`` for its side effect (output ignored)."""
    command = local_tmux_command(session_name) + args
    try:
        process = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await process.communicate()
    except Exception as exc:
        logger.debug(f"Local tmux command failed: {' '.join(args)}: {exc}")


# Distinct 256-color tmux codes for per-session status bars. Picked to read
# clearly against white status text and to roughly mirror the frontend palette
# (utils/sessionName.ts) — exact match isn't needed, only that each session is
# visibly different from its neighbours.
_TMUX_SESSION_COLORS = [
    "colour203",  # red
    "colour208",  # orange
    "colour178",  # amber
    "colour71",   # green
    "colour37",   # teal
    "colour33",   # blue
    "colour62",   # indigo
    "colour135",  # purple
    "colour168",  # magenta
    "colour095",  # brown
    "colour66",   # slate-teal
    "colour130",  # rust
]


def _tmux_color_for_session(session_name: str) -> str:
    """Deterministic tmux color code for a session name (FNV-1a, matches the UI)."""
    h = 2166136261
    for ch in session_name:
        h ^= ord(ch)
        h = (h * 16777619) & 0xFFFFFFFF
    return _TMUX_SESSION_COLORS[h % len(_TMUX_SESSION_COLORS)]


async def _configure_tmux_bindings(session_name: str) -> None:
    """Set tmux key bindings + a per-session status-bar color.

    Bindings: Ctrl+B c inherits the current pane's directory (without this, new
    windows open in the tmux server's CWD, usually ~).

    Color: deterministic per-session status-bar background so it's obvious which
    session you're in when immersed in the pane — mirrors the app-chrome accent.

    Idempotent — safe to call on every connection.
    """
    await _run_local_tmux_command(
        session_name, ["bind-key", "c", "new-window", "-c", "#{pane_current_path}"]
    )
    color = _tmux_color_for_session(session_name)
    await _run_local_tmux_command(
        session_name, ["set-option", "-t", session_name, "status-style", f"bg={color},fg=colour231"]
    )
