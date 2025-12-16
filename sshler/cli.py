from __future__ import annotations

import argparse
import asyncio
import secrets
import signal
import subprocess
import sys
import threading
import time
import webbrowser
import json
import os
from pathlib import Path

import uvicorn

from .webapp import ServerSettings, make_app

_RELOAD_ENV_KEY = "SSHLER_RELOAD_SETTINGS"


def _reload_app():
    """Factory used by uvicorn --reload to rebuild the app with stored settings."""
    raw = os.environ.get(_RELOAD_ENV_KEY)
    if not raw:
        raise RuntimeError("Reload settings not initialised")
    payload = json.loads(raw)
    settings = ServerSettings(
        allow_origins=payload.get("allow_origins") or [],
        csrf_token=payload.get("csrf_token"),
        max_upload_bytes=payload.get("max_upload_bytes", 50 * 1024 * 1024),
        allow_ssh_alias=payload.get("allow_ssh_alias", True),
        basic_auth=tuple(payload["basic_auth"]) if payload.get("basic_auth") else None,
    )
    return make_app(settings)


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


def _start_vite_dev_server(frontend_dir: Path) -> subprocess.Popen:
    """Start the Vite development server for the frontend."""
    if not frontend_dir.exists():
        raise RuntimeError(f"Frontend directory not found: {frontend_dir}")
    
    package_json = frontend_dir / "package.json"
    if not package_json.exists():
        raise RuntimeError(f"package.json not found in {frontend_dir}")
    
    # Check if pnpm is available, fall back to npm
    try:
        subprocess.run(["pnpm", "--version"], capture_output=True, check=True)
        cmd = ["pnpm", "dev"]
    except (subprocess.CalledProcessError, FileNotFoundError):
        cmd = ["npm", "run", "dev"]
    
    print(f"[sshler] Starting Vite dev server: {' '.join(cmd)}")
    
    # Start Vite dev server
    process = subprocess.Popen(
        cmd,
        cwd=frontend_dir,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        universal_newlines=True,
        bufsize=1
    )
    
    return process


def _monitor_vite_process(process: subprocess.Popen) -> None:
    """Monitor Vite process output and print relevant messages."""
    if not process.stdout:
        return
        
    for line in iter(process.stdout.readline, ''):
        if line.strip():
            # Filter and format Vite output
            if any(keyword in line.lower() for keyword in ['local:', 'ready', 'hmr', 'error']):
                print(f"[vite] {line.strip()}")


def _cleanup_processes(*processes: subprocess.Popen) -> None:
    """Gracefully terminate processes."""
    for process in processes:
        if process.poll() is None:  # Process is still running
            try:
                process.terminate()
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()


def serve_dev(
    host: str = "127.0.0.1",
    port: int = 8822,
    allow_origins: list[str] | None = None,
    basic_auth: tuple[str, str] | None = None,
    max_upload_mb: int = 50,
    allow_ssh_alias: bool = True,
    log_level: str = "info",
    open_browser: bool = True,
    token: str | None = None,
) -> None:
    """Start both FastAPI and Vite dev servers for development.
    
    English:
        Starts the FastAPI backend with auto-reload and the Vite frontend dev server
        concurrently. Provides hot module replacement for frontend changes and
        automatic restart for backend changes.
    
    日本語:
        FastAPI バックエンドを自動リロード付きで起動し、同時に Vite フロントエンド
        開発サーバーも起動します。フロントエンドの変更にはホットモジュール置換、
        バックエンドの変更には自動再起動を提供します。
    """
    
    # Find frontend directory
    current_dir = Path.cwd()
    frontend_dir = current_dir / "frontend"
    
    if not frontend_dir.exists():
        print("[sshler] Error: frontend/ directory not found")
        print("[sshler] Make sure you're running from the project root")
        sys.exit(1)
    
    # Start Vite dev server
    try:
        vite_process = _start_vite_dev_server(frontend_dir)
    except RuntimeError as e:
        print(f"[sshler] Error starting Vite: {e}")
        sys.exit(1)
    
    # Start monitoring Vite output in a separate thread
    vite_monitor_thread = threading.Thread(
        target=_monitor_vite_process, 
        args=(vite_process,), 
        daemon=True
    )
    vite_monitor_thread.start()
    
    # Add localhost:5173 to allowed origins for Vite dev server
    dev_origins = (allow_origins or []) + ["http://localhost:5173", "http://127.0.0.1:5173"]
    
    # Setup signal handlers for graceful shutdown
    def signal_handler(signum, frame):
        print("\n[sshler] Shutting down development servers...")
        _cleanup_processes(vite_process)
        sys.exit(0)
    
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    # Wait a moment for Vite to start
    time.sleep(2)
    
    # Open browser to Vite dev server URL
    if open_browser:
        vite_url = "http://localhost:5173/app/"
        print(f"[sshler] Opening browser to {vite_url}")
        _open_browser_later(vite_url, delay=1.0)
    
    try:
        # Start FastAPI with reload
        serve(
            host=host,
            port=port,
            reload=True,  # Always enable reload in dev mode
            allow_origins=dev_origins,
            basic_auth=basic_auth,
            max_upload_mb=max_upload_mb,
            allow_ssh_alias=allow_ssh_alias,
            log_level=log_level,
            open_browser=False,  # We already opened to Vite
            token=token,
            ui="vue",  # Force Vue UI in dev mode
        )
    except KeyboardInterrupt:
        pass
    finally:
        _cleanup_processes(vite_process)


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
    ui: str = "vue",
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
        serve_spa=ui in {"both", "vue"},
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

    if reload:
        payload = {
            "allow_origins": settings.allow_origins,
            "csrf_token": settings.csrf_token,
            "max_upload_bytes": settings.max_upload_bytes,
            "allow_ssh_alias": settings.allow_ssh_alias,
            "basic_auth": list(settings.basic_auth) if settings.basic_auth else None,
        }
        os.environ[_RELOAD_ENV_KEY] = json.dumps(payload)
        uvicorn.run(
            "sshler.cli:_reload_app",
            host=host,
            port=port,
            reload=True,
            log_level=log_level,
            factory=True,
        )
    else:
        uvicorn.run(
            fastapi_application,
            host=host,
            port=port,
            reload=False,
            log_level=log_level,
        )


