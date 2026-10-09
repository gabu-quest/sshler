"""ts-style per-session tmux server utilities.

The ``ts`` CLI tool runs each tmux session on its own server via
``-L ts-<name>``, creating sockets at ``$TMUX_TMPDIR/tmux-<UID>/ts-<name>`` (``/tmp`` by default).
This module provides the same convention so sshler and ts can see each
other's sessions.

Remote (SSH) tmux operations use the same convention. This is important when
``ts`` is run directly after ``ssh host``: both clients must address the same
remote socket, not create look-alike sessions on tmux's default socket.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import platform
import re
import shlex
import shutil
import signal
from pathlib import Path

logger = logging.getLogger(__name__)

_IS_WINDOWS = platform.system().lower().startswith("windows")

SOCKET_PREFIX = "ts-"
SOCKET_TIMEOUT = 2  # seconds — matches ts
# Upper bound for any one local tmux command. Generous because a server that is
# still loading the user's config (plugins, session restore) answers slowly; the
# point is that no caller can wait forever on a wedged server.
TMUX_COMMAND_TIMEOUT = 5.0
# ``capture-pane -S -`` on a long history can take far longer than a normal
# command, so a capture gets its own, larger bound.
TMUX_CAPTURE_TIMEOUT = 30.0
# After a timeout the child is SIGKILLed; this bounds the wait for asyncio to
# see it exit. ``wait()`` also waits for the stdout/stderr pipes, which a
# grandchild (e.g. a freshly forked tmux server) may hold open indefinitely.
REAP_GRACE = 1.0
# Subcommands that may fork a tmux server. The server inherits the client's
# stdout/stderr, so these must never run with captured output.
_SERVER_STARTING_COMMANDS = frozenset({"new-session", "new", "start-server", "start"})


def local_tmux_command(session: str) -> list[str]:
    """Build a tmux command targeting the per-session server for *session*.

    Returns ``["tmux", "-L", "ts-<session>"]`` (or the WSL variant on Windows).
    Callers append subcommand args, e.g.::

        local_tmux_command("myproj") + ["new", "-As", "myproj", "-c", "/tmp"]
    """
    if _IS_WINDOWS:
        return ["wsl", "--", "tmux", "-L", f"{SOCKET_PREFIX}{session}"]
    return ["tmux", "-L", f"{SOCKET_PREFIX}{session}"]


def default_tmux_command() -> list[str]:
    """Build a tmux command targeting the user's default tmux server."""
    if _IS_WINDOWS:
        return ["wsl", "--", "tmux"]
    return ["tmux"]


async def _kill_and_reap(process: asyncio.subprocess.Process) -> None:
    """SIGKILL *process* and wait (bounded) until it has been reaped."""
    try:
        with contextlib.suppress(ProcessLookupError):
            process.kill()
        with contextlib.suppress(asyncio.TimeoutError, Exception):
            await asyncio.wait_for(process.wait(), timeout=REAP_GRACE)
    finally:
        # A grandchild may still hold the pipe write ends after the child is
        # gone; closing the transport releases our read ends either way.
        transport = getattr(process, "_transport", None)
        if transport is not None:
            with contextlib.suppress(Exception):
                transport.close()


async def run_bounded(
    argv: list[str], timeout: float, *, capture_output: bool = True
) -> tuple[int, bytes, bytes]:
    """Run *argv* bounded by *timeout*; return ``(returncode, stdout, stderr)``.

    Every local tmux (and ``ts-add``) subprocess goes through here. On timeout
    or cancellation the child is killed and reaped before the exception
    propagates, so a wedged command never outlives its bound. Raises
    ``asyncio.TimeoutError`` on timeout.

    With *capture_output* False, stdout/stderr go to DEVNULL and only the exit
    is awaited: a tmux server forked by the command inherits those fds, and
    with pipes ``communicate()`` would never see EOF.
    """
    stream = asyncio.subprocess.PIPE if capture_output else asyncio.subprocess.DEVNULL
    process = await asyncio.create_subprocess_exec(
        *argv,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=stream,
        stderr=stream,
    )
    try:
        if capture_output:
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout)
        else:
            await asyncio.wait_for(process.wait(), timeout=timeout)
            stdout, stderr = b"", b""
    except BaseException:
        await _kill_and_reap(process)
        raise
    return process.returncode if process.returncode is not None else -1, stdout, stderr


def _session_names(stdout: bytes) -> set[str]:
    return {
        line.strip()
        for line in stdout.decode("utf-8", errors="ignore").splitlines()
        if line.strip()
    }


# A Windows-looking path (drive or UNC): only there does `\\` separate segments.
_WINDOWS_PATH = re.compile(r"^(?:[A-Za-z]:[/\\]|\\\\)")


def remote_tmux_command(session: str, args: list[str]) -> str:
    """Build a safely quoted remote command for the session's ``ts`` server."""
    return shlex.join(["tmux", "-L", f"{SOCKET_PREFIX}{session}", *args])


def ts_session_name(directory: str) -> str:
    """Tmux session name for a LOCAL directory, matching the ``ts`` CLI exactly.

    ``ts`` names a session after the directory basename with only ``.`` and
    ``:`` replaced by ``_`` (hyphens and everything else preserved) — no hash,
    no parent path. Matching it byte-for-byte lets sshler and ``ts`` share one
    session per directory, so a session opened in sshler can be attached from a
    plain terminal via ``ts`` (and vice-versa) if sshler is down.

    Same-basename directories collide onto one session — this is ``ts``'s own
    behavior and is intentional for parity. Remote tmux boxes use this too.
    """
    path = directory or ""
    separators = r"[/\\]" if _WINDOWS_PATH.match(path) else "/"
    parts = [segment for segment in re.split(separators, path) if segment]
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
    # Same rule as tmux itself: $TMUX_TMPDIR when set, otherwise /tmp.
    return Path(os.environ.get("TMUX_TMPDIR") or "/tmp") / f"tmux-{os.getuid()}"


