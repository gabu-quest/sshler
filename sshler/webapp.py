from __future__ import annotations

import asyncio
import base64
import contextlib
import json
import logging
import os
import secrets
import shlex
import subprocess
import sys
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path

import asyncssh
from fastapi import (
    FastAPI,
    Query,
    Request,
    WebSocket,
    WebSocketDisconnect,
)
from starlette.websockets import WebSocketState
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse, Response
from fastapi.staticfiles import StaticFiles

from . import __version__, state
from .api import APIDependencies, create_api_router
from .api.auth import create_auth_router
from .auth import AuthManager, PasswordHasher, PasswordValidator, PasswordPolicy
from .session import get_session_store
from .settings import get_settings as get_app_settings
from .api.helpers import (
    DEFAULT_MAX_UPLOAD_BYTES,
    LOCAL_IS_WINDOWS,
    _local_is_directory,
    _normalize_directory_path,
    _normalize_local_path,
)
from .config import (
    AppConfig,
    Box,
    find_box,
    load_config,
)
from .spa import mount_spa
from .ssh import (
    connect,
    open_tmux,
    sftp_is_directory,
)
from .ssh_pool import get_pool, initialize_pool, shutdown_pool
from .validation import PathValidator, ValidationError
from .rate_limit import get_rate_limiter

logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "static"
SPA_DIST_DIR = STATIC_DIR / "dist"

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
                f"[Security] IP {client_ip} locked out after {len(self._failures[client_ip])} failed auth attempts"
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


def _local_tmux_base_command() -> list[str]:
    if LOCAL_IS_WINDOWS:
        return ["wsl", "--", "tmux"]
    return ["tmux"]


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


# Import PTY-related modules for local terminal resize
import fcntl
import pty
import signal
import struct
import termios
from typing import IO


