from __future__ import annotations

import argparse
import os
import sys

from . import runtime_token

_DEFAULT_PROGRESS_URL = "http://127.0.0.1:8822"


def _resolve_progress_url(args: argparse.Namespace) -> str:
    """Pick the sshler base URL while preserving the progress-specific env var."""
    url = (
        getattr(args, "url", None)
        or os.environ.get("SSHLER_URL")
        or os.environ.get("SSHLER_PROGRESS_URL")
    )
    return (url or _DEFAULT_PROGRESS_URL).rstrip("/")


def _resolve_progress_token(args: argparse.Namespace) -> str:
    """Pick the auth token: --token > $SSHLER_TOKEN > runtime-token cache.

    Exits 2 with a helpful message if nothing is found.
    """
    token = getattr(args, "token", None) or os.environ.get("SSHLER_TOKEN")
    if not token:
        token = runtime_token._read_runtime_token()
    if not token:
        print(
            "[sshler] no token found. Pass --token, set $SSHLER_TOKEN, "
            "or run `sshler serve` first to populate the local token cache.",
            file=sys.stderr,
        )
        sys.exit(2)
    return token


def _add_progress_common_args(p: argparse.ArgumentParser) -> None:
    p.add_argument(
        "--url",
        help="sshler base URL (default: $SSHLER_PROGRESS_URL or http://127.0.0.1:8822)",
    )
    p.add_argument(
        "--token",
        help="X-SSHLER-TOKEN value (default: $SSHLER_TOKEN or runtime-token cache)",
    )
    p.add_argument(
        "--json",
        dest="json_output",
        action="store_true",
        help="Emit raw JSON instead of pretty output",
    )


def _artifact_request(
    args: argparse.Namespace,
    method: str,
    path: str,
    body: dict | None = None,
) -> tuple[int, dict | None]:
    import httpx

    url = _resolve_progress_url(args)
    token = _resolve_progress_token(args)
    try:
        with httpx.Client(timeout=10.0) as client:
            response = client.request(
                method,
                f"{url}/api/v1{path}",
                headers={"X-SSHLER-TOKEN": token},
                json=body,
            )
    except httpx.HTTPError as exc:
        print(f"[sshler] HTTP error: {exc}", file=sys.stderr)
        return 1, None
    if response.status_code >= 400:
        try:
            detail = response.json().get("detail", response.text)
        except (ValueError, AttributeError):
            detail = response.text
        print(
            f"[sshler] artifact request failed: HTTP {response.status_code} {detail}",
            file=sys.stderr,
        )
        return 1, None
    return 0, response.json()


def _add_artifact_common_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--url",
        help="sshler base URL (default: $SSHLER_URL or http://127.0.0.1:8822)",
    )
    parser.add_argument(
        "--token",
        help="X-SSHLER-TOKEN value (default: $SSHLER_TOKEN or runtime-token cache)",
    )
    parser.add_argument(
        "--json",
        dest="json_output",
        action="store_true",
        help="Emit raw JSON instead of pretty output",
    )
