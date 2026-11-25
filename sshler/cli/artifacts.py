from __future__ import annotations

import argparse
import json
import sys
import webbrowser
from pathlib import Path
from urllib.parse import urlencode

from .client import _add_artifact_common_args, _artifact_request, _resolve_progress_url


def _artifact_catalog_url(args: argparse.Namespace, artifact_id: str) -> str:
    return f"{_resolve_progress_url(args)}/app/artifacts/{artifact_id}"


def _render_artifact(artifact: dict, args: argparse.Namespace) -> str:
    status = "ready" if artifact.get("exists") else "missing"
    lines = [
        f"ID:      {artifact.get('id', '')}",
        f"Title:   {artifact.get('title') or '(from HTML)'}",
        f"Project: {artifact.get('project_name', '')}",
        f"Group:   {artifact.get('group_path') or '(root)'}",
        f"Mode:    {artifact.get('mode', '')}",
        f"Status:  {status}",
        f"Source:  {artifact.get('source_path', '')}",
        f"Alias:   {artifact.get('alias_path') or '(none)'}",
        f"Mount:   {artifact.get('mount_path') or '(none)'}",
        f"Catalog: {_artifact_catalog_url(args, str(artifact.get('id', '')))}",
    ]
    return "\n".join(lines)


def artifact_add(args: argparse.Namespace) -> int:
    source = str(Path(args.path).expanduser().resolve())
    mode = "collection" if getattr(args, "discover", False) else args.mode
    body: dict = {
        "source_path": source,
        "mode": mode,
        "group_path": args.group or "",
    }
    for key in ("project", "entrypoint", "title", "slug", "mount_path"):
        value = getattr(args, key, None)
        if value is not None:
            body[key] = value
    code, data = _artifact_request(args, "POST", "/artifacts", body)
    if code or data is None:
        return code
    data["catalog_url"] = _artifact_catalog_url(args, data["id"])
    if args.json_output:
        print(json.dumps(data))
    else:
        verb = "Registered" if data.get("created") else "Already registered"
        print(f"[sshler] {verb.lower()} artifact\n{_render_artifact(data, args)}")
        if data.get("mode") != "file":
            print(
                "[sshler] scope: non-hidden regular files beneath the source "
                "directory can be served"
            )
    return 0


def artifact_list(args: argparse.Namespace) -> int:
    params: dict[str, str] = {}
    for argument, parameter in (
        ("project", "project"),
        ("group", "group"),
        ("mode", "mode"),
        ("query", "q"),
    ):
        value = getattr(args, argument, None)
        if value:
            params[parameter] = str(value)
    if getattr(args, "exists", None) is not None:
        params["exists"] = "true" if args.exists else "false"
    path = "/artifacts"
    if params:
        path = f"{path}?{urlencode(params)}"
    code, data = _artifact_request(args, "GET", path)
    if code or data is None:
        return code
    artifacts = data.get("artifacts", [])
    payload = {"artifacts": artifacts}
    if args.json_output:
        print(json.dumps(payload))
        return 0
    if not artifacts:
        print("(no artifacts)")
        return 0
    for artifact in artifacts:
        marker = "✓" if artifact.get("exists") else "!"
        group = artifact.get("group_path") or "(root)"
        title = artifact.get("title") or Path(artifact.get("source_path", "")).stem
        print(
            f"{marker} {artifact['id']}  {artifact['project_name']} / {group}  "
            f"{title} [{artifact['mode']}]"
        )
    return 0


def artifact_find(args: argparse.Namespace) -> int:
    return artifact_list(args)


def artifact_show(args: argparse.Namespace) -> int:
    code, data = _artifact_request(args, "GET", f"/artifacts/{args.id}")
    if code or data is None:
        return code
    page_code, pages = _artifact_request(args, "GET", f"/artifacts/{args.id}/pages")
    if page_code == 0 and pages is not None:
        data["pages"] = pages.get("pages", [])
    data["catalog_url"] = _artifact_catalog_url(args, args.id)
    if args.json_output:
        print(json.dumps(data))
    else:
        print(_render_artifact(data, args))
        for page in data.get("pages", []):
            print(f"  - {page['title']}: {page['relative_path'] or '(entry)'}")
    return 0


def artifact_update(args: argparse.Namespace) -> int:
    body: dict = {}
    if args.path is not None:
        body["source_path"] = str(Path(args.path).expanduser().resolve())
    for key in (
        "project",
        "group_path",
        "mode",
        "entrypoint",
        "title",
        "slug",
        "mount_path",
    ):
        value = getattr(args, key, None)
        if value is not None:
            body["group_path" if key == "group_path" else key] = value
    if not body:
        print("[sshler] no artifact updates supplied", file=sys.stderr)
        return 2
    code, data = _artifact_request(args, "PATCH", f"/artifacts/{args.id}", body)
    if code or data is None:
        return code
    if args.json_output:
        print(json.dumps(data))
    else:
        print(f"[sshler] updated artifact\n{_render_artifact(data, args)}")
    return 0


