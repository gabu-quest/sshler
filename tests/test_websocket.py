import asyncio
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from sshler.config import ensure_config, load_config
from sshler.webapp import ServerSettings, make_app

TEST_TOKEN = "test-token"


class FakeStdout:
    async def read(self, size: int) -> bytes:
        await asyncio.sleep(0)
        return b""


class FakeStdin:
    def __init__(self) -> None:
        self.messages: list[bytes] = []
        self.eof_called = False

    def write(self, message: bytes) -> None:
        self.messages.append(message)

    def write_eof(self) -> None:
        self.eof_called = True


class FakeProcess:
    def __init__(self) -> None:
        self.stdin = FakeStdin()
        self.stdout = FakeStdout()
        self.closed = False

    def close(self) -> None:
        self.closed = True


class FakeConnection:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True

    async def start_sftp_client(self):
        raise AssertionError("sftp client should not be used in fallback test")


@pytest.fixture(name="configured_app")
def configured_app_fixture() -> TestClient:
    ensure_config()
    client = TestClient(make_app(ServerSettings(csrf_token=TEST_TOKEN)))
    try:
        yield client
    finally:
        client.close()


def test_websocket_falls_back_to_default_directory(monkeypatch, tmp_path):
    import yaml

    config_dir = tmp_path / "config"
    config_dir.mkdir()
    monkeypatch.setenv("SSHLER_CONFIG_DIR", str(config_dir))
    (config_dir / "boxes.yaml").write_text(
        yaml.safe_dump({"boxes": []}, sort_keys=False), encoding="utf-8"
    )

    ssh_config = tmp_path / "ssh_config"
    ssh_config.write_text(
        """
Host demo-box
  HostName demo.internal
  User demo
""".strip(),
        encoding="utf-8",
    )
    monkeypatch.setenv("SSHLER_SSH_CONFIG", str(ssh_config))

    client = TestClient(make_app(ServerSettings(csrf_token=TEST_TOKEN)))
    try:
        config = load_config()
        box = next(b for b in config.boxes if b.name != "local")
        fallback_directory = box.default_dir or f"/home/{box.user}"

        fake_process = FakeProcess()
        captured: dict[str, object] = {}

        async def fake_connect(*_args, **_kwargs):
            return FakeConnection()

        async def fake_sftp_is_directory(_connection, _path):
            return False

        async def fake_open_tmux(*_args, **kwargs):
            captured["working_directory"] = kwargs["working_directory"]
            return fake_process

        monkeypatch.setattr("sshler.webapp.connect", fake_connect)
        monkeypatch.setattr("sshler.webapp.sftp_is_directory", fake_sftp_is_directory)
        monkeypatch.setattr("sshler.webapp.open_tmux", fake_open_tmux)

        with client.websocket_connect(
            f"/ws/term?host={box.name}&dir=/does-not-exist&session=check&token={TEST_TOKEN}"
        ) as websocket:
            websocket.send_bytes(b"hello")

        assert captured["working_directory"] == fallback_directory
        # SSH processes with encoding=None receive raw bytes
        assert fake_process.stdin.messages == [b"hello"]
        assert fake_process.stdin.eof_called is True
        assert fake_process.closed is True
    finally:
        client.close()


def _receive_until(websocket, marker: bytes, count: int, seconds: float = 5.0) -> bytes:
    received = b""
    deadline = time.monotonic() + seconds
    while received.count(marker) < count and time.monotonic() < deadline:
        message = websocket.receive()
        if message.get("bytes"):
            received += message["bytes"]
    return received


def test_local_box_connects_successfully(
    monkeypatch, configured_app: TestClient, fake_tmux, tmux_tripwire: Path, tmp_path: Path
):
    """A local terminal bridges websocket bytes to the PTY child and back."""
    config = load_config()
    assert any(b.name == "local" for b in config.boxes)

    async def no_history(_session: str, _directory: str) -> None:
        return None

    monkeypatch.setattr("sshler.webapp.record_ts_history", no_history)
    workdir = tmp_path / "work"
    workdir.mkdir()

    with configured_app.websocket_connect(
        f"/ws/term?host=local&dir={workdir}&session=test&cols=80&rows=24&token={TEST_TOKEN}"
    ) as websocket:
        websocket.send_bytes(b"roundtrip-91c2\n")
        echoed = _receive_until(websocket, b"roundtrip-91c2", count=2)

    # Once from the tty's own echo, once from `cat` writing back what it read.
    assert echoed.count(b"roundtrip-91c2") == 2
    assert [call for call in fake_tmux.calls() if call.startswith("new ")] == [
        f"new -As test -c {workdir}"
    ]
    assert not tmux_tripwire.exists(), tmux_tripwire.read_text()
