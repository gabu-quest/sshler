import os
import tempfile
from pathlib import Path

import yaml
from fastapi.testclient import TestClient
from fastapi.websockets import WebSocketDisconnect

from sshler.webapp import ServerSettings, make_app
from sshler import state


TEST_TOKEN = "api-token"


def build_client(config_dir: Path) -> TestClient:
    os.environ["SSHLER_CONFIG_DIR"] = str(config_dir)
    return TestClient(make_app(ServerSettings(csrf_token=TEST_TOKEN)))


def setup_config(tmp_path: Path) -> Path:
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "boxes.yaml").write_text(yaml.safe_dump({"boxes": []}, sort_keys=False), encoding="utf-8")
    return config_dir


def auth_headers() -> dict[str, str]:
    return {"X-SSHLER-TOKEN": TEST_TOKEN}


def test_bootstrap_spa_toggle(tmp_path):
    config_dir = setup_config(tmp_path)
    client = TestClient(make_app(ServerSettings(csrf_token=TEST_TOKEN, serve_spa=False)))
    try:
        resp = client.get("/api/v1/bootstrap")
        assert resp.status_code == 200
        data = resp.json()
        assert data["spa_enabled"] is False
        assert data["spa_base"] == ""
    finally:
        client.close()


def test_api_bootstrap_and_boxes(tmp_path):
    config_dir = setup_config(tmp_path)
    client = build_client(config_dir)
    try:
        bootstrap = client.get("/api/v1/bootstrap")
        assert bootstrap.status_code == 200
        data = bootstrap.json()
        assert data["token"] == TEST_TOKEN
        boxes = client.get("/api/v1/boxes", headers=auth_headers())
        assert boxes.status_code == 200
        names = [box["name"] for box in boxes.json()]
        assert "local" in names
        root = client.get("/", follow_redirects=False)
        assert root.status_code in {302, 307}
        assert root.headers["location"] in {"/app/", "/boxes"}
    finally:
        client.close()


def test_local_directory_touch_delete(tmp_path):
    config_dir = setup_config(tmp_path)
    workdir = tmp_path / "work"
    workdir.mkdir()
    client = build_client(config_dir)
    try:
        # list directory
        listing = client.get(
            "/api/v1/boxes/local/ls",
            params={"directory": str(workdir)},
            headers=auth_headers(),
        )
        assert listing.status_code == 200

        # touch
        touch_resp = client.post(
            "/api/v1/boxes/local/touch",
            json={"directory": str(workdir), "filename": "demo.txt"},
            headers=auth_headers(),
        )
        assert touch_resp.status_code == 200
        assert (workdir / "demo.txt").exists()

        # delete
        delete_resp = client.post(
            "/api/v1/boxes/local/delete",
            json={"path": str(workdir / "demo.txt")},
            headers=auth_headers(),
        )
        assert delete_resp.status_code == 200
        assert not (workdir / "demo.txt").exists()
    finally:
        client.close()


def test_write_file(tmp_path):
    config_dir = setup_config(tmp_path)
    workdir = tmp_path / "work"
    workdir.mkdir()
    target = workdir / "edit.txt"
    target.write_text("old", encoding="utf-8")
    client = build_client(config_dir)
    try:
        write_resp = client.post(
          "/api/v1/boxes/local/write",
          json={"path": str(target), "content": "new-content"},
          headers=auth_headers(),
        )
        assert write_resp.status_code == 200
        assert target.read_text(encoding="utf-8") == "new-content"
    finally:
        client.close()


def test_favorites_and_pin(tmp_path):
    config_dir = setup_config(tmp_path)
    client = build_client(config_dir)
    try:
        fav_resp = client.post(
            "/api/v1/boxes/local/fav",
            json={"path": "/tmp", "favorite": True},
            headers=auth_headers(),
        )
        assert fav_resp.status_code == 200
        pin_resp = client.post(
            "/api/v1/boxes/local/pin",
            headers=auth_headers(),
        )
        assert pin_resp.status_code == 200
        status = client.get("/api/v1/boxes/local/status", headers=auth_headers())
        assert status.status_code == 200
        assert status.json()["status"] in {"online", "offline"}
    finally:
        client.close()


def test_sessions_crud(tmp_path):
    config_dir = setup_config(tmp_path)
    client = build_client(config_dir)
    try:
        create = client.post(
            "/api/v1/boxes/local/sessions",
            json={"session_name": "s1", "working_directory": "/"},
            headers=auth_headers(),
        )
        assert create.status_code == 200
        session_id = create.json()["id"]

        listing = client.get(
            "/api/v1/boxes/local/sessions",
            headers=auth_headers(),
        )
        assert listing.status_code == 200
        assert any(item["id"] == session_id for item in listing.json())

        update = client.patch(
            f"/api/v1/boxes/local/sessions/{session_id}",
            json={"active": False, "window_count": 2, "metadata": {"cols": 80}},
            headers=auth_headers(),
        )
        assert update.status_code == 200
        payload = update.json()
        assert payload["active"] is False
        assert payload["window_count"] == 2
        assert payload["metadata"]["cols"] == 80

        with client.websocket_connect(
            f"/ws/term?host=local&dir=/&session=test&cols=10&rows=5&token={TEST_TOKEN}"
        ) as ws:
            ws.send_text("ping")

        delete_resp = client.delete(
            f"/api/v1/boxes/local/sessions/{session_id}",
            headers=auth_headers(),
        )
        assert delete_resp.status_code == 200
        # download path
        download = client.get(
            "/api/v1/boxes/local/download",
            params={"path": str(tmp_path / "config" / "boxes.yaml")},
            headers=auth_headers(),
        )
        assert download.status_code == 200
    finally:
        client.close()
        state.reset_state()
