"""Every way ``/ws/term`` closes without a terminal: the exact close code, close reason
and the text sent before the close.

A row is one scene. A socket refused before it is accepted sends nothing; a terminal
that cannot open sends one line, then closes. The remote box is a fake connection
that runs nothing: the tmux check line is compared with ``remote.require``'s, and its
exit status is the scene's.
"""

from __future__ import annotations

import base64
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from urllib.parse import quote

import pytest
import yaml
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from sshler import state, webapp
from sshler.api.remote import command, require
from sshler.webapp import ServerSettings, make_app
from sshler.win_terminal_registry import TooManyTerminalsError

TOKEN = "closes-token"
USER = "admin"
PASSWORD = "Corr3ct-Horse-Battery!"
REMOTE_BOX = "demo-box"
TMUX_CHECK = command("sh", "-c", require("tmux"))
# A box name holding a quote, a newline and 200 more characters.
HOSTILE_BOX = "it's\nbox" + "x" * 200
# A box name that writes the clipboard when a terminal obeys it.
OSC_BOX = "a\x1b]52;c;QUJD\x07b"


def _url(host: str, *, session: str = "s1", token: str | None = TOKEN) -> str:
    url = f"/ws/term?host={quote(host)}&dir=/srv&session={quote(session)}"
    return url if token is None else f"{url}&token={quote(token)}"


def _basic(user: str, password: str) -> str:
    return "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode("ascii")


class CheckedConnection:
    """A remote connection that answers the tmux check with ``status`` and every other
    command with 0. It runs nothing; it records each command line and its close."""

    def __init__(self, status: int) -> None:
        self.status = status
        self.commands: list[str] = []
        self.closed = False

    async def run(self, command: str, check: bool = False, encoding: str | None = "utf-8"):
        self.commands.append(command)
        status = self.status if command == TMUX_CHECK else 0
        return SimpleNamespace(exit_status=status, returncode=status, stdout="", stderr="")

    def close(self) -> None:
        self.closed = True

    async def wait_closed(self) -> None:
        return None


@dataclass
class Scene:
    """What a row's scene left to check: the connection it handed out and what tmux was
    asked to open."""

    connection: CheckedConnection | None = None
    tmux_opened: list[str] = field(default_factory=list)


@pytest.fixture
def config_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    folder = tmp_path / "config"
    folder.mkdir()
    (folder / "boxes.yaml").write_text(yaml.safe_dump({"boxes": []}), encoding="utf-8")
    monkeypatch.setenv("SSHLER_CONFIG_DIR", str(folder))
    ssh_config = tmp_path / "ssh_config"
    ssh_config.write_text(f"Host {REMOTE_BOX}\n  HostName demo.internal\n  User demo\n")
    monkeypatch.setenv("SSHLER_SSH_CONFIG", str(ssh_config))
    state.reset_state()
    state.initialize(folder)
    yield folder
    state.reset_state()


def _remote(monkeypatch: pytest.MonkeyPatch, scene: Scene, status: int) -> None:
    connection = CheckedConnection(status)
    scene.connection = connection

    async def connect(*_args: Any, **_kwargs: Any) -> CheckedConnection:
        return connection

    async def is_directory(_connection: Any, _path: str) -> bool:
        return True

    async def open_tmux(_connection: Any, **kwargs: Any) -> Any:
        scene.tmux_opened.append(kwargs["session"])
        raise RuntimeError("the row expects no terminal to open")

    monkeypatch.setattr("sshler.webapp.connect", connect)
    monkeypatch.setattr("sshler.webapp.sftp_is_directory", is_directory)
    monkeypatch.setattr("sshler.webapp.open_tmux", open_tmux)


def _closes(client: TestClient, url: str, headers: dict[str, str]) -> tuple[int, str, list[str]]:
    """The close code, the close reason and the texts sent before the close."""
    try:
        with client.websocket_connect(url, headers=headers) as websocket:
            texts: list[str] = []
            while True:
                message = websocket.receive()
                if message["type"] == "websocket.send":
                    texts.append(message.get("text") or "")
                elif message["type"] == "websocket.close":
                    return message.get("code", 1000), message.get("reason") or "", texts
    except WebSocketDisconnect as refused:
        return refused.code, refused.reason, []


Setup = Callable[[pytest.MonkeyPatch, Path, Any, Scene], tuple[str, dict[str, str]]]


def _bad_session(mp, tmp_path, app, scene):
    return _url(REMOTE_BOX, session=""), {}


def _no_credentials(mp, tmp_path, app, scene):
    return _url(REMOTE_BOX), {}


def _bad_token(mp, tmp_path, app, scene):
    return _url(REMOTE_BOX, token="not-the-token"), {}


def _foreign_origin(mp, tmp_path, app, scene):
    return _url(REMOTE_BOX), {"Origin": "http://evil.example"}


def _unknown_box(mp, tmp_path, app, scene):
    return _url("no-such-box"), {}


