from __future__ import annotations

import argparse
import secrets
import threading
import webbrowser

import uvicorn

from .webapp import ServerSettings, make_app


# open the user's browser after uvicorn starts listening
def _open_browser_later(application_url: str, delay: float = 0.8) -> None:
    def open_browser() -> None:
        try:
            webbrowser.open(application_url)
        except Exception:
            pass

    timer = threading.Timer(delay, open_browser)
    timer.daemon = True
    timer.start()


def serve(
    host: str = "127.0.0.1",
    port: int = 8822,
    reload: bool = False,
    allow_origins: list[str] | None = None,
    basic_auth: tuple[str, str] | None = None,
    max_upload_mb: int = 50,
    allow_ssh_alias: bool = True,
    log_level: str = "info",
    open_browser: bool = True,
    token: str | None = None,
) -> None:
    """Start the sshler FastAPI application via uvicorn.

    English:
        Bootstraps the FastAPI app with the provided security settings and begins
        serving requests on the chosen host/port.

    日本語:
        指定されたセキュリティ設定で FastAPI アプリケーションを初期化し、
        指定したホストとポートでリクエスト受付を開始します。
    """

    settings = ServerSettings(
        allow_origins=allow_origins or [],
        csrf_token=token or secrets.token_urlsafe(32),
        max_upload_bytes=max_upload_mb * 1024 * 1024,
        allow_ssh_alias=allow_ssh_alias,
        basic_auth=basic_auth,
    )

    fastapi_application = make_app(settings)
    application_url = f"http://{host}:{port}"
    if open_browser and host in {"127.0.0.1", "localhost"}:
        _open_browser_later(application_url)

    print(f"[sshler] listening on {application_url}")
    print(f"[sshler] X-SSHLER-TOKEN={settings.csrf_token}")
    if basic_auth:
        print(f"[sshler] Basic auth enabled for user '{basic_auth[0]}'")
    if settings.allow_origins:
        print(f"[sshler] Additional allowed origins: {', '.join(settings.allow_origins)}")

    uvicorn.run(
        fastapi_application,
        host=host,
        port=port,
        reload=reload,
        log_level=log_level,
    )


def main() -> None:
    """Parse CLI arguments and invoke the requested subcommand.

    English:
        Handles ``sshler`` command-line parsing and dispatches to ``serve`` when
        no subcommand is explicitly provided.

    日本語:
        ``sshler`` のコマンドライン引数を解析し、サブコマンドが指定されて
        いない場合は ``serve`` を実行します。
    """

    parser = argparse.ArgumentParser(prog="sshler", description="Local SSH tmux-in-browser")
    subcommands = parser.add_subparsers(dest="command")

    serve_parser = subcommands.add_parser("serve", help="Start the sshler web app")
    serve_parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Interface to bind (default: 127.0.0.1)",
    )
    serve_parser.add_argument("--bind", default=None, help="Alias for --host")
    serve_parser.add_argument("--port", type=int, default=8822)
    serve_parser.add_argument("--reload", action="store_true")
    serve_parser.add_argument(
        "--allow-origin",
        action="append",
        dest="allow_origins",
        default=[],
        help="Allow cross-origin requests from this origin (repeatable)",
    )
    serve_parser.add_argument(
        "--auth",
        help="Enable HTTP basic auth with 'username:password'",
    )
    serve_parser.add_argument(
        "--max-upload-mb",
        type=int,
        default=50,
        help="Maximum upload size in MB (default: 50)",
    )
    serve_parser.add_argument(
        "--no-ssh-alias",
        action="store_true",
        help="Disable SSH config alias expansion",
    )
    serve_parser.add_argument(
        "--log-level",
        default="info",
        choices=["critical", "error", "warning", "info", "debug", "trace"],
        help="Uvicorn log level",
    )
    serve_parser.add_argument("--token", help="Provide a fixed X-SSHLER-TOKEN value")
    serve_parser.add_argument(
        "--no-browser",
        dest="open_browser",
        action="store_false",
        help="Do not automatically open a browser window",
    )
    serve_parser.set_defaults(open_browser=True)

    parsed_args = parser.parse_args()
    if parsed_args.command in (None, "serve"):
        bind_host = getattr(parsed_args, "bind", None) or getattr(parsed_args, "host", "127.0.0.1")
        basic_auth: tuple[str, str] | None = None
        auth_value = getattr(parsed_args, "auth", None)
        if auth_value:
            if ":" not in auth_value:
                parser.error("--auth must be in the form username:password")
            basic_auth = tuple(auth_value.split(":", 1))  # type: ignore[assignment]
        serve(
            host=bind_host,
            port=getattr(parsed_args, "port", 8822),
            reload=getattr(parsed_args, "reload", False),
            allow_origins=getattr(parsed_args, "allow_origins", []) or [],
            basic_auth=basic_auth,
            max_upload_mb=getattr(parsed_args, "max_upload_mb", 50),
            allow_ssh_alias=not getattr(parsed_args, "no_ssh_alias", False),
            log_level=getattr(parsed_args, "log_level", "info"),
            open_browser=getattr(parsed_args, "open_browser", True),
            token=getattr(parsed_args, "token", None),
        )
    else:
        parser.print_help()
