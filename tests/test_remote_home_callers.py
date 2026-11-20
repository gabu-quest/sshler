"""A remote ``~`` is the box's home folder in the terminal, the git routes and a transfer.

A fake connection answers the home lookup and records every command line it is
given; a fake SFTP session records every path it is handed. Nothing here runs a
command: a command line is checked by splitting it into words.
"""

from __future__ import annotations

import asyncio
import os
import shlex
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import quote

import asyncssh
import pytest
import yaml
from fastapi.testclient import TestClient

from sshler import ssh_pool, state
from sshler.api.dependencies import APIDependencies
from sshler.ssh_pool import SSHConnectionPool
from sshler.webapp import ServerSettings, make_app

TOKEN = "home-callers-token"
HEADERS = {"X-SSHLER-TOKEN": TOKEN}
HOME_CMD = 'printf %s "$HOME"'


def _reply(stdout: str = "", status: int = 0) -> SimpleNamespace:
    return SimpleNamespace(exit_status=status, returncode=status, stdout=stdout, stderr="")


class FakeConnection:
    """Answers the home lookup, records every other command line, never runs one."""

    def __init__(
        self,
        home: str | None,
        sftp: FakeSFTP | None = None,
        home_error: Exception | None = None,
    ) -> None:
        self.home = home
        self.home_error = home_error
        self.commands: list[str] = []
        self.sftp = sftp

    async def run(self, command: str, check: bool = False, encoding: str | None = "utf-8"):
        if command == "echo test":
            return _reply("test\n")
        if command == HOME_CMD:
            if self.home_error is not None:
                raise self.home_error
            return _reply(self.home or "", 0 if self.home else 1)
        self.commands.append(command)
        return _reply()

    async def start_sftp_client(self) -> FakeSFTP:
        assert self.sftp is not None
        return self.sftp

    def close(self) -> None:
        return None

    async def wait_closed(self) -> None:
        return None


class FakeHandle:
    """A file of three bytes, read in one chunk."""

    def __init__(self) -> None:
        self.unread = [b"abc"]

    async def chmod(self, mode: int) -> None:
        return None

    async def write(self, data: bytes) -> None:
        return None

    async def read(self, n: int = -1) -> bytes:
        return self.unread.pop() if self.unread else b""

    async def close(self) -> None:
        return None

    async def __aenter__(self) -> FakeHandle:
        return self

    async def __aexit__(self, *exc) -> None:
        return None


