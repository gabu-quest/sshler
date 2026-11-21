from __future__ import annotations

import json
import os
import secrets
import shutil
import signal
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path

import uvicorn

from ..webapp import ServerSettings, make_app
from . import runtime_token

_RELOAD_ENV_KEY = "SSHLER_RELOAD_SETTINGS"
_PID_FILE = (
    Path(os.environ.get("TEMP", "/tmp")) / "sshler.pid"
    if sys.platform == "win32"
    else Path("/tmp/sshler.pid")
)


def _write_pid() -> None:
    """Write current PID to file."""
    _PID_FILE.write_text(str(os.getpid()))


def _read_pid() -> int | None:
    """Read PID from file, return None if not found or invalid."""
    if not _PID_FILE.exists():
        return None
    try:
        pid = int(_PID_FILE.read_text().strip())
        # Check if process exists
        os.kill(pid, 0)
        return pid
    except (ValueError, ProcessLookupError, PermissionError):
        return None


def _remove_pid() -> None:
    """Remove PID file."""
    try:
        _PID_FILE.unlink(missing_ok=True)
    except Exception:
        pass


def _get_sshler_pids() -> list[int]:
    """Find sshler processes by checking for uvicorn serving sshler."""
    pids = []
    try:
        result = subprocess.run(["pgrep", "-f", "uvicorn.*sshler"], capture_output=True, text=True)
        if result.returncode == 0:
            for line in result.stdout.strip().split("\n"):
                if line.strip():
                    pids.append(int(line.strip()))
    except Exception:
        pass
    return pids


def status() -> int:
    """Check if sshler is running.

    Returns:
        0 if running, 1 if not running
    """
    # First check PID file
    pid = _read_pid()
    if pid:
        print(f"sshler is running (PID: {pid})")
        return 0

    # Also check for any uvicorn processes serving sshler
    pids = _get_sshler_pids()
    if pids:
        print(f"sshler is running (PIDs: {', '.join(map(str, pids))})")
        return 0

    print("sshler is not running")
    return 1


