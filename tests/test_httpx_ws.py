from __future__ import annotations

import asyncio
import os
import tempfile
from pathlib import Path

import pytest
import yaml
from httpx import AsyncClient, ASGITransport
from fastapi import WebSocketDisconnect

from sshler.webapp import ServerSettings, make_app


TEST_TOKEN = "token-async"


def _config_dir(tmp_path: Path) -> Path:
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "boxes.yaml").write_text(yaml.safe_dump({"boxes": []}, sort_keys=False), encoding="utf-8")
    return config_dir


@pytest.mark.asyncio
async def test_httpx_status_handshake(tmp_path):
    config_dir = _config_dir(tmp_path)
    os.environ["SSHLER_CONFIG_DIR"] = str(config_dir)
    app = make_app(ServerSettings(csrf_token=TEST_TOKEN))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        hs = await client.get("/api/v1/terminal/handshake", headers={"X-SSHLER-TOKEN": TEST_TOKEN})
        assert hs.status_code == 200
        status = await client.get("/api/v1/boxes/local/status", headers={"X-SSHLER-TOKEN": TEST_TOKEN})
        assert status.status_code == 200

@pytest.mark.asyncio
async def test_ws_connect(tmp_path):
    config_dir = _config_dir(tmp_path)
    os.environ["SSHLER_CONFIG_DIR"] = str(config_dir)
    app = make_app(ServerSettings(csrf_token=TEST_TOKEN))
    from starlette.testclient import TestClient as SyncClient
    sync_client = SyncClient(app)
    with sync_client.websocket_connect(f"/ws/term?host=local&dir=/&session=test&cols=10&rows=5&token={TEST_TOKEN}") as ws:
        ws.send_text("ping")
    sync_client.close()
