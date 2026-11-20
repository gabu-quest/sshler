from __future__ import annotations

import asyncio
import base64
import contextlib
import logging
import os
import secrets
import shlex
import signal
import struct
import subprocess
import sys
import threading
import time
from collections.abc import Awaitable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from functools import partial
from typing import IO, cast

import asyncssh
from fastapi import (
    FastAPI,
    HTTPException,
    Query,
    Request,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import RedirectResponse

from . import state
from .access import allowed_origins, origin_base, websocket_refusal
from .api import APIDependencies, create_api_router, failure
from .api.auth import create_auth_router, get_client_ip
from .api.helpers import (
    DEFAULT_MAX_UPLOAD_BYTES,
    LOCAL_IS_WINDOWS,
    _local_is_directory,
    _normalize_local_path,
    available_windows_shells,
    default_windows_shell,
)
from .api.remote import remote_directory
from .api.tunnels import TunnelRegistry
from .auth import AuthManager, PasswordHasher, PasswordPolicy, PasswordValidator
from .config import (
    AppConfig,
    Box,
    find_box,
    load_config,
)
from .ptyspawn import spawn_on_pty
from .rate_limit import get_rate_limiter
from .session import get_session_store
from .settings import get_settings as get_app_settings
from .spa import mount_spa
from .ssh import (
    connect,
    open_tmux,
    sftp_is_directory,
)
from .ssh_pool import get_pool, initialize_pool, shutdown_pool
from .terminal import OpenedTerminal, TerminalOpenError, run_pump, take_early_message
from .terminal.conpty import ConPtyTransport
from .terminal.local import LocalPtyTransport, LocalTmuxWindows
from .terminal.remote import RemoteTmuxWindows, SshTransport, close_connection
from .terminal.transport import open_or_close_on_cancel, teardown_scope
from .tmux import (
    _configure_tmux_bindings,
    local_tmux_command,
    record_ts_history,
)
from .validation import PathValidator, ValidationError
from .win_terminal_registry import TooManyTerminalsError, WindowsTerminalRegistry
from .winpty_proc import WinPTYProcess, WinPTYUnavailableError

logger = logging.getLogger(__name__)


class AuthFailureTracker:
    """Track failed authentication attempts and enforce lockouts."""

    def __init__(
        self,
        lockout_threshold: int = 5,
        lockout_duration: int = 300,  # 5 minutes
        failure_window: int = 600,  # 10 minutes
    ):
        """Initialize auth failure tracker.

        Args:
            lockout_threshold: Number of failures before lockout
            lockout_duration: Seconds to lock out after threshold
            failure_window: Seconds to keep failure history
        """
        self._failures: dict[str, list[float]] = {}
        self._lockouts: dict[str, float] = {}
        self._lockout_threshold = lockout_threshold
        self._lockout_duration = lockout_duration
        self._failure_window = failure_window

    def record_failure(self, client_ip: str) -> None:
        """Record a failed authentication attempt.

        Args:
            client_ip: IP address of the client
        """
        now = time.time()

        if client_ip not in self._failures:
            self._failures[client_ip] = []

        # Keep only failures from the failure window
        self._failures[client_ip] = [
            t for t in self._failures[client_ip] if now - t < self._failure_window
        ]
        self._failures[client_ip].append(now)

        # Check for lockout
        if len(self._failures[client_ip]) >= self._lockout_threshold:
            self._lockouts[client_ip] = now + self._lockout_duration
            logging.warning(
                f"[Security] IP {client_ip} locked out after "
                f"{len(self._failures[client_ip])} failed auth attempts"
            )

    def is_locked_out(self, client_ip: str) -> bool:
        """Check if an IP is currently locked out.

        Args:
            client_ip: IP address to check

        Returns:
            True if locked out, False otherwise
        """
        if client_ip in self._lockouts:
            if time.time() < self._lockouts[client_ip]:
                return True
            else:
                # Lockout expired, remove it
                del self._lockouts[client_ip]
                # Also clear failures for this IP
                self._failures.pop(client_ip, None)
        return False

    def get_lockout_remaining(self, client_ip: str) -> int:
        """Get remaining lockout time in seconds.

        Args:
            client_ip: IP address to check

        Returns:
            Seconds remaining in lockout, or 0 if not locked out
        """
        if client_ip in self._lockouts:
            remaining = int(self._lockouts[client_ip] - time.time())
            return max(0, remaining)
        return 0

    def reset_failures(self, client_ip: str) -> None:
        """Reset failure count for an IP (after successful auth).

        Args:
            client_ip: IP address to reset
        """
        self._failures.pop(client_ip, None)
        self._lockouts.pop(client_ip, None)

    def get_failure_count(self, client_ip: str) -> int:
        """Get current failure count for an IP.

        Args:
            client_ip: IP address to check

        Returns:
            Number of failures in the current window
        """
        if client_ip not in self._failures:
            return 0

        # Clean up old failures first
        now = time.time()
        self._failures[client_ip] = [
            t for t in self._failures[client_ip] if now - t < self._failure_window
        ]

        return len(self._failures[client_ip])


def _load_auth_from_env() -> tuple[str, str] | None:
    """Load authentication credentials from environment variables.

    Returns:
        Tuple of (username, password_hash) if credentials are set, None otherwise
    """
    username = os.getenv("SSHLER_USERNAME")
    password_hash = os.getenv("SSHLER_PASSWORD_HASH")
    password = os.getenv("SSHLER_PASSWORD")

    if not username:
        return None

    # Prefer hash over plaintext password
    if password_hash:
        return (username, password_hash)
    elif password:
        # Hash the plaintext password on startup
        print(
            "⚠️  WARNING: Using plaintext SSHLER_PASSWORD is not recommended.",
            file=sys.stderr,
        )
        print(
            "   Use 'sshler hash-password' to generate SSHLER_PASSWORD_HASH instead.",
            file=sys.stderr,
        )
        hasher = PasswordHasher()
        password_hash = hasher.hash(password)
        return (username, password_hash)
    else:
        print(
            "ERROR: SSHLER_USERNAME is set but no password provided.",
            file=sys.stderr,
        )
        print(
            "   Set either SSHLER_PASSWORD_HASH or SSHLER_PASSWORD.",
            file=sys.stderr,
        )
        sys.exit(1)


@dataclass
class ServerSettings:
    allow_origins: list[str] = field(default_factory=list)
    csrf_token: str | None = field(default_factory=lambda: secrets.token_urlsafe(32))
    max_upload_bytes: int = DEFAULT_MAX_UPLOAD_BYTES
    allow_ssh_alias: bool = True
    basic_auth: tuple[str, str] | None = None
    basic_auth_header: str | None = field(init=False, default=None)
    auth_manager: AuthManager | None = field(init=False, default=None)
    serve_spa: bool = True
    serve_artifacts: bool = False
    artifact_port: int = 0
    artifact_server_port_actual: int | None = field(init=False, default=None)
    snapshot_enabled: bool = field(default_factory=lambda: get_app_settings().snapshot_enabled)
    snapshot_interval: int = field(default_factory=lambda: get_app_settings().snapshot_interval)

    def __post_init__(self) -> None:
        # normalise origins without trailing slashes and drop duplicates while preserving order
        seen: set[str] = set()
        normalised: list[str] = []
        for origin in self.allow_origins:
            cleaned = origin.rstrip("/")
            if cleaned and cleaned not in seen:
                seen.add(cleaned)
                normalised.append(cleaned)
        self.allow_origins = normalised

        # Load auth from environment if not provided via basic_auth
        if not self.basic_auth:
            env_auth = _load_auth_from_env()
            if env_auth:
                username, password_hash = env_auth
                self.auth_manager = AuthManager(username, password_hash)
                # Generate basic_auth_header for backwards compatibility
                # (middleware needs it for header comparison)
                self.basic_auth_header = f"Basic {username}:{password_hash}"  # Placeholder
        elif self.basic_auth:
            # CLI --auth flag provided: validate and hash password
            user, password = self.basic_auth

            # Validate password strength
            policy = PasswordPolicy()
            is_valid, errors = PasswordValidator.validate(password, user, policy)
            if not is_valid:
                print("\n" + "=" * 70, file=sys.stderr)
                print("ERROR: Password does not meet security requirements:", file=sys.stderr)
                print("=" * 70, file=sys.stderr)
                for error in errors:
                    print(f"  - {error}", file=sys.stderr)
                print(
                    "\nUse 'sshler hash-password' to generate a strong password.",
                    file=sys.stderr,
                )
                sys.exit(1)

            # Hash password and create AuthManager
            hasher = PasswordHasher()
            password_hash = hasher.hash(password)
            self.auth_manager = AuthManager(user, password_hash)

            # Also generate basic_auth_header for backwards compatibility
            raw = f"{user}:{password}".encode()
            self.basic_auth_header = "Basic " + base64.b64encode(raw).decode("ascii")


async def _convert_path_to_wsl(path: str) -> str | None:
    if not LOCAL_IS_WINDOWS:
        return path

    def _worker() -> str | None:
        result = subprocess.run(
            ["wsl", "wslpath", "-a", path],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            return None
        return result.stdout.strip()

    return await asyncio.to_thread(_worker)


# PTY-related modules for local terminal resize (Unix-only)
if sys.platform != "win32":
    import fcntl
    import termios


class LocalPTYProcess:
    """Wrapper for a local PTY process with resize support.

    This class manages a process running in a pseudo-terminal, allowing
    proper terminal resize via ioctl TIOCSWINSZ + SIGWINCH.

    Two lifecycle rules keep teardown from touching a recycled number:

    * The master fd is closed only after every in-flight ``read``/``write`` has
      returned, so a thread blocked on the PTY never reads or writes whatever
      the kernel later assigns that fd number.
    * Reaping has one owner, ``_reap_lock``: every ``waitpid`` and every signal
      runs under it, and nothing is signalled once the pid is reaped. A zombie's
      pid cannot be reused, so a signal sent under the lock before reaping
      always reaches this child.
    """

    def __init__(self, master_fd: int, pid: int, stdin: IO[bytes], stdout: IO[bytes]):
        self.master_fd = master_fd
        self.pid = pid
        self.stdin = stdin
        self.stdout = stdout
        self._returncode: int | None = None
        self._reaped = False
        self._reap_lock = threading.Lock()
        self._io_lock = threading.Lock()
        self._io_in_flight = 0
        self._closing = False
        self._fd_closed = False
        self._reaper_started = False

    @property
    def returncode(self) -> int | None:
        return self._returncode

    def resize(self, cols: int, rows: int) -> None:
        """Resize the PTY to the given dimensions."""
        if sys.platform == "win32" or self.master_fd < 0:
            return
        if not self._begin_io():
            return
        try:
            # Pack rows/cols into winsize struct: rows, cols, xpixel, ypixel
            winsize = struct.pack("HHHH", rows, cols, 0, 0)
            fcntl.ioctl(self.master_fd, termios.TIOCSWINSZ, winsize)
            # Send SIGWINCH to the process group so child processes notice
            with self._reap_lock:
                if not self._reaped:
                    try:
                        os.killpg(os.getpgid(self.pid), signal.SIGWINCH)
                    except (ProcessLookupError, PermissionError):
                        # Process may have exited or we may not have permission
                        with contextlib.suppress(ProcessLookupError):
                            os.kill(self.pid, signal.SIGWINCH)
            logger.debug(f"[PTY] Resized to {cols}x{rows}")
        except Exception as exc:
            logger.warning(f"[PTY] Failed to resize: {exc}")
        finally:
            self._end_io()

    def read(self, size: int) -> bytes:
        """Blocking read from the PTY; returns b"" once the PTY is closing."""
        if not self._begin_io():
            return b""
        try:
            return self.stdout.read(size) or b""
        finally:
            self._end_io()

    def write(self, data: bytes) -> None:
        """Blocking write to the PTY; dropped once the PTY is closing."""
        if not self._begin_io():
            return
        try:
            self.stdin.write(data)
        finally:
            self._end_io()

    def _begin_io(self) -> bool:
        with self._io_lock:
            if self._closing:
                return False
            self._io_in_flight += 1
            return True

    def _end_io(self) -> None:
        with self._io_lock:
            self._io_in_flight -= 1
            if self._closing and self._io_in_flight == 0:
                self._close_fd_locked()

    def _close_fd_locked(self) -> None:
        if self._fd_closed:
            return
        self._fd_closed = True
        with contextlib.suppress(OSError):
            os.close(self.master_fd)

    async def wait(self) -> int:
        """Wait for the process to exit."""
        return await asyncio.to_thread(self._wait_blocking)

    def _wait_blocking(self) -> int:
        if not self._reaped:
            waitid = getattr(os, "waitid", None)
            if waitid is not None:
                # Block until it exits without reaping, so the reap stays under the lock.
                with contextlib.suppress(ChildProcessError):
                    waitid(os.P_PID, self.pid, os.WEXITED | os.WNOWAIT)
                self._try_reap(blocking=True)
            else:
                # No os.waitid (macOS before Python 3.13): poll, reaping under the lock.
                while not self._try_reap(blocking=False):
                    time.sleep(PTY_REAP_POLL_INTERVAL)
        # Reaped elsewhere without a status (never by this class): not recoverable.
        return self._returncode if self._returncode is not None else -1

    def _try_reap(self, *, blocking: bool) -> bool:
        """Reap the child if it has exited; True once it is reaped."""
        with self._reap_lock:
            if self._reaped:
                return True
            try:
                pid, status = os.waitpid(self.pid, 0 if blocking else os.WNOHANG)
            except ChildProcessError:
                self._reaped = True
                return True
            if pid:
                self._reaped = True
                self._returncode = os.waitstatus_to_exitcode(status)
            return self._reaped

    def _signal(self, sig: int) -> None:
        """Send *sig* unless the child is already reaped (its pid may be reused)."""
        with self._reap_lock:
            if self._reaped:
                return
            with contextlib.suppress(ProcessLookupError, PermissionError):
                os.kill(self.pid, sig)

    def terminate(self) -> None:
        """Terminate the process."""
        self._signal(signal.SIGTERM)

    def hangup(self) -> None:
        """Send SIGHUP, as a closing terminal would.

        A tmux client detaches and exits (the session keeps running in its
        server), which also ends any read blocked on the PTY. Closing the master
        fd alone does not do this while a reader thread still holds it open.
        """
        self._signal(signal.SIGHUP)

    def close(self) -> None:
        """Hang up the client, close the PTY and reap the child (no zombies).

        The fd closes now if no read or write is in flight, otherwise when the
        last one returns (the hangup ends a blocked read).
        """
        self.hangup()
        with self._io_lock:
            self._closing = True
            if self._io_in_flight == 0:
                self._close_fd_locked()
        self._reap()

    def _reap(self) -> None:
        if self._try_reap(blocking=False):
            return
        with self._reap_lock:
            if self._reaper_started:
                return
            self._reaper_started = True
        threading.Thread(
            target=_reap_pty_child, args=(self,), name=f"pty-reaper-{self.pid}", daemon=True
        ).start()


# How long a hung-up PTY child gets to exit before the reaper sends SIGKILL.
PTY_HANGUP_GRACE = 2.0
# How often a reap without os.waitid polls the child.
PTY_REAP_POLL_INTERVAL = 0.02


def _reap_pty_child(process: LocalPTYProcess, grace: float | None = None) -> None:
    """Reap a hung-up PTY child, escalating to SIGKILL if it ignores SIGHUP.

    Killing a tmux *client* never kills its session, which lives in the server.
    """
    deadline = time.monotonic() + (PTY_HANGUP_GRACE if grace is None else grace)
    while time.monotonic() < deadline:
        if process._try_reap(blocking=False):
            return
        time.sleep(0.05)
    process._signal(signal.SIGKILL)
    process._wait_blocking()


async def _open_local_pty_tmux(
    working_directory: str,
    session: str,
    cols: int = 80,
    rows: int = 24,
) -> LocalPTYProcess:
    """Open a local tmux client on a real PTY, for proper resize support (POSIX only).

    A Windows host never comes here: its local box runs a native shell instead.
    """
    command = local_tmux_command(session) + ["new", "-As", session]
    if working_directory:
        command.extend(["-c", working_directory])

    env = os.environ.copy()
    env["TERM"] = "xterm-256color"

    master_fd, pid = await asyncio.to_thread(spawn_on_pty, command, env=env, rows=rows, cols=cols)

    stdin = os.fdopen(master_fd, "wb", buffering=0, closefd=False)
    stdout = os.fdopen(master_fd, "rb", buffering=0, closefd=False)

    logger.info(f"[PTY] Spawned local tmux with PID {pid}, fd {master_fd}")
    return LocalPTYProcess(master_fd, pid, stdin, stdout)


class WSLNotAvailableError(RuntimeError):
    """Raised when a WSL shell is requested but no distro is installed."""


def _resolve_windows_shell(shell_id: str) -> dict[str, object]:
    """Return the catalog entry for *shell_id*, falling back to the default.

    Raises WSLNotAvailableError when WSL is selected but unavailable so the
    websocket handler can close with a distinct code and the UI can toast.
    """
    shells = available_windows_shells()
    by_id = {str(s["id"]): s for s in shells}

    chosen = by_id.get(shell_id)
    if chosen is None:
        fallback = default_windows_shell()
        chosen = by_id.get(fallback) if fallback else None
    if chosen is None:
        raise RuntimeError("No usable Windows shell found")

    if chosen["id"] == "wsl" and not chosen.get("available"):
        raise WSLNotAvailableError("WSL is not installed. Run 'wsl --install' to enable it.")
    return chosen


async def _open_windows_shell(
    shell_id: str,
    working_directory: str,
    cols: int = 80,
    rows: int = 24,
) -> WinPTYProcess:
    """Spawn a native Windows shell in a ConPTY for the local box.

    *shell_id* is one of the ids from :func:`available_windows_shells`. Spawning
    runs in a thread since pywinpty's spawn is blocking.
    """
    chosen = _resolve_windows_shell(shell_id)
    argv = [str(a) for a in cast("list[object]", chosen["argv"])]
    cwd = working_directory or None

    def _spawn() -> WinPTYProcess:
        return WinPTYProcess.spawn(argv, cwd=cwd, cols=cols, rows=rows)

    process = await asyncio.to_thread(_spawn)
    logger.info(
        f"[Connection] Spawned native Windows shell '{chosen['id']}' in {working_directory}"
    )
    return process


async def _open_local_tmux(
    working_directory: str,
    session: str,
) -> asyncio.subprocess.Process:
    command = local_tmux_command(session) + ["new", "-As", session]
    if working_directory:
        target_dir = working_directory
        if LOCAL_IS_WINDOWS:
            converted = await _convert_path_to_wsl(working_directory)
            if converted:
                target_dir = converted
        command.extend(["-c", target_dir])

    # tmux requires a PTY to work properly. Use 'script' to provide one.
    # This is necessary on both Windows and Linux for proper terminal I/O.
    if LOCAL_IS_WINDOWS:
        # Use 'script' command in WSL to create a PTY
        # SECURITY: Use shlex.quote() to prevent command injection
        cmd_str = " ".join(shlex.quote(arg) for arg in command[2:])
        script_command = ["wsl", "--", "script", "-qefc", cmd_str, "/dev/null"]
        return await asyncio.create_subprocess_exec(
            *script_command,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

    # On Linux, also use script to create a PTY
    # -q: quiet (no start/done messages)
    # -e: return exit code of child process
    # -f: flush output immediately
    # -c: run command directly
    cmd_str = " ".join(shlex.quote(arg) for arg in command)
    script_command = ["script", "-qefc", cmd_str, "/dev/null"]
    # Set TERM for proper 256-color support in tmux
    env = os.environ.copy()
    env["TERM"] = "xterm-256color"
    return await asyncio.create_subprocess_exec(
        *script_command,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
    )


class Broadcaster:
    """Fan-out hub for ``/ws/progress`` and ``/ws/ping`` clients.

    Per-app instance: one set of clients owned by a single FastAPI app, so
    multiple ``make_app`` calls (notably in tests) don't share state.

    Each client has a bounded queue and one writer task. ``broadcast`` only puts
    the event on every queue and never awaits a socket, so a client that stops
    reading cannot delay the others. A client whose queue is full or whose send
    fails is removed and its socket closed.
    """

    def __init__(self, max_queue: int = 256) -> None:
        self._max_queue = max_queue
        self._clients: dict[WebSocket, tuple[asyncio.Queue, asyncio.Task]] = {}
        self._closers: set[asyncio.Task] = set()

    async def connect(self, ws: WebSocket, first: Awaitable[dict] | None = None) -> None:
        """Register *ws*. *first*, if given, resolves to the message sent before any event."""
        queue: asyncio.Queue = asyncio.Queue(maxsize=self._max_queue)
        if first is not None:
            queue.put_nowait(asyncio.ensure_future(first))
        self._clients[ws] = (queue, asyncio.create_task(self._write(ws, queue)))

    async def disconnect(self, ws: WebSocket) -> None:
        entry = self._clients.pop(ws, None)
        if entry is not None:
            self._stop(entry)

    async def broadcast(self, event: dict) -> None:
        for ws, (queue, _) in list(self._clients.items()):
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                self._drop(ws)

    @staticmethod
    def _stop(entry: tuple[asyncio.Queue, asyncio.Task]) -> None:
        queue, writer = entry
        if writer is not asyncio.current_task():
            writer.cancel()
        while not queue.empty():
            item = queue.get_nowait()
            if isinstance(item, asyncio.Future):
                item.cancel()

    def _drop(self, ws: WebSocket) -> None:
        entry = self._clients.pop(ws, None)
        if entry is None:
            return
        self._stop(entry)
        closer = asyncio.create_task(self._close(ws))
        self._closers.add(closer)
        closer.add_done_callback(self._closers.discard)

    @staticmethod
    async def _close(ws: WebSocket) -> None:
        with contextlib.suppress(Exception):
            await asyncio.wait_for(ws.close(), timeout=5)

    async def _write(self, ws: WebSocket, queue: asyncio.Queue) -> None:
        try:
            while True:
                item = await queue.get()
                if isinstance(item, asyncio.Future):
                    item = await item
                await ws.send_json(item)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.debug("broadcast client dropped: %s", exc)
            self._drop(ws)


def make_app(settings: ServerSettings | None = None) -> FastAPI:
    """Create and configure the FastAPI application.

    English:
        Applies security middleware, template globals, and route wiring. A
        :class:`ServerSettings` instance controls auth, CORS, upload limits, and
        alias resolution.

    日本語:
        セキュリティミドルウェアやテンプレート変数、各種ルートを設定します。
        :class:`ServerSettings` により認証や CORS、アップロード上限、エイリアス解決
        を制御します。

    Returns:
        FastAPI: Configured ASGI application.
    """

    settings = settings or ServerSettings()
    deps = APIDependencies(settings)

    # Progress-bar fan-out hub. Wired into deps so api/progress.py can
    # broadcast without importing webapp.py (no circular imports).
    progress_broadcaster = Broadcaster()
    deps.broadcast_progress = progress_broadcaster.broadcast

    # Ping fan-out hub. Same pattern — wired here to avoid circular imports.
    ping_broadcaster = Broadcaster()
    deps.broadcast_ping = ping_broadcaster.broadcast

    # This app's open tunnels (closed at shutdown). Per app, never module-global.
    tunnel_registry = TunnelRegistry()
    deps.tunnels = tunnel_registry

    # Keeps native Windows ConPTY shells alive across websocket disconnects so
    # they can be re-attached (session persistence). Posix hosts pay nothing —
    # the reaper is only started on Windows (see lifespan). When a shell is
    # reaped/exits, mark its tracked DB row inactive so SessionSwitcher updates.
    win_terminal_registry = WindowsTerminalRegistry()

    async def _on_win_session_reaped(key: tuple[str, str], session_id: str | None) -> None:
        if session_id:
            with contextlib.suppress(Exception):
                await state.update_session_activity_async(session_id, active=False)

    win_terminal_registry._on_reaped = _on_win_session_reaped

    # Application lifespan handler
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        """Manage application lifecycle: startup and shutdown."""
        from .snapshot import reconcile_on_startup, set_recovery_sessions, snapshot_loop

        # Startup: Initialize state DB and SSH connection pool
        load_config()  # Ensures state DB is initialized
        await initialize_pool()
        app.state.ssh_pool = get_pool()
        artifact_sidecar = None
        if settings.serve_artifacts:
            from .artifacts import ArtifactSidecar

            artifact_sidecar = ArtifactSidecar(settings.artifact_port)
            settings.artifact_server_port_actual = await asyncio.to_thread(artifact_sidecar.start)
            app.state.artifact_sidecar = artifact_sidecar

        # Detect lost sessions from before crash
        set_recovery_sessions(await reconcile_on_startup())

        # Purge stale snapshots from old inactive sessions
        purged = await state.purge_stale_snapshots_async()
        if purged > 0:
            logger.info("Purged %d stale snapshot(s) at startup", purged)

        # Pass the live server settings: PUT /snapshot/config mutates them and the
        # loop re-reads both fields on every tick.
        snapshot_task = asyncio.create_task(snapshot_loop(settings=settings))

        # Launch headless chromium for PDF export if playwright is available.
        # Soft-failure — sets PDF_RENDERER.available based on outcome.
        from .pdf import PDF_RENDERER

        await PDF_RENDERER.start()

        # Sweep persisted Windows shells for exited/idle entries (Windows only).
        if LOCAL_IS_WINDOWS:
            win_terminal_registry.start_reaper()

        try:
            yield
        finally:
            # Shutdown: Cancel snapshot loop and cleanup pools/sidecars.
            snapshot_task.cancel()
            tunnel_registry.close_all()
            if artifact_sidecar is not None:
                await asyncio.to_thread(artifact_sidecar.stop)
                settings.artifact_server_port_actual = None
            await shutdown_pool()
            await PDF_RENDERER.stop()
            await win_terminal_registry.shutdown()

    # Disable automatic /docs and /redoc endpoints
    application = FastAPI(
        title="sshler", version="0.1.0", docs_url=None, redoc_url=None, lifespan=lifespan
    )
    failure.install(application)
    mount_spa(application, settings.serve_spa)

    application.state.settings = settings
    application.state.auth_tracker = AuthFailureTracker()
    application.state.win_terminal_registry = win_terminal_registry
    application.state.tunnels = tunnel_registry

    application.add_middleware(GZipMiddleware, minimum_size=1000)

    if settings.allow_origins:
        application.add_middleware(
            CORSMiddleware,
            allow_origins=settings.allow_origins,
            allow_credentials=True,
            allow_methods=["GET", "POST", "OPTIONS"],
            allow_headers=["*"],
        )

    @application.middleware("http")
    async def _security_middleware(request: Request, call_next):
        auth_tracker: AuthFailureTracker = request.app.state.auth_tracker
        client_ip = get_client_ip(request)

        # Check authentication if required
        # Skip auth for health check endpoint (needed for load balancers)
        need_session_cookie = False
        if settings.auth_manager and request.method != "OPTIONS" and request.url.path != "/health":
            # Check if IP is locked out
            if auth_tracker.is_locked_out(client_ip):
                retry_after = auth_tracker.get_lockout_remaining(client_ip)
                logging.warning(
                    f"[Security] Blocked locked-out IP {client_ip} ({retry_after}s remaining)"
                )
                return failure.answer(
                    "rate_limited",
                    "Too many failed authentication attempts. Try again later.",
                    headers={"Retry-After": str(retry_after)},
                )

            # Try session cookie first (browsers always send cookies,
            # including with WebSocket upgrades, unlike Basic Auth headers)
            app_settings = get_app_settings()
            authenticated = False

            cookie_sid = request.cookies.get(app_settings.cookie_name)
            if cookie_sid:
                session_obj = get_session_store().get_session(cookie_sid)
                if session_obj:
                    authenticated = True

            if not authenticated:
                # Fall back to Basic Auth
                auth_header = request.headers.get("authorization")
                if not auth_header or not auth_header.startswith("Basic "):
                    return failure.answer(
                        "auth",
                        "Authentication required",
                        headers={"WWW-Authenticate": 'Basic realm="sshler"'},
                    )

                if not settings.auth_manager.verify_basic_auth_header(auth_header):
                    auth_tracker.record_failure(client_ip)
                    failure_count = auth_tracker.get_failure_count(client_ip)

                    logging.warning(
                        f"[Security] Failed auth attempt from {client_ip} "
                        f"({failure_count}/5 failures)"
                    )

                    await asyncio.sleep(2)

                    return failure.answer(
                        "auth",
                        "Invalid credentials",
                        headers={"WWW-Authenticate": 'Basic realm="sshler"'},
                    )

                # Successful Basic Auth - set session cookie so WebSocket works
                auth_tracker.reset_failures(client_ip)
                need_session_cookie = True

        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        frame_sources = ["'self'"]
        if settings.artifact_server_port_actual is not None:
            frame_sources.append(f"http://127.0.0.1:{settings.artifact_server_port_actual}")
        csp_directives = [
            "default-src 'self'",
            "img-src 'self' data:",
            "style-src 'self' 'unsafe-inline' https://unpkg.com https://cdn.jsdelivr.net",
            "script-src 'self' https://unpkg.com https://cdn.jsdelivr.net",
            "connect-src 'self' https://unpkg.com",
            f"frame-src {' '.join(frame_sources)}",
        ]
        response.headers["Content-Security-Policy"] = "; ".join(csp_directives) + ";"

        # Set session cookie after successful Basic Auth so subsequent
        # requests (especially WebSocket upgrades) can authenticate via cookie
        if need_session_cookie:
            app_settings = get_app_settings()
            username = "user"
            try:
                auth_header = request.headers.get("authorization", "")
                decoded = base64.b64decode(auth_header[6:]).decode("utf-8")
                username = decoded.split(":", 1)[0]
            except Exception:
                pass
            session_obj = get_session_store().create_session(
                username=username,
                user_id=username,
                ttl_seconds=app_settings.session_ttl_seconds,
            )
            response.set_cookie(
                key=app_settings.cookie_name,
                value=session_obj.session_id,
                max_age=app_settings.session_ttl_seconds,
                httponly=True,
                secure=app_settings.cookie_secure,
                samesite=app_settings.cookie_samesite,
                path="/",
            )

        return response

    @application.middleware("http")
    async def _rate_limit_middleware(request: Request, call_next):
        """Rate limiting middleware to prevent abuse."""
        # Skip rate limiting for SPA assets and health checks
        path = request.url.path
        if path.startswith("/app") or path == "/":
            return await call_next(request)

        # Get client identifier (IP address, respecting X-Real-IP from trusted proxies)
        client_ip = get_client_ip(request)

        # Different rate limits for different endpoint types. WebSocket upgrades
        # never reach an http middleware, so they are not rate limited here.
        if request.method == "POST":
            # POST requests: 120 per minute
            limiter = get_rate_limiter("post", rate=120, per=60)
        else:
            # General GET requests: 600 per minute
            # Single-user localhost app — user keeps 20+ tabs open,
            # each polling stats; this still catches runaway loops
            limiter = get_rate_limiter("general", rate=600, per=60)

        if not limiter.check(client_ip):
            return failure.answer(
                "rate_limited",
                "Rate limit exceeded. Please try again later.",
                headers={"Retry-After": "60"},
            )

        return await call_next(request)

    @application.middleware("http")
    async def _origin_check_middleware(request: Request, call_next):
        """Origin header validation for CSRF protection on state-changing requests."""
        # Get settings
        app_settings = get_app_settings()

        # Only check state-changing methods if origin check is enabled
        if app_settings.origin_check_enabled and request.method in (
            "POST",
            "PUT",
            "PATCH",
            "DELETE",
        ):
            origin = request.headers.get("origin") or request.headers.get("referer")

            # Only validate Origin if present — missing Origin is allowed
            # Non-browser clients (curl, health checks, API clients) may not send Origin
            # Browser requests will always include Origin/Referer on state-changing requests
            if origin:
                origin_value = origin_base(origin)
                allowed_origins_list = allowed_origins(request.app.state.settings, app_settings)

                if origin_value not in allowed_origins_list:
                    logging.warning(
                        f"[Security] Blocked request with invalid Origin: {origin_value} "
                        f"(expected one of: {allowed_origins_list})"
                    )
                    return failure.answer("origin", "Invalid Origin header")

        return await call_next(request)

    _require_token = deps.require_token

    async def _get_application_config() -> AppConfig:
        """Dependency that loads the persisted configuration with caching."""

        return await deps.get_application_config()

    @application.get("/")
    async def root() -> RedirectResponse:
        """Redirect the index page to the Vue SPA.

        English:
            Visiting ``/`` immediately sends the browser to the Vue SPA at ``/app/``.

        日本語:
            ルート ``/`` にアクセスした際に Vue SPA (``/app/``) へリダイレクトします。

        Returns:
            RedirectResponse: HTTP redirect to ``/app/``.
        """
        return RedirectResponse(url="/app/")

    @application.websocket("/ws/term")
    async def terminal_socket(
        websocket: WebSocket,
        host: str = Query(...),
        directory: str = Query(..., alias="dir"),
        session: str = Query("sshler"),
        columns: int = Query(120, alias="cols"),
        rows: int = Query(32, alias="rows"),
        shell: str = Query("", alias="shell"),
    ) -> None:
        """Bridge between the browser websocket and a terminal: tmux locally or over
        SSH, or a native Windows shell.

        SECURITY: Session names are sanitized to prevent command injection attacks.

        English:
            Checks who may connect and the session name, then opens the terminal the
            box uses and runs the pump (``sshler.terminal``) until the session ends.

        日本語:
            接続の可否とセッション名を確認し、ボックスに応じたターミナルを開いて、
            セッションが終わるまでポンプ (``sshler.terminal``) を動かします。

        Args:
            websocket: Websocket connection from the browser, not yet accepted.
            host: Box identifier provided by the client.
            directory: Requested working directory.
            session: Requested tmux session name.
            columns: Terminal width reported by the client.
            rows: Terminal height reported by the client.
            shell: Native Windows shell id (Windows hosts only).

        Returns:
            None: The coroutine completes when the websocket terminates.
        """
        # Who may open the socket is decided before anything about the request is
        # validated, so a caller without credentials learns nothing else.
        refusal = websocket_refusal(websocket)
        if refusal is not None:
            await websocket.close(code=refusal[0], reason=refusal[1])
            return

        # Session names reach tmux command lines, so only safe characters pass.
        try:
            session = PathValidator.sanitize_session_name(session)
        except ValidationError as exc:
            logger.warning(f"[Security] Invalid session name rejected: {exc}")
            await websocket.close(code=4400, reason="Invalid session name")
            return

        await websocket.accept()
        request = TerminalRequest(host, directory, session, columns, rows, shell)
        await _serve_terminal(websocket, request)

    @application.websocket("/ws/progress")
    async def progress_socket(websocket: WebSocket) -> None:
        """Broadcast progress-bar upsert/delete events to all listeners.

        Who may connect is decided by ``websocket_refusal``, as for ``/ws/term``.
        On accept, sends a ``{"type": "snapshot"}`` event so the client sees current
        state without an extra GET.
        Clients are not expected to send anything; the receive loop runs
        purely as a disconnect sentinel.
        """

        refusal = websocket_refusal(websocket)
        if refusal is not None:
            await websocket.close(code=refusal[0], reason=refusal[1])
            return

        await websocket.accept()

        async def progress_snapshot() -> dict:
            bars = await state.list_progress_async()
            return {
                "type": "snapshot",
                "bars": [
                    {
                        "name": b.name,
                        "current": b.current,
                        "total": b.total,
                        "color": b.color,
                        "label": b.label,
                        "status": b.status,
                        "created_at": b.created_at,
                        "updated_at": b.updated_at,
                    }
                    for b in bars
                ],
            }

        await progress_broadcaster.connect(websocket, first=progress_snapshot())
        try:
            # Disconnect sentinel — clients are not expected to send anything.
            while True:
                await websocket.receive_text()
        except WebSocketDisconnect:
            pass
        except Exception as exc:  # pragma: no cover - defensive
            logger.debug("progress socket error: %s", exc)
        finally:
            await progress_broadcaster.disconnect(websocket)

    @application.websocket("/ws/ping")
    async def ping_socket(websocket: WebSocket) -> None:
        """Broadcast ephemeral ping events to all listeners.

        No snapshot on connect — pings are fire-and-forget. Clients are not
        expected to send anything; the receive loop is a disconnect sentinel.
        """
        refusal = websocket_refusal(websocket)
        if refusal is not None:
            await websocket.close(code=refusal[0], reason=refusal[1])
            return

        await websocket.accept()
        await ping_broadcaster.connect(websocket)
        try:
            while True:
                await websocket.receive_text()
        except WebSocketDisconnect:
            pass
        except Exception as exc:  # pragma: no cover - defensive
            logger.debug("ping socket error: %s", exc)
        finally:
            await ping_broadcaster.disconnect(websocket)

    # Health check endpoint for monitoring
    @application.get("/health")
    async def health_check():
        """Health check endpoint for load balancers and monitoring tools."""
        return {"status": "ok", "timestamp": time.time()}

    # Include auth router (for session-based authentication)
    auth_router = create_auth_router(settings.auth_manager, application.state.auth_tracker)
    application.include_router(auth_router, prefix="/api/v1")

    # Include main API router
    application.include_router(create_api_router(deps))
    return application


@dataclass(frozen=True)
class TerminalRequest:
    """What a client asked ``/ws/term`` for, its session name already sanitized."""

    host: str
    directory: str
    session: str
    columns: int
    rows: int
    shell: str


async def _serve_terminal(websocket: WebSocket, request: TerminalRequest) -> None:
    """Open the terminal *request* names on an accepted socket, run it, tear it down."""
    config = load_config()
    box = find_box(config, request.host)
    if not box:
        await websocket.close()
        return
    kind = getattr(box, "transport", "ssh")
    directory = (
        _normalize_local_path(request.directory)
        if kind == "local"
        else request.directory  # a remote folder is resolved on the terminal's connection
    )
    client_host = websocket.client.host if websocket.client else "unknown"
    logger.info(
        f"[Connection] WebSocket connected: box={box.name}, transport={kind}, "
        f"dir={directory}, session={request.session}, client={client_host}"
    )
    try:
        opened = await _open_terminal(websocket, box, config, request, directory)
    except TerminalOpenError as exc:
        with contextlib.suppress(Exception):
            await websocket.send_text(exc.message)
        await websocket.close(code=exc.code, reason=exc.reason)
        return
    # The one teardown: whatever ends the session, the transport closes here.
    try:
        await _run_terminal(websocket, opened, box, kind, request)
    finally:
        with teardown_scope():
            await opened.transport.close()
        logger.info(
            f"[Connection] WebSocket closed: box={box.name}, transport={kind}, "
            f"session={request.session}, client={client_host}"
        )


async def _open_terminal(
    websocket: WebSocket, box: Box, config: AppConfig, request: TerminalRequest, directory: str
) -> OpenedTerminal:
    """Open the transport *box* uses; raises ``TerminalOpenError`` with what to tell the client."""
    if getattr(box, "transport", "ssh") != "local":
        return await _open_remote_terminal(box, config, request, directory)
    directory = await _local_directory_or_default(box, directory)
    if LOCAL_IS_WINDOWS:
        registry = websocket.app.state.win_terminal_registry
        return await _open_windows_terminal(registry, box, request, directory)
    return await _open_local_terminal(request, directory)


async def _local_directory_or_default(box: Box, directory: str) -> str:
    try:
        is_directory = await _local_is_directory(directory)
    except Exception as exc:
        logger.debug(f"Failed to check if directory exists (using fallback): {exc}")
        is_directory = False
    return directory if is_directory else _normalize_local_path(box.default_dir)


async def _discard_pty(process: LocalPTYProcess) -> None:
    await LocalPtyTransport(process, None).close()


async def _open_local_terminal(request: TerminalRequest, directory: str) -> OpenedTerminal:
    """A tmux client on a local PTY, attached to (or creating) the session."""
    session = request.session
    logger.info(f"[Connection] Starting local tmux: dir={directory}, session={session}")
    try:
        process = await open_or_close_on_cancel(
            _open_local_pty_tmux(directory, session), _discard_pty
        )
    except Exception as exc:
        logger.error(f"[Connection] Failed to start local tmux: {exc}", exc_info=True)
        raise TerminalOpenError(f"Connection failed: {exc}\r\n") from exc
    logger.info("[Connection] Local PTY tmux process started successfully")
    # Ctrl+B c opens new windows in the pane's directory, and the session is recorded
    # in ts history so it appears in ts's picker.
    background = (_configure_tmux_bindings(session), record_ts_history(session, directory))
    transport = LocalPtyTransport(process, LocalTmuxWindows(session), background)
    return OpenedTerminal(transport, directory)


async def _open_windows_terminal(
    registry: WindowsTerminalRegistry, box: Box, request: TerminalRequest, directory: str
) -> OpenedTerminal:
    """A tab of the persisted native Windows shell for (box, session), spawned if none."""
    logger.info(
        f"[Connection] Starting native Windows shell: "
        f"dir={directory}, shell={request.shell or 'default'}"
    )

    async def spawn() -> WinPTYProcess:
        return await _open_windows_shell(request.shell, directory, request.columns, request.rows)

    try:
        session, created = await registry.get_or_create(
            (box.name, request.session), spawn, request.columns, request.rows
        )
    except TooManyTerminalsError as exc:
        logger.warning(
            "websocket rejected: terminal session cap reached",
            extra={
                "service": "terminal",
                "event": "session_cap",
                "host": request.host,
                "error": str(exc),
            },
        )
        too_many = "Too many open terminals on this server. Close one before opening another.\r\n"
        raise TerminalOpenError(too_many, 4504, "Too many terminals") from exc
    except WSLNotAvailableError as exc:
        logger.info(f"[Connection] WSL unavailable: {exc}")
        raise TerminalOpenError(f"{exc}\r\n", 4502, "WSL not installed") from exc
    except WinPTYUnavailableError as exc:
        logger.warning(f"[Connection] pywinpty unavailable: {exc}")
        raise TerminalOpenError(f"{exc}\r\n", 4503, "pywinpty not installed") from exc
    except Exception as exc:
        logger.error(f"[Connection] Failed to start Windows shell: {exc}", exc_info=True)
        raise TerminalOpenError(f"Connection failed: {exc}\r\n") from exc
    logger.info("[Connection] Windows shell %s", "started" if created else "re-attached")
    transport = await ConPtyTransport.attach(registry, session, request.columns, request.rows)

    # The registry marks the row inactive when the shell is reaped or exits.
    def hand_row_to_registry(session_id: str) -> None:
        session.session_id = session_id

    return OpenedTerminal(transport, directory, on_tracked=hand_row_to_registry)


async def _open_remote_terminal(
    box: Box, config: AppConfig, request: TerminalRequest, directory: str
) -> OpenedTerminal:
    """tmux on a remote box over a connection of its own, closed with the terminal."""
    logger.info(
        f"[Connection] Connecting to SSH: host={box.connect_host}, user={box.user}, port={box.port}"
    )
    try:
        connection = await connect(
            box.connect_host,
            box.user,
            box.port,
            box.keyfile,
            box.known_hosts,
            config.ssh_config_path,
            box.ssh_alias,
        )
    except Exception as exc:  # pragma: no cover - network errors are environment specific
        logger.error(f"[Connection] SSH connection failed: {exc}", exc_info=True)
        raise TerminalOpenError(f"Connection failed: {exc}") from exc
    logger.info("[Connection] SSH connection established successfully")
    try:
        directory = await _remote_directory_or_default(connection, box, directory)
        process = await open_tmux(
            connection,
            working_directory=directory,
            session=request.session,
            columns=request.columns,
            rows=request.rows,
        )
    except BaseException:
        with teardown_scope():
            await close_connection(connection)
        raise
    tmux = RemoteTmuxWindows(connection, request.session)
    return OpenedTerminal(SshTransport(connection, process, tmux), directory)


async def _remote_directory_or_default(
    connection: asyncssh.SSHClientConnection, box: Box, requested: str
) -> str:
    """The folder tmux starts in: ``requested``, or the box's default folder when that is
    not a directory. A ``~`` in either is the box's home, asked on this connection.

    A box whose home cannot be read still gets its terminal: a folder that needs the
    home is passed over, and the last resort is ``/``."""
    directory = await _resolved_remote_directory(connection, box, requested)
    if directory is not None:
        try:
            if await sftp_is_directory(connection, directory):
                return directory
        except Exception as exc:
            logger.debug(f"Failed to check if remote directory exists (using it): {exc}")
            return directory
    default = box.default_dir or f"/home/{box.user}"
    return await _resolved_remote_directory(connection, box, default) or "/"


async def _resolved_remote_directory(
    connection: asyncssh.SSHClientConnection, box: Box, folder: str
) -> str | None:
    """``folder`` with ``~`` resolved, or ``None`` when the box's home cannot be read."""
    try:
        return await remote_directory(connection, folder)
    except HTTPException as exc:
        reason = str(exc.detail)
    except TimeoutError:
        reason = "the lookup timed out"
    except (asyncssh.Error, OSError) as exc:
        reason = str(exc)
    logger.warning("Cannot resolve %r on %s: %s", folder, box.name, reason)
    return None


async def _run_terminal(
    websocket: WebSocket, opened: OpenedTerminal, box: Box, kind: str, request: TerminalRequest
) -> None:
    """Track the session and run the pump until the client leaves or the terminal ends."""
    if not await take_early_message(websocket, opened.transport):
        return
    session_id = await _track_terminal(opened, box, kind, request)
    try:
        await run_pump(websocket, opened.transport, partial(_record_windows, session_id))
    finally:
        # A persisted Windows shell's row is the registry's to mark (see on_tracked).
        if session_id and opened.on_tracked is None:
            with teardown_scope():
                try:
                    await state.update_session_activity_async(session_id, active=False)
                    logger.info(f"Session marked inactive: {session_id}")
                except Exception as exc:
                    logger.warning(f"Failed to mark session inactive: {exc}")


async def _track_terminal(
    opened: OpenedTerminal, box: Box, kind: str, request: TerminalRequest
) -> str | None:
    """Record the session row; its id, or None when it could not be recorded."""
    try:
        tracked = await state.create_or_update_session_async(
            box_name=box.name,
            session_name=request.session,
            working_directory=opened.directory,
            metadata={"columns": request.columns, "rows": request.rows, "transport": kind},
        )
        if opened.on_tracked is not None:
            opened.on_tracked(tracked.id)
    except Exception as exc:
        logger.warning(f"Failed to track session: {exc}")
        return None
    logger.info(f"Session tracked: {tracked.id}")
    return tracked.id


async def _record_windows(session_id: str | None, windows: list[dict[str, str | bool]]) -> None:
    """Record the window count each time the window list is sent."""
    if not session_id:
        return
    try:
        await state.update_session_activity_async(
            session_id, active=True, window_count=len(windows)
        )
    except Exception as exc:
        logger.debug(f"Failed to update session activity: {exc}")
