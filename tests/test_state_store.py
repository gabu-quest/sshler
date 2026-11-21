"""The state store: its async names, its indexes, two folders in one process, and a database
written by the release before the current sqler."""

from __future__ import annotations

import asyncio
import inspect
import sqlite3
import warnings
from collections.abc import Iterator
from pathlib import Path

import pytest

from sshler import state

FIXTURE = Path(__file__).parent / "fixtures" / "state_written_by_sqler_1_2025_9_24.sql"

ASYNC_NAMES = [
    "cleanup_old_sessions_async",
    "clear_session_snapshot_async",
    "create_artifact_registration_async",
    "create_or_update_session_async",
    "create_session_async",
    "create_snippet_async",
    "delete_artifact_project_async",
    "delete_artifact_registration_async",
    "delete_diff_notebook_async",
    "delete_layout_async",
    "delete_progress_async",
    "delete_session_async",
    "delete_snippet_async",
    "find_artifact_registration_async",
    "get_artifact_project_async",
    "get_artifact_registration_async",
    "get_diff_notebook_async",
    "get_or_create_artifact_project_async",
    "get_progress_async",
    "get_session_by_id_async",
    "get_session_by_name_async",
    "list_all_active_sessions_async",
    "list_all_sessions_async",
    "list_all_snapshotted_sessions_async",
    "list_artifact_projects_async",
    "list_artifact_registrations_async",
    "list_diff_notebooks_async",
    "list_favorites_async",
    "list_layouts_async",
    "list_progress_async",
    "list_sessions_async",
    "list_snippets_async",
    "purge_stale_snapshots_async",
    "record_directory_visit_async",
    "rename_artifact_project_async",
    "rename_session_async",
    "replace_favorites_async",
    "save_diff_notebook_async",
    "save_layout_async",
    "search_directories_async",
    "toggle_favorite_async",
    "update_artifact_registration_async",
    "update_session_activity_async",
    "update_session_metadata_async",
    "update_session_snapshot_async",
    "update_snippet_async",
    "upsert_progress_async",
]

INDEX_ROWS = [
    ("idx_artifact_projects_id", "artifact_projects"),
    ("idx_artifact_projects_normalized_name", "artifact_projects"),
    ("idx_artifact_projects_slug", "artifact_projects"),
    ("idx_artifact_registrations_id", "artifact_registrations"),
    ("idx_artifact_registrations_mount_path", "artifact_registrations"),
    ("idx_artifact_registrations_project_id", "artifact_registrations"),
    ("idx_artifact_registrations_slug", "artifact_registrations"),
    ("idx_artifact_registrations_source_path", "artifact_registrations"),
    ("idx_diff_notebooks_created_at", "diff_notebooks"),
    ("idx_diff_notebooks_id", "diff_notebooks"),
    ("idx_directory_visits_box", "directory_visits"),
    ("idx_directory_visits_box_path", "directory_visits"),
    ("idx_directory_visits_last_visited", "directory_visits"),
    ("idx_directory_visits_path", "directory_visits"),
    ("idx_favorites_box", "favorites"),
    ("idx_favorites_box_path", "favorites"),
    ("idx_favorites_position", "favorites"),
    ("idx_layouts_created_at", "layouts"),
    ("idx_layouts_name", "layouts"),
    ("idx_progress_bars_name", "progress_bars"),
    ("idx_progress_bars_updated_at", "progress_bars"),
    ("idx_sessions_active", "sessions"),
    ("idx_sessions_active_accessed", "sessions"),
    ("idx_sessions_box", "sessions"),
    ("idx_sessions_box_name", "sessions"),
    ("idx_sessions_last_accessed_at", "sessions"),
    ("idx_sessions_session_name", "sessions"),
    ("idx_snippets_box", "snippets"),
    ("idx_snippets_sort_order", "snippets"),
]


@pytest.fixture
def store(tmp_path: Path) -> Iterator[Path]:
    state.reset_state()
    folder = tmp_path / "state-folder"
    state.initialize(folder)
    yield folder
    state.reset_state()


def _index_rows(folder: Path) -> list[tuple[str, str]]:
    conn = sqlite3.connect(folder / state.STATE_FILENAME)
    try:
        found = conn.execute(
            "SELECT name, tbl_name FROM sqlite_master WHERE type = 'index' "
            "AND name LIKE 'idx_%' ORDER BY name"
        ).fetchall()
    finally:
        conn.close()
    return [(name, table) for name, table in found]


def test_a_new_folder_has_exactly_the_pinned_indexes(store: Path) -> None:
    assert _index_rows(store) == INDEX_ROWS


