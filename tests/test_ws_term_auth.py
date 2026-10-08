"""The `/ws/term` boundary: token and auth close codes, and session-name sanitization
before any tmux argv is built (audit M5, roadmap T2).

Every test names the mutation it kills. The M5 mutation is deleting the
`session = PathValidator.sanitize_session_name(session)` call in `terminal_socket`.
"""

from __future__ import annotations

import asyncio
import base64
import time
from pathlib import Path
from urllib.parse import quote

import pytest
import yaml
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from sshler.config import ensure_config
from sshler.webapp import ServerSettings, make_app

TOKEN = "ws-term-test-token"
USER = "admin"
PASSWORD = "Corr3ct-Horse-Battery!"
HOSTILE_SESSION = "evil;touch pwned$(id)"
# Computed by hand from PathValidator's allow-list (alnum, "-", "_", "."):
# ";", " ", "$", "(", ")" each become "_".
SANITIZED_SESSION = "evil_touch_pwned__id_"


def _basic(user: str, password: str) -> str:
    return "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode("ascii")


# The close-code tests target a box that does not exist, so a mutation that lets the
# socket through closes it at `find_box` instead of spawning a terminal.
def _term_url(
    *,
    session: str = "s1",
    token: str | None = TOKEN,
    host: str = "no-such-box",
    directory: str = "/tmp",
) -> str:
    url = f"/ws/term?host={host}&dir={quote(directory)}&session={quote(session)}"
    if token is not None:
        url += f"&token={quote(token)}"
    return url


def _close_of(client: TestClient, url: str, **kwargs) -> tuple[int, str]:
    with pytest.raises(WebSocketDisconnect) as excinfo:
        with client.websocket_connect(url, **kwargs):
            pass
    return excinfo.value.code, excinfo.value.reason


@pytest.fixture
def client() -> TestClient:
    ensure_config()
    # No `with`: entering the client would run the lifespan (pool, PDF renderer).
    test_client = TestClient(make_app(ServerSettings(csrf_token=TOKEN)))
    yield test_client
    test_client.close()


@pytest.fixture
def auth_client() -> TestClient:
    ensure_config()
    test_client = TestClient(
        make_app(ServerSettings(csrf_token=TOKEN, basic_auth=(USER, PASSWORD)))
    )
    yield test_client
    test_client.close()


def test_missing_token_closes_4403(client: TestClient):
    """Kills: dropping the CSRF token check (the socket would be accepted)."""
    assert _close_of(client, _term_url(token=None)) == (4403, "Invalid token")


def test_bad_token_closes_4403(client: TestClient):
    """Kills: comparing the token loosely (e.g. only checking it is present)."""
    assert _close_of(client, _term_url(token="not-the-token")) == (4403, "Invalid token")


def test_auth_enabled_without_credentials_closes_4401(auth_client: TestClient):
    """Kills: dropping the `settings.auth_manager` credential check (a correct token
    alone would get through to 4403's check and be accepted)."""
    assert _close_of(auth_client, _term_url()) == (4401, "Unauthorized")


def test_auth_enabled_with_bad_basic_credentials_closes_4401(auth_client: TestClient):
    """Kills: accepting any `Authorization` header without verifying it."""
    headers = {"Authorization": _basic(USER, "wrong-password")}
    assert _close_of(auth_client, _term_url(), headers=headers) == (4401, "Unauthorized")


def test_auth_enabled_with_valid_basic_but_bad_token_closes_4403(auth_client: TestClient):
    """Kills: skipping the token check once Basic auth succeeds."""
    headers = {"Authorization": _basic(USER, PASSWORD)}
    assert _close_of(auth_client, _term_url(token="nope"), headers=headers) == (
        4403,
        "Invalid token",
    )


def test_empty_session_name_closes_4400(client: TestClient):
    """Kills: swallowing the `ValidationError` from `sanitize_session_name`."""
    assert _close_of(client, _term_url(session="")) == (4400, "Invalid session name")


def _receive_until(websocket, marker: bytes, count: int, seconds: float = 5.0) -> bytes:
    received = b""
    deadline = time.monotonic() + seconds
    while received.count(marker) < count and time.monotonic() < deadline:
        message = websocket.receive()
        if message.get("bytes"):
            received += message["bytes"]
    return received