class LocalPTYProcess:
    """Wrapper for a local PTY process with resize support.
    
    This class manages a process running in a pseudo-terminal, allowing
    proper terminal resize via ioctl TIOCSWINSZ + SIGWINCH.
    """
    
    def __init__(self, master_fd: int, pid: int, stdin: IO[bytes], stdout: IO[bytes]):
        self.master_fd = master_fd
        self.pid = pid
        self.stdin = stdin
        self.stdout = stdout
        self._returncode: int | None = None
    
    @property
    def returncode(self) -> int | None:
        return self._returncode
    
    def resize(self, cols: int, rows: int) -> None:
        """Resize the PTY to the given dimensions."""
        if self.master_fd < 0:
            return
        try:
            # Pack rows/cols into winsize struct: rows, cols, xpixel, ypixel
            winsize = struct.pack("HHHH", rows, cols, 0, 0)
            fcntl.ioctl(self.master_fd, termios.TIOCSWINSZ, winsize)
            # Send SIGWINCH to the process group so child processes notice
            try:
                os.killpg(os.getpgid(self.pid), signal.SIGWINCH)
            except (ProcessLookupError, PermissionError):
                # Process may have exited or we may not have permission
                try:
                    os.kill(self.pid, signal.SIGWINCH)
                except ProcessLookupError:
                    pass
            logger.debug(f"[PTY] Resized to {cols}x{rows}")
        except Exception as exc:
            logger.warning(f"[PTY] Failed to resize: {exc}")
    
    async def wait(self) -> int:
        """Wait for the process to exit."""
        _, status = await asyncio.to_thread(os.waitpid, self.pid, 0)
        self._returncode = os.waitstatus_to_exitcode(status)
        return self._returncode
    
    def terminate(self) -> None:
        """Terminate the process."""
        try:
            os.kill(self.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    
    def close(self) -> None:
        """Close the PTY file descriptors."""
        try:
            os.close(self.master_fd)
        except OSError:
            pass


async def _open_local_pty_tmux(
    working_directory: str,
    session: str,
    cols: int = 80,
    rows: int = 24,
) -> LocalPTYProcess:
    """Open a local tmux session using a real PTY for proper resize support."""
    command = list(_local_tmux_base_command()) + ["new", "-As", session]
    if working_directory:
        target_dir = working_directory
        if LOCAL_IS_WINDOWS:
            converted = await _convert_path_to_wsl(working_directory)
            if converted:
                target_dir = converted
        command.extend(["-c", target_dir])
    
    def _spawn_pty() -> tuple[int, int]:
        """Spawn the process in a PTY (runs in thread)."""
        # Create a new PTY
        master_fd, slave_fd = pty.openpty()
        
        # Set initial window size
        winsize = struct.pack("HHHH", rows, cols, 0, 0)
        fcntl.ioctl(slave_fd, termios.TIOCSWINSZ, winsize)
        
        # Fork
        pid = os.fork()
        if pid == 0:
            # Child process
            os.close(master_fd)
            os.setsid()  # Create new session
            
            # Make slave the controlling terminal
            fcntl.ioctl(slave_fd, termios.TIOCSCTTY, 0)
            
            # Redirect std streams to slave
            os.dup2(slave_fd, 0)
            os.dup2(slave_fd, 1)
            os.dup2(slave_fd, 2)
            
            if slave_fd > 2:
                os.close(slave_fd)
            
            # Set up environment
            env = os.environ.copy()
            env["TERM"] = "xterm-256color"
            
            # Execute the command
            os.execvpe(command[0], command, env)
        
        # Parent process
        os.close(slave_fd)
        return master_fd, pid
    
    master_fd, pid = await asyncio.to_thread(_spawn_pty)
    
    # Create file objects for async I/O
    # Note: We use os.fdopen with buffering=0 for raw access
    stdin = os.fdopen(master_fd, 'wb', buffering=0, closefd=False)
    stdout = os.fdopen(master_fd, 'rb', buffering=0, closefd=False)
    
    logger.info(f"[PTY] Spawned local tmux with PID {pid}, fd {master_fd}")
    return LocalPTYProcess(master_fd, pid, stdin, stdout)


async def _open_local_tmux(
    working_directory: str,
    session: str,
) -> asyncio.subprocess.Process:
    command = list(_local_tmux_base_command()) + ["new", "-As", session]
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


async def _run_local_tmux_command(args: list[str]) -> None:
    command = list(_local_tmux_base_command()) + args
    try:
        process = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await process.communicate()
    except Exception as exc:
        logger.debug(f"Local tmux command failed: {' '.join(args)}: {exc}")


async def _list_local_tmux_windows(session: str) -> list[dict[str, str | bool]] | None:
    command = list(_local_tmux_base_command()) + [
        "list-windows",
        "-F",
        "#{window_index} #{window_name} #{window_active}",
        "-t",
        session,
    ]
    try:
        process = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await process.communicate()
    except Exception as exc:
        logger.debug(f"Failed to list local tmux windows for session {session}: {exc}")
        return None

    if process.returncode != 0:
        return None

    windows: list[dict[str, str | bool]] = []
    for line in stdout.decode("utf-8", errors="ignore").splitlines():
        parts = line.split(" ", 2)
        if len(parts) < 3:
            continue
        index, name, active = parts
        windows.append({"index": index, "name": name, "active": active == "1"})
    return windows


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

    # Application lifespan handler
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        """Manage application lifecycle: startup and shutdown."""
        # Startup: Initialize SSH connection pool
        await initialize_pool()
        app.state.ssh_pool = get_pool()
        yield
        # Shutdown: Cleanup SSH connection pool
        await shutdown_pool()

    # Disable automatic /docs and /redoc endpoints
    application = FastAPI(
        title="sshler", version="0.1.0", docs_url=None, redoc_url=None, lifespan=lifespan
    )
    application.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
    mount_spa(application, settings.serve_spa)
    app_version = _compute_app_version()

    application.state.settings = settings
    application.state.auth_tracker = AuthFailureTracker()

    if settings.allow_origins:
        application.add_middleware(
            CORSMiddleware,
            allow_origins=settings.allow_origins,
            allow_credentials=True,
            allow_methods=["GET", "POST", "OPTIONS"],
            allow_headers=["*"]
        )

    def _get_client_ip(request: Request) -> str:
        """Get client IP, respecting X-Real-IP header from trusted proxies.

        When behind a reverse proxy (Caddy), the actual client IP is forwarded
        via the X-Real-IP header. We only trust this header when the request
        comes from localhost (where Caddy runs).
        """
        # Trust Caddy's X-Real-IP header if present and request is from localhost
        if request.client and request.client.host == "127.0.0.1":
            forwarded_ip = request.headers.get("x-real-ip")
            if forwarded_ip:
                return forwarded_ip
        # Otherwise use direct client IP
        return request.client.host if request.client else "unknown"

    @application.middleware("http")
    async def _security_middleware(request: Request, call_next):
        auth_tracker: AuthFailureTracker = request.app.state.auth_tracker
        client_ip = _get_client_ip(request)

        # Check authentication if required
        # Skip auth for health check endpoint (needed for load balancers)
        need_session_cookie = False
        if settings.auth_manager and request.method != "OPTIONS" and request.url.path != "/health":
            # Check if IP is locked out
            if auth_tracker.is_locked_out(client_ip):
                retry_after = auth_tracker.get_lockout_remaining(client_ip)
                logging.warning(
                    f"[Security] Blocked locked-out IP {client_ip} "
                    f"({retry_after}s remaining)"
                )
                return Response(
                    status_code=429,
                    content="Too many failed authentication attempts. Try again later.",
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
                    return Response(
                        status_code=401,
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

                    return Response(
                        status_code=401,
                        headers={"WWW-Authenticate": 'Basic realm="sshler"'},
                    )

                # Successful Basic Auth - set session cookie so WebSocket works
                auth_tracker.reset_failures(client_ip)
                need_session_cookie = True

        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        csp_directives = [
            "default-src 'self'",
            "img-src 'self' data:",
            "style-src 'self' 'unsafe-inline' https://unpkg.com https://cdn.jsdelivr.net",
            "script-src 'self' https://unpkg.com https://cdn.jsdelivr.net",
            "connect-src 'self' https://unpkg.com",
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
        # Skip rate limiting for static files, spa assets, and health checks
        path = request.url.path
        if path.startswith("/static/") or path.startswith("/app") or path == "/":
            return await call_next(request)

        # Get client identifier (IP address, respecting X-Real-IP from trusted proxies)
        client_ip = _get_client_ip(request)

        # Different rate limits for different endpoint types
        if request.url.path.startswith("/ws/"):
            # WebSocket connections: 10 per minute
            limiter = get_rate_limiter("websocket", rate=10, per=60)
        elif request.method == "POST":
            # POST requests: 120 per minute
            limiter = get_rate_limiter("post", rate=120, per=60)
        else:
            # General GET requests: 600 per minute
            # Single-user localhost app — user keeps 20+ tabs open,
            # each polling stats; this still catches runaway loops
            limiter = get_rate_limiter("general", rate=600, per=60)

        if not limiter.check(client_ip):
            return Response(
                status_code=429,
                content="Rate limit exceeded. Please try again later.",
                headers={"Retry-After": "60"},
            )

        return await call_next(request)

    @application.middleware("http")
    async def _origin_check_middleware(request: Request, call_next):
        """Origin header validation for CSRF protection on state-changing requests."""
        # Get settings
        app_settings = get_app_settings()

        # Only check state-changing methods if origin check is enabled
        if (
            app_settings.origin_check_enabled
            and request.method in ("POST", "PUT", "PATCH", "DELETE")
            and not request.url.path.startswith("/static/")
        ):
            origin = request.headers.get("origin") or request.headers.get("referer")

            # Only validate Origin if present — missing Origin is allowed
            # Non-browser clients (curl, health checks, API clients) may not send Origin
            # Browser requests will always include Origin/Referer on state-changing requests
            if origin:
                # Normalize origin (remove trailing slash, extract scheme://host:port)
                from urllib.parse import urlparse

                parsed_origin = urlparse(origin)
                origin_base = f"{parsed_origin.scheme}://{parsed_origin.netloc}"

                # Parse expected origin from public_url
                parsed_public = urlparse(app_settings.public_url)
                expected_origin = f"{parsed_public.scheme}://{parsed_public.netloc}"

                # Check if origin matches public_url or is in allowed_origins
                # Include both env-based origins (SshlerSettings) and CLI-based origins (ServerSettings)
                cli_settings: ServerSettings = request.app.state.settings
                allowed_origins = (
                    [expected_origin]
                    + app_settings.allowed_origins_list
                    + cli_settings.allow_origins
                )

                if origin_base not in allowed_origins:
                    logging.warning(
                        f"[Security] Blocked request with invalid Origin: {origin_base} "
                        f"(expected one of: {allowed_origins})"
                    )
                    return Response(
                        status_code=403,
                        content="Invalid Origin header",
                    )

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
    ) -> None:
        """Bridge between the browser websocket and tmux over SSH or locally.

        SECURITY: Session names are sanitized to prevent command injection attacks.

        English:
            Streams bytes between the browser and tmux, handling command
            messages (resize, rename, etc.) and window polling.

        日本語:
            ブラウザと tmux の間でバイトストリームを仲介し、リサイズやウィンドウ
            切り替えなどのコマンドメッセージを処理します。

        Args:
            websocket: Accepted websocket connection from the browser.
            host: Box identifier provided by the client.
            directory: Requested working directory.
            session: Requested tmux session name.
            columns: Terminal width reported by the client.
            rows: Terminal height reported by the client.

        Returns:
            None: The coroutine completes when the websocket terminates.
        """

        settings: ServerSettings = websocket.app.state.settings
        logger = logging.getLogger("sshler.webapp")

        # Pre-initialize variables used in the finally block so early returns
        # (auth failures, validation errors) don't cause UnboundLocalError.
        box: Box | None = None
        transport = "ssh"
        client_host = "unknown"
        connection: asyncssh.SSHClientConnection | None = None
        process: asyncssh.SSHClientProcess | LocalPTYProcess | None = None
        session_id: str | None = None

        try:
            # Sanitize session name to prevent command injection
            # Session names are passed to tmux/subprocess commands, so only allow safe characters
            try:
                session = PathValidator.sanitize_session_name(session)
            except ValidationError as exc:
                logger.warning(f"[Security] Invalid session name rejected: {exc}")
                await websocket.close(code=4400, reason="Invalid session name")
                return

            # Authenticate WebSocket connection
            if settings.auth_manager:
                ws_authenticated = False

                # Try Basic Auth header (works for API clients)
                auth_header = websocket.headers.get("authorization")
                if auth_header and settings.auth_manager.verify_basic_auth_header(auth_header):
                    ws_authenticated = True

                # Try session cookie (browsers send cookies with WebSocket upgrades,
                # but may not send the Authorization header)
                if not ws_authenticated:
                    app_settings = get_app_settings()
                    cookie_sid = websocket.cookies.get(app_settings.cookie_name)
                    if cookie_sid:
                        session_obj = get_session_store().get_session(cookie_sid)
                        if session_obj:
                            ws_authenticated = True

                if not ws_authenticated:
                    logger.warning("[Connection] WebSocket auth failed: no valid credentials")
                    await websocket.close(code=4401, reason="Unauthorized")
                    return

            # Check CSRF token
            token_param = websocket.query_params.get("token")
            if settings.csrf_token and token_param != settings.csrf_token:
                logger.warning("[Connection] WebSocket auth failed: invalid CSRF token")
                await websocket.close(code=4403, reason="Invalid token")
                return

            await websocket.accept()
            application_config = load_config()
            box = find_box(application_config, host)
            if not box:
                await websocket.close()
                return

            transport = getattr(box, "transport", "ssh")
            normalized_directory = (
                _normalize_local_path(directory)
                if transport == "local"
                else _normalize_directory_path(directory)
            )

            client_host = websocket.client.host if websocket.client else "unknown"
            logger.info(
                f"[Connection] WebSocket connected: box={box.name}, transport={transport}, "
                f"dir={normalized_directory}, session={session}, client={client_host}"
            )

            if transport == "local":
                try:
                    is_directory = await _local_is_directory(normalized_directory)
                except Exception as exc:
                    logger.debug(f"Failed to check if directory exists (using fallback): {exc}")
                    is_directory = False
                if not is_directory:
                    normalized_directory = _normalize_local_path(box.default_dir)

                logger.info(
                    f"[Connection] Starting local tmux: dir={normalized_directory}, session={session}"
                )

                try:
                    # Use PTY-based local terminal for proper resize support
                    process = await _open_local_pty_tmux(normalized_directory, session)
                    logger.info(f"[Connection] Local PTY tmux process started successfully")
                except Exception as exc:
                    logger.error(f"[Connection] Failed to start local tmux: {exc}", exc_info=True)
                    error_msg = f"Connection failed: {exc}\r\n"
                    try:
                        await websocket.send_text(error_msg)
                    except Exception as ws_exc:
                        logger.debug(f"Failed to send error message via websocket: {ws_exc}")
                    await websocket.close()
                    return
            else:
                logger.info(
                    f"[Connection] Connecting to SSH: host={box.connect_host}, "
                    f"user={box.user}, port={box.port}"
                )
                try:
                    connection = await connect(
                        box.connect_host,
                        box.user,
                        box.port,
                        box.keyfile,
                        box.known_hosts,
                        application_config.ssh_config_path,
                        box.ssh_alias,
                    )
                    logger.info(f"[Connection] SSH connection established successfully")
                except Exception as exc:  # pragma: no cover
                    # network errors are environment specific
                    logger.error(f"[Connection] SSH connection failed: {exc}", exc_info=True)
                    await websocket.send_text(f"Connection failed: {exc}")
                    await websocket.close()
                    return

                try:
                    is_directory = await sftp_is_directory(connection, normalized_directory)
                    if not is_directory:
                        normalized_directory = box.default_dir or f"/home/{box.user}"
                except Exception as exc:
                    logger.debug(f"Failed to check if remote directory exists (using default): {exc}")

                process = await open_tmux(
                    connection,
                    working_directory=normalized_directory,
                    session=session,
                    columns=columns,
                    rows=rows,
                )

            async def _process_message(message: dict[str, object]) -> bool:
                message_type = message.get("type")
                if message_type == "websocket.disconnect":
                    logger.info("Writer: got disconnect")
                    return False

                if "text" in message and message["text"] is not None:
                    text_value = message["text"]
                    if isinstance(text_value, str):
                        logger.debug(f"Writer: got text message: {text_value[:50]}")
                        await _handle_control_message(
                            text_value,
                            process,
                            connection,
                            session,
                            transport,
                        )
                    return True

                if "bytes" in message and message["bytes"] is not None:
                    bytes_value = message["bytes"]
                    if isinstance(bytes_value, bytes):
                        num_bytes = len(bytes_value)
                        logger.debug(f"Writer: got {num_bytes} bytes, writing to stdin")
                        if process is not None and process.stdin is not None:
                            # LocalPTYProcess uses sync file objects
                            if isinstance(process, LocalPTYProcess):
                                await asyncio.to_thread(process.stdin.write, bytes_value)
                            elif isinstance(process, asyncio.subprocess.Process):
                                # Old script-based local process (deprecated)
                                process.stdin.write(bytes_value)
                            else:
                                # SSHClientProcess with encoding=None (binary mode)
                                process.stdin.write(bytes_value)
                return True

            # Handle any immediate message now that the process exists.
            try:
                initial_message = await asyncio.wait_for(websocket.receive(), timeout=0.05)
                if not await _process_message(dict(initial_message)):
                    return
            except asyncio.TimeoutError:
                pass
            except WebSocketDisconnect:
                return

            # Track session in database
            try:
                tracked_session = await state.create_or_update_session_async(
                    box_name=box.name,
                    session_name=session,
                    working_directory=normalized_directory,
                    metadata={
                        "columns": columns,
                        "rows": rows,
                        "transport": transport,
                    },
                )
                session_id = tracked_session.id
                logger.info(f"Session tracked: {session_id}")
            except Exception as exc:
                logger.warning(f"Failed to track session: {exc}")
                session_id = None

            async def reader() -> None:
                logger.info("Reader task started")
                try:
                    if process is None or process.stdout is None:
                        logger.error("Reader: process or stdout is None")
                        return
                    while True:
                        logger.debug("Reader: waiting for stdout data...")
                        # LocalPTYProcess uses sync file objects, need to read in thread
                        if isinstance(process, LocalPTYProcess):
                            data = await asyncio.to_thread(process.stdout.read, 32768)
                        else:
                            data = await process.stdout.read(32768)
                        if not data:
                            logger.info("Reader: got empty data, ending")
                            break
                        # Check if websocket is still connected before sending
                        if websocket.client_state != WebSocketState.CONNECTED:
                            logger.info("Reader: websocket no longer connected, ending")
                            break
                        logger.debug(f"Reader: got {len(data)} bytes, sending to websocket")
                        # Convert str to bytes if needed (SSH returns str, local returns bytes)
                        bytes_data = data.encode('utf-8') if isinstance(data, str) else data
                        await websocket.send_bytes(bytes_data)
                except Exception as exc:
                    logger.debug(f"Reader ended: {exc}")

            async def writer() -> None:
                logger.info("Writer task started")
                try:
                    while True:
                        logger.debug("Writer: waiting for websocket message...")
                        message = await websocket.receive()
                        if not await _process_message(dict(message)):
                            break
                except WebSocketDisconnect:
                    logger.info("Writer: websocket disconnected")
                except Exception as exc:
                    logger.error(f"Writer exception: {exc}", exc_info=True)

            async def poll_tmux_windows() -> None:
                try:
                    while True:
                        if websocket.client_state != WebSocketState.CONNECTED:
                            break
                        window_payload: list[dict[str, str | bool]] | None
                        if transport == "local":
                            window_payload = await _list_local_tmux_windows(session)
                        else:
                            if connection is not None:
                                window_payload = await _list_tmux_windows(connection, session)
                            else:
                                window_payload = None
                        if window_payload is not None:
                            if websocket.client_state != WebSocketState.CONNECTED:
                                break
                            await websocket.send_text(
                                json.dumps({"op": "windows", "windows": window_payload})
                            )
                            # Update window count in session tracking
                            if session_id:
                                try:
                                    await state.update_session_activity_async(
                                        session_id,
                                        active=True,
                                        window_count=len(window_payload),
                                    )
                                except Exception as exc:
                                    logger.debug(f"Failed to update session activity: {exc}")
                        await asyncio.sleep(2)
                except Exception as exc:
                    logger.debug(f"Tmux window polling ended: {exc}")

            async def websocket_pinger() -> None:
                """Send WebSocket pings to keep connection alive through proxies/NAT."""
                try:
                    while True:
                        await asyncio.sleep(30)  # Ping every 30 seconds
                        if websocket.client_state != WebSocketState.CONNECTED:
                            break
                        try:
                            await websocket.send_text(json.dumps({"op": "ping"}))
                            logger.debug("Sent WebSocket ping")
                        except Exception as exc:
                            logger.debug(f"WebSocket ping ended: {exc}")
                            break
                except Exception as exc:
                    logger.debug(f"WebSocket pinger task ended: {exc}")

            poller = asyncio.create_task(poll_tmux_windows())
            pinger = asyncio.create_task(websocket_pinger())
            try:
                await asyncio.gather(reader(), writer())
            finally:
                poller.cancel()
                pinger.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await poller
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await pinger

                # Mark session as inactive (detached) when WebSocket closes
                if session_id:
                    try:
                        await state.update_session_activity_async(session_id, active=False)
                        logger.info(f"Session marked inactive: {session_id}")
                    except Exception as exc:
                        logger.warning(f"Failed to mark session inactive: {exc}")
        finally:
            try:
                if process:
                    if transport == "local":
                        # Don't terminate! Just close stdin/stdout to detach gracefully
                        # The tmux session will continue running
                        if isinstance(process, LocalPTYProcess):
                            process.close()
                        else:
                            try:
                                if process.stdin is not None:
                                    process.stdin.close()
                            except Exception as exc:
                                logger.debug(f"Error closing process stdin: {exc}")
                        try:
                            # Give it a moment to flush
                            await asyncio.sleep(0.1)
                        except Exception as exc:
                            logger.debug(f"Error during flush sleep: {exc}")
                    else:
                        # SSH process cleanup
                        if process.stdin is not None:
                            process.stdin.write_eof()
                        if hasattr(process, "close"):
                            process.close()
            except Exception as exc:
                logger.debug(f"Error during process cleanup: {exc}")
            try:
                if connection is not None:
                    connection.close()
            except Exception as exc:
                logger.debug(f"Error closing SSH connection: {exc}")

            box_name = box.name if box is not None else "unknown"
            logger.info(
                f"[Connection] WebSocket closed: box={box_name}, transport={transport}, "
                f"session={session}, client={client_host}"
            )

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


def _compute_app_version() -> str:
    parts: list[str] = [__version__]
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
        )
        git_hash = result.stdout.strip()
        if git_hash:
            parts.append(f"({git_hash})")
    except Exception as exc:
        # Git hash retrieval is best-effort for version display
        logger.debug(f"Failed to get git hash for version display: {exc}")
    return " ".join(part for part in parts if part)


async def _handle_control_message(
    payload: str,
    process,
    connection: asyncssh.SSHClientConnection | None,
    session: str,
    transport: str,
) -> None:
    try:
        message = json.loads(payload)
    except json.JSONDecodeError:
        return

    operation = message.get("op")
    if operation == "resize":
        cols = int(message.get("cols", 0) or 0)
        rows = int(message.get("rows", 0) or 0)
        logger.info(f"[Resize] Received resize: {cols}x{rows} for {session} ({transport})")
        if cols > 0 and rows > 0:
            if transport == "local":
                # Use the PTY resize method which does proper ioctl + SIGWINCH
                if isinstance(process, LocalPTYProcess):
                    try:
                        process.resize(cols, rows)
                        logger.info(f"[Resize] Local PTY resized to {cols}x{rows}")
                    except Exception as exc:
                        logger.warning(f"Failed to resize local PTY: {exc}")
                else:
                    # Fallback for old script-based process (shouldn't happen)
                    try:
                        await _run_local_tmux_command(["refresh-client", "-C", f"{cols}x{rows}"])
                        await _run_local_tmux_command(["resize-window", "-t", session, "-x", str(cols), "-y", str(rows)])
                        logger.info(f"[Resize] Local tmux resized to {cols}x{rows}")
                    except Exception as exc:
                        logger.debug(f"Failed to resize local tmux: {exc}")
            else:
                # Resize SSH PTY - asyncssh handles the ioctl/SIGWINCH internally
                try:
                    process.change_terminal_size(cols, rows)
                    logger.info(f"[Resize] SSH PTY resized to {cols}x{rows}")
                except Exception as exc:
                    logger.warning(f"Failed to resize SSH PTY: {exc}")
    elif operation == "select-window":
        target = message.get("target")
        if target is not None:
            if transport == "local":
                await _run_local_tmux_command(["select-window", "-t", f"{session}:{target}"])
            elif connection is not None:
                try:
                    await connection.run(
                        f"tmux select-window -t {shlex.quote(session)}:{shlex.quote(str(target))}",
                        check=False,
                    )
                except Exception as exc:
                    logger.debug(f"Failed to select window: {exc}")
    elif operation == "send":
        data = message.get("data")
        if data:
            try:
                process.stdin.write(data.encode())
            except Exception as exc:
                logger.debug(f"Failed to send data to process: {exc}")
    elif operation == "rename-window":
        new_name = message.get("target")
        if new_name:
            if transport == "local":
                await _run_local_tmux_command(["rename-window", "-t", session, str(new_name)])
            elif connection is not None:
                try:
                    rename_command = (
                        f"tmux rename-window -t {shlex.quote(session)} {shlex.quote(str(new_name))}"
                    )
                    await connection.run(rename_command, check=False)
                except Exception as exc:
                    logger.debug(f"Failed to rename window: {exc}")


async def _list_tmux_windows(
    connection: asyncssh.SSHClientConnection, session: str
) -> list[dict[str, str | bool]] | None:
    try:
        result = await connection.run(
            "tmux list-windows -F '#{window_index} #{window_name} #{window_active}' -t "
            f"{shlex.quote(session)}",
            check=False,
        )
    except Exception as exc:
        logger.debug(f"Failed to list tmux windows for session {session}: {exc}")
        return None

    if result.returncode != 0 or result.stdout is None:
        return None

    stdout_str = result.stdout if isinstance(result.stdout, str) else result.stdout.decode('utf-8', errors='replace')
    windows: list[dict[str, str | bool]] = []
    for line in stdout_str.splitlines():
        parts = line.split(" ", 2)
        if len(parts) < 3:
            continue
        index, name, active = parts
        windows.append(
            {
                "index": index,
                "name": name,
                "active": active == "1",
            }
        )
    return windows