def fix_frontend():
    """Fix frontend issues by rebuilding and clearing cache."""
    print("Fixing frontend issues...")
    
    # Build frontend
    if not build_frontend():
        return False
    
    # Clear any cached files
    frontend_dir = Path(__file__).parent.parent / "frontend"
    dist_dir = Path(__file__).parent / "static" / "dist"
    
    if dist_dir.exists():
        print("Clearing dist cache...")
        import shutil
        try:
            shutil.rmtree(dist_dir)
        except Exception as e:
            print(f"Warning: Could not clear dist cache: {e}")
    
    # Rebuild
    if build_frontend():
        print("Frontend fixed! Hard refresh your browser (Ctrl+F5)")
        return True
    
    return False


def build_frontend():
    """Build the Vue frontend."""
    frontend_dir = Path(__file__).parent.parent / "frontend"
    if not frontend_dir.exists():
        print("Frontend directory not found")
        return False
    
    print("Building Vue frontend...")
    try:
        result = subprocess.run(
            ["pnpm", "build"],
            cwd=frontend_dir,
            check=True,
            capture_output=True,
            text=True
        )
        print("Frontend build completed successfully!")
        return True
    except subprocess.CalledProcessError as e:
        print(f"Frontend build failed: {e}")
        if e.stdout:
            print("STDOUT:", e.stdout)
        if e.stderr:
            print("STDERR:", e.stderr)
        return False
    except FileNotFoundError:
        print("pnpm not found. Please install pnpm first.")
        return False


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

    # Build command
    build_parser = subcommands.add_parser("build", help="Build the Vue frontend")
    build_parser.set_defaults(func=lambda: build_frontend())

    # Fix command
    fix_parser = subcommands.add_parser("fix", help="Fix frontend issues (rebuild + clear cache)")
    fix_parser.set_defaults(func=lambda: fix_frontend())

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
        "--dev",
        action="store_true",
        help="Start in development mode with both FastAPI and Vite dev servers"
    )
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
        "--ui",
        choices=["both", "vue", "legacy"],
        default="vue",
        help="Serve the Vue SPA, legacy templates, or both (default: vue)",
    )
    serve_parser.add_argument(
        "--no-browser",
        dest="open_browser",
        action="store_false",
        help="Do not automatically open a browser window",
    )
    serve_parser.set_defaults(open_browser=True)

    parsed_args = parser.parse_args()
    if parsed_args.command == "build":
        build_frontend()
    elif parsed_args.command == "fix":
        fix_frontend()
    elif parsed_args.command in (None, "serve"):
        bind_host = getattr(parsed_args, "bind", None) or getattr(parsed_args, "host", "127.0.0.1")
        basic_auth: tuple[str, str] | None = None
        auth_value = getattr(parsed_args, "auth", None)
        if auth_value:
            if ":" not in auth_value:
                parser.error("--auth must be in the form username:password")
            basic_auth = tuple(auth_value.split(":", 1))  # type: ignore[assignment]
        
        # Check if dev mode is requested
        if getattr(parsed_args, "dev", False):
            serve_dev(
                host=bind_host,
                port=getattr(parsed_args, "port", 8822),
                allow_origins=getattr(parsed_args, "allow_origins", []) or [],
                basic_auth=basic_auth,
                max_upload_mb=getattr(parsed_args, "max_upload_mb", 50),
                allow_ssh_alias=not getattr(parsed_args, "no_ssh_alias", False),
                log_level=getattr(parsed_args, "log_level", "info"),
                open_browser=getattr(parsed_args, "open_browser", True),
                token=getattr(parsed_args, "token", None),
            )
        else:
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
                ui=getattr(parsed_args, "ui", "both"),
            )
    else:
        parser.print_help()
