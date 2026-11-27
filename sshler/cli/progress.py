from __future__ import annotations

import argparse
import json
import re
import sys
import time

from .client import _add_progress_common_args, _resolve_progress_token, _resolve_progress_url

_PROGRESS_NAME_RE = re.compile(r"^[A-Za-z0-9._:-]{1,64}$")
_PROGRESS_STATUSES = ("running", "done", "failed", "cancelled")


def _format_relative_time(updated_at: float) -> str:
    delta = max(0.0, time.time() - updated_at)
    if delta < 60:
        return f"{int(delta)}s ago"
    if delta < 3600:
        return f"{int(delta / 60)}m ago"
    if delta < 86400:
        return f"{int(delta / 3600)}h ago"
    return f"{int(delta / 86400)}d ago"


def _truncate(text: str, width: int) -> str:
    if len(text) <= width:
        return text.ljust(width)
    return (text[: max(0, width - 1)] + "…").ljust(width)


def _render_progress_table(bars: list[dict]) -> str:
    if not bars:
        return "(no progress bars)"
    header = (
        f"{_truncate('NAME', 20)} {_truncate('STATUS', 10)} "
        f"{_truncate('PROGRESS', 18)} {_truncate('UPDATED', 12)} LABEL"
    )
    lines = [header]
    for bar in bars:
        name = str(bar.get("name", ""))
        status = str(bar.get("status", ""))
        current = float(bar.get("current", 0))
        total = float(bar.get("total", 1))
        pct = int(current / total * 100) if total > 0 else 0  # floor, never round up
        progress = f"{current:g}/{total:g} ({pct}%)"
        updated = _format_relative_time(float(bar.get("updated_at", 0)))
        label = str(bar.get("label") or "")
        lines.append(
            f"{_truncate(name, 20)} {_truncate(status, 10)} "
            f"{_truncate(progress, 18)} {_truncate(updated, 12)} {label}"
        )
    return "\n".join(lines)


def progress_push(args: argparse.Namespace) -> int:
    import httpx

    name = args.name
    if not _PROGRESS_NAME_RE.match(name):
        print(
            f"[sshler] invalid name '{name}': must match ^[A-Za-z0-9._:-]{{1,64}}$",
            file=sys.stderr,
        )
        return 2
    if args.status not in _PROGRESS_STATUSES:
        print(
            f"[sshler] invalid status '{args.status}': must be one of {list(_PROGRESS_STATUSES)}",
            file=sys.stderr,
        )
        return 2

    url = _resolve_progress_url(args)
    token = _resolve_progress_token(args)

    body: dict = {
        "current": args.current,
        "total": args.total,
        "status": args.status,
    }
    if args.color is not None:
        body["color"] = args.color
    if args.label is not None:
        body["label"] = args.label

    # Metadata: --clear-meta wins; else build from --meta-json (base) + --meta k=v
    # (overrides). A bad --meta-json is reported but does NOT block the push — the
    # bar still advances, just without the new metadata.
    if getattr(args, "clear_meta", False):
        body["metadata"] = {}
    else:
        meta: dict = {}
        if getattr(args, "meta_json", None):
            try:
                parsed = json.loads(args.meta_json)
                if isinstance(parsed, dict):
                    meta.update(parsed)
                else:
                    print(
                        "[sshler] --meta-json must be a JSON object; ignoring it",
                        file=sys.stderr,
                    )
            except json.JSONDecodeError as exc:
                print(
                    f"[sshler] invalid --meta-json ({exc}); pushing without it",
                    file=sys.stderr,
                )
        for pair in getattr(args, "meta", None) or []:
            key, sep, value = pair.partition("=")
            if not sep:
                print(
                    f"[sshler] ignoring malformed --meta '{pair}' (expected KEY=VALUE)",
                    file=sys.stderr,
                )
                continue
            meta[key] = value
        if meta:
            body["metadata"] = meta
    if getattr(args, "merge", False):
        body["merge"] = True

    try:
        with httpx.Client(timeout=5.0) as client:
            resp = client.post(
                f"{url}/api/v1/progress/{name}",
                headers={"X-SSHLER-TOKEN": token},
                json=body,
            )
    except httpx.HTTPError as exc:
        print(f"[sshler] HTTP error: {exc}", file=sys.stderr)
        return 1

    if resp.status_code != 200:
        print(
            f"[sshler] push failed: HTTP {resp.status_code} {resp.text}",
            file=sys.stderr,
        )
        return 1
    if getattr(args, "json_output", False):
        print(json.dumps(resp.json()))
    else:
        data = resp.json()
        pct = int(data["current"] / data["total"] * 100) if data["total"] > 0 else 0
        print(
            f"[sshler] {data['name']}: {data['current']:g}/{data['total']:g} "
            f"({pct}%) [{data['status']}]"
        )
    return 0


