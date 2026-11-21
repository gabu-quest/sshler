from __future__ import annotations

import argparse
import os
import sys
import time

from dotenv import load_dotenv

from .artifacts import (
    add_artifact_parsers,
    artifact_add,
    artifact_find,
    artifact_list,
    artifact_open,
    artifact_remove,
    artifact_rescan,
    artifact_show,
    artifact_update,
)
from .ping import add_ping_parser, ping_send
from .progress import add_progress_parsers, progress_delete, progress_list, progress_push


def _add_server_parsers(subcommands: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    # Build command
    subcommands.add_parser("build", help="Build the Vue frontend")

    # Fix command
    subcommands.add_parser("fix", help="Fix frontend issues (rebuild + clear cache)")

    # Hash-password command
    hash_parser = subcommands.add_parser(
        "hash-password", help="Generate Argon2 password hash for authentication"
    )
    hash_parser.add_argument(
        "--username", help="Username for authentication (will prompt if not provided)"
    )
    hash_parser.add_argument(
        "--no-env", action="store_true", help="Do not append hash to .env file (just display it)"
    )

    # Status command
    subcommands.add_parser("status", help="Check if sshler is running")

    # Stop command
    subcommands.add_parser("stop", help="Stop sshler if running")

    # Restart command
    subcommands.add_parser("restart", help="Restart sshler")


def _add_serve_parser(subcommands: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
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
        help="Start in development mode with both FastAPI and Vite dev servers",
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
        "--artifact-port",
        type=int,
        default=0,
        help="Loopback artifact sidecar port (default: choose an available port)",
    )
    serve_parser.add_argument(
        "--no-artifacts",
        action="store_true",
        help="Disable the local HTML artifact sidecar",
    )
    serve_parser.add_argument(
        "--no-browser",
        dest="open_browser",
        action="store_false",
        help="Do not automatically open a browser window",
    )
    serve_parser.set_defaults(open_browser=True)


def _run_server_command(parser: argparse.ArgumentParser, parsed_args: argparse.Namespace) -> None:
    from . import server

    if parsed_args.command == "build":
        server.build_frontend()
    elif parsed_args.command == "fix":
        server.fix_frontend()
    elif parsed_args.command == "hash-password":
        from .password import hash_password

        hash_password(
            username=getattr(parsed_args, "username", None),
            append_to_env=not getattr(parsed_args, "no_env", False),
        )
    elif parsed_args.command == "status":
        sys.exit(server.status())
    elif parsed_args.command == "stop":
        sys.exit(server.stop())
    elif parsed_args.command == "restart":
        server.restart()
    elif parsed_args.command in (None, "serve"):
        bind_host_value = getattr(parsed_args, "bind", None) or getattr(
            parsed_args, "host", "127.0.0.1"
        )
        bind_host: str = bind_host_value if bind_host_value is not None else "127.0.0.1"
        no_password = getattr(parsed_args, "no_password", False)
        basic_auth: tuple[str, str] | None = None
        auth_value = getattr(parsed_args, "auth", None)

        if auth_value:
            if ":" not in auth_value:
                parser.error("--auth must be in the form username:password")
            basic_auth = tuple(auth_value.split(":", 1))

        # Check if authentication is configured (from env vars or CLI)
        env_username = os.getenv("SSHLER_USERNAME")
        env_has_password = bool(os.getenv("SSHLER_PASSWORD_HASH") or os.getenv("SSHLER_PASSWORD"))
        has_auth = bool(basic_auth or (env_username and env_has_password))

        # Security validation
        if not has_auth and not no_password:
            # No auth configured - check if we're binding to a non-localhost interface
            if bind_host not in ("127.0.0.1", "localhost"):
                print("=" * 70, file=sys.stderr)
                print(
                    "SECURITY ERROR: Authentication required for non-localhost binding",
                    file=sys.stderr,
                )
                print("=" * 70, file=sys.stderr)
                print(f"\nYou are trying to bind to: {bind_host}", file=sys.stderr)
                print(
                    "This would expose sshler to your network without authentication!",
                    file=sys.stderr,
                )
                print("\nTo fix this, choose one of these options:", file=sys.stderr)
                print("\n1. Set up authentication with environment variables:", file=sys.stderr)
                print(
                    "   sshler hash-password  # This will guide you through setup", file=sys.stderr
                )
                print(
                    "\n2. Use --auth flag (not recommended - password visible in process list):",
                    file=sys.stderr,
                )
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
                print(
                    f"Binding to: {bind_host}:{getattr(parsed_args, 'port', 8822)}", file=sys.stderr
                )
                print("This is ONLY safe because you're bound to localhost.", file=sys.stderr)
                print("\nFor production deployments, set up authentication:", file=sys.stderr)
                print("  sshler hash-password", file=sys.stderr)
                print("=" * 70, file=sys.stderr)
                print()
        elif no_password and bind_host not in ("127.0.0.1", "localhost"):
            # --no-password with non-localhost - show big warning
            print("=" * 70, file=sys.stderr)
            print(
                "⚠️  SECURITY WARNING: Running without authentication on network interface!",
                file=sys.stderr,
            )
            print("=" * 70, file=sys.stderr)
            print(f"Binding to: {bind_host}:{getattr(parsed_args, 'port', 8822)}", file=sys.stderr)
            print("Anyone on your network can access this sshler instance!", file=sys.stderr)
            print(
                "\nThis is EXTREMELY UNSAFE and should ONLY be used for testing.", file=sys.stderr
            )
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
            server.serve_dev(
                host=bind_host,
                port=getattr(parsed_args, "port", 8822),
                allow_origins=getattr(parsed_args, "allow_origins", []) or [],
                basic_auth=basic_auth,
                max_upload_mb=getattr(parsed_args, "max_upload_mb", 50),
                allow_ssh_alias=not getattr(parsed_args, "no_ssh_alias", False),
                log_level=getattr(parsed_args, "log_level", "info"),
                open_browser=getattr(parsed_args, "open_browser", True),
                token=getattr(parsed_args, "token", None),
                serve_artifacts=not getattr(parsed_args, "no_artifacts", False),
                artifact_port=getattr(parsed_args, "artifact_port", 0),
            )
        else:
            server.serve(
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
                serve_artifacts=not getattr(parsed_args, "no_artifacts", False),
                artifact_port=getattr(parsed_args, "artifact_port", 0),
            )
    else:
        parser.print_help()


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

    _add_server_parsers(subcommands)
    progress_parser = add_progress_parsers(subcommands)
    artifact_parser = add_artifact_parsers(subcommands)
    add_ping_parser(subcommands)
    _add_serve_parser(subcommands)

    parsed_args = parser.parse_args()
    if parsed_args.command == "ping":
        sys.exit(ping_send(parsed_args))
    elif parsed_args.command == "progress":
        sub = getattr(parsed_args, "progress_command", None)
        if sub == "push":
            sys.exit(progress_push(parsed_args))
        elif sub == "list":
            sys.exit(progress_list(parsed_args))
        elif sub == "delete":
            sys.exit(progress_delete(parsed_args))
        else:
            progress_parser.print_help()
            sys.exit(1)
    elif parsed_args.command == "artifact":
        sub = getattr(parsed_args, "artifact_command", None)
        handlers = {
            "add": artifact_add,
            "list": artifact_list,
            "find": artifact_find,
            "show": artifact_show,
            "update": artifact_update,
            "rescan": artifact_rescan,
            "open": artifact_open,
            "remove": artifact_remove,
        }
        handler = handlers.get(sub) if isinstance(sub, str) else None
        if handler is None:
            artifact_parser.print_help()
            sys.exit(1)
        sys.exit(handler(parsed_args))
    else:
        _run_server_command(parser, parsed_args)