class FakeSFTP:
    """Records the path of every call; a source file has three bytes, a destination is free."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    async def stat(self, path: str):
        self.calls.append(("stat", path))
        return SimpleNamespace(permissions=0o100644, size=3, mtime=1, mtime_ns=1)

    async def lstat(self, path: str):
        self.calls.append(("lstat", path))
        raise asyncssh.SFTPNoSuchFile("no such file")

    async def mkdir(self, path: str) -> None:
        self.calls.append(("mkdir", path))

    async def open(self, path: str, mode: str, attrs=None):
        self.calls.append(("open", path))
        return FakeHandle()

    def exit(self) -> None:
        return None


@pytest.fixture
def boxes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "boxes.yaml").write_text(yaml.safe_dump({"boxes": []}), encoding="utf-8")
    ssh_config = tmp_path / "ssh_config"
    ssh_config.write_text(
        "Host alpha\n  HostName alpha.invalid\n  User ann\n"
        "Host beta\n  HostName beta.invalid\n  User bob\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("SSHLER_CONFIG_DIR", str(config_dir))
    monkeypatch.setenv("SSHLER_SSH_CONFIG", str(ssh_config))
    state.initialize(config_dir)
    monkeypatch.setattr(ssh_pool, "_global_pool", SSHConnectionPool())
    try:
        yield
    finally:
        state.reset_state()


# --- the terminal ---------------------------------------------------------------


class _Stdout:
    async def read(self, size: int) -> bytes:
        await asyncio.sleep(0)
        return b""


class _Stdin:
    def write(self, message: bytes) -> None:
        pass

    def write_eof(self) -> None:
        pass


class _Process:
    def __init__(self) -> None:
        self.stdin = _Stdin()
        self.stdout = _Stdout()

    def close(self) -> None:
        pass


def _terminal(
    monkeypatch: pytest.MonkeyPatch, connection: FakeConnection, *, is_directory: bool = True
):
    """Open a remote terminal on ``connection``; what tmux and SFTP are given is recorded."""
    seen = SimpleNamespace(tmux_folder=None, probed=[])

    async def connect(*_args, **_kwargs):
        return connection

    async def sftp_is_directory(_connection, path):
        seen.probed.append(path)
        return is_directory

    async def open_tmux(_connection, **kwargs):
        seen.tmux_folder = kwargs["working_directory"]
        return _Process()

    monkeypatch.setattr("sshler.webapp.connect", connect)
    monkeypatch.setattr("sshler.webapp.sftp_is_directory", sftp_is_directory)
    monkeypatch.setattr("sshler.webapp.open_tmux", open_tmux)
    return seen


def _open_terminal(directory: str) -> None:
    client = TestClient(make_app(ServerSettings(csrf_token=TOKEN)))
    try:
        url = f"/ws/term?host=alpha&dir={quote(directory)}&session=s1&token={TOKEN}"
        with client.websocket_connect(url) as websocket:
            websocket.send_bytes(b"x")
    finally:
        client.close()


@pytest.mark.parametrize(
    ("asked", "folder"),
    [("~", "/home/ann"), ("~/proj", "/home/ann/proj"), ("~/", "/home/ann")],
)
def test_a_remote_terminal_opened_in_the_home_starts_in_the_home_folder(
    boxes, monkeypatch, asked, folder
):
    seen = _terminal(monkeypatch, FakeConnection("/home/ann"))
    _open_terminal(asked)
    assert (seen.tmux_folder, seen.probed) == (folder, [folder])


def test_a_remote_terminal_in_an_absolute_folder_asks_nothing_about_the_home(boxes, monkeypatch):
    connection = FakeConnection(None)  # a home lookup would fail
    seen = _terminal(monkeypatch, connection)
    _open_terminal("/srv/app")
    assert seen.tmux_folder == "/srv/app"


def test_a_remote_terminal_in_a_missing_home_subfolder_starts_in_the_box_default(
    boxes, monkeypatch
):
    # The box's default folder is /home/ann; its home is somewhere else.
    seen = _terminal(monkeypatch, FakeConnection("/users/ann"), is_directory=False)
    _open_terminal("~/gone")
    assert (seen.tmux_folder, seen.probed) == ("/home/ann", ["/users/ann/gone"])


def test_a_remote_terminal_on_a_box_whose_home_cannot_be_read_starts_in_the_box_default(
    boxes, monkeypatch
):
    """The terminal still opens: it is the way to repair such a box."""
    seen = _terminal(monkeypatch, FakeConnection(None))
    _open_terminal("~")
    assert (seen.tmux_folder, seen.probed) == ("/home/ann", [])


@pytest.mark.parametrize(
    "error",
    [asyncssh.ChannelOpenError(1, "open failed"), TimeoutError()],
    ids=["refused", "timeout"],
)
def test_a_remote_terminal_whose_home_lookup_raises_starts_in_the_box_default(
    boxes, monkeypatch, error
):
    seen = _terminal(monkeypatch, FakeConnection("/users/ann", home_error=error))
    _open_terminal("~/proj")
    assert (seen.tmux_folder, seen.probed) == ("/home/ann", [])


def _default_folder(folder: str) -> None:
    """Give the box ``alpha`` a default folder in ``boxes.yaml``."""
    boxes_yaml = Path(os.environ["SSHLER_CONFIG_DIR"]) / "boxes.yaml"
    boxes_yaml.write_text(
        yaml.safe_dump({"boxes": [{"name": "alpha", "default_dir": folder}]}), encoding="utf-8"
    )


def test_a_box_default_folder_under_the_home_is_resolved_when_the_terminal_falls_back_to_it(
    boxes, monkeypatch
):
    _default_folder("~/code")
    seen = _terminal(monkeypatch, FakeConnection("/home/ann"), is_directory=False)
    _open_terminal("/srv/gone")
    assert (seen.tmux_folder, seen.probed) == ("/home/ann/code", ["/srv/gone"])


def test_a_terminal_with_no_home_and_a_default_folder_under_the_home_starts_in_the_root(
    boxes, monkeypatch
):
    _default_folder("~/code")
    seen = _terminal(monkeypatch, FakeConnection(None))
    _open_terminal("~")
    assert (seen.tmux_folder, seen.probed) == ("/", [])


# --- the git routes -------------------------------------------------------------


@pytest.fixture
def git_box(boxes, monkeypatch):
    connection = FakeConnection("/home/ann")

    async def fake_connect(self, box, application_config):
        return connection

    monkeypatch.setattr(APIDependencies, "connect_for_box", fake_connect)
    client = TestClient(make_app(ServerSettings(csrf_token=TOKEN)))
    try:
        yield SimpleNamespace(client=client, connection=connection)
    finally:
        client.close()


def _git(git_box, route: str, **params):
    return git_box.client.get(f"/api/v1/boxes/alpha/git/{route}", params=params, headers=HEADERS)


def _first_words(git_box, count: int) -> list[str]:
    assert len(git_box.connection.commands) == 1
    return shlex.split(git_box.connection.commands[0])[:count]


@pytest.mark.parametrize("asked", ["~", "~/proj"])
@pytest.mark.parametrize("route", ["log", "branches"])
def test_a_git_route_on_a_remote_home_folder_changes_into_the_resolved_folder(
    git_box, route, asked
):
    folder = "/home/ann" if asked == "~" else "/home/ann/proj"
    resp = _git(git_box, route, directory=asked)
    assert resp.status_code == 200
    assert _first_words(git_box, 3) == ["cd", folder, "&&"]


def test_git_diff_files_on_a_remote_home_folder_changes_into_the_resolved_folder(git_box):
    _git(git_box, "diff-files", directory="~/proj", ref_a="main", ref_b="dev")
    assert _first_words(git_box, 3) == ["cd", "/home/ann/proj", "&&"]


@pytest.mark.parametrize("route", ["show", "blame"])
def test_git_show_and_blame_on_a_remote_home_folder_look_for_the_file_under_the_resolved_folder(
    git_box, route
):
    _git(git_box, route, directory="~/proj", path="a.txt")
    words = shlex.split(git_box.connection.commands[0])
    assert (words[:3], "DIR=/home/ann/proj" in words) == (["cd", "/home/ann/proj", "&&"], True)


def test_a_git_route_with_a_failed_home_lookup_answers_502(boxes, monkeypatch):
    connection = FakeConnection(None)

    async def fake_connect(self, box, application_config):
        return connection

    monkeypatch.setattr(APIDependencies, "connect_for_box", fake_connect)
    client = TestClient(make_app(ServerSettings(csrf_token=TOKEN)))
    try:
        resp = client.get("/api/v1/boxes/alpha/git/log", params={"directory": "~"}, headers=HEADERS)
    finally:
        client.close()
    assert (resp.status_code, resp.json(), connection.commands) == (
        502,
        {"detail": "Could not resolve the remote home directory"},
        [],
    )


# --- a transfer -----------------------------------------------------------------


def _transfer(client, paths: list[str], destination: str) -> str:
    resp = client.post(
        "/api/v1/transfer",
        json={"src_box": "alpha", "dest_box": "beta", "paths": paths, "destination": destination},
        headers=HEADERS,
    )
    assert resp.status_code == 200
    return resp.text


def test_a_transfer_between_remote_boxes_resolves_each_sides_home_and_names_the_request_paths(
    boxes, monkeypatch
):
    sides = {
        "alpha": FakeConnection("/home/ann", FakeSFTP()),
        "beta": FakeConnection("/home/bob", FakeSFTP()),
    }

    async def fake_connect(self, box, application_config):
        return sides[box.name]

    monkeypatch.setattr(APIDependencies, "connect_for_box", fake_connect)
    client = TestClient(make_app(ServerSettings(csrf_token=TOKEN)))
    try:
        text = _transfer(client, ["~/docs/a.txt"], "~/in")
    finally:
        client.close()
    assert (
        sides["alpha"].sftp.calls,
        [path for call, path in sides["beta"].sftp.calls if call == "open"],
        '"file": "~/docs/a.txt"' in text,
        '"succeeded": ["~/docs/a.txt"]' in text,
    ) == (
        [("stat", "/home/ann/docs/a.txt"), ("open", "/home/ann/docs/a.txt")],
        ["/home/bob/in/a.txt"],
        True,
        True,
    )


def test_a_transfer_with_a_failed_home_lookup_ends_with_an_error_event_and_copies_nothing(
    boxes, monkeypatch
):
    sides = {
        "alpha": FakeConnection(None, FakeSFTP()),
        "beta": FakeConnection("/home/bob", FakeSFTP()),
    }

    async def fake_connect(self, box, application_config):
        return sides[box.name]

    monkeypatch.setattr(APIDependencies, "connect_for_box", fake_connect)
    client = TestClient(make_app(ServerSettings(csrf_token=TOKEN)))
    try:
        text = _transfer(client, ["~/a.txt"], "/in")
    finally:
        client.close()
    assert (text, sides["alpha"].sftp.calls, sides["beta"].sftp.calls) == (
        'event: error\ndata: {"detail": "Could not resolve the remote home directory"}\n\n',
        [],
        [],
    )