def progress_list(args: argparse.Namespace) -> int:
    import httpx

    url = _resolve_progress_url(args)
    token = _resolve_progress_token(args)
    try:
        with httpx.Client(timeout=5.0) as client:
            resp = client.get(
                f"{url}/api/v1/progress",
                headers={"X-SSHLER-TOKEN": token},
            )
    except httpx.HTTPError as exc:
        print(f"[sshler] HTTP error: {exc}", file=sys.stderr)
        return 1

    if resp.status_code != 200:
        print(
            f"[sshler] list failed: HTTP {resp.status_code} {resp.text}",
            file=sys.stderr,
        )
        return 1
    payload = resp.json()
    if getattr(args, "json_output", False):
        print(json.dumps(payload))
    else:
        print(_render_progress_table(payload.get("bars", [])))
    return 0


def progress_delete(args: argparse.Namespace) -> int:
    import httpx

    name = args.name
    if not _PROGRESS_NAME_RE.match(name):
        print(
            f"[sshler] invalid name '{name}': must match ^[A-Za-z0-9._:-]{{1,64}}$",
            file=sys.stderr,
        )
        return 2
    url = _resolve_progress_url(args)
    token = _resolve_progress_token(args)
    try:
        with httpx.Client(timeout=5.0) as client:
            resp = client.delete(
                f"{url}/api/v1/progress/{name}",
                headers={"X-SSHLER-TOKEN": token},
            )
    except httpx.HTTPError as exc:
        print(f"[sshler] HTTP error: {exc}", file=sys.stderr)
        return 1
    if resp.status_code != 200:
        print(
            f"[sshler] delete failed: HTTP {resp.status_code} {resp.text}",
            file=sys.stderr,
        )
        return 1
    data = resp.json()
    if getattr(args, "json_output", False):
        print(json.dumps(data))
    else:
        if data.get("removed"):
            print(f"[sshler] deleted '{name}'")
        else:
            print(f"[sshler] '{name}' did not exist (no-op)")
    return 0


def add_progress_parsers(
    subcommands: argparse._SubParsersAction[argparse.ArgumentParser],
) -> argparse.ArgumentParser:
    # Progress-bar pusher CLI
    progress_parser = subcommands.add_parser(
        "progress",
        help="Push or query global progress bars (see docs/skills/progress-bars/SKILL.md)",
    )
    progress_sub = progress_parser.add_subparsers(dest="progress_command")

    push_parser = progress_sub.add_parser("push", help="Upsert a progress bar")
    push_parser.add_argument("name", help="Bar name (regex: ^[A-Za-z0-9._:-]{1,64}$)")
    push_parser.add_argument("current", type=float, help="Current progress value")
    push_parser.add_argument("total", type=float, help="Total / target value")
    push_parser.add_argument("--color", default=None, help="Color hint (string)")
    push_parser.add_argument("--label", default=None, help="Human-readable label")
    push_parser.add_argument(
        "--status",
        default="running",
        choices=list(_PROGRESS_STATUSES),
        help="Status flag (default: running)",
    )
    push_parser.add_argument(
        "--meta",
        action="append",
        metavar="KEY=VALUE",
        help="Attach a metadata field (repeatable). String values.",
    )
    push_parser.add_argument(
        "--meta-json",
        default=None,
        help="Attach metadata as a raw JSON object (merged under --meta pairs)",
    )
    push_parser.add_argument(
        "--merge",
        action="store_true",
        help="Merge metadata into the existing bag instead of replacing it",
    )
    push_parser.add_argument(
        "--clear-meta",
        action="store_true",
        help="Clear all metadata on the bar (overrides --meta/--meta-json)",
    )
    _add_progress_common_args(push_parser)

    list_parser = progress_sub.add_parser("list", help="List all progress bars")
    _add_progress_common_args(list_parser)

    delete_parser = progress_sub.add_parser("delete", help="Delete a progress bar by name")
    delete_parser.add_argument("name", help="Bar name to delete")
    _add_progress_common_args(delete_parser)
    return progress_parser
