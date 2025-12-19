from __future__ import annotations

import argparse
import asyncio
import getpass
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
from dotenv import load_dotenv

from .auth import PasswordHasher, PasswordValidator, PasswordPolicy
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


def hash_password(username: str | None = None, append_to_env: bool = True) -> None:
    """Generate an Argon2 password hash for sshler authentication.

    Prompts for password securely, validates it, generates hash, and optionally
    appends the configuration to .env file.

    Args:
        username: Username for authentication (prompts if None)
        append_to_env: If True, append hash to .env file

    Returns:
        None
    """
    print("=" * 70)
    print("sshler Password Hash Generator")
    print("=" * 70)
    print()

    # Get username
    if not username:
        username = input("Username: ").strip()
        if not username:
            print("Error: Username cannot be empty")
            sys.exit(1)

    # Get password securely
    password = getpass.getpass("Password: ")
    password_confirm = getpass.getpass("Confirm password: ")

    if password != password_confirm:
        print("Error: Passwords do not match")
        sys.exit(1)

    # Validate password
    policy = PasswordPolicy()
    is_valid, errors = PasswordValidator.validate(password, username, policy)

    if not is_valid:
        print("\nError: Password does not meet security requirements:")
        for error in errors:
          print(f"  - {error}")
        print("\nPassword requirements:")
        print(f"  - At least {policy.min_length} characters long")
        print("  - At least 1 uppercase letter (A-Z)")
        print("  - At least 1 lowercase letter (a-z)")
        print("  - At least 1 digit (0-9)")
        print(f"  - At least 1 special character ({policy.special_chars})")
        print("  - Cannot be a commonly used password")
        print("\nGenerate a strong password:")
        print("  openssl rand -base64 18")
        print("  Or use a password manager like 1Password, Bitwarden, etc.")
        sys.exit(1)

    # Hash the password
    print("\nGenerating Argon2 hash...")
    hasher = PasswordHasher()
    password_hash = hasher.hash(password)

    # Display results
    print("\n" + "=" * 70)
    print("Hash generated successfully!")
    print("=" * 70)
    print(f"\nUsername: {username}")
    print(f"Password Hash:\n{password_hash}")
    print()

    # Append to .env if requested
    if append_to_env:
        env_file = Path.cwd() / ".env"

        try:
            # Check if .env exists, create if not
            if not env_file.exists():
                print(f"Creating new .env file at {env_file}")
                env_file.touch(mode=0o600)  # Create with restricted permissions

            # Read existing content
            existing_content = env_file.read_text() if env_file.exists() else ""

            # Check if auth variables already exist
            has_username = "SSHLER_USERNAME=" in existing_content
            has_password = "SSHLER_PASSWORD=" in existing_content
            has_hash = "SSHLER_PASSWORD_HASH=" in existing_content

            # Prepare new content
            new_lines = []

            if has_username or has_password or has_hash:
                print("\nWarning: Existing auth configuration found in .env")
                response = input("Replace existing auth configuration? [y/N]: ").strip().lower()

                if response != "y":
                    print("\nNot modifying .env file.")
                    print("\nManually add these lines to your .env file:")
                    print(f"SSHLER_USERNAME={username}")
                    print(f"SSHLER_PASSWORD_HASH={password_hash}")
                    return

                # Remove existing auth lines
                for line in existing_content.splitlines():
                    if not any(line.startswith(prefix) for prefix in [
                        "SSHLER_USERNAME=",
                        "SSHLER_PASSWORD=",
                        "SSHLER_PASSWORD_HASH="
                    ]):
                        new_lines.append(line)
            else:
                new_lines = existing_content.splitlines() if existing_content else []

            # Add authentication section
            if new_lines and new_lines[-1].strip():  # Add blank line if needed
                new_lines.append("")

            new_lines.extend([
                "# Authentication (generated by sshler hash-password)",
                f"SSHLER_USERNAME={username}",
                f"SSHLER_PASSWORD_HASH={password_hash}",
            ])

            # Write back to file
            env_file.write_text("\n".join(new_lines) + "\n")

            # Set restrictive permissions
            env_file.chmod(0o600)

            print(f"\n✓ Authentication configuration added to {env_file}")
            print("✓ File permissions set to 0600 (owner read/write only)")
            print("\nYou can now start sshler with:")
            print("  sshler serve")

        except (IOError, PermissionError) as e:
            print(f"\nWarning: Could not write to .env file: {e}")
            print("\nManually add these lines to your .env file:")
            print(f"SSHLER_USERNAME={username}")
            print(f"SSHLER_PASSWORD_HASH={password_hash}")
    else:
        print("\nAdd these lines to your .env file:")
        print(f"SSHLER_USERNAME={username}")
        print(f"SSHLER_PASSWORD_HASH={password_hash}")


