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
from collections.abc import Coroutine, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .proc import run_bounded as run_bounded

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


def _session_names(stdout: bytes) -> set[str]:
    return {
        line.strip()
        for line in stdout.decode("utf-8", errors="ignore").splitlines()
        if line.strip()
    }


# The ``list-sessions -F`` format of every listing, local and remote: one line per
# session between two fixed marks, the server's socket, the name, then the folder (free
# text, so it may hold tabs). ``#{socket_path}`` is what says which server a line came
# from; a tmux without it prints it empty and no session is found on its own server.
LIST_FORMAT = "S\t#{socket_path}\t#{session_name}\t#{session_path}\tE"


def parse_listing(text: str) -> LiveSessions:
    """The sessions in the output of ``list-sessions -F LIST_FORMAT`` (of one server or
    several). A session NAME on its own server, ``ts-NAME``, also gets its folder. A
    folder with a newline in it splits its line: the session is still seen (its name
    stands before the folder), but it gets no folder, and the rest of the split line is
    dropped. Output without the marks is read as one name per line."""
    # tmux ends a line with a newline and nothing else: ``splitlines`` would also cut
    # at a form feed or a Unicode line separator inside a folder's name.
    lines = [line.removesuffix("\r") for line in text.split("\n")]
    if not any(line.startswith("S\t") for line in lines):
        return LiveSessions(frozenset(line.strip() for line in lines if line.strip()))
    names: set[str] = set()
    folders: dict[str, str] = {}
    for line in lines:
        parts = line.split("\t")
        if len(parts) < 4 or parts[0] != "S":
            continue
        name = parts[2].strip()
        if not name:
            continue
        names.add(name)
        whole = len(parts) >= 5 and parts[-1] == "E"
        folder = "\t".join(parts[3:-1]) if whole else ""
        if folder and parts[1].rsplit("/", 1)[-1] == f"{SOCKET_PREFIX}{name}":
            folders[name] = folder
    return LiveSessions(frozenset(names), folders=folders)


# A Windows-looking path (drive or UNC): only there does `\\` separate segments.
_WINDOWS_PATH = re.compile(r"^(?:[A-Za-z]:[/\\]|\\\\)")


def remote_tmux_command(session: str, args: list[str]) -> str:
    """Build a safely quoted remote command for the session's ``ts`` server."""
    return shlex.join(["tmux", "-L", f"{SOCKET_PREFIX}{session}", *args])


# The force kill of a remote session, what ``force_kill_local_session`` does on this
# machine: ask the session's own server (``$1``, always ``ts-<name>``) for its pane
# process ids, stop that server, then SIGKILL each pane's process group and process.
# Only a line of digits tmux printed is a pane process id (0 and 1 never are); a
# session that is already gone lists none and is not an error. Exits 127 when the box
# has no tmux (the shell's status for a command it cannot find), 2 when ``$1`` is not
# a ``ts-`` server, else 0. A fixed text, never built from a request; sent as one
# word to ``sh -c``, the server name as its own word after it
# (``remote_force_kill_words``).
REMOTE_FORCE_KILL = """command -v tmux >/dev/null 2>&1 || exit 127
case $1 in "$SOCKET_PREFIX"?*) ;; *) exit 2 ;; esac
panes=$(tmux -L "$1" list-panes -a -F '#{pane_pid}' 2>/dev/null)
tmux -L "$1" kill-server >/dev/null 2>&1
printf '%s\\n' "$panes" | while IFS= read -r pid; do
    case $pid in ''|*[!0-9]*|0|1) continue ;; esac
    group=$(ps -o pgid= -p "$pid" 2>/dev/null)
    group=${group##* }
    case $group in ''|*[!0-9]*|0|1) ;; *) kill -s KILL -- "-$group" 2>/dev/null ;; esac
    kill -s KILL "$pid" 2>/dev/null
done
exit 0
""".replace('"$SOCKET_PREFIX"', SOCKET_PREFIX)


def remote_force_kill_words(session: str) -> list[str]:
    """The words a remote runner sends to force-kill ``session``: ``REMOTE_FORCE_KILL``
    run by ``sh``, with the session's own server name as one word."""
    return ["sh", "-c", REMOTE_FORCE_KILL, "sh", f"{SOCKET_PREFIX}{session}"]


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


@dataclass(frozen=True)
class LiveSessions:
    """What tmux answered when asked which sessions it has: the one answer of every
    listing (local here, remote in ``api/sessions.py``).

    ``names`` are the sessions of the servers that answered. ``complete`` is False when
    a server could not be asked (past its limit, or tmux could not be run): a session
    it did not see may still run there. A socket with no server behind it is an answer,
    no sessions, as tmux's own "no server running" is.
    """

    names: frozenset[str] = frozenset()
    complete: bool = True
    # The folder of each session that sits on its own server (NAME on ``ts-NAME``),
    # by name: the sessions sshler can open. A session of the default server, or of a
    # ``ts-`` server of another name, is in ``names`` only.
    folders: Mapping[str, str] = field(default_factory=dict)

    def has(self, session: str) -> bool | None:
        """Whether tmux has *session*: True when it was seen, False when every server
        answered without it, None when that could not be learnt."""
        if session in self.names:
            return True
        return False if self.complete else None

    def __or__(self, other: LiveSessions) -> LiveSessions:
        return LiveSessions(
            self.names | other.names,
            self.complete and other.complete,
            {**self.folders, **other.folders},
        )