def _hostile_unknown_box(mp, tmp_path, app, scene):
    return _url(HOSTILE_BOX), {}


def _osc_unknown_box(mp, tmp_path, app, scene):
    return _url(OSC_BOX), {}


def _remote_tmux_fails_to_start(mp, tmp_path, app, scene):
    _remote(mp, scene, 0)
    return _url(REMOTE_BOX), {}


def _remote_without_tmux(mp, tmp_path, app, scene):
    _remote(mp, scene, 127)
    return _url(REMOTE_BOX), {}


def _local_without_tmux(mp, tmp_path, app, scene):
    empty = tmp_path / "empty-path"
    empty.mkdir()
    # No program at all is on PATH: the local box has no tmux, and nothing can start.
    mp.setenv("PATH", str(empty))
    # The tmux check is the POSIX local box's; a Windows one opens a shell instead.
    mp.setattr(webapp, "LOCAL_IS_WINDOWS", False)
    return _url("local"), {}


def _windows_cap(mp, tmp_path, app, scene):
    class FullRegistry:
        async def get_or_create(self, key, spawn, cols, rows):
            raise TooManyTerminalsError("cap of 1 reached")

    mp.setattr(webapp, "LOCAL_IS_WINDOWS", True)
    app.state.win_terminal_registry = FullRegistry()
    return _url("local"), {}


@dataclass(frozen=True)
class Row:
    setup: Setup
    code: int
    reason: str
    sent: list[str]
    basic_auth: bool = False
    opens_tmux: bool = False


ROWS = {
    "4400-session-name": Row(_bad_session, 4400, "Invalid session name", []),
    "4401-credentials": Row(_no_credentials, 4401, "Unauthorized", [], basic_auth=True),
    "4403-token": Row(_bad_token, 4403, "Invalid token", []),
    "4403-origin": Row(_foreign_origin, 4403, "Invalid origin", []),
    "4404-unknown-box": Row(_unknown_box, 4404, "Unknown box", ["Unknown box: no-such-box\r\n"]),
    "4404-hostile-name": Row(
        _hostile_unknown_box,
        4404,
        "Unknown box",
        ["Unknown box: it's?box" + "x" * 56 + "\u2026\r\n"],
    ),
    "4404-escape-sequence": Row(
        _osc_unknown_box, 4404, "Unknown box", ["Unknown box: a?]52;c;QUJD?b\r\n"]
    ),
    "4501-remote": Row(
        _remote_without_tmux,
        4501,
        "tmux not installed",
        [f"tmux is not installed on {REMOTE_BOX}\r\n"],
    ),
    "1000-remote-tmux-fails": Row(
        _remote_tmux_fails_to_start,
        1000,
        "",
        ["Connection failed: the row expects no terminal to open\r\n"],
        opens_tmux=True,
    ),
    "4501-local": Row(
        _local_without_tmux, 4501, "tmux not installed", ["tmux is not installed on local\r\n"]
    ),
    "4504-windows-cap": Row(
        _windows_cap,
        4504,
        "Too many terminals",
        ["Too many open terminals on this server. Close one before opening another.\r\n"],
    ),
}


@pytest.mark.parametrize("row", list(ROWS.values()), ids=list(ROWS))
def test_a_socket_closes_with_its_code_reason_and_text(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, config_dir: Path, fake_tmux, row: Row
):
    basic = (USER, PASSWORD) if row.basic_auth else None
    app = make_app(
        ServerSettings(csrf_token=TOKEN, basic_auth=basic, serve_spa=False, snapshot_enabled=False)
    )
    scene = Scene()
    url, headers = row.setup(monkeypatch, tmp_path, app, scene)
    client = TestClient(app)
    try:
        assert _closes(client, url, headers) == (row.code, row.reason, row.sent)
    finally:
        client.close()
    assert len(row.reason.encode()) <= 123
    assert fake_tmux.calls() == []
    if scene.connection is not None:
        assert (scene.connection.commands, scene.connection.closed) == ([TMUX_CHECK], True)
    assert scene.tmux_opened == (["s1"] if row.opens_tmux else [])


@pytest.mark.parametrize(
    ("name", "shown"),
    [
        ("no-such-box", "no-such-box"),
        ("存在しないボックス", "存在しないボックス"),
        ("a\x1b]52;c;QUJD\x07b", "a?]52;c;QUJD?b"),
        ("a\r\nb", "a??b"),
        ("a\x9bb", "a?b"),
        ("a\u202eb", "a?b"),
        ("x" * 64, "x" * 64),
        ("x" * 65, "x" * 64 + "\u2026"),
        ("x" * 5000, "x" * 64 + "\u2026"),
    ],
    ids=[
        "plain",
        "japanese",
        "osc-52",
        "cr-lf",
        "c1-control",
        "rtl-override",
        "64-chars",
        "65-chars",
        "5000-chars",
    ],
)
def test_a_box_name_is_shown_cleaned_and_cut(name: str, shown: str) -> None:
    assert webapp._shown_box_name(name) == shown
