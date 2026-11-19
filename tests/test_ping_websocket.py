"""WebSocket fan-out tests for /ws/ping."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from sshler import state
from sshler.webapp import ServerSettings, make_app

TEST_TOKEN = "api-token"


def build_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "boxes.yaml").write_text(
        yaml.safe_dump({"boxes": []}, sort_keys=False), encoding="utf-8"
    )
    monkeypatch.setenv("SSHLER_CONFIG_DIR", str(config_dir))
    state.reset_state()
    state.initialize(config_dir)
    return TestClient(make_app(ServerSettings(csrf_token=TEST_TOKEN)))


def test_two_ping_clients_receive_the_same_events_in_order(tmp_path, monkeypatch):
    client = build_client(tmp_path, monkeypatch)
    try:
        with (
            client.websocket_connect(f"/ws/ping?token={TEST_TOKEN}") as ws_a,
            client.websocket_connect(f"/ws/ping?token={TEST_TOKEN}") as ws_b,
        ):
            for title in ("first", "second"):
                resp = client.post(
                    "/api/v1/ping", headers={"X-SSHLER-TOKEN": TEST_TOKEN}, json={"title": title}
                )
                assert resp.status_code == 200
            assert [ws_a.receive_json()["title"] for _ in range(2)] == ["first", "second"]
            assert [ws_b.receive_json()["title"] for _ in range(2)] == ["first", "second"]
    finally:
        client.close()
