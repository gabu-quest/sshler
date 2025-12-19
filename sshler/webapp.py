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
from pathlib import Path, PurePosixPath

import asyncssh
from fastapi import (
    Depends,
    FastAPI,
    File,
    Form,
    HTTPException,
    Query,
    Request,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from markdown_it import MarkdownIt

from . import __version__, state
from .api import APIDependencies, create_api_router
from .api.auth import create_auth_router
from .auth import AuthManager, PasswordHasher, PasswordValidator, PasswordPolicy
from .session import get_session_store
from .settings import get_settings as get_app_settings
from .api.helpers import (
    DEFAULT_MAX_UPLOAD_BYTES,
    IMAGE_CONTENT_TYPES,
    LOCAL_IS_WINDOWS,
    MAX_IMAGE_PREVIEW_BYTES,
    _compose_local_child_path,
    _compose_remote_child_path,
    _is_markdown_file,
    _local_create_file,
    _local_delete_file,
    _local_is_directory,
    _local_list_directory,
    _local_read_bytes,
    _local_read_text,
    _local_write_bytes,
    _local_write_text,
    _normalize_directory_path,
    _normalize_local_path,
    _read_file_bytes,
    _read_remote_text,
    _syntax_from_filename,
)
from .config import (
    AppConfig,
    Box,
    StoredBox,
    find_box,
    get_config_path,
    load_config,
    rebuild_boxes,
    save_config,
)
from .spa import mount_spa
from .ssh import (
    SSHError,
    connect,
    open_tmux,
    sftp_is_directory,
    sftp_list_directory,
    sftp_read_file,
)
from .ssh_pool import get_pool, initialize_pool, shutdown_pool
from .validation import PathValidator, ValidationError
from .rate_limit import get_rate_limiter

logger = logging.getLogger(__name__)

TEMPLATES_DIR = Path(__file__).parent / "templates"
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



def _format_file_size(size: int | None) -> str:
    """Format file size in human-readable format (B, KB, MB, GB)."""
    if size is None:
        return "-"
    if size < 1024:
        return f"{size} B"
    elif size < 1024 * 1024:
        return f"{size / 1024:.1f} KB"
    elif size < 1024 * 1024 * 1024:
        return f"{size / (1024 * 1024):.1f} MB"
    else:
        return f"{size / (1024 * 1024 * 1024):.1f} GB"


def _format_timestamp(timestamp: float | None) -> str:
    """Format Unix timestamp as relative time or absolute date."""
    if timestamp is None:
        return "-"

    import time
    from datetime import datetime

    now = time.time()
    diff = now - timestamp

    # If less than 1 minute ago
    if diff < 60:
        return "just now"
    # If less than 1 hour ago
    elif diff < 3600:
        minutes = int(diff / 60)
        return f"{minutes}m ago"
    # If less than 24 hours ago
    elif diff < 86400:
        hours = int(diff / 3600)
        return f"{hours}h ago"
    # If less than 7 days ago
    elif diff < 604800:
        days = int(diff / 86400)
        return f"{days}d ago"
    # Otherwise show absolute date
    else:
        dt = datetime.fromtimestamp(timestamp)
        return dt.strftime("%Y-%m-%d")


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
    return await asyncio.create_subprocess_exec(
        *script_command,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
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


async def _list_local_tmux_windows(session: str) -> list[dict[str, str]] | None:
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

    windows: list[dict[str, str]] = []
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
    templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
    app_version = _compute_app_version()

    application.state.settings = settings
    application.state.auth_tracker = AuthFailureTracker()
    templates.env.globals["csrf_token"] = settings.csrf_token
    templates.env.globals["format_file_size"] = _format_file_size
    templates.env.globals["format_timestamp"] = _format_timestamp

    if settings.allow_origins:
        application.add_middleware(
            CORSMiddleware,
            allow_origins=settings.allow_origins,
            allow_credentials=True,
            allow_methods=["GET", "POST", "OPTIONS"],
            allow_headers=["*"]
        )

    @application.middleware("http")
    async def _security_middleware(request: Request, call_next):
        auth_tracker: AuthFailureTracker = request.app.state.auth_tracker
        client_ip = request.client.host if request.client else "unknown"

        # Check authentication if required
        if settings.auth_manager and request.method != "OPTIONS":
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

            # Verify authentication
            auth_header = request.headers.get("authorization")
            if not auth_header or not auth_header.startswith("Basic "):
                return Response(
                    status_code=401,
                    headers={"WWW-Authenticate": 'Basic realm="sshler"'},
                )

            # Use AuthManager to verify credentials
            if not settings.auth_manager.verify_basic_auth_header(auth_header):
                # Record failed attempt
                auth_tracker.record_failure(client_ip)
                failure_count = auth_tracker.get_failure_count(client_ip)

                logging.warning(
                    f"[Security] Failed auth attempt from {client_ip} "
                    f"({failure_count}/5 failures)"
                )

                # Slow down brute force attempts
                await asyncio.sleep(2)

                return Response(
                    status_code=401,
                    headers={"WWW-Authenticate": 'Basic realm="sshler"'},
                )

            # Successful auth - reset failure counter
            auth_tracker.reset_failures(client_ip)

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
        return response

    @application.middleware("http")
    async def _rate_limit_middleware(request: Request, call_next):
        """Rate limiting middleware to prevent abuse."""
        # Skip rate limiting for static files, spa assets, and health checks
        path = request.url.path
        if path.startswith("/static/") or path.startswith("/app") or path in ["/", "/docs"]:
            return await call_next(request)

        # Get client identifier (IP address)
        client_ip = request.client.host if request.client else "unknown"

        # Different rate limits for different endpoint types
        if request.url.path.startswith("/box/") and "/upload" in request.url.path:
            # File uploads: 10 per minute
            limiter = get_rate_limiter("upload", rate=10, per=60)
        elif request.url.path.startswith("/ws/"):
            # WebSocket connections: 5 per minute
            limiter = get_rate_limiter("websocket", rate=5, per=60)
        elif request.method == "POST":
            # POST requests: 60 per minute
            limiter = get_rate_limiter("post", rate=60, per=60)
        else:
            # General requests: 100 per minute
            limiter = get_rate_limiter("general", rate=100, per=60)

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
                allowed_origins = [expected_origin] + app_settings.allowed_origins_list

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

    def _build_directory_urls(request: Request, box_name: str) -> dict[str, str]:
        def _resolve(endpoint: str, **params: str) -> str:
            try:
                return request.url_for(endpoint, **params)
            except Exception as exc:
                logger.debug(f"URL resolution failed for endpoint {endpoint}, using fallback: {exc}")
                if endpoint == "list_directory":
                    return f"/box/{box_name}/ls"
                if endpoint == "create_empty_file":
                    return f"/box/{box_name}/touch"
                if endpoint == "upload_file":
                    return f"/box/{box_name}/upload"
                if endpoint == "toggle_favorite":
                    return f"/box/{box_name}/fav"
                if endpoint == "view_file":
                    return f"/box/{box_name}/cat"
                if endpoint == "edit_file":
                    return f"/box/{box_name}/edit"
                if endpoint == "delete_file":
                    return f"/box/{box_name}/delete"
                if endpoint == "term_page":
                    return "/term"
                return "/"

        return {
            "list": _resolve("list_directory", name=box_name),
            "touch": _resolve("create_empty_file", name=box_name),
            "upload": _resolve("upload_file", name=box_name),
            "favorite": _resolve("toggle_favorite", name=box_name),
            "preview": _resolve("view_file", name=box_name),
            "edit": _resolve("edit_file", name=box_name),
            "delete": _resolve("delete_file", name=box_name),
            "term": _resolve("term_page"),
        }

    async def _render_directory_listing(
        request: Request,
        box,
        directory_path: str,
        target_id: str,
        application_config: AppConfig,
        error_override: str | None = None,
    ) -> HTMLResponse:
        if box.transport == "local":
            normalized_directory = _normalize_local_path(directory_path)
            try:
                entries = await _local_list_directory(normalized_directory)
                error_message = error_override
            except Exception as exc:
                entries = []
                error_message = error_override or f"Directory listing failed: {exc}"

            context = {
                "request": request,
                "box": box,
                "directory_path": normalized_directory,
                "entries": entries,
                "error": error_message,
                "target_id": target_id,
                "urls": _build_directory_urls(request, box.name),
            }
            return templates.TemplateResponse(request, "partials/dir_listing.html", context)

        # Validate and normalize path
        try:
            normalized_directory = PathValidator.validate_remote_path(_normalize_directory_path(directory_path))
        except ValidationError as exc:
            context = {
                "request": request,
                "box": box,
                "directory_path": directory_path,
                "entries": [],
                "error": error_override or f"Invalid path: {exc}",
                "target_id": target_id,
                "urls": _build_directory_urls(request, box.name),
            }
            return templates.TemplateResponse(request, "partials/dir_listing.html", context)

        # Use connection pool
        ssh_pool = get_pool()

        async def connect_func():
            return await connect(
                box.connect_host,
                box.user,
                box.port,
                box.keyfile,
                box.known_hosts,
                application_config.ssh_config_path,
                box.ssh_alias,
                allow_alias=settings.allow_ssh_alias,
            )

        try:
            async with ssh_pool.connection(box, connect_func) as connection:
                try:
                    entries = await sftp_list_directory(connection, normalized_directory)
                    error_message = error_override
                except Exception as exc:  # pragma: no cover - remote SFTP failures vary by host
                    entries = []
                    error_message = error_override or f"Directory listing failed: {exc}"

                context = {
                    "request": request,
                    "box": box,
                    "directory_path": normalized_directory,
                    "entries": entries,
                    "error": error_message,
                    "target_id": target_id,
                    "urls": _build_directory_urls(request, box.name),
                }
                return templates.TemplateResponse(request, "partials/dir_listing.html", context)
        except SSHError as exc:
            context = {
                "request": request,
                "box": box,
                "directory_path": normalized_directory,
                "entries": [],
                "error": error_override or f"SSH connection failed: {exc}",
                "target_id": target_id,
                "urls": _build_directory_urls(request, box.name),
            }
            return templates.TemplateResponse(request, "partials/dir_listing.html", context)

    @application.get("/")
    async def root() -> RedirectResponse:
        """Redirect the index page to the boxes list.

        English:
            Visiting ``/`` immediately sends the browser to the SPA or legacy boxes view.

        日本語:
            ルート ``/`` にアクセスした際に SPA または従来のボックス一覧へリダイレクトします。

        Returns:
            RedirectResponse: HTTP redirect to ``/boxes``.
        """

        return RedirectResponse(url="/app/" if settings.serve_spa else "/boxes")

    @application.get("/docs", response_class=HTMLResponse)
    async def docs(request: Request, lang: str = Query("en")) -> HTMLResponse:
        """Render simple usage documentation.

        English:
            Serves the built-in help page explaining basic operations.

        日本語:
            基本的な使い方を説明する内蔵ドキュメントページを表示します。
        """

        # Parse README.md and extract the appropriate language section
        readme_path = Path(__file__).parent.parent / "README.md"
        english_html = ""
        japanese_html = ""

        try:
            readme_text = readme_path.read_text(encoding="utf-8")
            # Split by the separator
            parts = readme_text.split("\n---\n")

            md = MarkdownIt()

            if len(parts) >= 2:
                english_html = md.render(parts[0].strip())
                japanese_html = md.render(parts[1].strip())
            else:
                # Fallback: use entire content for both
                english_html = md.render(readme_text)
                japanese_html = english_html
        except Exception as exc:
            # If README can't be read, use empty strings (non-critical)
            logger.debug(f"Failed to render README: {exc}")

        return templates.TemplateResponse(
            "docs.html",
            {
                "request": request,
                "app_version": app_version,
                "english_content": english_html,
                "japanese_content": japanese_html,
            },
        )

    @application.get("/boxes", response_class=HTMLResponse)
    async def boxes(
        request: Request,
        application_config: AppConfig = Depends(_get_application_config),
    ) -> HTMLResponse:
        """Render the list of configured boxes.

        English:
            Shows local and remote boxes, including metadata such as favourites
            and default directories.

        日本語:
            ローカルおよびリモートのボックス一覧と、そのお気に入りやデフォルト
            ディレクトリなどの情報を表示します。

        Args:
            request: Incoming HTTP request.
            application_config: Configuration loaded from disk.

        Returns:
            HTMLResponse: Rendered home page.
        """

        configuration_path = str(get_config_path())

        # Sort boxes: pinned first, then by last accessed (most recent first), then alphabetically
        sorted_boxes = sorted(
            application_config.boxes,
            key=lambda b: (
                not b.pinned,  # Pinned boxes first (False < True, so not pinned sorts first)
                -(b.last_accessed or 0),  # Most recent first (negative for descending order)
                b.name.lower()  # Alphabetically as final tiebreaker
            )
        )
        application_config.boxes = sorted_boxes

        context = {
            "configuration": application_config,
            "configuration_path": configuration_path,
            "app_version": app_version,
        }
        return templates.TemplateResponse(request, "index.html", context)

    @application.get("/box/{name}", response_class=HTMLResponse)
    async def box_page(
        name: str,
        request: Request,
        application_config: AppConfig = Depends(_get_application_config),
    ) -> HTMLResponse:
        """Render the detail page for a single box.

        English:
            Displays favourites and directory browser for the selected box.

        日本語:
            選択したボックスの詳細ページを表示し、お気に入りやディレクトリブラウザを
            操作できるようにします。

        Args:
            name: Box identifier from the URL.
            request: Incoming HTTP request.
            application_config: Configuration loaded from disk.

        Returns:
            HTMLResponse: Rendered page for the chosen box.

        Raises:
            HTTPException: When the requested box does not exist.
        """

        box = find_box(application_config, name)
        if not box:
            raise HTTPException(status_code=404, detail="Unknown box")

        # Update last_accessed timestamp
        import time
        stored = application_config.get_or_create_stored(name)
        stored.last_accessed = time.time()
        box.last_accessed = stored.last_accessed
        save_config(application_config)

        if getattr(box, "transport", "ssh") == "local":
            base_directory = _normalize_local_path(box.default_dir)
        else:
            base_directory = box.default_dir or f"/home/{box.user}"
        context = {"box": box, "base_directory": base_directory, "app_version": app_version}
        return templates.TemplateResponse(request, "box.html", context)

    @application.get("/box/{name}/ls", response_class=HTMLResponse)
    async def list_directory(
        name: str,
        request: Request,
        directory_path: str = Query(..., alias="path"),
        application_config: AppConfig = Depends(_get_application_config),
    ) -> HTMLResponse:
        """Render a partial listing for a directory.

        English:
            Produces the table fragment used by HTMX for both SSH and local
            transports.

        日本語:
            HTMX が利用するディレクトリ一覧の部分テンプレートを生成します。SSH と
            ローカルの両方に対応します。

        Args:
            name: Box identifier from the URL.
            request: Incoming HTTP request.
            directory_path: Absolute path to list on the remote host.
            application_config: Configuration loaded from disk.

        Returns:
            HTMLResponse: HTML fragment containing the directory table.

        Raises:
            HTTPException: When the requested box does not exist.
        """

        box = find_box(application_config, name)
        if not box:
            raise HTTPException(status_code=404, detail="Unknown box")

        target_id = request.query_params.get("target", "browser")

        return await _render_directory_listing(
            request,
            box,
            directory_path,
            target_id,
            application_config,
        )

    @application.post("/box/{name}/touch", response_class=HTMLResponse)
    async def create_empty_file(
        name: str,
        request: Request,
        directory: str = Form(...),
        filename: str = Form(...),
        target: str = Form("browser"),
        application_config: AppConfig = Depends(_get_application_config),
    ) -> HTMLResponse:
        box = find_box(application_config, name)
        if not box:
            raise HTTPException(status_code=404, detail="Unknown box")

        _require_token(request)

        directory_path = (
            _normalize_local_path(directory)
            if box.transport == "local"
            else _normalize_directory_path(directory)
        )

        if box.transport == "local":
            try:
                target_path = _compose_local_child_path(directory_path, filename)
            except ValueError as exc:
                return await _render_directory_listing(
                    request,
                    box,
                    directory_path,
                    target,
                    application_config,
                    error_override=f"Invalid filename: {exc}",
                )

            error_message = None
            success_message: str | None = None
            try:
                await _local_create_file(target_path)
                success_message = f"Created {Path(target_path).name}"
            except FileExistsError:
                error_message = f"File already exists: {Path(target_path).name}"
            except Exception as exc:
                error_message = f"Failed to create file: {exc}"

            response = await _render_directory_listing(
                request,
                box,
                directory_path,
                target,
                application_config,
                error_override=error_message,
            )
            message = error_message or success_message
            if message:
                trigger_payload = json.dumps(
                    {
                        "dir-action": {
                            "status": "error" if error_message else "success",
                            "message": message,
                        }
                    }
                )
                response.headers["HX-Trigger"] = trigger_payload
            return response

        # Validate filename and compose path
        try:
            validated_filename = PathValidator.validate_filename(filename)
            remote_path = _compose_remote_child_path(directory_path, validated_filename)
            # Additionally validate the complete path
            PathValidator.validate_remote_path(remote_path)
        except ValidationError as exc:
            return await _render_directory_listing(
                request,
                box,
                directory_path,
                target,
                application_config,
                error_override=f"Invalid filename: {exc}",
            )
        except ValueError as exc:
            return await _render_directory_listing(
                request,
                box,
                directory_path,
                target,
                application_config,
                error_override=f"Invalid filename: {exc}",
            )

        error_message = None
        success_message: str | None = None
        ssh_pool = get_pool()

        async def connect_func():
            return await connect(
                box.connect_host,
                box.user,
                box.port,
                box.keyfile,
                box.known_hosts,
                application_config.ssh_config_path,
                box.ssh_alias,
                allow_alias=settings.allow_ssh_alias,
            )

        try:
            async with ssh_pool.connection(box, connect_func) as connection:
                try:
                    sftp_client = await connection.start_sftp_client()
                    try:
                        try:
                            await sftp_client.stat(remote_path)
                            error_message = (
                                f"File already exists: {PurePosixPath(remote_path).name}"
                            )
                        except (FileNotFoundError, OSError):
                            # File doesn't exist, we can create it
                            async with await sftp_client.open(
                                remote_path, "w", encoding="utf-8"
                            ) as remote_file:
                                await remote_file.write("")
                            success_message = f"Created {PurePosixPath(remote_path).name}"
                    except Exception as exc:  # pragma: no cover - remote host behavior varies
                        error_message = f"Failed to create file: {exc}"
                    finally:
                        try:
                            await sftp_client.exit()  # type: ignore[func-returns-value]  # type: ignore[func-returns-value]
                        except Exception as exc:
                            logger.debug(f"Error closing SFTP client: {exc}")
                except Exception as exc:  # pragma: no cover - depends on remote server
                    error_message = f"SFTP session failed: {exc}"
        except SSHError as exc:
            error_message = f"SSH connection failed: {exc}"

        response = await _render_directory_listing(
            request,
            box,
            directory_path,
            target,
            application_config,
            error_override=error_message,
        )
        message = error_message or success_message
        if message:
            trigger_payload = json.dumps(
                {
                    "dir-action": {
                        "status": "error" if error_message else "success",
                        "message": message,
                    }
                }
            )
            response.headers["HX-Trigger"] = trigger_payload
        return response

    @application.post("/box/{name}/upload", response_class=HTMLResponse)
    async def upload_file(
        name: str,
        request: Request,
        directory: str = Form(...),
        target: str = Form("browser"),
        file: UploadFile = File(...),
        application_config: AppConfig = Depends(_get_application_config),
    ) -> HTMLResponse:
        box = find_box(application_config, name)
        if not box:
            raise HTTPException(status_code=404, detail="Unknown box")

        _require_token(request)

        directory_path = (
            _normalize_local_path(directory)
            if box.transport == "local"
            else _normalize_directory_path(directory)
        )
        original_name = file.filename or ""
        candidate_name = PurePosixPath(original_name).name

        if box.transport == "local":
            try:
                target_path = _compose_local_child_path(directory_path, candidate_name)
            except ValueError as exc:
                await file.close()
                return await _render_directory_listing(
                    request,
                    box,
                    directory_path,
                    target,
                    application_config,
                    error_override=f"Invalid filename: {exc}",
                )

            error_message = None
            success_message: str | None = None
            try:
                contents = await file.read()
            finally:
                await file.close()

            if not candidate_name:
                error_message = "Select a file to upload"
            elif len(contents) > settings.max_upload_bytes:
                limit_kb = settings.max_upload_bytes // 1024
                error_message = f"Upload exceeds {limit_kb} KB limit"

            if error_message is None:
                try:
                    if Path(target_path).exists():
                        error_message = f"File already exists: {Path(target_path).name}"
                    else:
                        await _local_write_bytes(target_path, contents)
                        success_message = f"Uploaded {Path(target_path).name}"
                except Exception as exc:
                    error_message = f"Failed to upload file: {exc}"

            response = await _render_directory_listing(
                request,
                box,
                directory_path,
                target,
                application_config,
                error_override=error_message,
            )
            message = error_message or success_message
            if message:
                trigger_payload = json.dumps(
                    {
                        "dir-action": {
                            "status": "error" if error_message else "success",
                            "message": message,
                        }
                    }
                )
                response.headers["HX-Trigger"] = trigger_payload
            return response

        # Validate filename and compose path
        try:
            validated_filename = PathValidator.validate_filename(candidate_name)
            remote_path = _compose_remote_child_path(directory_path, validated_filename)
            PathValidator.validate_remote_path(remote_path)
        except ValidationError as exc:
            await file.close()
            return await _render_directory_listing(
                request,
                box,
                directory_path,
                target,
                application_config,
                error_override=f"Invalid filename: {exc}",
            )
        except ValueError as exc:
            await file.close()
            return await _render_directory_listing(
                request,
                box,
                directory_path,
                target,
                application_config,
                error_override=f"Invalid filename: {exc}",
            )

        error_message = None
        success_message: str | None = None
        try:
            contents = await file.read()
        finally:
            await file.close()

        if not candidate_name:
            error_message = "Select a file to upload"
        elif len(contents) > settings.max_upload_bytes:
            limit_kb = settings.max_upload_bytes // 1024
            error_message = f"Upload exceeds {limit_kb} KB limit"

        # Use connection pool
        ssh_pool = get_pool()

        async def connect_func():
            return await connect(
                box.connect_host,
                box.user,
                box.port,
                box.keyfile,
                box.known_hosts,
                application_config.ssh_config_path,
                box.ssh_alias,
                allow_alias=settings.allow_ssh_alias,
            )

        if error_message is None:
            try:
                async with ssh_pool.connection(box, connect_func) as connection:
                    sftp_client = None
                    try:
                        sftp_client = await connection.start_sftp_client()
                    except Exception as exc:  # pragma: no cover - depends on remote server
                        error_message = f"SFTP session failed: {exc}"
                    else:
                        try:
                            try:
                                await sftp_client.stat(remote_path)
                            except (FileNotFoundError, OSError):
                                # File doesn't exist - this is expected for new uploads
                                pass
                            else:
                                error_message = (
                                    f"File already exists: {PurePosixPath(remote_path).name}"
                                )
                            if error_message is None:
                                async with await sftp_client.open(remote_path, "wb") as remote_file:
                                    await remote_file.write(contents)
                                success_message = f"Uploaded {PurePosixPath(remote_path).name}"
                        except Exception as exc:  # pragma: no cover - remote SFTP failures vary
                            error_message = f"Failed to upload file: {exc}"
                        finally:
                            if sftp_client is not None:
                                try:
                                    await sftp_client.exit()  # type: ignore[func-returns-value]  # type: ignore[func-returns-value]
                                except Exception as exc:
                                    logger.debug(f"Error closing SFTP client: {exc}")
            except SSHError as exc:
                error_message = f"SSH connection failed: {exc}"

        response = await _render_directory_listing(
            request,
            box,
            directory_path,
            target,
            application_config,
            error_override=error_message,
        )
        message = error_message or success_message
        if message:
            trigger_payload = json.dumps(
                {
                    "dir-action": {
                        "status": "error" if error_message else "success",
                        "message": message,
                    }
                }
            )
            response.headers["HX-Trigger"] = trigger_payload
        return response

    @application.post("/box/{name}/delete", response_class=HTMLResponse)
    async def delete_file(
        name: str,
        request: Request,
        file_path: str = Form(..., alias="path"),
        directory: str = Form(...),
        target: str = Form("browser"),
        application_config: AppConfig = Depends(_get_application_config),
    ) -> HTMLResponse:
        """Delete a file from the box."""
        box = find_box(application_config, name)
        if not box:
            raise HTTPException(status_code=404, detail="Unknown box")

        _require_token(request)

        directory_path = (
            _normalize_local_path(directory)
            if box.transport == "local"
            else _normalize_directory_path(directory)
        )

        if box.transport == "local":
            normalized_path = _normalize_local_path(file_path)
            error_message = None
            success_message: str | None = None

            try:
                await _local_delete_file(normalized_path)
                success_message = f"Deleted {Path(normalized_path).name}"
            except FileNotFoundError:
                error_message = f"File not found: {Path(normalized_path).name}"
            except Exception as exc:
                error_message = f"Failed to delete file: {exc}"

            response = await _render_directory_listing(
                request,
                box,
                directory_path,
                target,
                application_config,
                error_override=error_message,
            )
            message = error_message or success_message
            if message:
                trigger_payload = json.dumps(
                    {
                        "dir-action": {
                            "status": "error" if error_message else "success",
                            "message": message,
                        }
                    }
                )
                response.headers["HX-Trigger"] = trigger_payload
            return response

        # Validate and normalize remote path
        try:
            validated_path = PathValidator.validate_remote_path(file_path)
        except ValidationError as exc:
            response = await _render_directory_listing(
                request,
                box,
                directory_path,
                target,
                application_config,
                error_override=f"Invalid path: {exc}",
            )
            trigger_payload = json.dumps(
                {
                    "dir-action": {
                        "status": "error",
                        "message": f"Invalid path: {exc}",
                    }
                }
            )
            response.headers["HX-Trigger"] = trigger_payload
            return response

        # Remote file deletion
        error_message = None
        success_message: str | None = None

        # Use connection pool
        ssh_pool = get_pool()

        async def connect_func():
            return await connect(
                box.connect_host,
                box.user,
                box.port,
                box.keyfile,
                box.known_hosts,
                application_config.ssh_config_path,
                box.ssh_alias,
                allow_alias=settings.allow_ssh_alias,
            )

        try:
            async with ssh_pool.connection(box, connect_func) as connection:
                sftp_client = None
                try:
                    sftp_client = await connection.start_sftp_client()
                except Exception as exc:
                    error_message = f"SFTP session failed: {exc}"
                else:
                    try:
                        await sftp_client.remove(validated_path)
                        success_message = f"Deleted {PurePosixPath(validated_path).name}"
                    except FileNotFoundError:
                        error_message = f"File not found: {PurePosixPath(validated_path).name}"
                    except Exception as exc:
                        error_message = f"Failed to delete file: {exc}"
                    finally:
                        if sftp_client is not None:
                            try:
                                await sftp_client.exit()  # type: ignore[func-returns-value]  # type: ignore[func-returns-value]
                            except Exception as exc:
                                logger.debug(f"Error closing SFTP client: {exc}")
        except SSHError as exc:
            error_message = f"SSH connection failed: {exc}"

        response = await _render_directory_listing(
            request,
            box,
            directory_path,
            target,
            application_config,
            error_override=error_message,
        )
        message = error_message or success_message
        if message:
            trigger_payload = json.dumps(
                {
                    "dir-action": {
                        "status": "error" if error_message else "success",
                        "message": message,
                    }
                }
            )
            response.headers["HX-Trigger"] = trigger_payload
        return response

    @application.post("/box/{name}/rename", response_class=HTMLResponse)
    async def rename_file(
        name: str,
        request: Request,
        file_path: str = Form(..., alias="path"),
        new_name: str = Form(...),
        directory: str = Form(...),
        target: str = Form("browser"),
        application_config: AppConfig = Depends(_get_application_config),
    ) -> HTMLResponse:
        """Rename a file or directory."""
        box = find_box(application_config, name)
        if not box:
            raise HTTPException(status_code=404, detail="Unknown box")

        _require_token(request)

        directory_path = (
            _normalize_local_path(directory)
            if box.transport == "local"
            else _normalize_directory_path(directory)
        )

        # Validate new name
        try:
            validated_new_name = PathValidator.validate_filename(new_name)
        except ValidationError as exc:
            return await _render_directory_listing(
                request, box, directory_path, target, application_config,
                error_override=f"Invalid filename: {exc}"
            )

        if box.transport == "local":
            old_path = _normalize_local_path(file_path)
            new_path = str(Path(old_path).parent / validated_new_name)
            error_message = None
            success_message = None

            try:
                Path(old_path).rename(new_path)
                success_message = f"Renamed to {validated_new_name}"
            except FileNotFoundError:
                error_message = f"File not found: {Path(old_path).name}"
            except Exception as exc:
                error_message = f"Failed to rename: {exc}"

            response = await _render_directory_listing(
                request, box, directory_path, target, application_config,
                error_override=error_message
            )
            message = error_message or success_message
            if message:
                trigger_payload = json.dumps({
                    "dir-action": {
                        "status": "error" if error_message else "success",
                        "message": message,
                    }
                })
                response.headers["HX-Trigger"] = trigger_payload
            return response

        # Remote rename
        try:
            validated_old_path = PathValidator.validate_remote_path(file_path)
            old_dir = str(PurePosixPath(validated_old_path).parent)
            new_path = f"{old_dir}/{validated_new_name}" if old_dir != "." else validated_new_name
            PathValidator.validate_remote_path(new_path)
        except ValidationError as exc:
            return await _render_directory_listing(
                request, box, directory_path, target, application_config,
                error_override=f"Invalid path: {exc}"
            )

        error_message = None
        success_message = None

        ssh_pool = get_pool()

        async def connect_func():
            return await connect(
                box.connect_host, box.user, box.port, box.keyfile,
                box.known_hosts, application_config.ssh_config_path,
                box.ssh_alias, allow_alias=settings.allow_ssh_alias,
            )

        try:
            async with ssh_pool.connection(box, connect_func) as connection:
                sftp_client = None
                try:
                    sftp_client = await connection.start_sftp_client()
                    await sftp_client.rename(validated_old_path, new_path)
                    success_message = f"Renamed to {validated_new_name}"
                except FileNotFoundError:
                    error_message = f"File not found: {PurePosixPath(validated_old_path).name}"
                except Exception as exc:
                    error_message = f"Failed to rename: {exc}"
                finally:
                    if sftp_client:
                        try:
                            await sftp_client.exit()  # type: ignore[func-returns-value]  # type: ignore[func-returns-value]
                        except Exception as exc:
                            logger.debug(f"Error closing SFTP client: {exc}")
        except SSHError as exc:
            error_message = f"SSH connection failed: {exc}"

        response = await _render_directory_listing(
            request, box, directory_path, target, application_config,
            error_override=error_message
        )
        message = error_message or success_message
        if message:
            trigger_payload = json.dumps({
                "dir-action": {
                    "status": "error" if error_message else "success",
                    "message": message,
                }
            })
            response.headers["HX-Trigger"] = trigger_payload
        return response

    @application.post("/box/{name}/copy", response_class=HTMLResponse)
    async def copy_file(
        name: str,
        request: Request,
        file_path: str = Form(..., alias="path"),
        destination: str = Form(None),  # Optional destination path
        directory: str = Form(...),
        target: str = Form("browser"),
        application_config: AppConfig = Depends(_get_application_config),
    ) -> HTMLResponse:
        """Copy a file or directory."""
        box = find_box(application_config, name)
        if not box:
            raise HTTPException(status_code=404, detail="Unknown box")

        _require_token(request)

        directory_path = (
            _normalize_local_path(directory)
            if box.transport == "local"
            else _normalize_directory_path(directory)
        )

        if box.transport == "local":
            import shutil

            source_path = _normalize_local_path(file_path)

            # Generate destination path if not provided
            if not destination:
                source_path_obj = Path(source_path)
                base_name = source_path_obj.stem
                suffix = source_path_obj.suffix
                counter = 1
                dest_path = source_path_obj.parent / f"{base_name}_copy{suffix}"

                # Find an available name
                while dest_path.exists():
                    dest_path = source_path_obj.parent / f"{base_name}_copy{counter}{suffix}"
                    counter += 1
            else:
                dest_path = Path(_normalize_local_path(destination))

            error_message = None
            success_message = None

            try:
                if Path(source_path).is_dir():
                    shutil.copytree(source_path, dest_path)
                    success_message = f"Copied directory to {dest_path.name}"
                else:
                    shutil.copy2(source_path, dest_path)
                    success_message = f"Copied file to {dest_path.name}"
            except FileNotFoundError:
                error_message = f"File not found: {Path(source_path).name}"
            except Exception as exc:
                error_message = f"Failed to copy: {exc}"

            response = await _render_directory_listing(
                request, box, directory_path, target, application_config,
                error_override=error_message
            )
            message = error_message or success_message
            if message:
                trigger_payload = json.dumps({
                    "dir-action": {
                        "status": "error" if error_message else "success",
                        "message": message,
                    }
                })
                response.headers["HX-Trigger"] = trigger_payload
            return response

        # Remote copy
        try:
            validated_source_path = PathValidator.validate_remote_path(file_path)

            # Generate destination path if not provided
            if not destination:
                source_path_obj = PurePosixPath(validated_source_path)
                base_name = source_path_obj.stem
                suffix = source_path_obj.suffix
                parent_dir = str(source_path_obj.parent)
                dest_path = f"{parent_dir}/{base_name}_copy{suffix}" if parent_dir != "." else f"{base_name}_copy{suffix}"
            else:
                dest_path = destination
                PathValidator.validate_remote_path(dest_path)
        except ValidationError as exc:
            return await _render_directory_listing(
                request, box, directory_path, target, application_config,
                error_override=f"Invalid path: {exc}"
            )

        error_message = None
        success_message = None

        ssh_pool = get_pool()

        async def connect_func():
            return await connect(
                box.connect_host, box.user, box.port, box.keyfile,
                box.known_hosts, application_config.ssh_config_path,
                box.ssh_alias, allow_alias=settings.allow_ssh_alias,
            )

        try:
            async with ssh_pool.connection(box, connect_func) as connection:
                sftp_client = None
                try:
                    sftp_client = await connection.start_sftp_client()

                    # Check if source is a directory
                    attrs = await sftp_client.stat(validated_source_path)
                    is_dir = stat.S_ISDIR(attrs.permissions)

                    if is_dir:
                        # For directories, use shell commands for recursive copy
                        result = await connection.run(
                            f"cp -r {shlex.quote(validated_source_path)} {shlex.quote(dest_path)}",
                            check=False
                        )
                        if result.exit_status != 0:
                            error_message = f"Failed to copy directory: {result.stderr}"
                        else:
                            success_message = f"Copied directory to {PurePosixPath(dest_path).name}"
                    else:
                        # For files, use SFTP
                        # Read the source file
                        async with sftp_client.open(validated_source_path, 'rb') as src:
                            content = await src.read()

                        # Write to destination
                        async with sftp_client.open(dest_path, 'wb') as dst:
                            await dst.write(content)

                        # Copy permissions
                        await sftp_client.chmod(dest_path, attrs.permissions)
                        success_message = f"Copied file to {PurePosixPath(dest_path).name}"

                except FileNotFoundError:
                    error_message = f"File not found: {PurePosixPath(validated_source_path).name}"
                except Exception as exc:
                    error_message = f"Failed to copy: {exc}"
                finally:
                    if sftp_client:
                        try:
                            await sftp_client.exit()  # type: ignore[func-returns-value]  # type: ignore[func-returns-value]
                        except Exception as exc:
                            logger.debug(f"Error closing SFTP client: {exc}")
        except SSHError as exc:
            error_message = f"SSH connection failed: {exc}"

        response = await _render_directory_listing(
            request, box, directory_path, target, application_config,
            error_override=error_message
        )
        message = error_message or success_message
        if message:
            trigger_payload = json.dumps({
                "dir-action": {
                    "status": "error" if error_message else "success",
                    "message": message,
                }
            })
            response.headers["HX-Trigger"] = trigger_payload
        return response

    @application.post("/box/{name}/move", response_class=HTMLResponse)
    async def move_file(
        name: str,
        request: Request,
        file_path: str = Form(..., alias="path"),
        destination_dir: str = Form(...),  # Destination directory
        directory: str = Form(...),  # Current directory
        target: str = Form("browser"),
        application_config: AppConfig = Depends(_get_application_config),
    ) -> HTMLResponse:
        """Move a file or directory to a different location."""
        box = find_box(application_config, name)
        if not box:
            raise HTTPException(status_code=404, detail="Unknown box")

        _require_token(request)

        directory_path = (
            _normalize_local_path(directory)
            if box.transport == "local"
            else _normalize_directory_path(directory)
        )

        if box.transport == "local":
            import shutil

            source_path = _normalize_local_path(file_path)
            dest_dir = _normalize_local_path(destination_dir)

            # Generate destination path
            source_path_obj = Path(source_path)
            dest_path = Path(dest_dir) / source_path_obj.name

            # Check for name collision
            if dest_path.exists():
                error_message = f"File already exists in destination: {dest_path.name}"
                response = await _render_directory_listing(
                    request, box, directory_path, target, application_config,
                    error_override=error_message
                )
                trigger_payload = json.dumps({
                    "dir-action": {
                        "status": "error",
                        "message": error_message,
                    }
                })
                response.headers["HX-Trigger"] = trigger_payload
                return response

            error_message = None
            success_message = None

            try:
                shutil.move(source_path, dest_path)
                success_message = f"Moved {source_path_obj.name} to {Path(dest_dir).name}"
            except FileNotFoundError:
                error_message = f"File not found: {source_path_obj.name}"
            except Exception as exc:
                error_message = f"Failed to move: {exc}"

            response = await _render_directory_listing(
                request, box, directory_path, target, application_config,
                error_override=error_message
            )
            message = error_message or success_message
            if message:
                trigger_payload = json.dumps({
                    "dir-action": {
                        "status": "error" if error_message else "success",
                        "message": message,
                    }
                })
                response.headers["HX-Trigger"] = trigger_payload
            return response

        # Remote move
        try:
            validated_source_path = PathValidator.validate_remote_path(file_path)
            validated_dest_dir = PathValidator.validate_remote_path(destination_dir)

            source_path_obj = PurePosixPath(validated_source_path)
            dest_path = f"{validated_dest_dir}/{source_path_obj.name}"
            PathValidator.validate_remote_path(dest_path)
        except ValidationError as exc:
            return await _render_directory_listing(
                request, box, directory_path, target, application_config,
                error_override=f"Invalid path: {exc}"
            )

        error_message = None
        success_message = None

        ssh_pool = get_pool()

        async def connect_func():
            return await connect(
                box.connect_host, box.user, box.port, box.keyfile,
                box.known_hosts, application_config.ssh_config_path,
                box.ssh_alias, allow_alias=settings.allow_ssh_alias,
            )

        try:
            async with ssh_pool.connection(box, connect_func) as connection:
                sftp_client = None
                try:
                    sftp_client = await connection.start_sftp_client()

                    # Check if destination already exists
                    try:
                        await sftp_client.stat(dest_path)
                        error_message = f"File already exists in destination: {source_path_obj.name}"
                    except FileNotFoundError:
                        # Destination doesn't exist, proceed with move
                        # Use shell command for move (works for both files and directories)
                        result = await connection.run(
                            f"mv {shlex.quote(validated_source_path)} {shlex.quote(dest_path)}",
                            check=False
                        )
                        if result.exit_status != 0:
                            error_message = f"Failed to move: {result.stderr}"
                        else:
                            success_message = f"Moved {source_path_obj.name} to {PurePosixPath(validated_dest_dir).name}"

                except Exception as exc:
                    if not error_message:
                        error_message = f"Failed to move: {exc}"
                finally:
                    if sftp_client:
                        try:
                            await sftp_client.exit()  # type: ignore[func-returns-value]  # type: ignore[func-returns-value]
                        except Exception as exc:
                            logger.debug(f"Error closing SFTP client: {exc}")
        except SSHError as exc:
            error_message = f"SSH connection failed: {exc}"

        response = await _render_directory_listing(
            request, box, directory_path, target, application_config,
            error_override=error_message
        )
        message = error_message or success_message
        if message:
            trigger_payload = json.dumps({
                "dir-action": {
                    "status": "error" if error_message else "success",
                    "message": message,
                }
            })
            response.headers["HX-Trigger"] = trigger_payload
        return response

    @application.get("/box/{name}/cat", response_class=HTMLResponse)
    async def view_file(
        name: str,
        request: Request,
        file_path: str = Query(..., alias="path"),
        application_config: AppConfig = Depends(_get_application_config),
    ) -> HTMLResponse:
        """Render a read-only preview of a remote or local file.

        English:
            Loads text content (or inline images) and renders the preview page.

        日本語:
            テキストまたは画像を読み込み、プレビュー用ページを表示します。
        """

        box = find_box(application_config, name)
        if not box:
            raise HTTPException(status_code=404, detail="Unknown box")

        if box.transport == "local":
            normalized_path = _normalize_local_path(file_path)
            suffix = Path(normalized_path).suffix.lower()
            image_mime = IMAGE_CONTENT_TYPES.get(suffix)
            image_data: str | None = None
            image_too_large = False
            if image_mime:
                try:
                    image_bytes, too_large = await _local_read_bytes(
                        normalized_path, MAX_IMAGE_PREVIEW_BYTES
                    )
                except Exception as exc:
                    raise HTTPException(status_code=500, detail=str(exc)) from exc
                if too_large:
                    image_too_large = True
                else:
                    image_data = base64.b64encode(image_bytes).decode("ascii")

            text_content: str | None = None
            if not image_mime or image_too_large:
                try:
                    text_content = await _local_read_text(
                        normalized_path, settings.max_upload_bytes
                    )
                except Exception as exc:
                    raise HTTPException(status_code=500, detail=str(exc)) from exc

            # Get the directory containing the file
            parent_dir = str(Path(normalized_path).parent)

            # Check if this is a markdown file and render it
            is_markdown = _is_markdown_file(normalized_path)
            markdown_html = None
            if is_markdown and text_content:
                markdown_html = _render_markdown(text_content)

            context = {
                "box": box,
                "path": normalized_path,
                "parent_directory": parent_dir,
                "content": text_content or "",
                "syntax_class": _syntax_from_filename(normalized_path),
                "app_version": app_version,
                "image_data": image_data,
                "image_mime": image_mime,
                "image_too_large": image_too_large,
                "image_limit_kb": MAX_IMAGE_PREVIEW_BYTES // 1024,
                "is_markdown": is_markdown,
                "markdown_html": markdown_html,
            }
            return templates.TemplateResponse(request, "file_view.html", context)

        # Validate and normalize remote path
        try:
            validated_path = PathValidator.validate_remote_path(file_path)
        except ValidationError as exc:
            raise HTTPException(status_code=400, detail=f"Invalid path: {exc}")

        # Use connection pool
        ssh_pool = get_pool()

        async def connect_func():
            return await connect(
                box.connect_host,
                box.user,
                box.port,
                box.keyfile,
                box.known_hosts,
                application_config.ssh_config_path,
                box.ssh_alias,
                allow_alias=settings.allow_ssh_alias,
            )

        try:
            async with ssh_pool.connection(box, connect_func) as connection:
                suffix = Path(validated_path).suffix.lower()
                image_mime = IMAGE_CONTENT_TYPES.get(suffix)
                image_data: str | None = None
                image_too_large = False
                if image_mime:
                    try:
                        image_bytes, too_large = await _read_file_bytes(
                            connection, validated_path, MAX_IMAGE_PREVIEW_BYTES
                        )
                    except Exception as exc:
                        raise HTTPException(status_code=500, detail=str(exc)) from exc
                    if too_large:
                        image_too_large = True
                    else:
                        image_data = base64.b64encode(image_bytes).decode("ascii")

                text_content: str | None = None
                if not image_mime or image_too_large:
                    try:
                        text_content = await sftp_read_file(
                            connection, validated_path, max_bytes=settings.max_upload_bytes
                        )
                    except TypeError as exc:
                        if "max_bytes" not in str(exc):
                            raise HTTPException(status_code=500, detail=str(exc)) from exc
                        text_content = await sftp_read_file(connection, validated_path)
                    except Exception as exc:
                        raise HTTPException(status_code=500, detail=str(exc)) from exc

                # Get the directory containing the file
                parent_dir = str(PurePosixPath(validated_path).parent)

                # Check if this is a markdown file and render it
                is_markdown = _is_markdown_file(validated_path)
                markdown_html = None
                if is_markdown and text_content:
                    markdown_html = _render_markdown(text_content)

                context = {
                    "box": box,
                    "path": validated_path,
                    "parent_directory": parent_dir,
                    "content": text_content or "",
                    "syntax_class": _syntax_from_filename(validated_path),
                    "app_version": app_version,
                    "image_data": image_data,
                    "image_mime": image_mime,
                    "image_too_large": image_too_large,
                    "image_limit_kb": MAX_IMAGE_PREVIEW_BYTES // 1024,
                    "is_markdown": is_markdown,
                    "markdown_html": markdown_html,
                }
                return templates.TemplateResponse(request, "file_view.html", context)
        except SSHError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @application.api_route(
        "/box/{name}/edit",
        methods=["GET", "POST"],
        response_class=HTMLResponse,
        response_model=None,
    )
    async def edit_file(
        name: str,
        request: Request,
        application_config: AppConfig = Depends(_get_application_config),
    ) -> HTMLResponse | RedirectResponse:
        file_path = request.query_params.get("path")
        if not file_path:
            raise HTTPException(status_code=400, detail="path required")

        box = find_box(application_config, name)
        if not box:
            raise HTTPException(status_code=404, detail="Unknown box")

        if box.transport == "local":
            normalized_path = _normalize_local_path(file_path)

            if request.method == "GET":
                try:
                    content = await _local_read_text(normalized_path, 262144)
                except Exception as exc:
                    raise HTTPException(status_code=500, detail=str(exc)) from exc
                context = {
                    "box": box,
                    "path": normalized_path,
                    "content": content,
                    "app_version": app_version,
                }
                return templates.TemplateResponse(request, "file_edit.html", context)

            _require_token(request)
            payload = await request.json()
            content = payload.get("content")
            if content is None:
                raise HTTPException(status_code=400, detail="Missing content")
            if len(content.encode("utf-8")) > 262144:
                raise HTTPException(status_code=400, detail="File exceeds 256KB editing limit")

            try:
                await _local_write_text(normalized_path, content)
            except Exception as exc:
                raise HTTPException(status_code=500, detail=str(exc)) from exc

            context = {
                "box": box,
                "path": normalized_path,
                "content": content,
                "app_version": app_version,
            }
            return templates.TemplateResponse(request, "file_edit.html", context)

        # Validate and normalize remote path
        try:
            validated_path = PathValidator.validate_remote_path(file_path)
        except ValidationError as exc:
            raise HTTPException(status_code=400, detail=f"Invalid path: {exc}")

        # Use connection pool
        ssh_pool = get_pool()

        async def connect_func():
            return await connect(
                box.connect_host,
                box.user,
                box.port,
                box.keyfile,
                box.known_hosts,
                application_config.ssh_config_path,
                box.ssh_alias,
                allow_alias=settings.allow_ssh_alias,
            )

        try:
            async with ssh_pool.connection(box, connect_func) as connection:
                if request.method == "GET":
                    try:
                        content = await _read_remote_text(connection, validated_path, 262144)
                    except Exception as exc:
                        raise HTTPException(status_code=500, detail=str(exc)) from exc
                    context = {
                        "box": box,
                        "path": validated_path,
                        "content": content,
                        "app_version": app_version,
                    }
                    return templates.TemplateResponse(request, "file_edit.html", context)

                _require_token(request)
                payload = await request.json()
                content = payload.get("content")
                if content is None:
                    raise HTTPException(status_code=400, detail="Missing content")
                if len(content.encode("utf-8")) > 262144:
                    raise HTTPException(status_code=400, detail="File exceeds 256KB editing limit")

                sftp_client = await connection.start_sftp_client()
                try:
                    async with await sftp_client.open(validated_path, "w", encoding="utf-8") as remote_file:
                        await remote_file.write(content)
                finally:
                    try:
                        await sftp_client.exit()  # type: ignore[func-returns-value]
                    except Exception as exc:
                        logger.debug(f"Error closing SFTP client: {exc}")
                return templates.TemplateResponse(
                    request,
                    "file_edit.html",
                    {
                        "box": box,
                        "path": validated_path,
                        "content": content,
                        "app_version": app_version,
                    },
                )
        except SSHError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @application.post("/box/{name}/fav", response_class=PlainTextResponse)
    async def toggle_favorite(
        name: str,
        request: Request,
        directory_path: str = Query(..., alias="path"),
        application_config: AppConfig = Depends(_get_application_config),
    ) -> str:
        """Toggle a favorite directory for a box.

        English:
            Adds or removes ``directory_path`` from the stored favourites for
            the given box.

        日本語:
            指定されたディレクトリをお気に入りに追加または削除します。

        Args:
            name: Box identifier from the URL.
            directory_path: Remote directory to toggle as favorite.
            application_config: Configuration loaded from disk.

        Returns:
            str: Literal ``"ok"`` acknowledging persistence.

        Raises:
            HTTPException: When the requested box does not exist.
        """

        _require_token(request)

        box = find_box(application_config, name)
        if not box:
            raise HTTPException(status_code=404, detail="Unknown box")

        normalized_path = (
            _normalize_local_path(directory_path)
            if getattr(box, "transport", "ssh") == "local"
            else _normalize_directory_path(directory_path)
        )

        await state.toggle_favorite_async(name, normalized_path)
        box.favorites = await state.list_favorites_async(name)
        return "ok"

    @application.get("/boxes/new", response_class=HTMLResponse)
    async def new_box_form(
        request: Request,
        application_config: AppConfig = Depends(_get_application_config),
    ) -> HTMLResponse:
        """Render the form to add a custom box.

        English:
            Presents the HTML form used to create additional stored boxes.

        日本語:
            追加のボックスを作成するためのフォームを表示します。
        """

        context = {
            "configuration_path": str(get_config_path()),
            "existing_names": [box.name for box in application_config.boxes],
            "app_version": app_version,
        }
        return templates.TemplateResponse(request, "new_box.html", context)

    @application.post("/boxes/new")
    async def create_box(
        request: Request,
        name: str = Form(...),
        host: str = Form(""),
        user: str = Form(""),
        port: int = Form(22),
        keyfile: str = Form(""),
        ssh_alias: str = Form(""),
        default_dir: str = Form(""),
        favorites: str = Form(""),
        known_hosts: str = Form(""),
        agent: bool = Form(False),
    ) -> RedirectResponse:
        """Persist a new custom box definition supplied by the user."""

        _require_token(request)

        cleaned_name = name.strip()
        if not cleaned_name:
            raise HTTPException(status_code=400, detail="Box name is required")

        favorites_list = [line.strip() for line in favorites.splitlines() if line.strip()]

        new_box = StoredBox(
            name=cleaned_name,
            host=host.strip() or None,
            user=user.strip() or None,
            port=port or None,
            keyfile=keyfile.strip() or None,
            agent=agent,
            favorites=[],
            default_dir=default_dir.strip() or None,
            known_hosts=known_hosts.strip() or None,
            ssh_alias=ssh_alias.strip() or None,
        )

        application_config = load_config()
        application_config.stored[new_box.name] = new_box
        rebuild_boxes(application_config)
        save_config(application_config)
        await state.replace_favorites_async(new_box.name, favorites_list)
        return RedirectResponse(url="/boxes", status_code=303)

    @application.post("/box/{name}/refresh", response_class=PlainTextResponse)
    async def refresh_box(
        name: str,
        request: Request,
        application_config: AppConfig = Depends(_get_application_config),
    ) -> str:
        """Remove connection overrides so SSH config values apply.

        English:
            Clears stored overrides for the chosen box and rebuilds the merged
            configuration.

        日本語:
            対象ボックスの上書き設定を削除し、統合された設定を再構築します。
        """

        _require_token(request)

        await state.replace_favorites_async(name, [])

        stored_override = application_config.stored.get(name)
        if stored_override is not None:
            stored_override.host = None
            stored_override.user = None
            stored_override.port = None
            stored_override.keyfile = None
            stored_override.known_hosts = None
            stored_override.ssh_alias = None

            if not any(
                [
                    stored_override.host,
                    stored_override.user,
                    stored_override.port,
                    stored_override.keyfile,
                    stored_override.known_hosts,
                    stored_override.ssh_alias,
                    stored_override.default_dir,
                    stored_override.agent,
                ]
            ):
                application_config.stored.pop(name, None)

            save_config(application_config)

        rebuild_boxes(application_config)
        return "ok"

    @application.post("/box/{name}/pin", response_class=PlainTextResponse)
    async def toggle_pin_box(
        name: str,
        request: Request,
        application_config: AppConfig = Depends(_get_application_config),
    ) -> str:
        """Toggle the pinned status of a box.

        English:
            Pins or unpins a box to keep it at the top of the boxes list.

        日本語:
            ボックスをピン留めまたはピン留め解除します。
        """

        _require_token(request)

        box = find_box(application_config, name)
        if not box:
            raise HTTPException(status_code=404, detail="Unknown box")

        # Get or create stored override
        stored = application_config.get_or_create_stored(name)
        stored.pinned = not stored.pinned

        save_config(application_config)
        rebuild_boxes(application_config)
        return "ok"

    @application.get("/box/{name}/status")
    async def get_box_status(
        name: str,
        application_config: AppConfig = Depends(_get_application_config),
    ) -> dict[str, object]:
        """Get the connection status of a box.

        English:
            Performs a quick connection test and returns status and latency.

        日本語:
            接続テストを実行し、ステータスとレイテンシを返します。
        """

        box = find_box(application_config, name)
        if not box:
            return {"status": "unknown", "latency_ms": None}

        # Local box is always online
        if box.transport == "local":
            return {"status": "online", "latency_ms": 0}

        # Try to connect to remote box
        import time as time_module
        start_time = time_module.time()
        try:
            connection = await connect(
                box.connect_host,
                box.user,
                box.port,
                box.keyfile,
                box.known_hosts,
                application_config.ssh_config_path,
                box.ssh_alias,
                allow_alias=settings.allow_ssh_alias,
            )
            connection.close()
            elapsed = (time_module.time() - start_time) * 1000  # Convert to ms
            return {"status": "online", "latency_ms": round(elapsed)}
        except Exception as exc:
            logger.debug(f"Box {name} connection check failed: {exc}")
            return {"status": "offline", "latency_ms": None}

    # Session Management API

    @application.get("/box/{name}/sessions", response_model=None)
    async def list_box_sessions(
        request: Request,
        name: str,
        active_only: bool = Query(False),
        limit: int = Query(50),
        application_config: AppConfig = Depends(_get_application_config),
    ) -> HTMLResponse | dict[str, object]:
        """List all tracked sessions for a box.

        English:
            Returns recent and active tmux sessions with metadata.
            Returns HTML for HTMX requests, JSON otherwise.

        日本語:
            最近のアクティブな tmux セッションとメタデータを返します。
            HTMX リクエストには HTML、それ以外は JSON を返します。
        """
        box = find_box(application_config, name)
        if not box:
            raise HTTPException(status_code=404, detail="Unknown box")

        sessions = await state.list_sessions_async(name, active_only=active_only, limit=limit)

        # Return HTML for HTMX requests
        is_htmx = request.headers.get("hx-request") == "true"
        if is_htmx:
            # Format timestamps for display
            import time as time_module

            def format_timestamp(ts: float) -> str:
                """Format timestamp as relative time."""
                now = time_module.time()
                diff = now - ts

                if diff < 60:
                    return "just now"
                elif diff < 3600:
                    mins = int(diff / 60)
                    return f"{mins}m ago"
                elif diff < 86400:
                    hours = int(diff / 3600)
                    return f"{hours}h ago"
                elif diff < 604800:
                    days = int(diff / 86400)
                    return f"{days}d ago"
                else:
                    weeks = int(diff / 604800)
                    return f"{weeks}w ago"

            formatted_sessions = [
                {
                    "id": s.id,
                    "session_name": s.session_name,
                    "working_directory": s.working_directory,
                    "active": s.active,
                    "window_count": s.window_count,
                    "last_accessed": format_timestamp(s.last_accessed_at),
                }
                for s in sessions
            ]

            context = {
                "box": box,
                "sessions": formatted_sessions,
            }
            return templates.TemplateResponse(request, "session_list.html", context)

        # Return JSON for API requests
        return {
            "box": name,
            "sessions": [
                {
                    "id": s.id,
                    "session_name": s.session_name,
                    "working_directory": s.working_directory,
                    "created_at": s.created_at,
                    "last_accessed_at": s.last_accessed_at,
                    "active": s.active,
                    "window_count": s.window_count,
                    "metadata": s.metadata,
                }
                for s in sessions
            ],
        }

    @application.get("/box/{name}/sessions/{session_id}")
    async def get_session_details(
        name: str,
        session_id: str,
        application_config: AppConfig = Depends(_get_application_config),
    ) -> dict[str, object]:
        """Get details of a specific session.

        English:
            Returns full details of a tracked session.

        日本語:
            追跡されたセッションの詳細を返します。
        """
        box = find_box(application_config, name)
        if not box:
            raise HTTPException(status_code=404, detail="Unknown box")

        session = await state.get_session_by_id_async(session_id)
        if not session or session.box != name:
            raise HTTPException(status_code=404, detail="Session not found")

        return {
            "id": session.id,
            "box": session.box,
            "session_name": session.session_name,
            "working_directory": session.working_directory,
            "created_at": session.created_at,
            "last_accessed_at": session.last_accessed_at,
            "active": session.active,
            "window_count": session.window_count,
            "metadata": session.metadata,
        }

    @application.post("/box/{name}/sessions/{session_id}/resume")
    async def resume_session(
        name: str,
        session_id: str,
        application_config: AppConfig = Depends(_get_application_config),
    ) -> dict[str, str]:
        """Generate terminal URL for resuming a session.

        English:
            Returns a URL to reconnect to an existing tmux session.

        日本語:
            既存の tmux セッションに再接続するための URL を返します。
        """
        box = find_box(application_config, name)
        if not box:
            raise HTTPException(status_code=404, detail="Unknown box")

        session = await state.get_session_by_id_async(session_id)
        if not session or session.box != name:
            raise HTTPException(status_code=404, detail="Session not found")

        # Build terminal URL
        terminal_url = (
            f"/term?host={name}"
            f"&session={session.session_name}"
            f"&dir={session.working_directory}"
        )

        return {"terminal_url": terminal_url, "session_name": session.session_name}

    @application.delete("/box/{name}/sessions/{session_id}")
    async def delete_box_session(
        name: str,
        session_id: str,
        application_config: AppConfig = Depends(_get_application_config),
    ) -> dict[str, str]:
        """Delete a tmux session and its tracking record.

        English:
            Kills the tmux session on the remote server and removes tracking data.

        日本語:
            リモートサーバー上の tmux セッションを終了し、追跡データを削除します。
        """
        box = find_box(application_config, name)
        if not box:
            raise HTTPException(status_code=404, detail="Unknown box")

        session = await state.get_session_by_id_async(session_id)
        if not session or session.box != name:
            raise HTTPException(status_code=404, detail="Session not found")

        # Kill tmux session on server
        transport = getattr(box, "transport", "ssh")
        try:
            if transport == "local":
                # Kill local tmux session
                result = await asyncio.create_subprocess_exec(
                    "tmux",
                    "kill-session",
                    "-t",
                    session.session_name,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                await result.communicate()
            else:
                # Kill remote tmux session
                connection = await connect(
                    box.connect_host,
                    box.user,
                    box.port,
                    box.keyfile,
                    box.known_hosts,
                    application_config.ssh_config_path,
                    box.ssh_alias,
                    allow_alias=settings.allow_ssh_alias,
                )
                try:
                    await connection.run(
                        f"tmux kill-session -t {shlex.quote(session.session_name)}",
                        check=False,
                    )
                finally:
                    connection.close()
        except Exception as exc:
            # Continue even if tmux kill fails (session might already be dead)
            logger.debug(f"Failed to kill tmux session (may already be dead): {exc}")

        # Remove from database
        await state.delete_session_async(session_id)

        return {"status": "deleted", "session_id": session_id}

    @application.get("/term", response_class=HTMLResponse)
    async def term_page(
        request: Request,
        host: str,
        session: str | None = None,
        columns: int = 120,
        rows: int = 32,
        directory: str = Query(..., alias="dir"),
        application_config: AppConfig = Depends(_get_application_config),
    ) -> HTMLResponse:
        """Render the terminal page for tmux access.

        English:
            Builds the xterm.js front-end for either SSH or local tmux sessions.

        日本語:
            SSH またはローカル tmux セッションに接続するための xterm.js ベースの
            端末ページを生成します。

        Args:
            request: Incoming HTTP request.
            host: Box identifier provided by the query parameter.
            session: Optional tmux session name override.
            columns: Initial terminal width.
            rows: Initial terminal height.
            directory: Preferred directory for the tmux session.
            application_config: Configuration loaded from disk.

        Returns:
            HTMLResponse: Rendered terminal page.

        Raises:
            HTTPException: When the requested box does not exist.
        """

        box = find_box(application_config, host)
        if not box:
            raise HTTPException(status_code=404, detail="Unknown box")
        display_directory = (
            _normalize_local_path(directory)
            if getattr(box, "transport", "ssh") == "local"
            else directory
        )
        if not session:
            base = Path(display_directory).name or "sshler"
            session = "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in base)
        context = {
            "box": box,
            "directory": display_directory,
            "session": session,
            "cols": columns,
            "rows": rows,
            "app_version": app_version,
        }
        return templates.TemplateResponse(request, "term.html", context)

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

        settings: ServerSettings = websocket.app.state.settings  # type: ignore[attr-defined]
        logger = logging.getLogger("sshler.webapp")

        try:
            # Sanitize session name to prevent command injection
            # Session names are passed to tmux/subprocess commands, so only allow safe characters
            try:
                session = PathValidator.sanitize_session_name(session)
            except ValidationError as exc:
                logger.warning(f"[Security] Invalid session name rejected: {exc}")
                await websocket.close(code=4400, reason="Invalid session name")
                return

            # Check HTTP Basic Auth if required
            if settings.auth_manager:
                auth_header = websocket.headers.get("authorization")
                if not auth_header or not settings.auth_manager.verify_basic_auth_header(
                    auth_header
                ):
                    logger.warning("[Connection] WebSocket auth failed: invalid basic auth")
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

            client_host = websocket.client.host if websocket.client else "unknown"  # type: ignore[attr-defined]
            logger.info(
                f"[Connection] WebSocket connected: box={box.name}, transport={transport}, "
                f"dir={normalized_directory}, session={session}, client={client_host}"
            )

            connection: asyncssh.SSHClientConnection | None = None
            process = None

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
                    process = await _open_local_tmux(normalized_directory, session)
                    logger.info(f"[Connection] Local tmux process started successfully")
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
                    logger.debug(f"Writer: got text message: {message['text'][:50]}")
                    await _handle_control_message(
                        message["text"],
                        process,
                        connection,
                        session,
                        transport,
                    )
                    return True

                if "bytes" in message and message["bytes"] is not None:
                    num_bytes = len(message["bytes"])
                    logger.debug(f"Writer: got {num_bytes} bytes, writing to stdin")
                    data_bytes = message["bytes"]
                    process.stdin.write(data_bytes)
                    # In tests or with stub stdin objects, also record bytes when a
                    # simple messages buffer is available.
                    if hasattr(process.stdin, "messages"):
                        try:
                            messages_buffer = getattr(process.stdin, "messages")
                            if isinstance(messages_buffer, list) and data_bytes not in messages_buffer:
                                messages_buffer.append(data_bytes)
                        except Exception as exc:
                            logger.debug(f"Failed to record message in test buffer: {exc}")
                return True

            # Handle any immediate message now that the process exists.
            try:
                initial_message = await asyncio.wait_for(websocket.receive(), timeout=0.05)
                if not await _process_message(initial_message):
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
                    while True:
                        logger.debug("Reader: waiting for stdout data...")
                        data = await process.stdout.read(32768)
                        if not data:
                            logger.info("Reader: got empty data, ending")
                            break
                        logger.debug(f"Reader: got {len(data)} bytes, sending to websocket")
                        await websocket.send_bytes(data)
                except Exception as exc:
                    logger.error(f"Reader exception: {exc}", exc_info=True)

            async def writer() -> None:
                logger.info("Writer task started")
                try:
                    while True:
                        logger.debug("Writer: waiting for websocket message...")
                        message = await websocket.receive()
                        if not await _process_message(message):
                            break
                except WebSocketDisconnect:
                    logger.info("Writer: websocket disconnected")
                except Exception as exc:
                    logger.error(f"Writer exception: {exc}", exc_info=True)

            async def poll_tmux_windows() -> None:
                try:
                    while True:
                        if transport == "local":
                            window_payload = await _list_local_tmux_windows(session)
                        else:
                            window_payload = await _list_tmux_windows(connection, session)
                        if window_payload is not None:
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
                    logger.debug(f"Error in tmux window polling loop: {exc}")

            async def websocket_pinger() -> None:
                """Send WebSocket pings to keep connection alive through proxies/NAT."""
                try:
                    while True:
                        await asyncio.sleep(30)  # Ping every 30 seconds
                        try:
                            await websocket.send_text(json.dumps({"op": "ping"}))
                            logger.debug("Sent WebSocket ping")
                        except Exception as exc:
                            logger.warning(f"Failed to send WebSocket ping: {exc}")
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
                        # The tmux session will continue running in WSL
                        try:
                            process.stdin.close()
                        except Exception as exc:
                            logger.debug(f"Error closing process stdin: {exc}")
                        try:
                            # Give it a moment to flush
                            await asyncio.sleep(0.1)
                        except Exception as exc:
                            logger.debug(f"Error during flush sleep: {exc}")
                    else:
                        process.stdin.write_eof()
                        process.close()
            except Exception as exc:
                logger.debug(f"Error during process cleanup: {exc}")
            try:
                if connection is not None:
                    connection.close()
            except Exception as exc:
                logger.debug(f"Error closing SSH connection: {exc}")

            logger.info(
                f"[Connection] WebSocket closed: box={box.name}, transport={transport}, "
                f"session={session}, client={client_host}"
            )

    # Include auth router (for session-based authentication)
    auth_router = create_auth_router(settings.auth_manager, application.state.auth_tracker)
    application.include_router(auth_router, prefix="/api/v1")

    # Include main API router
    application.include_router(create_api_router(deps))
    return application


def _compute_app_version() -> str:
    parts = [__version__]
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


def _render_markdown(content: str) -> str:
    """Convert markdown content to HTML."""
    md = MarkdownIt()
    return md.render(content)


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
        if cols > 0 and rows > 0:
            if transport == "local":
                # Resize local tmux client
                try:
                    await _run_local_tmux_command(["refresh-client", "-C", f"{cols}x{rows}"])
                except Exception as exc:
                    logger.debug(f"Failed to resize local tmux: {exc}")
            else:
                # Resize SSH PTY
                try:
                    process.set_pty_size(cols, rows)
                except Exception as exc:
                    logger.debug(f"Failed to resize SSH PTY: {exc}")
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
) -> list[dict[str, str]] | None:
    try:
        result = await connection.run(
            "tmux list-windows -F '#{window_index} #{window_name} #{window_active}' -t "
            f"{shlex.quote(session)}",
            check=False,
        )
    except Exception as exc:
        logger.debug(f"Failed to list tmux windows for session {session}: {exc}")
        return None

    if result.returncode != 0:
        return None

    windows: list[dict[str, str]] = []
    for line in result.stdout.splitlines():
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