def artifact_rescan(args: argparse.Namespace) -> int:
    code, data = _artifact_request(args, "POST", f"/artifacts/{args.id}/rescan")
    if code or data is None:
        return code
    if args.json_output:
        print(json.dumps(data))
    else:
        print(f"[sshler] found {len(data.get('pages', []))} HTML page(s)")
    return 0


def artifact_remove(args: argparse.Namespace) -> int:
    code, data = _artifact_request(args, "DELETE", f"/artifacts/{args.id}")
    if code or data is None:
        return code
    if args.json_output:
        print(json.dumps(data))
    elif data.get("removed"):
        print("[sshler] unregistered artifact; files on disk were not changed")
    else:
        print("[sshler] artifact did not exist (no-op)")
    return 0


def artifact_open(args: argparse.Namespace) -> int:
    code, data = _artifact_request(args, "GET", f"/artifacts/{args.id}")
    if code or data is None:
        return code
    url = _artifact_catalog_url(args, args.id)
    if args.json_output:
        print(json.dumps({"id": args.id, "catalog_url": url}))
    else:
        print(f"[sshler] opening {url}")
        webbrowser.open(url)
    return 0


def _add_artifact_filter_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--project", help="Filter by project name or slug")
    parser.add_argument("--group", help="Filter by group and its descendants")
    parser.add_argument("--mode", choices=["file", "site", "collection"], help="Filter by mode")
    presence = parser.add_mutually_exclusive_group()
    presence.add_argument("--exists", dest="exists", action="store_true", default=None)
    presence.add_argument("--missing", dest="exists", action="store_false")


def add_artifact_parsers(
    subcommands: argparse._SubParsersAction[argparse.ArgumentParser],
) -> argparse.ArgumentParser:
    artifact_parser = subcommands.add_parser(
        "artifact", help="Register and manage local HTML artifacts"
    )
    artifact_sub = artifact_parser.add_subparsers(dest="artifact_command")

    artifact_add_parser = artifact_sub.add_parser("add", help="Register a file or directory")
    artifact_add_parser.add_argument("path")
    artifact_add_parser.add_argument("--project")
    artifact_add_parser.add_argument("--group")
    artifact_add_parser.add_argument(
        "--mode",
        default="auto",
        choices=["auto", "file", "site", "collection"],
    )
    artifact_add_parser.add_argument(
        "--discover",
        action="store_true",
        help="Register a directory as a recursively discovered collection",
    )
    artifact_add_parser.add_argument("--entry", dest="entrypoint")
    artifact_add_parser.add_argument("--title")
    artifact_add_parser.add_argument("--slug", help="Stable URL alias within the project")
    artifact_add_parser.add_argument(
        "--mount",
        dest="mount_path",
        help="Optional root-relative URL mount, such as published/reports",
    )
    _add_artifact_common_args(artifact_add_parser)

    artifact_list_parser = artifact_sub.add_parser("list", help="List artifacts")
    _add_artifact_filter_args(artifact_list_parser)
    _add_artifact_common_args(artifact_list_parser)

    artifact_find_parser = artifact_sub.add_parser(
        "find", help="Search titles, paths, projects, groups, and discovered pages"
    )
    artifact_find_parser.add_argument("query")
    _add_artifact_filter_args(artifact_find_parser)
    _add_artifact_common_args(artifact_find_parser)

    artifact_show_parser = artifact_sub.add_parser("show", help="Show one artifact")
    artifact_show_parser.add_argument("id")
    _add_artifact_common_args(artifact_show_parser)

    artifact_update_parser = artifact_sub.add_parser("update", help="Update an artifact")
    artifact_update_parser.add_argument("id")
    artifact_update_parser.add_argument("--source", dest="path")
    artifact_update_parser.add_argument("--project")
    artifact_update_parser.add_argument("--group", dest="group_path")
    artifact_update_parser.add_argument("--mode", choices=["file", "site", "collection"])
    artifact_update_parser.add_argument("--entry", dest="entrypoint")
    artifact_update_parser.add_argument("--title")
    artifact_update_parser.add_argument("--slug", help="Stable URL alias within the project")
    artifact_update_parser.add_argument(
        "--mount",
        dest="mount_path",
        help="Root-relative URL mount; pass an empty value to clear it",
    )
    _add_artifact_common_args(artifact_update_parser)

    artifact_rescan_parser = artifact_sub.add_parser("rescan", help="Refresh discovered pages")
    artifact_rescan_parser.add_argument("id")
    _add_artifact_common_args(artifact_rescan_parser)

    artifact_open_parser = artifact_sub.add_parser("open", help="Open an artifact catalog page")
    artifact_open_parser.add_argument("id")
    _add_artifact_common_args(artifact_open_parser)

    artifact_remove_parser = artifact_sub.add_parser("remove", help="Unregister an artifact")
    artifact_remove_parser.add_argument("id")
    _add_artifact_common_args(artifact_remove_parser)
    return artifact_parser