async def _query_server(server_name: str) -> set[str]:
    """Query a single tmux server for session names, with timeout."""
    cmd = local_tmux_command(server_name.removeprefix(SOCKET_PREFIX)) + [
        "list-sessions", "-F", "#{session_name}",
    ]
    try:
        returncode, stdout, _ = await run_bounded(cmd, SOCKET_TIMEOUT)
        if returncode == 0:
            return _session_names(stdout)
    except (TimeoutError, Exception) as exc:
        logger.debug("Failed to query tmux server %s: %s", server_name, exc)
    return set()


async def _query_default_server() -> set[str]:
    """Query the default tmux server (backward compat for pre-ts sessions)."""
    cmd = default_tmux_command() + ["list-sessions", "-F", "#{session_name}"]
    try:
        returncode, stdout, _ = await run_bounded(cmd, SOCKET_TIMEOUT)
        if returncode == 0:
            return _session_names(stdout)
    except (TimeoutError, Exception) as exc:
        logger.debug("Failed to query default tmux server: %s", exc)
    return set()


async def discover_local_sessions() -> set[str]:
    """Discover live local tmux sessions across all ts-* servers.

    Scans the ``tmux-<UID>/ts-*`` sockets in ``_socket_dir()`` and queries each for session
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
        returncode, stdout, _ = await run_bounded(cmd, SOCKET_TIMEOUT)
        if returncode == 0:
            return _session_names(stdout)
    except (TimeoutError, Exception) as exc:
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
        await run_bounded(
            [ts_add, session, directory], TMUX_COMMAND_TIMEOUT, capture_output=False
        )
    except Exception:
        pass


async def run_local_tmux(
    session_name: str,
    args: list[str],
    timeout: float | None = None,
    *,
    capture_output: bool = True,
) -> tuple[int, bytes, bytes]:
    """Run ``tmux -L ts-<session> <args>`` and return ``(returncode, stdout, stderr)``.

    Bounded by ``TMUX_COMMAND_TIMEOUT`` (or *timeout*) through ``run_bounded``,
    which kills and reaps the child on timeout or cancellation. Raises
    ``asyncio.TimeoutError`` on timeout. A subcommand that may fork a server
    (``new-session``, ``start-server``) always runs without captured output.
    """
    if args and args[0] in _SERVER_STARTING_COMMANDS:
        capture_output = False
    limit = TMUX_COMMAND_TIMEOUT if timeout is None else timeout
    return await run_bounded(
        local_tmux_command(session_name) + args, limit, capture_output=capture_output
    )


async def _run_local_tmux_command(session_name: str, args: list[str]) -> None:
    """Run ``tmux -L ts-<session> <args>`` for its side effect (output discarded)."""
    try:
        await run_local_tmux(session_name, args, capture_output=False)
    except Exception as exc:
        logger.debug(f"Local tmux command failed: {' '.join(args)}: {exc}")


async def _list_local_pane_pids(session_name: str) -> list[int]:
    """Return the pane process PIDs for a local ``ts`` session (empty on error).

    Uses ``-a`` scoped to this session's own socket (``ts-<name>``); since sshler
    runs one session per socket this only ever sees this session's panes.
    """
    cmd = local_tmux_command(session_name) + [
        "list-panes", "-a", "-F", "#{pane_pid}",
    ]
    pids: list[int] = []
    try:
        returncode, stdout, _ = await run_bounded(cmd, SOCKET_TIMEOUT)
        if returncode == 0:
            for line in stdout.decode("utf-8", errors="ignore").splitlines():
                line = line.strip()
                if line.isdigit():
                    pids.append(int(line))
    except (TimeoutError, Exception) as exc:
        logger.debug("_list_local_pane_pids(%s) failed: %s", session_name, exc)
    return pids


async def force_kill_local_session(session_name: str) -> None:
    """Forcibly terminate a local ``ts`` session and its shell processes.

    Captures the session's pane PIDs, kills its per-session tmux server
    (``kill-server`` on the ``ts-<name>`` socket — which cannot reach any other
    session's socket), then SIGKILLs any pane processes that survived the SIGHUP
    from the server dying. Best-effort: every step is suppressed so a partial
    failure never blocks session deletion.

    LOCAL ONLY. Remote boxes share the default tmux server, so ``kill-server``
    there would nuke unrelated sessions — callers must not use this for remote.
    """
    # 1. Capture pane PIDs before the socket goes away.
    pids = await _list_local_pane_pids(session_name)
    # 2. Kill this session's tmux server (scoped to its own socket).
    await _run_local_tmux_command(session_name, ["kill-server"])
    # 3. SIGKILL any survivors. Try the process group first (catches children
    #    the shell spawned) then the bare PID as a fallback.
    for pid in pids:
        with contextlib.suppress(ProcessLookupError, PermissionError, OSError):
            os.killpg(os.getpgid(pid), signal.SIGKILL)
        with contextlib.suppress(ProcessLookupError, PermissionError, OSError):
            os.kill(pid, signal.SIGKILL)


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
