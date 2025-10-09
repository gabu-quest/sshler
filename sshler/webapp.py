from __future__ import annotations

import asyncio
import json
import shlex
import subprocess
from pathlib import Path

import asyncssh
from fastapi import (
    Depends,
    FastAPI,
    Form,
    HTTPException,
    Query,
    Request,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import __version__
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


def make_app() -> FastAPI:
    """Create and configure the FastAPI application.

    Returns:
        FastAPI: Configured ASGI application.
    """

    application = FastAPI(title="sshler", version="0.1.0")
    application.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
    templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
    app_version = _compute_app_version()

    def _get_application_config() -> AppConfig:
        """Dependency that loads the persisted configuration.

        Returns:
            AppConfig: Configuration loaded from disk.
        """

        return load_config()

    @application.get("/")
    async def root() -> RedirectResponse:
        """Redirect the index page to the boxes list.

        Returns:
            RedirectResponse: HTTP redirect to ``/boxes``.
        """

        return RedirectResponse(url="/boxes")

    @application.get("/docs", response_class=HTMLResponse)
    async def docs(request: Request) -> HTMLResponse:
        """Render simple usage documentation."""

        return templates.TemplateResponse(
            "docs.html",
            {"request": request, "app_version": app_version},
        )

    @application.get("/boxes", response_class=HTMLResponse)
    async def boxes(
        request: Request,
        application_config: AppConfig = Depends(_get_application_config),
    ) -> HTMLResponse:
        """Render the list of configured boxes.

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
        """Render a partial listing for a directory via SFTP.

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
            )
        except SSHError as exc:
            context = {
                "box": box,
                "directory_path": directory_path,
                "entries": [],
                "error": f"SSH connection failed: {exc}",
                "target_id": target_id,
            }
            return templates.TemplateResponse(request, "partials/dir_listing.html", context)

        try:
            try:
                directory_entries = await sftp_list_directory(connection, directory_path)
                error_message = None
            except Exception as exc:  # pragma: no cover - SFTP failures vary by host
                directory_entries = []
                error_message = f"Directory listing failed: {exc}"
            context = {
                "box": box,
                "directory_path": directory_path,
                "entries": directory_entries,
                "error": error_message,
                "target_id": target_id,
            }
            return templates.TemplateResponse(request, "partials/dir_listing.html", context)
        finally:
            if connection is not None:
                connection.close()

    @application.get("/box/{name}/cat", response_class=HTMLResponse)
    async def view_file(
        name: str,
        request: Request,
        file_path: str = Query(..., alias="path"),
        application_config: AppConfig = Depends(_get_application_config),
    ) -> HTMLResponse:
        """Render a read-only preview of a remote file."""

        box = find_box(application_config, name)
        if not box:
            raise HTTPException(status_code=404, detail="Unknown box")

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
            )
        except SSHError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

        try:
            try:
                content = await sftp_read_file(connection, file_path)
            except Exception as exc:
                raise HTTPException(status_code=500, detail=str(exc)) from exc
            context = {
                "box": box,
                "path": file_path,
                "content": content,
                "syntax_class": _syntax_from_filename(file_path),
                "app_version": app_version,
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

        connection = await connect(
            box.connect_host,
            box.user,
            box.port,
            box.keyfile,
            box.known_hosts,
            application_config.ssh_config_path,
            box.ssh_alias,
        )

        try:
            if request.method == "GET":
                try:
                    content = await sftp_read_file(connection, file_path, max_bytes=262144)
                except Exception as exc:
                    raise HTTPException(status_code=500, detail=str(exc)) from exc
                context = {
                    "box": box,
                    "path": file_path,
                    "content": content,
                    "app_version": app_version,
                }
                return templates.TemplateResponse(request, "file_edit.html", context)

            payload = await request.json()
            content = payload.get("content")
            if content is None:
                raise HTTPException(status_code=400, detail="Missing content")
            if len(content.encode("utf-8")) > 262144:
                raise HTTPException(status_code=400, detail="File exceeds 256KB editing limit")

            sftp_client = await connection.start_sftp_client()
            try:
                async with await sftp_client.open(file_path, "w") as remote_file:
                    await remote_file.write(content.encode("utf-8"))
            finally:
                try:
                    await sftp_client.exit()
                except Exception:
                    pass
            return templates.TemplateResponse(request, "file_edit.html", {
                "box": box,
                "path": file_path,
                "content": content,
                "app_version": app_version,
            })
        finally:
            connection.close()

    @application.post("/box/{name}/fav", response_class=PlainTextResponse)
    async def toggle_favorite(
        name: str,
        directory_path: str = Query(..., alias="path"),
        application_config: AppConfig = Depends(_get_application_config),
    ) -> str:
        """Toggle a favorite directory for a box.

        Args:
            name: Box identifier from the URL.
            directory_path: Remote directory to toggle as favorite.
            application_config: Configuration loaded from disk.

        Returns:
            str: Literal ``"ok"`` acknowledging persistence.

        Raises:
            HTTPException: When the requested box does not exist.
        """

        box = find_box(application_config, name)
        if not box:
            raise HTTPException(status_code=404, detail="Unknown box")
        stored_override = application_config.get_or_create_stored(name)
        favorites = stored_override.favorites
        if directory_path in favorites:
            favorites.remove(directory_path)
        else:
            favorites.append(directory_path)
        box.favorites = list(favorites)
        save_config(application_config)
        return "ok"

    @application.get("/boxes/new", response_class=HTMLResponse)
    async def new_box_form(
        request: Request,
        application_config: AppConfig = Depends(_get_application_config),
    ) -> HTMLResponse:
        """Render the form to add a custom box."""

        context = {
            "configuration_path": str(get_config_path()),
            "existing_names": [box.name for box in application_config.boxes],
            "app_version": app_version,
        }
        return templates.TemplateResponse(request, "new_box.html", context)

    @application.post("/boxes/new")
    async def create_box(
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
            favorites=favorites_list,
            default_dir=default_dir.strip() or None,
            known_hosts=known_hosts.strip() or None,
            ssh_alias=ssh_alias.strip() or None,
        )

        application_config = load_config()
        application_config.stored[new_box.name] = new_box
        rebuild_boxes(application_config)
        save_config(application_config)
        return RedirectResponse(url="/boxes", status_code=303)

    @application.post("/box/{name}/refresh", response_class=PlainTextResponse)
    async def refresh_box(
        name: str,
        application_config: AppConfig = Depends(_get_application_config),
    ) -> str:
        """Remove connection overrides so SSH config values apply."""

        stored_override = application_config.stored.get(name)
        if stored_override is None:
            return "ok"

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
                stored_override.favorites,
                stored_override.default_dir,
                stored_override.agent,
            ]
        ):
            application_config.stored.pop(name, None)

        rebuild_boxes(application_config)
        save_config(application_config)
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
        if not session:
            base = Path(directory).name or "sshler"
            session = "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in base)
        context = {
            "box": box,
            "directory": directory,
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
        """Bridge between the browser websocket and tmux over SSH.

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

        await websocket.accept()
        application_config = load_config()
        box = find_box(application_config, host)
        if not box:
            await websocket.close()
            return

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
        except Exception as exc:  # pragma: no cover - network errors are environment specific
            await websocket.send_text(f"Connection failed: {exc}")
            await websocket.close()
            return

        process: asyncssh.SSHClientProcess | None = None
        try:
            # make sure the directory exists (best-effort)
            try:
                is_directory = await sftp_is_directory(connection, directory)
                if not is_directory:
                    directory = box.default_dir or f"/home/{box.user}"
            except Exception:
                pass

            process = await open_tmux(
                connection,
                working_directory=directory,
                session=session,
                columns=columns,
                rows=rows,
            )

            async def reader() -> None:
                try:
                    while True:
                        data = await process.stdout.read(32768)
                        if not data:
                            break
                        await websocket.send_bytes(data)
                except Exception:
                    pass

            async def writer() -> None:
                try:
                    while True:
                        message = await websocket.receive()
                        message_type = message.get("type")
                        if message_type == "websocket.disconnect":
                            break
                        if "text" in message and message["text"] is not None:
                            await _handle_control_message(
                                message["text"],
                                process,
                                connection,
                                session,
                            )
                        elif "bytes" in message and message["bytes"] is not None:
                            process.stdin.write(message["bytes"])
                except WebSocketDisconnect:
                    pass
                except Exception:
                    pass

            async def poll_tmux_windows() -> None:
                try:
                    while True:
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
                    process.stdin.write_eof()
                    process.close()
            except Exception:
                pass
            try:
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


async def _handle_control_message(
    payload: str,
    process: asyncssh.SSHClientProcess,
    connection: asyncssh.SSHClientConnection,
    session: str,
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
            try:
                process.set_pty_size(cols, rows)
            except Exception:
                pass
    elif operation == "select-window":
        target = message.get("target")
        if target is not None:
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
            try:
                await connection.run(
                    f"tmux rename-window -t {shlex.quote(session)} {shlex.quote(str(new_name))}",
                    check=False,
                )
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
        windows.append({
            "index": index,
            "name": name,
            "active": active == "1",
        })
    return windows
