from __future__ import annotations

import argparse
import threading
import webbrowser

import uvicorn

from .webapp import make_app


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


def serve(host: str = "127.0.0.1", port: int = 8822, reload: bool = False) -> None:
    """Start the sshler FastAPI application via uvicorn.

    Args:
        host: Host interface to bind the development server.
        port: TCP port for the development server.
        reload: Whether to enable auto-reload for code changes.
    """

    fastapi_application = make_app()
    application_url = f"http://{host}:{port}"
    _open_browser_later(application_url)
    uvicorn.run(
        fastapi_application,
        host=host,
        port=port,
        reload=reload,
        log_level="info",
    )


def main() -> None:
    """Parse CLI arguments and invoke the requested subcommand.

    Returns:
        None: The function exits after invoking the requested command.
    """

    parser = argparse.ArgumentParser(prog="sshler", description="Local SSH tmux-in-browser")
    subcommands = parser.add_subparsers(dest="command")

    serve_parser = subcommands.add_parser("serve", help="Start the sshler web app")
    serve_parser.add_argument("--host", default="127.0.0.1")
    serve_parser.add_argument("--port", type=int, default=8822)
    serve_parser.add_argument("--reload", action="store_true")

    parsed_args = parser.parse_args()
    if parsed_args.command in (None, "serve"):
        serve(
            host=getattr(parsed_args, "host", "127.0.0.1"),
            port=getattr(parsed_args, "port", 8822),
            reload=getattr(parsed_args, "reload", False),
        )
    else:
        parser.print_help()
