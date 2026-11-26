from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from sshler.terminal import pump
from sshler.webapp import ServerSettings, make_app

TEST_TOKEN = "token-e2e"


def setup_config(tmp_path: Path) -> Path:
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "boxes.yaml").write_text(
        yaml.safe_dump({"boxes": []}, sort_keys=False), encoding="utf-8"
    )
    return config_dir


def build_client(config_dir: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("SSHLER_CONFIG_DIR", str(config_dir))
    return TestClient(make_app(ServerSettings(csrf_token=TEST_TOKEN)))


def auth_headers():
    return {"X-SSHLER-TOKEN": TEST_TOKEN}


def test_handshake_and_status(tmp_path, monkeypatch):
    config_dir = setup_config(tmp_path)
    client = build_client(config_dir, monkeypatch)
    try:
        hs = client.get("/api/v1/terminal/handshake", headers=auth_headers())
        assert hs.status_code == 200
        data = hs.json()
        # Mutation killed: handshake returns no token (None) or the wrong one.
        assert data["token"] == TEST_TOKEN
        # Mutation killed: the page's silence limit drifts from the server's ping interval.
        assert data["ping_interval"] == pump.PING_INTERVAL
        status = client.get("/api/v1/boxes/local/status", headers=auth_headers())
        assert status.status_code == 200
        # Mutation killed: local box reported as anything but online.
        assert status.json() == {"name": "local", "status": "online", "latency_ms": 0.0}
    finally:
        client.close()


def test_handshake_reports_the_pump_ping_interval(tmp_path, monkeypatch):
    """The page's silence limit is a multiple of this number: it is read where the pump reads it."""
    monkeypatch.setattr(pump, "PING_INTERVAL", 7.0)
    client = build_client(setup_config(tmp_path), monkeypatch)
    try:
        hs = client.get("/api/v1/terminal/handshake", headers=auth_headers())
        assert hs.json()["ping_interval"] == 7.0
    finally:
        client.close()
