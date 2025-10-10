import asyncio
import os
import tempfile

import pytest
from fastapi.testclient import TestClient

if "SSHLER_CONFIG_DIR" not in os.environ:
    os.environ["SSHLER_CONFIG_DIR"] = tempfile.mkdtemp(prefix="sshler_")

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


def test_websocket_falls_back_to_default_directory(monkeypatch, configured_app: TestClient):
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

    with configured_app.websocket_connect(
        f"/ws/term?host={box.name}&dir=/does-not-exist&session=check&token={TEST_TOKEN}"
    ) as websocket:
        websocket.send_bytes(b"hello")

    assert captured["working_directory"] == fallback_directory
    assert fake_process.stdin.messages == [b"hello"]
    assert fake_process.stdin.eof_called is True
    assert fake_process.closed is True


def test_local_box_connects_successfully(monkeypatch, configured_app: TestClient):
    """Test that local boxes can connect successfully."""
    config = load_config()

    # Find or create a local box
    local_box = next((b for b in config.boxes if b.name == "local"), None)
    if not local_box:
        pytest.skip("No local box configured")

    class FakeLocalProcess:
        def __init__(self) -> None:
            self.stdin = FakeStdin()
            self.stdout = FakeStdout()
            self.terminated = False

        def terminate(self) -> None:
            self.terminated = True

        async def wait(self) -> None:
            await asyncio.sleep(0)

    async def fake_open_local_tmux(*args, **kwargs):
        return FakeLocalProcess()

    async def fake_local_is_directory(_path: str) -> bool:
        return True

    async def fake_list_local_tmux_windows(_session: str):
        return None

    monkeypatch.setattr("sshler.webapp._local_is_directory", fake_local_is_directory)
    monkeypatch.setattr("sshler.webapp._open_local_tmux", fake_open_local_tmux)
    monkeypatch.setattr("sshler.webapp._list_local_tmux_windows", fake_list_local_tmux_windows)

    with configured_app.websocket_connect(
        f"/ws/term?host=local&dir=/tmp&session=test&token={TEST_TOKEN}"
    ) as websocket:
        websocket.send_bytes(b"test")

    # If we get here without exceptions, the connection succeeded
    assert True
