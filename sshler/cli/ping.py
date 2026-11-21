from __future__ import annotations

import argparse
import json
import sys

from .client import _resolve_progress_token, _resolve_progress_url


def ping_send(args: argparse.Namespace) -> int:
    import httpx

    url = _resolve_progress_url(args)
    token = _resolve_progress_token(args)

    body: dict = {"title": args.title}
    if args.body is not None:
        body["body"] = args.body
    if args.color is not None:
        body["color"] = args.color
    if args.icon is not None:
        body["icon"] = args.icon
    if args.duration is not None:
        body["duration"] = args.duration
    if args.source is not None:
        body["source"] = args.source
    if args.metadata is not None:
        try:
            parsed = json.loads(args.metadata)
            if isinstance(parsed, dict):
                body["metadata"] = parsed
            else:
                print("[sshler] --metadata must be a JSON object; ignoring it", file=sys.stderr)
        except json.JSONDecodeError as exc:
            print(f"[sshler] invalid --metadata ({exc}); sending without it", file=sys.stderr)

    try:
        with httpx.Client(timeout=5.0) as client:
            resp = client.post(
                f"{url}/api/v1/ping",
                headers={"X-SSHLER-TOKEN": token},
                json=body,
            )
    except httpx.HTTPError as exc:
        print(f"[sshler] HTTP error: {exc}", file=sys.stderr)
        return 1

    if resp.status_code != 200:
        print(
            f"[sshler] ping failed: HTTP {resp.status_code} {resp.text}",
            file=sys.stderr,
        )
        return 1

    data = resp.json()
    print(f"[sshler] ping sent (id: {data['id']})")
    return 0


def add_ping_parser(subcommands: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    # Ping notification sender CLI
    ping_parser = subcommands.add_parser(
        "ping",
        help="Push a toast notification to all connected browsers",
    )
    ping_parser.add_argument("--title", required=True, help="Notification title")
    ping_parser.add_argument("--body", default=None, help="Notification body text")
    ping_parser.add_argument(
        "--color",
        default=None,
        choices=["success", "warning", "error", "info"],
        help="Notification color type",
    )
    ping_parser.add_argument(
        "--icon", default=None, help="Emoji shown as notification avatar (e.g. 🚀)"
    )
    ping_parser.add_argument(
        "--duration",
        type=int,
        default=None,
        metavar="MS",
        help="Auto-dismiss after this many milliseconds (omit for manual dismiss)",
    )
    ping_parser.add_argument(
        "--source", default=None, help="Label identifying the sender (e.g. deploy-bot)"
    )
    ping_parser.add_argument(
        "--metadata",
        default=None,
        metavar="JSON",
        help="Extra metadata as a JSON object string",
    )
    ping_parser.add_argument(
        "--url",
        help="sshler base URL (default: $SSHLER_PROGRESS_URL or http://127.0.0.1:8822)",
    )
    ping_parser.add_argument(
        "--token",
        help="X-SSHLER-TOKEN value (default: $SSHLER_TOKEN or runtime-token cache)",
    )
