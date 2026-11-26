"""Request bodies of the theme and batch-recreate routes are validated.

A body a correct client sends is answered as before; a body of the wrong
shape is a 422 before any work is done.
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
import yaml
from fastapi.testclient import TestClient

from sshler import snapshot, state
from sshler.webapp import ServerSettings, make_app

TOKEN = "body-token"
HEADERS = {"X-SSHLER-TOKEN": TOKEN}


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "boxes.yaml").write_text(
        yaml.safe_dump({"boxes": []}, sort_keys=False), encoding="utf-8"
    )
    ssh_config = tmp_path / "ssh_config"
    ssh_config.write_text("Host themebox\n  HostName example.invalid\n  User t\n", encoding="utf-8")
    monkeypatch.setenv("SSHLER_CONFIG_DIR", str(config_dir))
    monkeypatch.setenv("SSHLER_SSH_CONFIG", str(ssh_config))
    state.initialize(config_dir)
    # No `with`: entering the client would run the lifespan (pool, PDF renderer).
    test_client = TestClient(make_app(ServerSettings(csrf_token=TOKEN)), headers=HEADERS)
    try:
        yield test_client
    finally:
        test_client.close()
        state.reset_state()


def _put_theme(client: TestClient, body) -> httpx.Response:
    return client.put("/api/v1/boxes/themebox/theme", json=body)


def test_a_known_theme_is_stored_and_echoed(client):
    resp = _put_theme(client, {"terminal_theme": "dracula"})

    assert resp.status_code == 200, resp.text
    assert resp.json() == {"status": "ok", "terminal_theme": "dracula"}


def _frontend_theme_ids() -> list[str]:
    """The ids the frontend theme select offers, read from its one list."""
    import re

    src = (Path(__file__).parent.parent / "frontend/src/utils/terminalThemes.ts").read_text(
        encoding="utf-8"
    )
    block = re.search(r"TERMINAL_THEME_NAMES = \[(.*?)\] as const", src, re.S)
    assert block, "terminalThemes.ts no longer defines TERMINAL_THEME_NAMES"
    ids = re.findall(r"'([a-z]+)'", block.group(1))
    assert len(ids) >= 8
    return ids


@pytest.mark.parametrize("theme", _frontend_theme_ids())
def test_every_theme_the_select_offers_is_saved(client, theme):
    resp = _put_theme(client, {"terminal_theme": theme})

    assert resp.status_code == 200, resp.text
    assert resp.json() == {"status": "ok", "terminal_theme": theme}


@pytest.mark.parametrize("body", [{}, {"terminal_theme": ""}, {"terminal_theme": None}])
def test_a_missing_empty_or_null_theme_clears_it(client, body):
    assert _put_theme(client, {"terminal_theme": "dracula"}).status_code == 200

    resp = _put_theme(client, body)

    assert resp.status_code == 200, resp.text
    assert resp.json() == {"status": "ok", "terminal_theme": None}


def test_an_unknown_theme_name_is_400_with_the_list_of_valid_names(client):
    resp = _put_theme(client, {"terminal_theme": "no-such-theme"})

    assert resp.status_code == 400
    assert resp.json()["detail"].startswith("Invalid theme. Valid: ")


@pytest.mark.parametrize(
    "body",
    [["dark"], 5, "dark", {"terminal_theme": ["dark"]}, {"terminal_theme": 5}],
)
def test_a_theme_body_of_the_wrong_shape_is_422(client, body):
    resp = _put_theme(client, body)

    assert resp.status_code == 422


def _lost(session_id: str) -> dict:
    return {
        "id": session_id,
        "box": "local",
        "session_name": session_id,
        "working_directory": "/tmp",
        "last_snapshot_at": 0,
        "windows": [],
    }


@pytest.fixture
def recreated(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Recovery list of two lost sessions; recreating one always succeeds."""
    names: list[str] = []

    async def fake_recreate(session_name: str, windows: list) -> bool:
        names.append(session_name)
        return True

    monkeypatch.setattr("sshler.api.recovery.recreate_session", fake_recreate)
    snapshot.set_recovery_sessions([_lost("a"), _lost("b")])
    return names


def test_a_batch_of_ids_is_answered_per_id(client, recreated):
    resp = client.post("/api/v1/recovery/recreate-batch", json={"session_ids": ["a", "zzz"]})

    assert resp.status_code == 200, resp.text
    assert resp.json() == {
        "status": "partial",
        "succeeded": ["a"],
        "failed": [{"path": "zzz", "error": "Session has no saved layout", "reason": "not_found"}],
    }
    assert recreated == ["a"]


def test_a_batch_without_the_key_is_an_empty_result(client, recreated):
    resp = client.post("/api/v1/recovery/recreate-batch", json={})

    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "succeeded": [], "failed": []}
    assert recreated == []


@pytest.mark.parametrize(
    "body",
    [
        {"session_ids": "ab"},
        {"session_ids": [{"id": "a"}]},
        {"session_ids": [1]},
        {"session_ids": None},
        ["a"],
    ],
)
def test_a_batch_of_the_wrong_shape_is_422_and_recreates_nothing(client, recreated, body):
    resp = client.post("/api/v1/recovery/recreate-batch", json=body)

    assert resp.status_code == 422
    assert recreated == []


def test_a_batch_of_ten_thousand_ids_is_answered_per_id(client, recreated):
    ids = ["a"] + [str(n) for n in range(10_000)]

    resp = client.post("/api/v1/recovery/recreate-batch", json={"session_ids": ids})

    assert resp.status_code == 200
    assert resp.json() == {
        "status": "partial",
        "succeeded": ["a"],
        "failed": [
            {"path": str(n), "error": "Session has no saved layout", "reason": "not_found"}
            for n in range(10_000)
        ],
    }
    assert recreated == ["a"]
