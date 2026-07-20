"""Tests for the Claude session scanner and dashboard endpoints.

The scanner reads transcript ``.jsonl`` files under ``CLAUDE_CONFIG_DIR`` and
the ``open`` endpoint resumes a session into a local tmux session. tmux and the
session-state persistence are patched so no real tmux server is touched.
"""

import json
import os
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from sshler import claude_sessions as scanner
from sshler import state
from sshler.api import claude_sessions as claude_api
from sshler.webapp import ServerSettings, make_app

TEST_TOKEN = "claude-test-token"

UUID_A = "aaaaaaaa-0000-4000-8000-000000000001"
UUID_B = "bbbbbbbb-0000-4000-8000-000000000002"
UUID_C = "cccccccc-0000-4000-8000-000000000003"
UUID_D = "dddddddd-0000-4000-8000-000000000004"
UUID_E = "eeeeeeee-0000-4000-8000-000000000005"

BASE_TIME = 1_700_000_000.0


@pytest.fixture(autouse=True)
def _clear_scanner_cache():
    scanner._CACHE.clear()
    yield
    scanner._CACHE.clear()


def _write_lines(path: Path, lines: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_jsonl(path: Path, objs: list[dict]) -> None:
    _write_lines(path, [json.dumps(obj) for obj in objs])


def _set_mtime(path: Path, when: float) -> None:
    os.utime(path, (when, when))


# --------------------------------------------------------------------------- #
# Scanner
# --------------------------------------------------------------------------- #


def _build_corpus(root: Path) -> Path:
    """Write a deterministic corpus under <root>/projects and return projects dir."""
    projects = root / "projects"

    # Session A: early ai-title overridden by a final one BEYOND the head window
    # (65 filler lines > _HEAD_LINES), so only the tail read can capture it.
    filler = [
        {"type": "assistant", "sessionId": UUID_A, "message": {"role": "assistant", "content": []}}
        for _ in range(65)
    ]
    a_path = projects / "-proj-one" / f"{UUID_A}.jsonl"
    _write_jsonl(
        a_path,
        [
            {"type": "mode", "mode": "normal", "sessionId": UUID_A},
            {
                "type": "user",
                "promptSource": "typed",
                "cwd": "/proj/one",
                "version": "2.1.0",
                "gitBranch": "main",
                "message": {"role": "user", "content": "first prompt A"},
                "sessionId": UUID_A,
            },
            {"type": "ai-title", "aiTitle": "Title A v1", "sessionId": UUID_A},
            *filler,
            {"type": "ai-title", "aiTitle": "Title A FINAL", "sessionId": UUID_A},
            {"type": "last-prompt", "lastPrompt": "last prompt A", "sessionId": UUID_A},
        ],
    )
    _set_mtime(a_path, BASE_TIME + 300)

    # Session B: no ai-title -> title falls back to last-prompt.
    b_path = projects / "-proj-one" / f"{UUID_B}.jsonl"
    _write_jsonl(
        b_path,
        [
            {"type": "mode", "mode": "normal", "sessionId": UUID_B},
            {
                "type": "user",
                "promptSource": "typed",
                "cwd": "/proj/one",
                "message": {"role": "user", "content": "ignored first B"},
                "sessionId": UUID_B,
            },
            {"type": "last-prompt", "lastPrompt": "only last B", "sessionId": UUID_B},
        ],
    )
    _set_mtime(b_path, BASE_TIME + 200)

    # Session C: no ai-title, no last-prompt -> title falls back to first prompt.
    c_path = projects / "-proj-two" / f"{UUID_C}.jsonl"
    _write_jsonl(
        c_path,
        [
            {"type": "mode", "mode": "normal", "sessionId": UUID_C},
            {
                "type": "user",
                "promptSource": "typed",
                "cwd": "/proj/two",
                "message": {"role": "user", "content": "the only prompt C"},
                "sessionId": UUID_C,
            },
        ],
    )
    _set_mtime(c_path, BASE_TIME + 100)
    return projects


def test_scanner_parses_title_prompt_cwd_branch(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    _build_corpus(tmp_path)

    sessions = scanner.list_claude_sessions()
    assert len(sessions) == 3

    by_id = {s.id: s for s in sessions}
    a = by_id[UUID_A]
    assert a.title == "Title A FINAL"  # latest ai-title, captured via tail read
    assert a.last_prompt == "last prompt A"
    assert a.cwd == "/proj/one"
    assert a.git_branch == "main"
    assert a.version == "2.1.0"
    assert a.project_dir == "-proj-one"

    assert by_id[UUID_B].title == "only last B"  # falls back to last-prompt
    assert by_id[UUID_C].title == "the only prompt C"  # falls back to first prompt


def test_custom_title_takes_priority_over_ai_title(tmp_path, monkeypatch):
    """`/resume` shows the user's /rename (custom-title) over the auto aiTitle."""
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    path = tmp_path / "projects" / "-proj" / f"{UUID_A}.jsonl"
    _write_jsonl(
        path,
        [
            {"type": "user", "promptSource": "typed", "cwd": "/p",
             "message": {"role": "user", "content": "first"}, "sessionId": UUID_A},
            {"type": "ai-title", "aiTitle": "auto-generated-title", "sessionId": UUID_A},
            {"type": "custom-title", "customTitle": "camera", "sessionId": UUID_A},
        ],
    )
    session = scanner.get_claude_session(UUID_A)
    assert session is not None
    assert session.title == "camera"


def test_latest_custom_title_wins(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    path = tmp_path / "projects" / "-proj" / f"{UUID_B}.jsonl"
    _write_jsonl(
        path,
        [
            {"type": "custom-title", "customTitle": "old-name", "sessionId": UUID_B},
            {"type": "ai-title", "aiTitle": "auto", "sessionId": UUID_B},
            {"type": "custom-title", "customTitle": "new-name", "sessionId": UUID_B},
        ],
    )
    session = scanner.get_claude_session(UUID_B)
    assert session is not None
    assert session.title == "new-name"


def test_title_found_after_oversized_line(tmp_path, monkeypatch):
    """A giant tool-output line (larger than the read block, no newline within
    a block) must be skipped without breaking the scan — the title after it is
    still found, and header cwd from before it is retained."""
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    path = tmp_path / "projects" / "-proj" / f"{UUID_C}.jsonl"
    giant = json.dumps(
        {"type": "assistant", "sessionId": UUID_C,
         "message": {"role": "assistant", "content": "x" * 2_000_000}}
    )
    _write_lines(
        path,
        [
            json.dumps({"type": "user", "promptSource": "typed", "cwd": "/p",
                        "message": {"role": "user", "content": "first"}, "sessionId": UUID_C}),
            giant,
            json.dumps({"type": "custom-title", "customTitle": "survived", "sessionId": UUID_C}),
        ],
    )
    session = scanner.get_claude_session(UUID_C)
    assert session is not None
    assert session.title == "survived"
    assert session.cwd == "/p"


def test_scanner_orders_by_last_active_desc(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    _build_corpus(tmp_path)

    sessions = scanner.list_claude_sessions()
    assert [s.id for s in sessions] == [UUID_A, UUID_B, UUID_C]
    assert sessions[0].last_active == BASE_TIME + 300


def test_scanner_limit_caps_results(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    _build_corpus(tmp_path)

    sessions = scanner.list_claude_sessions(limit=2)
    assert [s.id for s in sessions] == [UUID_A, UUID_B]


def test_scanner_since_days_filters_old(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    projects = _build_corpus(tmp_path)
    # An ancient session ~100 days before the corpus.
    old_path = projects / "-proj-two" / f"{UUID_E}.jsonl"
    _write_jsonl(
        old_path,
        [{"type": "ai-title", "aiTitle": "Ancient", "sessionId": UUID_E, "cwd": "/x"}],
    )
    _set_mtime(old_path, BASE_TIME - 100 * 86400)

    # Reference "now" is the newest corpus mtime; cutoff = now - 30 days excludes E.
    import time as _time

    monkeypatch.setattr(_time, "time", lambda: BASE_TIME + 300)
    sessions = scanner.list_claude_sessions(since_days=30)
    assert [s.id for s in sessions] == [UUID_A, UUID_B, UUID_C]


def test_scanner_skips_malformed_lines(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    path = tmp_path / "projects" / "-proj-d" / f"{UUID_D}.jsonl"
    _write_lines(
        path,
        [
            json.dumps({"type": "mode", "mode": "normal", "sessionId": UUID_D}),
            "this is not json {{{",
            "",
            json.dumps(
                {
                    "type": "user",
                    "promptSource": "typed",
                    "cwd": "/proj/d",
                    "message": {"role": "user", "content": "good prompt D"},
                    "sessionId": UUID_D,
                }
            ),
            json.dumps({"type": "ai-title", "aiTitle": "Title D", "sessionId": UUID_D}),
        ],
    )

    session = scanner.get_claude_session(UUID_D)
    assert session is not None
    assert session.title == "Title D"
    assert session.cwd == "/proj/d"


def test_scanner_ignores_symlink_escape(tmp_path, monkeypatch):
    """A symlink under projects/ pointing outside must not be followed."""
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    projects = tmp_path / "projects"
    projects.mkdir(parents=True)
    # A transcript that lives OUTSIDE the projects tree.
    outside = tmp_path / "outside"
    outside.mkdir()
    _write_jsonl(
        outside / f"{UUID_E}.jsonl",
        [{"type": "ai-title", "aiTitle": "SECRET", "sessionId": UUID_E, "cwd": "/x"}],
    )
    # A symlink inside projects/ pointing at it — glob() would otherwise follow it.
    (projects / "evil").symlink_to(outside, target_is_directory=True)

    assert scanner.list_claude_sessions() == []
    assert scanner.get_claude_session(UUID_E) is None


def test_repo_root_resolves_git_root_for_subdir(tmp_path, monkeypatch):
    """A session run in repo/sub reports repo_root=repo (matching /resume grouping)."""
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    scanner._GIT_ROOT_CACHE.clear()
    repo = tmp_path / "myrepo"
    (repo / ".git").mkdir(parents=True)
    sub = repo / "server"
    sub.mkdir()

    path = tmp_path / "projects" / "-enc-a" / f"{UUID_A}.jsonl"
    _write_jsonl(
        path,
        [
            {
                "type": "user",
                "promptSource": "typed",
                "cwd": str(sub),
                "message": {"role": "user", "content": "hi"},
                "sessionId": UUID_A,
            },
            {"type": "ai-title", "aiTitle": "server session", "sessionId": UUID_A},
        ],
    )

    info = scanner.get_claude_session(UUID_A)
    assert info is not None
    # Exact cwd is preserved (resume lands in the subdir's own tmux session)…
    assert info.cwd == str(sub)
    # …but grouping collapses it under the repo root.
    assert info.repo_root == str(repo)


def test_repo_root_none_outside_git(tmp_path, monkeypatch):
    """A session with no .git anywhere up-tree reports repo_root=None.

    The recorded cwd is a synthetic absolute path that is NOT under the pytest
    tmp dir on purpose: this machine has a stray ``/tmp/.git``, and pytest's
    tmp_path lives under ``/tmp``, so a tmp-based cwd would (correctly) resolve
    to ``/tmp``. A path under a nonexistent root has no ``.git`` ancestor.
    """
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    scanner._GIT_ROOT_CACHE.clear()

    path = tmp_path / "projects" / "-enc-b" / f"{UUID_B}.jsonl"
    _write_jsonl(
        path,
        [
            {
                "type": "user",
                "promptSource": "typed",
                "cwd": "/nonexistent-sshler-test/lonely",
                "message": {"role": "user", "content": "hi"},
                "sessionId": UUID_B,
            },
        ],
    )

    info = scanner.get_claude_session(UUID_B)
    assert info is not None
    assert info.repo_root is None


@pytest.mark.parametrize(
    "value,expected",
    [
        (UUID_A, True),
        ("4BB8041B-F0BE-4E24-9372-52EAFA3468DB", True),  # uppercase ok
        ("not-a-uuid", False),
        ("../../etc/passwd", False),
        ("aaaaaaaa-0000-4000-8000-000000000001; rm -rf /", False),
        ("", False),
    ],
)
def test_is_valid_session_id(value, expected):
    assert scanner.is_valid_session_id(value) is expected


# --------------------------------------------------------------------------- #
# Endpoints
# --------------------------------------------------------------------------- #


@pytest.fixture
def tmux_spy(monkeypatch):
    """Patch tmux + persistence; record every interaction. No real tmux."""
    runs: list[tuple[str, list[str]]] = []
    created: list[dict] = []
    live: set[str] = set()  # live tmux sessions
    windows: set[str] = set()  # window names in the (single) session under test

    async def fake_run(name, args):
        runs.append((name, list(args)))

    async def fake_discover():
        return set(live)

    async def fake_windows(session):
        return set(windows)

    async def fake_configure(session):
        return None

    async def fake_record(name, directory):
        return None

    async def fake_create(box_name, session_name, working_directory, metadata=None):
        created.append(
            {
                "box": box_name,
                "session_name": session_name,
                "working_directory": working_directory,
                "metadata": metadata or {},
            }
        )
        return None

    monkeypatch.setattr(claude_api, "_run_local_tmux_command", fake_run)
    monkeypatch.setattr(claude_api, "discover_local_sessions", fake_discover)
    monkeypatch.setattr(claude_api, "list_local_window_names", fake_windows)
    monkeypatch.setattr(claude_api, "_configure_tmux_bindings", fake_configure)
    monkeypatch.setattr(claude_api, "record_ts_history", fake_record)
    monkeypatch.setattr(state, "create_or_update_session_async", fake_create)

    return {"runs": runs, "created": created, "live": live, "windows": windows}


def _make_client(tmp_path, monkeypatch, *, work_cwd: str) -> TestClient:
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "boxes.yaml").write_text(
        yaml.safe_dump({"boxes": []}, sort_keys=False), encoding="utf-8"
    )
    os.environ["SSHLER_CONFIG_DIR"] = str(config_dir)

    claude_root = tmp_path / "claude"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(claude_root))
    # One transcript whose cwd is a directory that actually exists.
    transcript = claude_root / "projects" / "-work" / f"{UUID_A}.jsonl"
    _write_jsonl(
        transcript,
        [
            {
                "type": "user",
                "promptSource": "typed",
                "cwd": work_cwd,
                "message": {"role": "user", "content": "hi"},
                "sessionId": UUID_A,
            },
            {"type": "ai-title", "aiTitle": "Work Session", "sessionId": UUID_A},
        ],
    )
    return TestClient(make_app(ServerSettings(csrf_token=TEST_TOKEN)))


def _headers() -> dict[str, str]:
    return {"X-SSHLER-TOKEN": TEST_TOKEN}


def test_open_invalid_id_returns_400(tmp_path, monkeypatch, tmux_spy):
    work = tmp_path / "work"
    work.mkdir()
    (work / ".git").mkdir()  # make it its own repo root (repo_root == work)
    client = _make_client(tmp_path, monkeypatch, work_cwd=str(work))
    resp = client.post("/api/v1/claude/sessions/not-a-uuid/open", headers=_headers())
    assert resp.status_code == 400
    assert tmux_spy["runs"] == []


def test_open_unknown_id_returns_404(tmp_path, monkeypatch, tmux_spy):
    work = tmp_path / "work"
    work.mkdir()
    (work / ".git").mkdir()  # make it its own repo root (repo_root == work)
    client = _make_client(tmp_path, monkeypatch, work_cwd=str(work))
    resp = client.post(f"/api/v1/claude/sessions/{UUID_C}/open", headers=_headers())
    assert resp.status_code == 404
    assert tmux_spy["runs"] == []


def test_open_spawns_and_sends_resume(tmp_path, monkeypatch, tmux_spy):
    work = tmp_path / "work"
    work.mkdir()
    (work / ".git").mkdir()  # make it its own repo root (repo_root == work)
    client = _make_client(tmp_path, monkeypatch, work_cwd=str(work))

    resp = client.post(f"/api/v1/claude/sessions/{UUID_A}/open", headers=_headers())
    assert resp.status_code == 200
    body = resp.json()
    session = body["session_name"]
    window = body["window"]
    assert body["box"] == "local"
    assert body["working_directory"] == str(work)
    assert session == "work"  # ts basename of the dir (no hash)
    assert window == f"cl-{UUID_A.replace('-', '')[:6]}"
    assert body["already_open"] is False
    target = f"{session}:{window}"

    # dir session created, then the conversation's own window inside it.
    assert (session, ["new-session", "-d", "-s", session, "-c", str(work)]) in tmux_spy["runs"]
    assert (session, ["new-window", "-t", session, "-n", window, "-c", str(work)]) in tmux_spy["runs"]
    # resume typed into that window literally, then Enter, then focus it.
    assert (session, ["send-keys", "-t", target, "-l", f"claude --resume {UUID_A}"]) in tmux_spy["runs"]
    assert (session, ["send-keys", "-t", target, "Enter"]) in tmux_spy["runs"]
    assert (session, ["select-window", "-t", target]) in tmux_spy["runs"]

    # Persisted as a claude-kind session under the dir session name.
    assert len(tmux_spy["created"]) == 1
    record = tmux_spy["created"][0]
    assert record["box"] == "local"
    assert record["session_name"] == session
    assert record["metadata"]["kind"] == "claude"
    assert record["metadata"]["claude_session_id"] == UUID_A
    assert record["metadata"]["window"] == window


def test_open_resumes_subdir_session_into_repo_root(tmp_path, monkeypatch, tmux_spy):
    """A session that ran in repo/server resumes into the repo-ROOT tmux session
    (top-level session + shell), not the subdir's own session."""
    scanner._GIT_ROOT_CACHE.clear()
    repo = tmp_path / "myrepo"
    (repo / ".git").mkdir(parents=True)
    sub = repo / "server"
    sub.mkdir()
    client = _make_client(tmp_path, monkeypatch, work_cwd=str(sub))

    resp = client.post(f"/api/v1/claude/sessions/{UUID_A}/open", headers=_headers())
    assert resp.status_code == 200
    body = resp.json()
    session = body["session_name"]
    window = body["window"]
    # Session name + persisted dir are the repo root (the tmux session it groups
    # under), NOT the subdir.
    assert session == "myrepo"
    assert body["working_directory"] == str(repo)
    target = f"{session}:{window}"
    # But the window's shell starts in the EXACT subdir so `claude --resume` (which
    # is cwd-scoped) can find the session; the repo-root session is created first.
    assert (session, ["new-session", "-d", "-s", session, "-c", str(repo)]) in tmux_spy["runs"]
    assert (session, ["new-window", "-t", session, "-n", window, "-c", str(sub)]) in tmux_spy["runs"]
    assert (session, ["send-keys", "-t", target, "-l", f"claude --resume {UUID_A}"]) in tmux_spy["runs"]
    # Persisted under the repo-root session name.
    assert tmux_spy["created"][0]["session_name"] == "myrepo"
    assert tmux_spy["created"][0]["working_directory"] == str(repo)


def test_open_uses_custom_command_template(tmp_path, monkeypatch, tmux_spy):
    work = tmp_path / "work"
    work.mkdir()
    (work / ".git").mkdir()  # make it its own repo root (repo_root == work)
    client = _make_client(tmp_path, monkeypatch, work_cwd=str(work))

    resp = client.post(
        f"/api/v1/claude/sessions/{UUID_A}/open",
        headers=_headers(),
        json={"command_template": "claudeee --resume {id} --foo"},
    )
    assert resp.status_code == 200
    body = resp.json()
    target = f"{body['session_name']}:{body['window']}"
    # The {id} placeholder is replaced with the validated UUID; the rest is typed verbatim.
    assert (
        body["session_name"],
        ["send-keys", "-t", target, "-l", f"claudeee --resume {UUID_A} --foo"],
    ) in tmux_spy["runs"]


def test_open_rejects_template_without_id_placeholder(tmp_path, monkeypatch, tmux_spy):
    work = tmp_path / "work"
    work.mkdir()
    (work / ".git").mkdir()  # make it its own repo root (repo_root == work)
    client = _make_client(tmp_path, monkeypatch, work_cwd=str(work))
    resp = client.post(
        f"/api/v1/claude/sessions/{UUID_A}/open",
        headers=_headers(),
        json={"command_template": "claude --continue"},
    )
    assert resp.status_code == 400
    assert tmux_spy["runs"] == []


def test_open_rejects_template_with_control_chars(tmp_path, monkeypatch, tmux_spy):
    work = tmp_path / "work"
    work.mkdir()
    (work / ".git").mkdir()  # make it its own repo root (repo_root == work)
    client = _make_client(tmp_path, monkeypatch, work_cwd=str(work))
    resp = client.post(
        f"/api/v1/claude/sessions/{UUID_A}/open",
        headers=_headers(),
        json={"command_template": "claude --resume {id}\nrm -rf ~"},
    )
    assert resp.status_code == 400
    assert tmux_spy["runs"] == []


def test_open_same_session_distinct_windows_for_same_dir(tmp_path, monkeypatch, tmux_spy):
    """Two conversations in ONE dir → one shared session, two distinct windows."""
    work = tmp_path / "work"
    work.mkdir()
    (work / ".git").mkdir()  # make it its own repo root (repo_root == work)
    client = _make_client(tmp_path, monkeypatch, work_cwd=str(work))
    # Add a second transcript sharing the same cwd.
    second = tmp_path / "claude" / "projects" / "-work" / f"{UUID_B}.jsonl"
    _write_jsonl(
        second,
        [
            {
                "type": "user",
                "promptSource": "typed",
                "cwd": str(work),
                "message": {"role": "user", "content": "two"},
                "sessionId": UUID_B,
            }
        ],
    )

    a = client.post(f"/api/v1/claude/sessions/{UUID_A}/open", headers=_headers()).json()
    b = client.post(f"/api/v1/claude/sessions/{UUID_B}/open", headers=_headers()).json()

    assert a["session_name"] == b["session_name"] == "work"  # same dir session
    assert a["window"] != b["window"]  # distinct windows
    assert a["window"] == f"cl-{UUID_A.replace('-', '')[:6]}"
    assert b["window"] == f"cl-{UUID_B.replace('-', '')[:6]}"


def test_open_reselects_existing_window(tmp_path, monkeypatch, tmux_spy):
    """Re-opening a conversation whose window already exists selects it and does
    NOT re-type the resume command (never disturb a running claude)."""
    work = tmp_path / "work"
    work.mkdir()
    (work / ".git").mkdir()  # make it its own repo root (repo_root == work)
    client = _make_client(tmp_path, monkeypatch, work_cwd=str(work))

    first = client.post(f"/api/v1/claude/sessions/{UUID_A}/open", headers=_headers()).json()
    session, window = first["session_name"], first["window"]
    assert first["already_open"] is False
    assert len([r for r in tmux_spy["runs"] if r[1][:1] == ["send-keys"]]) == 2

    # Simulate the session live AND this window already present, then re-open.
    tmux_spy["runs"].clear()
    tmux_spy["created"].clear()
    tmux_spy["live"].add(session)
    tmux_spy["windows"].add(window)

    second = client.post(f"/api/v1/claude/sessions/{UUID_A}/open", headers=_headers())
    assert second.status_code == 200
    body = second.json()
    assert body["already_open"] is True
    assert body["session_name"] == session
    assert body["window"] == window
    # Selected the existing window; nothing spawned, typed, or persisted.
    assert (session, ["select-window", "-t", f"{session}:{window}"]) in tmux_spy["runs"]
    assert not any(r[1][:1] == ["send-keys"] for r in tmux_spy["runs"])
    assert not any(r[1][:1] == ["new-window"] for r in tmux_spy["runs"])
    assert tmux_spy["created"] == []


def test_list_endpoint_returns_sessions_sorted(tmp_path, monkeypatch, tmux_spy):
    work = tmp_path / "work"
    work.mkdir()
    (work / ".git").mkdir()  # make it its own repo root (repo_root == work)
    client = _make_client(tmp_path, monkeypatch, work_cwd=str(work))
    # Add an older second session.
    older = tmp_path / "claude" / "projects" / "-work" / f"{UUID_B}.jsonl"
    _write_jsonl(
        older,
        [{"type": "ai-title", "aiTitle": "Older Session", "sessionId": UUID_B, "cwd": str(work)}],
    )
    _set_mtime(older, BASE_TIME)
    _set_mtime(
        tmp_path / "claude" / "projects" / "-work" / f"{UUID_A}.jsonl", BASE_TIME + 500
    )

    resp = client.get("/api/v1/claude/sessions", headers=_headers())
    assert resp.status_code == 200
    data = resp.json()
    assert [s["id"] for s in data] == [UUID_A, UUID_B]
    assert data[0]["title"] == "Work Session"
    assert data[1]["title"] == "Older Session"


def test_list_endpoint_forwards_repo_root(tmp_path, monkeypatch, tmux_spy):
    """The API response must carry repo_root end-to-end (guards the endpoint
    from silently dropping the field, which would make repo grouping inert)."""
    scanner._GIT_ROOT_CACHE.clear()
    repo = tmp_path / "myrepo"
    (repo / ".git").mkdir(parents=True)
    sub = repo / "server"
    sub.mkdir()
    client = _make_client(tmp_path, monkeypatch, work_cwd=str(sub))

    resp = client.get("/api/v1/claude/sessions", headers=_headers())
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["cwd"] == str(sub)
    assert data[0]["repo_root"] == str(repo)


def test_endpoints_require_token(tmp_path, monkeypatch, tmux_spy):
    work = tmp_path / "work"
    work.mkdir()
    (work / ".git").mkdir()  # make it its own repo root (repo_root == work)
    client = _make_client(tmp_path, monkeypatch, work_cwd=str(work))
    resp = client.get("/api/v1/claude/sessions")
    assert resp.status_code == 403
