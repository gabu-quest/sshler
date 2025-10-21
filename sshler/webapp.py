from __future__ import annotations

import asyncio
import base64
import json
import platform
import posixpath
import secrets
import shlex
import subprocess
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

import asyncssh
from markdown_it import MarkdownIt
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

from . import __version__, state
from .config import (
    AppConfig,
    StoredBox,
    find_box,
    get_config_path,
    load_config,
    rebuild_boxes,
    save_config,
)
from .ssh import (
    SSHError,
    connect,
    open_tmux,
    sftp_is_directory,
    sftp_list_directory,
    sftp_read_file,
)

TEMPLATES_DIR = Path(__file__).parent / "templates"
STATIC_DIR = Path(__file__).parent / "static"

DEFAULT_MAX_UPLOAD_BYTES = 50 * 1024 * 1024
MAX_IMAGE_PREVIEW_BYTES = 2 * 1024 * 1024
IMAGE_CONTENT_TYPES: dict[str, str] = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".svg": "image/svg+xml",
}
LOCAL_IS_WINDOWS = platform.system().lower().startswith("windows")


@dataclass
class ServerSettings:
    allow_origins: list[str] = field(default_factory=list)
    csrf_token: str | None = field(default_factory=lambda: secrets.token_urlsafe(32))
    max_upload_bytes: int = DEFAULT_MAX_UPLOAD_BYTES
    allow_ssh_alias: bool = True
    basic_auth: tuple[str, str] | None = None
    basic_auth_header: str | None = field(init=False, default=None)

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

        if self.basic_auth:
            user, password = self.basic_auth
            raw = f"{user}:{password}".encode()
            self.basic_auth_header = "Basic " + base64.b64encode(raw).decode("ascii")



def _normalize_directory_path(directory: str | None) -> str:
    raw = (directory or "/").strip()
    if not raw:
        raw = "/"
    if not raw.startswith("/"):
        raw = "/" + raw.lstrip("/")

    normalized = posixpath.normpath(raw)
    if normalized == ".":
        return "/"
    if not normalized.startswith("/"):
        normalized = "/" + normalized.lstrip("/")
    return normalized or "/"


def _compose_remote_child_path(directory: str, filename: str) -> str:
    cleaned = (filename or "").strip()
    if not cleaned:
        raise ValueError("Filename is required")
    if cleaned in {".", ".."} or "/" in cleaned:
        raise ValueError("Filename cannot contain path separators")
    if "\x00" in cleaned:
        raise ValueError("Filename contains unsupported characters")
    parent = PurePosixPath(_normalize_directory_path(directory))
    return (parent / cleaned).as_posix()


def _normalize_local_path(directory: str | None) -> str:
    if directory:
        base = Path(directory).expanduser()
    else:
        base = Path.home()
    try:
        resolved = base.resolve()
    except Exception:
        resolved = base
    if LOCAL_IS_WINDOWS:
        return resolved.as_posix()
    return str(resolved)


def _compose_local_child_path(directory: str, filename: str) -> str:
    cleaned = (filename or "").strip()
    if not cleaned:
        raise ValueError("Filename is required")
    if cleaned in {".", ".."} or any(sep in cleaned for sep in ("/", "\\")):
        raise ValueError("Filename cannot contain path separators")
    parent = Path(directory or Path.home())
    target = parent / cleaned
    if LOCAL_IS_WINDOWS:
        return target.expanduser().as_posix()
    return str(target.expanduser())


async def _local_list_directory(path: str) -> list[dict[str, object]]:
    def _worker() -> list[dict[str, object]]:
        entries: list[dict[str, object]] = []
        base_path = Path(path)
        for child in base_path.iterdir():
            try:
                stats = child.stat()
            except Exception:
                continue
            entries.append(
                {
                    "name": child.name,
                    "is_directory": child.is_dir(),
                    "size": stats.st_size if child.is_file() else None,
                }
            )
        entries.sort(key=lambda entry: (not entry["is_directory"], entry["name"].lower()))
        return entries

    return await asyncio.to_thread(_worker)


async def _local_is_directory(path: str) -> bool:
    return await asyncio.to_thread(lambda: Path(path).is_dir())


async def _local_read_text(path: str, max_bytes: int) -> str:
    def _worker() -> str:
        with open(Path(path), "rb") as handle:
            data = handle.read(max_bytes)
        return data.decode("utf-8", errors="replace")

    return await asyncio.to_thread(_worker)


async def _local_write_text(path: str, content: str) -> None:
    def _worker() -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with open(Path(path), "w", encoding="utf-8") as handle:
            handle.write(content)

    await asyncio.to_thread(_worker)