def stop() -> int:
    """Stop sshler if running.

    Returns:
        0 if stopped successfully, 1 if not running, 2 if error
    """
    # First try PID file
    pid = _read_pid()
    if pid:
        try:
            print(f"Stopping sshler (PID: {pid})...")
            os.kill(pid, signal.SIGTERM)
            # Wait for process to terminate
            for _ in range(50):  # 5 seconds max
                time.sleep(0.1)
                try:
                    os.kill(pid, 0)
                except ProcessLookupError:
                    break
            _remove_pid()
            print("sshler stopped")
            return 0
        except ProcessLookupError:
            _remove_pid()
            print("sshler was not running (stale PID file removed)")
            return 1
        except PermissionError:
            print(f"Permission denied stopping PID {pid}")
            return 2

    # Try finding and stopping uvicorn processes
    pids = _get_sshler_pids()
    if not pids:
        print("sshler is not running")
        return 1

    print(f"Stopping sshler (PIDs: {', '.join(map(str, pids))})...")
    for pid in pids:
        try:
            os.kill(pid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            pass

    # Wait for processes to terminate
    time.sleep(1)
    remaining = _get_sshler_pids()
    if remaining:
        print(f"Force killing remaining processes: {remaining}")
        for pid in remaining:
            try:
                os.kill(pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass

    _remove_pid()
    print("sshler stopped")
    return 0


def restart() -> None:
    """Restart sshler - stop if running, then start."""
    print("Restarting sshler...")
    stop()
    time.sleep(1)

    # Re-exec with serve command
    # This replaces the current process
    os.execvp("sshler", ["sshler", "serve", "--no-browser"])


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
        serve_artifacts=payload.get("serve_artifacts", True),
        artifact_port=payload.get("artifact_port", 0),
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

    # Resolve the package manager to an absolute executable path. On Windows the
    # runnable is a shim (`pnpm.cmd` / `npm.cmd`), and subprocess.Popen without
    # shell=True won't resolve a bare `npm`/`pnpm` name to a `.cmd` file — it
    # fails with WinError 2. shutil.which() resolves the real path + extension
    # using PATHEXT, so the spawned command is something Popen can launch
    # directly on every platform.
    pnpm = shutil.which("pnpm")
    if pnpm is not None:
        try:
            subprocess.run([pnpm, "--version"], capture_output=True, check=True)
            cmd = [pnpm, "dev"]
        except (subprocess.CalledProcessError, OSError):
            pnpm = None
    if pnpm is None:
        npm = shutil.which("npm")
        if npm is None:
            raise RuntimeError(
                "Could not find 'pnpm' or 'npm' on PATH. Install Node.js / a package "
                "manager, or run the Vite dev server manually in the frontend directory."
            )
        cmd = [npm, "run", "dev"]

    print(f"[sshler] Starting Vite dev server: {' '.join(cmd)}")

    # Start Vite dev server
    process = subprocess.Popen(
        cmd,
        cwd=frontend_dir,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        universal_newlines=True,
        bufsize=1,
    )

    return process


def _monitor_vite_process(process: subprocess.Popen) -> None:
    """Monitor Vite process output and print relevant messages."""
    if not process.stdout:
        return

    for line in iter(process.stdout.readline, ""):
        if line.strip():
            # Filter and format Vite output
            if any(keyword in line.lower() for keyword in ["local:", "ready", "hmr", "error"]):
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
    serve_artifacts: bool = True,
    artifact_port: int = 0,
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
        target=_monitor_vite_process, args=(vite_process,), daemon=True
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
            serve_artifacts=serve_artifacts,
            artifact_port=artifact_port,
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
    serve_artifacts: bool = True,
    artifact_port: int = 0,
) -> None:
    """Start the sshler FastAPI application via uvicorn.

    English:
        Bootstraps the FastAPI app with the provided security settings and begins
        serving requests on the chosen host/port.

    日本語:
        指定されたセキュリティ設定で FastAPI アプリケーションを初期化し、
        指定したホストとポートでリクエスト受付を開始します。
    """

    # Auto-add the actual listening address to allowed origins
    # so origin checks pass regardless of custom port
    auto_origins = list(allow_origins or [])
    listen_origins = {
        f"http://{host}:{port}",
        f"http://localhost:{port}",
        f"http://127.0.0.1:{port}",
    }
    for o in listen_origins:
        if o not in auto_origins:
            auto_origins.append(o)

    csrf_token = token or secrets.token_urlsafe(32)
    settings = ServerSettings(
        allow_origins=auto_origins,
        csrf_token=csrf_token,
        max_upload_bytes=max_upload_mb * 1024 * 1024,
        allow_ssh_alias=allow_ssh_alias,
        basic_auth=basic_auth,
        serve_spa=True,
        serve_artifacts=serve_artifacts,
        artifact_port=artifact_port,
    )

    fastapi_application = make_app(settings)
    application_url = f"http://{host}:{port}"
    if open_browser and host in {"127.0.0.1", "localhost"}:
        _open_browser_later(application_url)

    # Write PID file for status/stop commands
    _write_pid()

    # Cache the active token so local `sshler progress` calls can find it
    # without needing the user to copy/paste from stdout into an env var.
    runtime_token._write_runtime_token(csrf_token)

    print(f"[sshler] listening on {application_url}")
    print(f"[sshler] X-SSHLER-TOKEN={settings.csrf_token}")
    if basic_auth:
        print(f"[sshler] Basic auth enabled for user '{basic_auth[0]}'")
    if settings.allow_origins:
        print(f"[sshler] Additional allowed origins: {', '.join(settings.allow_origins)}")

    try:
        if reload:
            payload = {
                "allow_origins": settings.allow_origins,
                "csrf_token": settings.csrf_token,
                "max_upload_bytes": settings.max_upload_bytes,
                "allow_ssh_alias": settings.allow_ssh_alias,
                "basic_auth": list(settings.basic_auth) if settings.basic_auth else None,
                "serve_artifacts": settings.serve_artifacts,
                "artifact_port": settings.artifact_port,
            }
            os.environ[_RELOAD_ENV_KEY] = json.dumps(payload)
            uvicorn.run(
                "sshler.cli.server:_reload_app",
                host=host,
                port=port,
                reload=True,
                log_level=log_level,
                factory=True,
                # Watch the installed package, not the cwd sshler happens to start in.
                reload_dirs=[str(Path(__file__).resolve().parent.parent)],
            )
        else:
            uvicorn.run(
                fastapi_application,
                host=host,
                port=port,
                reload=False,
                log_level=log_level,
            )
    finally:
        _remove_pid()


def fix_frontend():
    """Fix frontend issues by rebuilding and clearing cache."""
    print("Fixing frontend issues...")

    # Build frontend
    if not build_frontend():
        return False

    # Clear any cached files
    dist_dir = Path(__file__).parent.parent / "static" / "dist"

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
    frontend_dir = Path(__file__).parent.parent.parent / "frontend"
    if not frontend_dir.exists():
        print("Frontend directory not found")
        return False

    print("Building Vue frontend...")
    try:
        subprocess.run(
            ["pnpm", "build"], cwd=frontend_dir, check=True, capture_output=True, text=True
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
