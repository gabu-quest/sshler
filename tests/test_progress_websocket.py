"""WebSocket fan-out tests for /ws/progress."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient
from fastapi.websockets import WebSocketDisconnect

from sshler import state
from sshler.webapp import ServerSettings, make_app


TEST_TOKEN = "api-token"


def setup_config(tmp_path: Path) -> Path:
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "boxes.yaml").write_text(
        yaml.safe_dump({"boxes": []}, sort_keys=False), encoding="utf-8"
    )
    return config_dir


def build_client(config_dir: Path) -> TestClient:
    os.environ["SSHLER_CONFIG_DIR"] = str(config_dir)
    state.reset_state()
    state.initialize(config_dir)
    return TestClient(make_app(ServerSettings(csrf_token=TEST_TOKEN)))


def auth_headers() -> dict[str, str]:
    return {"X-SSHLER-TOKEN": TEST_TOKEN}


def test_snapshot_on_connect_when_empty(tmp_path):
    client = build_client(setup_config(tmp_path))
    try:
        with client.websocket_connect(f"/ws/progress?token={TEST_TOKEN}") as ws:
            snapshot = ws.receive_json()
            assert snapshot["type"] == "snapshot"
            assert snapshot["bars"] == []
    finally:
        client.close()


def test_snapshot_includes_existing_bars(tmp_path):
    client = build_client(setup_config(tmp_path))
    try:
        client.post(
            "/api/v1/progress/preexisting",
            headers=auth_headers(),
            json={"current": 5, "total": 10, "label": "before"},
        )
        with client.websocket_connect(f"/ws/progress?token={TEST_TOKEN}") as ws:
            snapshot = ws.receive_json()
            assert snapshot["type"] == "snapshot"
            assert len(snapshot["bars"]) == 1
            assert snapshot["bars"][0]["name"] == "preexisting"
            assert snapshot["bars"][0]["current"] == 5
            assert snapshot["bars"][0]["label"] == "before"
    finally:
        client.close()


def test_push_broadcasts_upsert_event(tmp_path):
    client = build_client(setup_config(tmp_path))
    try:
        with client.websocket_connect(f"/ws/progress?token={TEST_TOKEN}") as ws:
            snapshot = ws.receive_json()
            assert snapshot["type"] == "snapshot"

            resp = client.post(
                "/api/v1/progress/live-bar",
                headers=auth_headers(),
                json={"current": 1, "total": 4, "color": "green"},
            )
            assert resp.status_code == 200

            event = ws.receive_json()
            assert event["type"] == "upsert"
            assert event["name"] == "live-bar"
            assert event["bar"]["current"] == 1
            assert event["bar"]["total"] == 4
            assert event["bar"]["color"] == "green"
    finally:
        client.close()


def test_delete_broadcasts_delete_event(tmp_path):
    client = build_client(setup_config(tmp_path))
    try:
        client.post(
            "/api/v1/progress/will-vanish",
            headers=auth_headers(),
            json={"current": 1, "total": 2},
        )
        with client.websocket_connect(f"/ws/progress?token={TEST_TOKEN}") as ws:
            ws.receive_json()  # snapshot

            resp = client.delete(
                "/api/v1/progress/will-vanish", headers=auth_headers()
            )
            assert resp.status_code == 200

            event = ws.receive_json()
            assert event["type"] == "delete"
            assert event["name"] == "will-vanish"
            assert event["bar"] is None
    finally:
        client.close()


def test_two_clients_both_receive_broadcast(tmp_path):
    client = build_client(setup_config(tmp_path))
    try:
        with client.websocket_connect(
            f"/ws/progress?token={TEST_TOKEN}"
        ) as ws_a, client.websocket_connect(
            f"/ws/progress?token={TEST_TOKEN}"
        ) as ws_b:
            ws_a.receive_json()  # snapshot for A
            ws_b.receive_json()  # snapshot for B

            resp = client.post(
                "/api/v1/progress/fanout",
                headers=auth_headers(),
                json={"current": 2, "total": 3},
            )
            assert resp.status_code == 200

            event_a = ws_a.receive_json()
            event_b = ws_b.receive_json()
            assert event_a["type"] == "upsert"
            assert event_a["name"] == "fanout"
            assert event_b["type"] == "upsert"
            assert event_b["name"] == "fanout"
            assert event_a["bar"]["current"] == 2
            assert event_b["bar"]["current"] == 2
    finally:
        client.close()


def test_bad_token_closes_connection(tmp_path):
    client = build_client(setup_config(tmp_path))
    try:
        with pytest.raises(WebSocketDisconnect) as exc_info:
            with client.websocket_connect("/ws/progress?token=wrong") as ws:
                ws.receive_json()
        assert exc_info.value.code == 4403
    finally:
        client.close()