def test_hostile_session_name_reaches_local_tmux_argv_sanitized(
    monkeypatch: pytest.MonkeyPatch,
    client: TestClient,
    fake_tmux,
    tmux_tripwire: Path,
    tmp_path: Path,
):
    """Kills the M5 mutation on the local path: without the handler's sanitize call the
    raw name reaches `local_tmux_command(session) + ["new", "-As", session]`."""

    async def no_history(_session: str, _directory: str) -> None:
        return None

    monkeypatch.setattr("sshler.webapp.record_ts_history", no_history)
    workdir = tmp_path / "work"
    workdir.mkdir()

    with client.websocket_connect(
        _term_url(session=HOSTILE_SESSION, host="local", directory=str(workdir))
    ) as websocket:
        # Wait for the PTY child (the fake tmux's `cat`) to echo, so the argv is logged.
        websocket.send_bytes(b"argv-ready-5e1\n")
        echoed = _receive_until(websocket, b"argv-ready-5e1", count=2)
    assert echoed.count(b"argv-ready-5e1") == 2

    calls = fake_tmux.calls()
    assert [call for call in calls if call.startswith("new ")] == [
        f"new -As {SANITIZED_SESSION} -c {workdir}"
    ]
    assert [call for call in calls if "evil" in call and SANITIZED_SESSION not in call] == []
    assert not tmux_tripwire.exists(), tmux_tripwire.read_text()


class _FakeStdout:
    async def read(self, size: int) -> bytes:
        await asyncio.sleep(0)
        return b""


class _FakeStdin:
    def write(self, message: bytes) -> None:
        pass

    def write_eof(self) -> None:
        pass


class _FakeProcess:
    def __init__(self) -> None:
        self.stdin = _FakeStdin()
        self.stdout = _FakeStdout()

    def close(self) -> None:
        pass


class _FakeConnection:
    def close(self) -> None:
        pass


@pytest.fixture
def remote_box(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> str:
    config_dir = tmp_path / "remote-config"
    config_dir.mkdir()
    monkeypatch.setenv("SSHLER_CONFIG_DIR", str(config_dir))
    (config_dir / "boxes.yaml").write_text(yaml.safe_dump({"boxes": []}), encoding="utf-8")
    ssh_config = tmp_path / "ssh_config"
    ssh_config.write_text(
        "Host demo-box\n  HostName demo.internal\n  User demo\n", encoding="utf-8"
    )
    monkeypatch.setenv("SSHLER_SSH_CONFIG", str(ssh_config))
    return "demo-box"


def _capture_remote_open_tmux(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    sessions: list[str] = []

    async def fake_connect(*_args, **_kwargs):
        return _FakeConnection()

    async def fake_sftp_is_directory(_connection, _path):
        return True

    async def fake_open_tmux(_connection, **kwargs):
        sessions.append(kwargs["session"])
        return _FakeProcess()

    monkeypatch.setattr("sshler.webapp.connect", fake_connect)
    monkeypatch.setattr("sshler.webapp.sftp_is_directory", fake_sftp_is_directory)
    monkeypatch.setattr("sshler.webapp.open_tmux", fake_open_tmux)
    return sessions


def test_hostile_session_name_reaches_remote_open_tmux_sanitized(
    monkeypatch: pytest.MonkeyPatch, remote_box: str
):
    """Kills the M5 mutation on the SSH path: `open_tmux` would receive the raw name."""
    sessions = _capture_remote_open_tmux(monkeypatch)
    client = TestClient(make_app(ServerSettings(csrf_token=TOKEN)))
    with client.websocket_connect(
        _term_url(session=HOSTILE_SESSION, host=remote_box, directory="/srv")
    ) as websocket:
        websocket.send_bytes(b"x")
    client.close()
    assert sessions == [SANITIZED_SESSION]


def test_valid_basic_auth_and_token_is_accepted(monkeypatch: pytest.MonkeyPatch, remote_box: str):
    """Positive control for the 4401/4403 tests: Basic auth plus the right token opens
    the terminal. Kills: rejecting every authenticated socket."""
    sessions = _capture_remote_open_tmux(monkeypatch)
    client = TestClient(make_app(ServerSettings(csrf_token=TOKEN, basic_auth=(USER, PASSWORD))))
    with client.websocket_connect(
        _term_url(session="plain", host=remote_box, directory="/srv"),
        headers={"Authorization": _basic(USER, PASSWORD)},
    ) as websocket:
        websocket.send_bytes(b"x")
    client.close()
    assert sessions == ["plain"]