async def _local_read_bytes(path: str, limit: int) -> tuple[bytes, bool]:
    def _worker() -> tuple[bytes, bool]:
        with open(Path(path), "rb") as handle:
            data = handle.read(limit + 1)
        return (data[:limit], len(data) > limit)

    return await asyncio.to_thread(_worker)


async def _local_write_bytes(path: str, data: bytes) -> None:
    def _worker() -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with open(Path(path), "wb") as handle:
            handle.write(data)

    await asyncio.to_thread(_worker)


async def _local_create_file(path: str) -> None:
    def _worker() -> None:
        file_path = Path(path)
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.touch(exist_ok=False)

    await asyncio.to_thread(_worker)


async def _local_delete_file(path: str) -> None:
    def _worker() -> None:
        Path(path).unlink()

    await asyncio.to_thread(_worker)


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

    # On Windows, we need to use winpty or conpty to provide a PTY for tmux
    # For now, let's try using script to provide a PTY
    if LOCAL_IS_WINDOWS:
        # Use 'script' command in WSL to create a PTY
        script_command = ["wsl", "--", "script", "-qfc", " ".join(f"'{arg}'" if " " in arg else arg for arg in command[2:]), "/dev/null"]
        return await asyncio.create_subprocess_exec(
            *script_command,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

    return await asyncio.create_subprocess_exec(
        *command,
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
    except Exception:
        pass


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
    except Exception:
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

    # Disable automatic /docs and /redoc endpoints
    application = FastAPI(title="sshler", version="0.1.0", docs_url=None, redoc_url=None)
    application.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
    templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
    app_version = _compute_app_version()

    application.state.settings = settings
    templates.env.globals["csrf_token"] = settings.csrf_token

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
        if settings.basic_auth_header and request.method != "OPTIONS":
            auth_header = request.headers.get("authorization")
            if auth_header != settings.basic_auth_header:
                return Response(
                    status_code=401,
                    headers={"WWW-Authenticate": 'Basic realm="sshler"'},
                )

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

    def _require_token(request: Request) -> None:
        if not settings.csrf_token:
            return
        supplied = request.headers.get("x-sshler-token")
        if supplied != settings.csrf_token:
            raise HTTPException(status_code=403, detail="Missing or invalid X-SSHLER-TOKEN header")

    def _build_directory_urls(request: Request, box_name: str) -> dict[str, str]:
        def _resolve(endpoint: str, **params: str) -> str:
            try:
                return request.url_for(endpoint, **params)
            except Exception:
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

        normalized_directory = _normalize_directory_path(directory_path)

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

        try:
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
        finally:
            connection.close()

    def _get_application_config() -> AppConfig:
        """Dependency that loads the persisted configuration.

        Returns:
            AppConfig: Configuration loaded from disk.
        """

        return load_config()

    @application.get("/")
    async def root() -> RedirectResponse:
        """Redirect the index page to the boxes list.

        English:
            Visiting ``/`` immediately sends the browser to ``/boxes``.

        日本語:
            ルート ``/`` にアクセスした際に ``/boxes`` へリダイレクトします。

        Returns:
            RedirectResponse: HTTP redirect to ``/boxes``.
        """

        return RedirectResponse(url="/boxes")

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
        except Exception:
            # If README can't be read, use empty strings
            pass

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

        try:
            remote_path = _compose_remote_child_path(directory_path, filename)
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
        connection = None
        sftp_client = None
        success_message: str | None = None
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
            try:
                sftp_client = await connection.start_sftp_client()
            except Exception as exc:  # pragma: no cover - depends on remote server
                error_message = f"SFTP session failed: {exc}"
            else:
                try:
                    try:
                        await sftp_client.stat(remote_path)
                    except Exception:
                        pass
                    else:
                        error_message = (
                            f"File already exists: {PurePosixPath(remote_path).name}"
                        )
                    if error_message is None:
                        async with await sftp_client.open(
                            remote_path, "w", encoding="utf-8"
                        ) as remote_file:
                            await remote_file.write("")
                        success_message = f"Created {PurePosixPath(remote_path).name}"
                except Exception as exc:  # pragma: no cover - remote host behavior varies
                    error_message = f"Failed to create file: {exc}"
        except SSHError as exc:
            error_message = f"SSH connection failed: {exc}"
        finally:
            if sftp_client is not None:
                try:
                    await sftp_client.exit()
                except Exception:
                    pass
            if connection is not None:
                connection.close()

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

        try:
            remote_path = _compose_remote_child_path(directory_path, candidate_name)
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

        connection = None
        sftp_client = None
        if error_message is None:
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
                try:
                    sftp_client = await connection.start_sftp_client()
                except Exception as exc:  # pragma: no cover - depends on remote server
                    error_message = f"SFTP session failed: {exc}"
                else:
                    try:
                        try:
                            await sftp_client.stat(remote_path)
                        except Exception:
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
            except SSHError as exc:
                error_message = f"SSH connection failed: {exc}"
            finally:
                if sftp_client is not None:
                    try:
                        await sftp_client.exit()
                    except Exception:
                        pass
                if connection is not None:
                    connection.close()

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

        # Remote file deletion
        error_message = None
        success_message: str | None = None
        connection = None
        sftp_client = None

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
            try:
                sftp_client = await connection.start_sftp_client()
            except Exception as exc:
                error_message = f"SFTP session failed: {exc}"
            else:
                try:
                    await sftp_client.remove(file_path)
                    success_message = f"Deleted {PurePosixPath(file_path).name}"
                except FileNotFoundError:
                    error_message = f"File not found: {PurePosixPath(file_path).name}"
                except Exception as exc:
                    error_message = f"Failed to delete file: {exc}"
        except SSHError as exc:
            error_message = f"SSH connection failed: {exc}"
        finally:
            if sftp_client is not None:
                try:
                    await sftp_client.exit()
                except Exception:
                    pass
            if connection is not None:
                connection.close()

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
            }
            return templates.TemplateResponse(request, "file_view.html", context)

        connection = None
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
        except SSHError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

        try:
            suffix = Path(file_path).suffix.lower()
            image_mime = IMAGE_CONTENT_TYPES.get(suffix)
            image_data: str | None = None
            image_too_large = False
            if image_mime:
                try:
                    image_bytes, too_large = await _read_file_bytes(
                        connection, file_path, MAX_IMAGE_PREVIEW_BYTES
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
                    text_content = await _read_remote_text(
                        connection, file_path, settings.max_upload_bytes
                    )
                except Exception as exc:
                    raise HTTPException(status_code=500, detail=str(exc)) from exc

            # Get the directory containing the file
            parent_dir = str(PurePosixPath(file_path).parent)

            context = {
                "box": box,
                "path": file_path,
                "parent_directory": parent_dir,
                "content": text_content or "",
                "syntax_class": _syntax_from_filename(file_path),
                "app_version": app_version,
                "image_data": image_data,
                "image_mime": image_mime,
                "image_too_large": image_too_large,
                "image_limit_kb": MAX_IMAGE_PREVIEW_BYTES // 1024,
            }
            return templates.TemplateResponse(request, "file_view.html", context)
        finally:
            if connection is not None:
                connection.close()

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
            if request.method == "GET":
                try:
                    content = await _read_remote_text(connection, file_path, 262144)
                except Exception as exc:
                    raise HTTPException(status_code=500, detail=str(exc)) from exc
                context = {
                    "box": box,
                    "path": file_path,
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
                async with await sftp_client.open(file_path, "w", encoding="utf-8") as remote_file:
                    await remote_file.write(content)
            finally:
                try:
                    await sftp_client.exit()
                except Exception:
                    pass
            return templates.TemplateResponse(
                request,
                "file_edit.html",
                {
                    "box": box,
                    "path": file_path,
                    "content": content,
                    "app_version": app_version,
                },
            )
        finally:
            connection.close()

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

        if settings.basic_auth_header:
            auth_header = websocket.headers.get("authorization")
            if auth_header != settings.basic_auth_header:
                await websocket.close(code=4401, reason="Unauthorized")
                return

        token_param = websocket.query_params.get("token")
        if settings.csrf_token and token_param != settings.csrf_token:
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

        # Set up logging
        import logging
        logger = logging.getLogger("sshler.webapp")

        # Configure file logging if not already configured
        if not logger.handlers:
            logger.setLevel(logging.DEBUG)
            file_handler = logging.FileHandler("debug.log")
            file_handler.setLevel(logging.DEBUG)
            formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
            file_handler.setFormatter(formatter)
            logger.addHandler(file_handler)

        connection: asyncssh.SSHClientConnection | None = None
        process = None

        try:
            if transport == "local":
                try:
                    is_directory = await _local_is_directory(normalized_directory)
                except Exception:
                    is_directory = False
                if not is_directory:
                    normalized_directory = _normalize_local_path(box.default_dir)

                # Debug: Log the command we're about to run
                logger.info(f"Starting local tmux: transport={transport}, dir={normalized_directory}, session={session}")

                try:
                    process = await _open_local_tmux(normalized_directory, session)
                    logger.info(f"Local tmux process started: {process}")
                except Exception as exc:
                    logger.error(f"Failed to start local tmux: {exc}", exc_info=True)
                    error_msg = f"Connection failed: {exc}\r\n"
                    try:
                        await websocket.send_text(error_msg)
                    except Exception:
                        pass
                    await websocket.close()
                    return
            else:
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
                except Exception as exc:  # pragma: no cover
                    # network errors are environment specific
                    await websocket.send_text(f"Connection failed: {exc}")
                    await websocket.close()
                    return

                try:
                    is_directory = await sftp_is_directory(connection, normalized_directory)
                    if not is_directory:
                        normalized_directory = box.default_dir or f"/home/{box.user}"
                except Exception:
                    pass

                process = await open_tmux(
                    connection,
                    working_directory=normalized_directory,
                    session=session,
                    columns=columns,
                    rows=rows,
                )

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
                        message_type = message.get("type")
                        if message_type == "websocket.disconnect":
                            logger.info("Writer: got disconnect")
                            break
                        if "text" in message and message["text"] is not None:
                            logger.debug(f"Writer: got text message: {message['text'][:50]}")
                            await _handle_control_message(
                                message["text"],
                                process,
                                connection,
                                session,
                                transport,
                            )
                        elif "bytes" in message and message["bytes"] is not None:
                            logger.debug(f"Writer: got {len(message['bytes'])} bytes, writing to stdin")
                            process.stdin.write(message["bytes"])
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
                        await asyncio.sleep(2)
                except Exception:
                    pass

            poller = asyncio.create_task(poll_tmux_windows())
            try:
                await asyncio.gather(reader(), writer(), poller)
            finally:
                poller.cancel()
        finally:
            try:
                if process:
                    if transport == "local":
                        # Don't terminate! Just close stdin/stdout to detach gracefully
                        # The tmux session will continue running in WSL
                        try:
                            process.stdin.close()
                        except Exception:
                            pass
                        try:
                            # Give it a moment to flush
                            await asyncio.sleep(0.1)
                        except Exception:
                            pass
                    else:
                        process.stdin.write_eof()
                        process.close()
            except Exception:
                pass
            try:
                if connection is not None:
                    connection.close()
            except Exception:
                pass

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
    except Exception:
        pass
    return " ".join(part for part in parts if part)


def _syntax_from_filename(path: str) -> str:
    suffix = Path(path).suffix.lower()
    mapping = {
        ".py": "python",
        ".js": "javascript",
        ".ts": "typescript",
        ".json": "json",
        ".yaml": "yaml",
        ".yml": "yaml",
        ".md": "markdown",
        ".sh": "bash",
        ".bash": "bash",
        ".html": "markup",
        ".css": "css",
        ".toml": "toml",
        ".ini": "ini",
    }
    return mapping.get(suffix, "").strip()


async def _read_file_bytes(
    connection: asyncssh.SSHClientConnection, path: str, limit: int
) -> tuple[bytes, bool]:
    sftp_client = await connection.start_sftp_client()
    try:
        async with await sftp_client.open(path, "rb") as remote_file:
            data = await remote_file.read(limit + 1)
    finally:
        try:
            await sftp_client.exit()
        except Exception:
            pass
    too_large = len(data) > limit
    if too_large:
        return b"", True
    return data, False


async def _read_remote_text(
    connection: asyncssh.SSHClientConnection, path: str, limit: int
) -> str:
    """Retrieve UTF-8 text from an SFTP connection with graceful fallback."""

    try:
        return await sftp_read_file(connection, path, max_bytes=limit)
    except TypeError as exc:
        if "max_bytes" not in str(exc):
            raise
        return await sftp_read_file(connection, path)


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
        if cols > 0 and rows > 0 and transport != "local":
            try:
                process.set_pty_size(cols, rows)
            except Exception:
                pass
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
                except Exception:
                    pass
    elif operation == "send":
        data = message.get("data")
        if data:
            try:
                process.stdin.write(data.encode())
            except Exception:
                pass
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
                except Exception:
                    pass


async def _list_tmux_windows(
    connection: asyncssh.SSHClientConnection, session: str
) -> list[dict[str, str]] | None:
    try:
        result = await connection.run(
            "tmux list-windows -F '#{window_index} #{window_name} #{window_active}' -t "
            f"{shlex.quote(session)}",
            check=False,
        )
    except Exception:
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
