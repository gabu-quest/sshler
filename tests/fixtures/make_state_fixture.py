# Needs sqler 1.2025.9.24.2 to write; the fixture was made with it.
"""Fill a state database through sshler's own state functions (argv[1] = config folder),
then print what a reader sees. Run once with the old sqler to write, again to read."""

import json
import sys
from pathlib import Path

from sshler import state

cfg = Path(sys.argv[1])
mode = sys.argv[2]
state.initialize(cfg)
if mode == "write":
    state.replace_favorites("local", ["/srv/a", "/srv/b", "/srv/名前"])
    state.toggle_favorite("web", "/var/www")
    s = state.create_or_update_session("local", "alpha", "/srv/a", {"k": [1, 2.5, None]})
    state.update_session_snapshot(s.id, [{"index": 0, "name": "zsh", "cwd": "/srv/a"}])
    state.create_or_update_session("web", "beta", "/var/www")
    for _ in range(3):
        state.record_directory_visit("local", "/srv/a")
    state.record_directory_visit("local", "/srv/b")
    state.save_layout("two", [{"box": "local", "dir": "/srv/a"}, {"box": "web", "dir": "/var/www"}])
    state.create_snippet("local", "list", "ls -la", "files")
    state.upsert_progress(
        "job.one", 3, 10, color="blue", label="Job", apply_metadata=True, metadata={"a": 1}
    )
    state.save_diff_notebook("nb", json.dumps({"v": 1, "cells": []}), 0)
    p, _ = state.get_or_create_artifact_project("My Project")
    state.create_artifact_registration(
        project_id=p.id,
        group_path="g",
        source_path="/srv/a/index.html",
        mode="file",
        entrypoint=None,
        title_override="T",
    )


def dump(rows):
    return sorted(
        json.dumps(r.model_dump(), sort_keys=True, default=str, ensure_ascii=False) for r in rows
    )


out = {
    "favorites": {b: state.list_favorites(b) for b in ("local", "web")},
    "sessions": dump(state.list_sessions("local") + state.list_sessions("web")),
    "dirs": state.search_directories("local", "srv"),
    "layouts": dump(state.list_layouts()),
    "snippets": dump(state.list_snippets("local")),
    "progress": dump(state.list_progress()),
    "notebooks": dump(state.list_diff_notebooks()),
    "projects": dump(state.list_artifact_projects()),
    "registrations": dump(state.list_artifact_registrations()),
}
if mode == "touch":
    # An ordinary write to a row the old version wrote.
    s = state.get_session_by_name("local", "alpha")
    print(
        "rename:",
        state.rename_session(s.id, "alpha2"),
        "toggle:",
        state.toggle_favorite("local", "/srv/b"),
    )
    print("progress:", state.upsert_progress("job.one", 4, 10).current)
print(json.dumps(out, indent=1, ensure_ascii=False, default=str))
