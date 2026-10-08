from __future__ import annotations

import time
from pathlib import Path

import pytest
import yaml
from httpx import ASGITransport, AsyncClient
from starlette.testclient import TestClient as SyncClient

from sshler.webapp import ServerSettings, make_app

TEST_TOKEN = "token-async"


def _config_dir(tmp_path: Path) -> Path:
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "boxes.yaml").write_text(
        yaml.safe_dump({"boxes": []}, sort_keys=False), encoding="utf-8"
    )
    return config_dir


@pytest.mark.asyncio
async def test_httpx_status_handshake(tmp_path, monkeypatch):
    monkeypatch.setenv("SSHLER_CONFIG_DIR", str(_config_dir(tmp_path)))
    app = make_app(ServerSettings(csrf_token=TEST_TOKEN))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        hs = await client.get("/api/v1/terminal/handshake", headers={"X-SSHLER-TOKEN": TEST_TOKEN})
        assert hs.status_code == 200
        status = await client.get(
            "/api/v1/boxes/local/status", headers={"X-SSHLER-TOKEN": TEST_TOKEN}
        )
        assert status.status_code == 200


def test_ws_connect(tmp_path, monkeypatch, fake_tmux, tmux_tripwire):
    """Bytes sent to /ws/term reach the PTY child (``cat``) and come back over the socket."""
    monkeypatch.setenv("SSHLER_CONFIG_DIR", str(_config_dir(tmp_path)))

    async def no_history(_session: str, _directory: str) -> None:
        return None

    async def no_live_sessions() -> set[str]:
        return set()

    monkeypatch.setattr("sshler.webapp.record_ts_history", no_history)
    # Startup recovery lists live sessions with the bare tmux binary.
    monkeypatch.setattr("sshler.snapshot.discover_local_sessions", no_live_sessions)
    workdir = tmp_path / "work"
    workdir.mkdir()

    received = b""
    with SyncClient(make_app(ServerSettings(csrf_token=TEST_TOKEN))) as sync_client:
        with sync_client.websocket_connect(
            f"/ws/term?host=local&dir={workdir}&session=test&cols=10&rows=5&token={TEST_TOKEN}"
        ) as ws:
            ws.send_bytes(b"ping-5e0d\n")
            deadline = time.monotonic() + 5.0
            while received.count(b"ping-5e0d") < 2 and time.monotonic() < deadline:
                message = ws.receive()
                if message.get("bytes"):
                    received += message["bytes"]

    # Once from the tty's own echo, once from `cat` writing back what it read.
    assert received.count(b"ping-5e0d") == 2
    assert [call for call in fake_tmux.calls() if call.startswith("new ")] == [
        f"new -As test -c {workdir}"
    ]
    assert not tmux_tripwire.exists(), tmux_tripwire.read_text()