def test_a_new_folder_has_the_four_default_snippets(store: Path) -> None:
    labels = [row.label for row in state.list_snippets("local")]
    assert labels == ["System Info", "Who Am I", "Disk Usage", "Top Processes"]


def test_initialising_again_adds_no_index(store: Path) -> None:
    state.reset_state()
    state.initialize(store)
    assert _index_rows(store) == INDEX_ROWS


def test_two_folders_in_one_process_keep_their_own_rows(tmp_path: Path) -> None:
    first, second = tmp_path / "a", tmp_path / "b"
    state.reset_state()
    try:
        state.initialize(first)
        state.toggle_favorite("local", "/srv/a")
        state.reset_state()
        state.initialize(second)
        assert state.list_favorites("local") == []
        state.reset_state()
        state.initialize(first)
        assert state.list_favorites("local") == ["/srv/a"]
    finally:
        state.reset_state()


def test_the_store_raises_no_deprecation_warning(tmp_path: Path) -> None:
    state.reset_state()
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", DeprecationWarning)
            state.initialize(tmp_path / "quiet")
            state.replace_favorites("local", ["/srv/a", "/srv/b"])
            state.toggle_favorite("local", "/srv/a")
            session = state.create_or_update_session("local", "alpha", "/srv/a")
            state.rename_session(session.id, "beta")
            state.record_directory_visit("local", "/srv/a")
            state.upsert_progress("job", 1, 2)
            assert state.delete_progress("job") is True
    finally:
        state.reset_state()


def test_every_async_name_is_pinned_and_a_coroutine_function() -> None:
    present = sorted(name for name in dir(state) if name.endswith("_async"))
    assert present == ASYNC_NAMES
    assert len(ASYNC_NAMES) == 47
    for name in ASYNC_NAMES:
        twin = getattr(state, name)
        sync = getattr(state, name.removesuffix("_async"))
        assert inspect.iscoroutinefunction(twin), name
        assert inspect.signature(twin).parameters == inspect.signature(sync).parameters, name


def test_async_forms_return_what_the_sync_forms_return(store: Path) -> None:
    async def run() -> tuple[list[str], bool, list[str]]:
        await state.replace_favorites_async(
            "local", (path for path in ["/srv/a", " /srv/b ", "/srv/a"])
        )
        listed = await state.list_favorites_async("local")
        created = await state.upsert_progress_async("job", 3, 10, label="Job", apply_metadata=False)
        removed = await state.delete_progress_async("job")
        assert created.current == 3
        return listed, removed, await state.list_favorites_async("local")

    listed, removed, again = asyncio.run(run())
    assert listed == ["/srv/a", "/srv/b"]
    assert removed is True
    assert again == state.list_favorites("local") == ["/srv/a", "/srv/b"]


def test_a_state_file_written_by_the_last_release_reads_and_takes_writes(tmp_path: Path) -> None:
    folder = tmp_path / "old"
    folder.mkdir()
    conn = sqlite3.connect(folder / state.STATE_FILENAME)
    conn.executescript(FIXTURE.read_text(encoding="utf-8"))
    conn.close()
    state.reset_state()
    try:
        state.initialize(folder)
        assert state.list_favorites("local") == ["/srv/a", "/srv/b", "/srv/名前"]
        assert state.list_favorites("web") == ["/var/www"]

        alpha = state.get_session_by_name("local", "alpha")
        assert alpha is not None
        assert alpha.metadata["k"] == [1, 2.5, None]
        assert [window["name"] for window in alpha.metadata["windows"]] == ["zsh"]
        assert state.get_session_by_name("web", "beta") is not None
        assert len(state.list_sessions("local")) + len(state.list_sessions("web")) == 2

        layouts = state.list_layouts()
        assert [layout.name for layout in layouts] == ["two"]
        assert len(layouts[0].terminals) == 2

        snippets = state.list_snippets("local")
        assert len(snippets) == 5
        assert sum(1 for snippet in snippets if snippet.box == "local") == 1

        bars = state.list_progress()
        assert [(bar.name, bar.current, bar.total) for bar in bars] == [("job.one", 3, 10)]
        assert len(state.list_diff_notebooks()) == 1
        assert state.list_diff_notebooks()[0].label == "nb"

        projects = state.list_artifact_projects()
        assert [(p.name, p.slug) for p in projects] == [("My Project", "my-project")]
        registrations = state.list_artifact_registrations()
        assert [r.slug for r in registrations] == ["t"]

        assert state.rename_session(alpha.id, "alpha2") is True
        assert state.get_session_by_name("local", "alpha") is None
        renamed = state.get_session_by_name("local", "alpha2")
        assert renamed is not None
        assert renamed.id == alpha.id
    finally:
        state.reset_state()