def main() -> None:
    """Parse CLI arguments and invoke the requested subcommand.

    English:
        Handles ``sshler`` command-line parsing and dispatches to ``serve`` when
        no subcommand is explicitly provided.

    日本語:
        ``sshler`` のコマンドライン引数を解析し、サブコマンドが指定されて
        いない場合は ``serve`` を実行します。
    """

    # Load .env file from current directory or parent directories
    load_dotenv()

    parser = argparse.ArgumentParser(prog="sshler", description="Local SSH tmux-in-browser")
    subcommands = parser.add_subparsers(dest="command")

    # Build command
    build_parser = subcommands.add_parser("build", help="Build the Vue frontend")
    build_parser.set_defaults(func=lambda: build_frontend())

    # Fix command
    fix_parser = subcommands.add_parser("fix", help="Fix frontend issues (rebuild + clear cache)")
    fix_parser.set_defaults(func=lambda: fix_frontend())

    # Hash-password command
    hash_parser = subcommands.add_parser(
        "hash-password",
        help="Generate Argon2 password hash for authentication"
    )
    hash_parser.add_argument(
        "--username",
        help="Username for authentication (will prompt if not provided)"
    )
    hash_parser.add_argument(
        "--no-env",
        action="store_true",
        help="Do not append hash to .env file (just display it)"
    )
    hash_parser.set_defaults(func=lambda args: hash_password(
        username=getattr(args, "username", None),
        append_to_env=not getattr(args, "no_env", False)
    ))

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
        "--no-password",
        action="store_true",
        help="Disable authentication (UNSAFE - only for testing on localhost)",
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
    elif parsed_args.command == "hash-password":
        hash_password(
            username=getattr(parsed_args, "username", None),
            append_to_env=not getattr(parsed_args, "no_env", False)
        )
    elif parsed_args.command in (None, "serve"):
        bind_host = getattr(parsed_args, "bind", None) or getattr(parsed_args, "host", "127.0.0.1")
        no_password = getattr(parsed_args, "no_password", False)
        basic_auth: tuple[str, str] | None = None
        auth_value = getattr(parsed_args, "auth", None)

        if auth_value:
            if ":" not in auth_value:
                parser.error("--auth must be in the form username:password")
            basic_auth = tuple(auth_value.split(":", 1))  # type: ignore[assignment]

        # Check if authentication is configured (from env vars or CLI)
        env_username = os.getenv("SSHLER_USERNAME")
        env_has_password = bool(os.getenv("SSHLER_PASSWORD_HASH") or os.getenv("SSHLER_PASSWORD"))
        has_auth = bool(basic_auth or (env_username and env_has_password))

        # Security validation
        if not has_auth and not no_password:
            # No auth configured - check if we're binding to a non-localhost interface
            if bind_host not in ("127.0.0.1", "localhost"):
                print("=" * 70, file=sys.stderr)
                print("SECURITY ERROR: Authentication required for non-localhost binding", file=sys.stderr)
                print("=" * 70, file=sys.stderr)
                print(f"\nYou are trying to bind to: {bind_host}", file=sys.stderr)
                print("This would expose sshler to your network without authentication!", file=sys.stderr)
                print("\nTo fix this, choose one of these options:", file=sys.stderr)
                print("\n1. Set up authentication with environment variables:", file=sys.stderr)
                print("   sshler hash-password  # This will guide you through setup", file=sys.stderr)
                print("\n2. Use --auth flag (not recommended - password visible in process list):", file=sys.stderr)
                print("   sshler serve --host 0.0.0.0 --auth username:password", file=sys.stderr)
                print("\n3. Use --no-password flag (UNSAFE - only for testing):", file=sys.stderr)
                print("   sshler serve --host 0.0.0.0 --no-password", file=sys.stderr)
                print("\n4. Bind to localhost only (no auth required):", file=sys.stderr)
                print("   sshler serve --host 127.0.0.1", file=sys.stderr)
                print("\n" + "=" * 70, file=sys.stderr)
                sys.exit(1)
            else:
                # Localhost without auth - show warning
                print("=" * 70, file=sys.stderr)
                print("⚠️  WARNING: Running without authentication", file=sys.stderr)
                print("=" * 70, file=sys.stderr)
                print(f"Binding to: {bind_host}:{getattr(parsed_args, 'port', 8822)}", file=sys.stderr)
                print("This is ONLY safe because you're bound to localhost.", file=sys.stderr)
                print("\nFor production deployments, set up authentication:", file=sys.stderr)
                print("  sshler hash-password", file=sys.stderr)
                print("=" * 70, file=sys.stderr)
                print()
        elif no_password and bind_host not in ("127.0.0.1", "localhost"):
            # --no-password with non-localhost - show big warning
            print("=" * 70, file=sys.stderr)
            print("⚠️  SECURITY WARNING: Running without authentication on network interface!", file=sys.stderr)
            print("=" * 70, file=sys.stderr)
            print(f"Binding to: {bind_host}:{getattr(parsed_args, 'port', 8822)}", file=sys.stderr)
            print("Anyone on your network can access this sshler instance!", file=sys.stderr)
            print("\nThis is EXTREMELY UNSAFE and should ONLY be used for testing.", file=sys.stderr)
            print("Press Ctrl+C now to cancel, or wait 5 seconds to continue...", file=sys.stderr)
            print("=" * 70, file=sys.stderr)
            try:
                time.sleep(5)
            except KeyboardInterrupt:
                print("\nCancelled.", file=sys.stderr)
                sys.exit(0)

        # Override auth if --no-password is set
        if no_password:
            basic_auth = None
            # Also need to temporarily clear env vars for this run
            os.environ.pop("SSHLER_USERNAME", None)
            os.environ.pop("SSHLER_PASSWORD", None)
            os.environ.pop("SSHLER_PASSWORD_HASH", None)

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