# What tmux says, on its error output, when no server is behind the socket: the
# socket is stale (``ECONNREFUSED``), or there is none (``ENOENT``). tmux sets only
# ``LC_CTYPE``, so the reason is in English. ``REMOTE_LIST_SESSIONS`` reads the same two.
_NO_SERVER = re.compile(
    r"^(no server running on |error connecting to .*\(No such file or directory\))"
)


async def _ask(argv: list[str], server: str) -> LiveSessions:
    """``argv`` (a ``list-sessions`` of one server) run and read as ``LiveSessions``."""
    try:
        returncode, stdout, stderr = await run_bounded(argv, SOCKET_TIMEOUT)
    except Exception as exc:  # past its limit, or tmux could not be run
        logger.info("tmux server %s could not be asked: %s", server, exc)
        return LiveSessions(complete=False)
    if returncode == 0:
        return parse_listing(stdout.decode("utf-8", errors="ignore"))
    errors = stderr.decode("utf-8", errors="replace").strip()
    if _NO_SERVER.match(errors):
        return LiveSessions()
    logger.info("tmux server %s could not be asked: status %s, %s", server, returncode, errors)
    return LiveSessions(complete=False)


async def _query_server(server_name: str) -> LiveSessions:
    """The sessions of the tmux server ``server_name`` (``ts-<name>``)."""
    cmd = local_tmux_command(server_name.removeprefix(SOCKET_PREFIX)) + [
        "list-sessions",
        "-F",
        LIST_FORMAT,
    ]
    return await _ask(cmd, server_name)


async def _query_default_server() -> LiveSessions:
    """The sessions of the default tmux server (sessions made before the ts convention)."""
    cmd = default_tmux_command() + ["list-sessions", "-F", LIST_FORMAT]
    return await _ask(cmd, "default")


async def _gather(queries: list[Coroutine[Any, Any, LiveSessions]]) -> LiveSessions:
    answered = LiveSessions()
    for result in await asyncio.gather(*queries):
        answered |= result
    return answered


async def discover_local_sessions() -> LiveSessions:
    """The live local tmux sessions: those of every ts-* server and of the default one.

    Scans the ``tmux-<UID>/ts-*`` sockets in ``_socket_dir()`` and asks each, and the
    default server, concurrently, each within ``SOCKET_TIMEOUT``. Stale sockets are
    NOT deleted (sshler is a daemon: deleting sockets could race with the user's
    ``ts`` commands).
    """
    sock_dir = _socket_dir()
    queries: list[Coroutine[Any, Any, LiveSessions]] = []
    if sock_dir.is_dir():
        for entry in sock_dir.iterdir():
            if entry.name.startswith(SOCKET_PREFIX) and entry.is_socket():
                queries.append(_query_server(entry.name))
    queries.append(_query_default_server())
    return await _gather(queries)


async def ask_local_session(session: str) -> LiveSessions:
    """What tmux says about the local session *session* alone: its own ``ts-<session>``
    server and the default server are asked (two tmux commands, concurrently), however
    many other servers run. Read with ``LiveSessions.has(session)``."""
    return await _gather([_query_server(f"{SOCKET_PREFIX}{session}"), _query_default_server()])


# Window list: the fields that cannot hold free text come first and the window name
# is last, so a name may hold spaces, ``|`` or tabs and still comes back whole.
WINDOW_LIST_FORMAT = "#{window_index} #{window_active} #{window_name}"

# Pane list: tab-separated, the folder (free text) last. The window name is not in it;
# tmux prints a tab in a name as a tab, so it is read from the window list instead.
PANE_LIST_FORMAT = "#{window_index}\t#{pane_index}\t#{pane_current_command}\t#{pane_current_path}"


def parse_window_list(text: str) -> list[dict[str, str | bool]]:
    """Parse ``list-windows -F WINDOW_LIST_FORMAT`` output into index/name/active dicts."""
    windows: list[dict[str, str | bool]] = []
    for line in text.split("\n"):
        parts = line.split(" ", 2)
        if len(parts) < 3:
            continue
        index, active, name = parts
        windows.append({"index": index, "name": name, "active": active == "1"})
    return windows


def parse_pane_list(text: str) -> list[tuple[int, int, str, str]]:
    """Parse ``list-panes -F PANE_LIST_FORMAT`` output into (window, pane, command, path)."""
    panes: list[tuple[int, int, str, str]] = []
    for line in text.split("\n"):
        parts = line.split("\t", 3)
        if len(parts) < 4:
            continue
        try:
            panes.append((int(parts[0]), int(parts[1]), parts[2], parts[3]))
        except ValueError:
            continue
    return panes


async def list_local_window_names(session: str) -> set[str]:
    """Return the set of window names in a local ``ts`` session (empty if none)."""
    cmd = local_tmux_command(session) + [
        "list-windows",
        "-t",
        session,
        "-F",
        "#{window_name}",
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
        await run_bounded([ts_add, session, directory], TMUX_COMMAND_TIMEOUT, capture_output=False)
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
        "list-panes",
        "-a",
        "-F",
        "#{pane_pid}",
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

    It runs tmux and signals processes on this machine (``LocalRunner.force_kill``);
    a remote box runs ``REMOTE_FORCE_KILL`` instead.
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
    "colour71",  # green
    "colour37",  # teal
    "colour33",  # blue
    "colour62",  # indigo
    "colour135",  # purple
    "colour168",  # magenta
    "colour095",  # brown
    "colour66",  # slate-teal
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
