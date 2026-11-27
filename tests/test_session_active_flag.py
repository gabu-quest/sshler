"""A tmux session's row is active exactly while tmux has that session.

The local tests drive the real app over raw ASGI on the test's event loop (as
``tests/test_terminal_endings.py`` does) against a real tmux: every server runs in the
run's private ``TMUX_TMPDIR`` with a throwaway ``HOME`` and ``/bin/sh`` as its shell,
and each test stops the servers it started by the process id it recorded. The flag is
read back through the routes the page reads: the sessions list and the sync.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import shlex
import shutil
import signal
import subprocess
import time
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import asyncssh
import pytest
import yaml
from httpx import ASGITransport, AsyncClient
from terminal_fakes import TMUX_CHECK_LINE, FakeSshChannel, FakeSshConnection
from test_terminal_endings import AsgiSocket

from sshler import snapshot, state, tmux
from sshler.api import sessions as sessions_api
from sshler.api.dependencies import APIDependencies
from sshler.webapp import ServerSettings, make_app

pytestmark = [
    pytest.mark.posix_only("runs a real tmux on PTYs"),
    pytest.mark.skipif(shutil.which("tmux") is None, reason="needs tmux"),
]

TOKEN = "flag-token"
REMOTE_BOX = "demo-box"
# How long a test waits for something it caused, before it fails.
LIMIT = 10.0
# Found once, so a test that empties PATH still stops its servers.
TMUX = shutil.which("tmux")


# --- tmux servers of this test --------------------------------------------------------


def _tmux(server: str | None, *args: str) -> subprocess.CompletedProcess[str]:
    """``tmux -L ts-<server> ARGS`` (the default server when *server* is None) in the
    run's private socket folder."""
    assert TMUX is not None
    words = [TMUX] if server is None else [TMUX, "-L", f"ts-{server}"]
    return subprocess.run(
        [*words, *args],
        capture_output=True,
        text=True,
        timeout=5,
        check=False,
        stdin=subprocess.DEVNULL,
    )


def _start(server: str | None, session: str, folder: Path | None = None) -> None:
    """A detached session *session* on *server* (in *folder* when given), as ``ts`` or a
    plain ``tmux`` in an SSH login starts one; no pipe for the server it forks to hold."""
    assert TMUX is not None
    words = [TMUX] if server is None else [TMUX, "-L", f"ts-{server}"]
    where = [] if folder is None else ["-c", str(folder)]
    started = subprocess.run(
        [*words, "new-session", "-d", "-s", session, *where],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=5,
        check=False,
    )
    assert started.returncode == 0


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


async def _until(check: Any, what: str) -> None:
    """Wait until ``check()`` is true, at most ``LIMIT`` seconds."""
    deadline = time.monotonic() + LIMIT
    while not check():
        if time.monotonic() > deadline:
            raise AssertionError(f"waited {LIMIT:g} s for {what}")
        await asyncio.sleep(0.02)


class Servers:
    """The tmux servers a test starts. A server is named (``expect``) before anything
    that starts it runs, so it is stopped even when the test fails before it read the
    server's process id; every one is stopped by that process id, never by name."""

    def __init__(self) -> None:
        self.expected: list[str | None] = []
        self.pids: dict[str | None, int] = {}

    def expect(self, server: str | None) -> None:
        """*server* (``ts-<server>``, or the default server for None) may be started."""
        if server not in self.expected:
            self.expected.append(server)

    def record(self, server: str | None) -> int:
        self.expect(server)
        pid = self._pid(server)
        assert pid is not None, f"no tmux server for {server!r}"
        self.pids[server] = pid
        return pid

    def stop_all(self) -> None:
        for server in self.expected:
            pid = self.pids.get(server) or self._pid(server)
            if pid is not None:
                with contextlib.suppress(ProcessLookupError):
                    os.kill(pid, signal.SIGKILL)

    @staticmethod
    def _pid(server: str | None) -> int | None:
        shown = _tmux(server, "display-message", "-p", "#{pid}")
        text = shown.stdout.strip()
        return int(text) if shown.returncode == 0 and text.isdigit() else None


@pytest.fixture
def servers() -> Iterator[Servers]:
    started = Servers()
    yield started
    started.stop_all()


# --- the app --------------------------------------------------------------------------


@pytest.fixture
def app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Any]:
    config_dir = Path(os.environ["SSHLER_CONFIG_DIR"])
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "boxes.yaml").write_text(yaml.safe_dump({"boxes": []}), encoding="utf-8")
    ssh_config = tmp_path / "ssh_config"
    ssh_config.write_text(f"Host {REMOTE_BOX}\n  HostName demo.internal\n  User demo\n")
    monkeypatch.setenv("SSHLER_SSH_CONFIG", str(ssh_config))
    monkeypatch.setenv("SHELL", "/bin/sh")
    state.reset_state()
    state.initialize(config_dir)

    async def no_history(_session: str, _directory: str) -> None:
        return None

    monkeypatch.setattr("sshler.webapp.record_ts_history", no_history)
    yield make_app(ServerSettings(csrf_token=TOKEN, serve_spa=False, snapshot_enabled=False))


@pytest.fixture
def workdir(tmp_path: Path) -> Path:
    path = tmp_path / "work"
    path.mkdir()
    return path


def _query(workdir: Path, session: str, host: str = "local") -> str:
    return f"host={host}&dir={workdir}&session={session}&cols=80&rows=24&token={TOKEN}"


async def _open(app: Any, workdir: Path, session: str, servers: Servers) -> AsgiSocket:
    """A tab on local tmux session *session*, once its shell has run a command; its
    server is named to *servers* before the tab starts it."""
    servers.expect(session)
    sock = AsgiSocket(app, _query(workdir, session))
    sock.start()
    sock.send_bytes(b"echo up-$((1+1))\n")
    await sock.wait_output(b"up-2", seconds=LIMIT)
    return sock


async def _close(sock: AsgiSocket) -> None:
    sock.disconnect()
    await sock.finished()


async def _listed(app: Any, box: str = "local") -> list[tuple[str, bool]]:
    """What the sessions list answers: (name, active) of each row."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        answer = await client.get(
            f"/api/v1/boxes/{box}/sessions", headers={"X-SSHLER-TOKEN": TOKEN}
        )
    assert answer.status_code == 200
    return sorted((row["session_name"], row["active"]) for row in answer.json())


async def _synced(app: Any, box: str = "local") -> tuple[int, Any]:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        answer = await client.post(
            f"/api/v1/boxes/{box}/sessions/sync", headers={"X-SSHLER-TOKEN": TOKEN}
        )
    return answer.status_code, answer.json()


# --- a socket ends --------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_tab_that_closes_on_a_living_session_leaves_it_active(
    app, servers: Servers, workdir: Path
) -> None:
    sock = await _open(app, workdir, "flag-close", servers)
    pid = servers.record("flag-close")
    await _close(sock)

    assert _alive(pid) is True
    assert await _listed(app) == [("flag-close", True)]


@pytest.mark.asyncio
async def test_one_of_two_tabs_on_a_session_closing_leaves_it_active(
    app, servers: Servers, workdir: Path
) -> None:
    first = await _open(app, workdir, "flag-two", servers)
    servers.record("flag-two")
    second = await _open(app, workdir, "flag-two", servers)
    await _close(first)
    after_first = await _listed(app)
    await _close(second)

    assert (after_first, await _listed(app)) == ([("flag-two", True)], [("flag-two", True)])


@pytest.mark.asyncio
async def test_a_detach_by_the_tmux_key_leaves_the_session_active(
    app, servers: Servers, workdir: Path
) -> None:
    sock = await _open(app, workdir, "flag-detach", servers)
    pid = servers.record("flag-detach")
    sock.send_bytes(b"\x02d")  # Ctrl+B d: the tmux client detaches and exits
    await sock.finished()

    assert (sock.closes(), _alive(pid)) == ([(1000, "shell exited")], True)
    assert await _listed(app) == [("flag-detach", True)]


@pytest.mark.asyncio
async def test_exit_in_the_last_window_with_a_tab_open_leaves_the_session_inactive(
    app, servers: Servers, workdir: Path
) -> None:
    sock = await _open(app, workdir, "flag-exit", servers)
    pid = servers.record("flag-exit")
    sock.send_bytes(b"exit\n")
    await sock.finished()
    await _until(lambda: not _alive(pid), "the tmux server to end")

    assert sock.closes() == [(1000, "shell exited")]
    assert await _listed(app) == [("flag-exit", False)]


def _unanswering(monkeypatch: pytest.MonkeyPatch, session: str) -> None:
    """From now on every tmux command to the ``ts-<session>`` server runs past its
    limit (the server is wedged, or slow at shutdown); every other one runs."""
    real = tmux.run_bounded

    async def bounded(argv: list[str], timeout: float, **kwargs: Any) -> Any:
        if f"ts-{session}" in argv:
            raise TimeoutError(f"{argv} timed out after {timeout} s")
        return await real(argv, timeout, **kwargs)

    monkeypatch.setattr("sshler.tmux.run_bounded", bounded)


@pytest.mark.asyncio
async def test_a_tab_that_closes_while_its_tmux_does_not_answer_leaves_the_row_active(
    app, servers: Servers, workdir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sock = await _open(app, workdir, "flag-slow", servers)
    servers.record("flag-slow")
    _unanswering(monkeypatch, "flag-slow")

    await _close(sock)

    assert await _listed(app) == [("flag-slow", True)]


@pytest.mark.asyncio
async def test_a_tab_close_asks_tmux_only_about_its_own_session(
    app, servers: Servers, workdir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    others = [f"flag-other-{n}" for n in range(5)]
    for name in others:
        servers.expect(name)
        _start(name, name)
        servers.record(name)
    sock = await _open(app, workdir, "flag-own", servers)
    servers.record("flag-own")
    asked: list[list[str]] = []
    real = tmux.run_bounded

    async def counted(argv: list[str], timeout: float, **kwargs: Any) -> Any:
        asked.append(argv)
        return await real(argv, timeout, **kwargs)

    monkeypatch.setattr("sshler.tmux.run_bounded", counted)

    await _close(sock)

    assert sorted(asked) == sorted(
        [
            ["tmux", "-L", "ts-flag-own", "list-sessions", "-F", tmux.LIST_FORMAT],
            ["tmux", "list-sessions", "-F", tmux.LIST_FORMAT],
        ]
    )
    assert await _listed(app) == [("flag-own", True)]


@pytest.mark.asyncio
async def test_a_session_on_the_default_server_keeps_its_row_active_after_its_tab_closes(
    app, servers: Servers, workdir: Path
) -> None:
    # A session made before the ts convention lives on the default server. A tab on
    # its name runs on ts-<name>; once that server is gone the tab ends, and the row
    # says what tmux has: the session on the default server.
    servers.expect(None)
    _start(None, "flag-legacy")
    servers.record(None)
    sock = await _open(app, workdir, "flag-legacy", servers)
    os.kill(servers.record("flag-legacy"), signal.SIGKILL)
    await sock.finished()

    assert await _listed(app) == [("flag-legacy", True)]


class _SilentConnection(FakeSshConnection):
    """A connection whose box stops answering once the terminal is open."""

    async def run(self, command: str, *args: Any, **kwargs: Any) -> Any:
        if command == TMUX_CHECK_LINE:
            return await super().run(command, *args, **kwargs)
        await asyncio.Event().wait()


class _LostConnection(FakeSshConnection):
    """A connection that is lost once the terminal is open."""

    async def run(self, command: str, *args: Any, **kwargs: Any) -> Any:
        if command == TMUX_CHECK_LINE:
            return await super().run(command, *args, **kwargs)
        raise asyncssh.ConnectionLost("connection dropped")


class _ListingConnection(FakeSshConnection):
    """A connection to a box whose tmux has the sessions *names*: it answers the session
    listing with them and with *status* (0: every server answered)."""

    def __init__(self, names: list[str], status: int = 0, output: str | None = None) -> None:
        super().__init__()
        self.names = names
        self.status = status
        self.output = output

    async def run(self, command: str, *args: Any, **kwargs: Any) -> Any:
        if shlex.split(command)[:3] != ["sh", "-c", sessions_api.REMOTE_LIST_SESSIONS]:
            return await super().run(command, *args, **kwargs)
        self.commands.append(command)
        if self.output is not None:
            out = self.output.encode()
        else:
            out = "".join(f"{name}\n" for name in self.names).encode()
        return SimpleNamespace(exit_status=self.status, stdout=out, stderr=b"")

    def listings(self) -> int:
        return sum(
            shlex.split(line)[:3] == ["sh", "-c", sessions_api.REMOTE_LIST_SESSIONS]
            for line in self.commands
        )


async def _remote_tab(
    app: Any, monkeypatch: pytest.MonkeyPatch, fake: FakeSshConnection, session: str
) -> AsgiSocket:
    """A tab on *session* of the remote box, its connection *fake*, once its shell ran."""
    channel = FakeSshChannel()

    async def connect(*_args: Any, **_kwargs: Any) -> FakeSshConnection:
        return fake

    async def is_directory(_connection: Any, _path: str) -> bool:
        return True

    async def open_tmux(_connection: Any, **_kwargs: Any) -> FakeSshChannel:
        return channel

    monkeypatch.setattr("sshler.webapp.connect", connect)
    monkeypatch.setattr("sshler.webapp.sftp_is_directory", is_directory)
    monkeypatch.setattr("sshler.webapp.open_tmux", open_tmux)
    sock = AsgiSocket(app, _query(Path("/srv"), session, host=REMOTE_BOX))
    sock.start()
    sock.send_bytes(b"echo ready\n")
    await sock.wait_output(b"ready\r\n", count=2)
    return sock


@pytest.mark.asyncio
@pytest.mark.parametrize("connection", [_SilentConnection, _LostConnection], ids=["silent", "lost"])
async def test_a_remote_box_that_cannot_be_asked_when_the_tab_closes_leaves_the_row_as_it_was(
    app, monkeypatch: pytest.MonkeyPatch, connection: type[FakeSshConnection]
) -> None:
    monkeypatch.setattr("sshler.tmux.TMUX_COMMAND_TIMEOUT", 0.5)
    sock = await _remote_tab(app, monkeypatch, connection(), "flag-remote")

    started = time.monotonic()
    sock.disconnect()
    await sock.finished(seconds=5.0)

    assert time.monotonic() - started < 3.0
    assert await _listed(app, REMOTE_BOX) == [("flag-remote", True)]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("names", "active"), [(["flag-remote", "other"], True), (["other"], False)], ids=["has", "gone"]
)
async def test_a_remote_tab_that_closes_leaves_the_row_saying_what_the_listing_says(
    app, monkeypatch: pytest.MonkeyPatch, names: list[str], active: bool
) -> None:
    fake = _ListingConnection(names)
    sock = await _remote_tab(app, monkeypatch, fake, "flag-remote")

    await _close(sock)

    assert (fake.listings(), await _listed(app, REMOTE_BOX)) == (1, [("flag-remote", active)])


@pytest.mark.asyncio
async def test_a_remote_listing_that_could_not_ask_a_server_leaves_an_unseen_session_row_as_it_was(
    app, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = _ListingConnection(["other"], status=sessions_api.REMOTE_LISTING_INCOMPLETE)
    sock = await _remote_tab(app, monkeypatch, fake, "flag-remote")

    await _close(sock)

    assert (fake.listings(), await _listed(app, REMOTE_BOX)) == (1, [("flag-remote", True)])


# --- a sync ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_sync_makes_the_row_of_a_living_session_active(
    app, servers: Servers, workdir: Path
) -> None:
    sock = await _open(app, workdir, "flag-sync", servers)
    servers.record("flag-sync")
    await _close(sock)
    row = await state.get_session_by_name_async("local", "flag-sync")
    assert row is not None
    await state.update_session_activity_async(row.id, active=False)

    status, rows = await _synced(app)

    assert status == 200
    assert [(r["session_name"], r["active"]) for r in rows] == [("flag-sync", True)]


@pytest.mark.asyncio
async def test_a_session_ended_in_another_terminal_is_inactive_at_the_next_sync(
    app, servers: Servers, workdir: Path
) -> None:
    sock = await _open(app, workdir, "flag-ended", servers)
    pid = servers.record("flag-ended")
    await _close(sock)
    assert _tmux("flag-ended", "send-keys", "-t", "flag-ended", "exit", "Enter").returncode == 0
    await _until(lambda: not _alive(pid), "the tmux server to end")

    status, rows = await _synced(app)

    assert status == 200
    assert [(r["session_name"], r["active"]) for r in rows] == [("flag-ended", False)]


@pytest.mark.asyncio
async def test_a_sync_while_the_tmux_of_a_living_session_does_not_answer_leaves_its_row_active(
    app, servers: Servers, workdir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sock = await _open(app, workdir, "flag-slow", servers)
    servers.record("flag-slow")
    await _close(sock)
    _unanswering(monkeypatch, "flag-slow")

    status, rows = await _synced(app)

    assert status == 200
    assert [(r["session_name"], r["active"]) for r in rows] == [("flag-slow", True)]


@pytest.mark.asyncio
@pytest.mark.parametrize("active", [True, False])
async def test_a_sync_on_a_remote_box_sets_each_row_by_the_listing(
    app, monkeypatch: pytest.MonkeyPatch, active: bool
) -> None:
    from sshler import ssh_pool
    from sshler.ssh_pool import SSHConnectionPool

    for name in ("flag-has", "flag-gone"):
        row = await state.create_or_update_session_async(
            REMOTE_BOX, name, "/srv", metadata={"transport": "ssh"}
        )
        await state.update_session_activity_async(row.id, active=active)
    fake = _ListingConnection(["flag-has", "other"])

    async def connect_for_box(_deps: Any, _box: Any, _config: Any) -> FakeSshConnection:
        return fake

    monkeypatch.setattr(APIDependencies, "connect_for_box", connect_for_box)
    monkeypatch.setattr(ssh_pool, "_global_pool", SSHConnectionPool())

    status, rows = await _synced(app, REMOTE_BOX)

    assert status == 200
    assert sorted((r["session_name"], r["active"]) for r in rows) == [
        ("flag-gone", False),
        ("flag-has", True),
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("active", [True, False])
async def test_a_sync_on_a_box_without_tmux_answers_the_failure_and_changes_no_row(
    app, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, active: bool
) -> None:
    row = await state.create_or_update_session_async("local", "flag-none", "/")
    await state.update_session_activity_async(row.id, active=active)
    empty = tmp_path / "empty-path"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))

    status, body = await _synced(app)

    assert (status, body["reason"]) == (501, "program_missing")
    assert await _listed(app) == [("flag-none", active)]


@pytest.mark.asyncio
async def test_a_sync_leaves_the_row_of_a_persisted_windows_shell_alone(
    app, fake_tmux, monkeypatch: pytest.MonkeyPatch
) -> None:
    # On a Windows server a local row is a persisted native shell, never tmux.
    monkeypatch.setattr("sshler.tmux._IS_WINDOWS", True)
    await state.create_or_update_session_async(
        "local", "flag-windows", "C:/", metadata={"transport": "local"}
    )

    status, rows = await _synced(app)

    assert status == 200
    assert [(r["session_name"], r["active"]) for r in rows] == [("flag-windows", True)]


# --- a session started outside sshler -------------------------------------------------


def _rows(rows: Any) -> list[tuple[str, bool, str, str]]:
    """(name, active, folder, transport) of each row a route answered, by name."""
    return sorted(
        (r["session_name"], r["active"], r["working_directory"], r["metadata"].get("transport"))
        for r in rows
    )


@pytest.fixture
def outside_folder(tmp_path: Path) -> Path:
    """A folder whose name holds a space, a tab and a non-ASCII character."""
    path = tmp_path / "an outside\tfolder é"
    path.mkdir()
    return path


@pytest.mark.asyncio
async def test_a_sync_makes_one_row_for_a_session_started_outside_sshler(
    app, servers: Servers, outside_folder: Path, tmp_path: Path
) -> None:
    for server, session in (
        ("flag-out", "flag-out"),
        ("flag-out", "flag-stray"),
        (None, "flag-default"),
    ):
        servers.expect(server)
        _start(server, session, tmp_path)
    servers.expect("flag-own")
    _start("flag-own", "flag-own", outside_folder)
    for server in ("flag-out", None, "flag-own"):
        servers.record(server)

    first_status, first = await _synced(app)
    second_status, second = await _synced(app)

    expected = [
        ("flag-out", True, str(tmp_path), "local"),
        ("flag-own", True, str(outside_folder), "local"),
    ]
    assert (first_status, _rows(first)) == (200, expected)
    assert (second_status, _rows(second)) == (200, expected)


@pytest.mark.asyncio
async def test_two_syncs_at_once_make_one_row(app, servers: Servers, tmp_path: Path) -> None:
    servers.expect("flag-twice")
    _start("flag-twice", "flag-twice", tmp_path)
    servers.record("flag-twice")

    await asyncio.gather(_synced(app), _synced(app))

    assert await _listed(app) == [("flag-twice", True)]


@pytest.mark.asyncio
async def test_the_row_of_an_outside_session_that_ends_is_inactive_at_the_next_sync(
    app, servers: Servers, tmp_path: Path
) -> None:
    servers.expect("flag-gone")
    _start("flag-gone", "flag-gone", tmp_path)
    pid = servers.record("flag-gone")
    await _synced(app)
    os.kill(pid, signal.SIGKILL)
    await _until(lambda: not _alive(pid), "the tmux server to end")

    status, rows = await _synced(app)

    assert (status, _rows(rows)) == (200, [("flag-gone", False, str(tmp_path), "local")])


@pytest.mark.asyncio
async def test_a_terminal_opened_on_an_outside_session_updates_its_row(
    app, servers: Servers, workdir: Path
) -> None:
    servers.expect("flag-join")
    _start("flag-join", "flag-join", workdir)
    servers.record("flag-join")
    await _synced(app)

    await _close(await _open(app, workdir, "flag-join", servers))

    rows = await state.list_sessions_async("local", active_only=False)
    assert [(r.session_name, r.active, r.metadata) for r in rows] == [
        ("flag-join", True, {"columns": 80, "rows": 24, "transport": "local"})
    ]


@pytest.mark.asyncio
async def test_a_sync_turns_an_inactive_row_of_an_outside_session_active_and_keeps_its_folder(
    app, servers: Servers, tmp_path: Path
) -> None:
    row = await state.create_or_update_session_async(
        "local", "flag-kept", "/kept", metadata={"transport": "local"}
    )
    await state.update_session_activity_async(row.id, active=False)
    servers.expect("flag-kept")
    _start("flag-kept", "flag-kept", tmp_path)
    servers.record("flag-kept")

    status, rows = await _synced(app)

    assert (status, _rows(rows)) == (200, [("flag-kept", True, "/kept", "local")])


@pytest.mark.asyncio
async def test_a_session_whose_folder_has_a_newline_is_seen_and_gets_no_row(
    app, servers: Servers, tmp_path: Path
) -> None:
    folder = tmp_path / "two\nlines"
    folder.mkdir()
    servers.expect("flag-newline")
    _start("flag-newline", "flag-newline", folder)
    servers.record("flag-newline")

    seen = await tmux.discover_local_sessions()
    status, rows = await _synced(app)

    assert (seen.names, seen.complete, dict(seen.folders)) == (
        frozenset({"flag-newline"}),
        True,
        {},
    )
    assert (status, rows) == (200, [])


@pytest.mark.asyncio
async def test_a_sync_on_a_windows_server_makes_no_local_row_from_a_tmux_listing(
    app, fake_tmux, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("sshler.tmux._IS_WINDOWS", True)

    async def listing() -> tmux.LiveSessions:
        return tmux.LiveSessions(frozenset({"flag-win"}), folders={"flag-win": "/w"})

    monkeypatch.setattr("sshler.api.sessions.discover_local_sessions", listing)

    status, rows = await _synced(app)

    assert (status, rows) == (200, [])


def _remote_line(server: str, name: str, folder: str) -> str:
    return f"S\t/run/tmux-1000/{server}\t{name}\t{folder}\tE\n"


@pytest.fixture
def remote_listing(monkeypatch: pytest.MonkeyPatch) -> Any:
    """Point the remote box's connection at a ``_ListingConnection`` answering *output*."""
    from sshler import ssh_pool
    from sshler.ssh_pool import SSHConnectionPool

    def install(output: str, status: int = 0) -> _ListingConnection:
        fake = _ListingConnection([], status=status, output=output)

        async def connect_for_box(_deps: Any, _box: Any, _config: Any) -> FakeSshConnection:
            return fake

        monkeypatch.setattr(APIDependencies, "connect_for_box", connect_for_box)
        monkeypatch.setattr(ssh_pool, "_global_pool", SSHConnectionPool())
        return fake

    return install


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status", [0, sessions_api.REMOTE_LISTING_INCOMPLETE], ids=["complete", "incomplete"]
)
async def test_a_remote_sync_makes_a_row_only_for_a_session_on_its_own_server(
    app, remote_listing: Any, status: int
) -> None:
    local = await state.create_or_update_session_async(
        "local", "flag-r", "/l", metadata={"transport": "local"}
    )
    await state.update_session_activity_async(local.id, active=False)
    remote_listing(
        _remote_line("ts-flag-r", "flag-r", "/srv/a b")
        + _remote_line("default", "flag-default", "/srv")
        + _remote_line("ts-flag-r", "flag-stray", "/srv")
        + _remote_line("ts- ", " ", "/srv")
        + _remote_line("ts-bad\x00", "bad\x00", "/srv")
        + _remote_line("ts-cr\r", "cr\r", "/srv"),
        status,
    )

    status_code, rows = await _synced(app, REMOTE_BOX)

    assert (status_code, _rows(rows)) == (200, [("flag-r", True, "/srv/a b", "ssh")])
    assert await _listed(app) == [("flag-r", False)]


@pytest.mark.asyncio
async def test_a_remote_sync_of_200_outside_sessions_runs_one_listing(
    app, remote_listing: Any
) -> None:
    names = [f"flag-{n:03d}" for n in range(200)]
    fake = remote_listing("".join(_remote_line(f"ts-{n}", n, f"/srv/{n}") for n in names))

    status, answered = await _synced(app, REMOTE_BOX)

    made = await state.list_sessions_async(REMOTE_BOX, active_only=False, limit=1000)
    tmux_commands = [command for command in fake.commands if "tmux" in command]
    assert (status, fake.listings(), len(tmux_commands)) == (200, 1, 1)
    assert sorted(r.session_name for r in made) == names
    assert sorted((r["session_name"], r["active"]) for r in answered) == [(n, True) for n in names]


@pytest.mark.asyncio
async def test_a_sync_sets_the_flag_of_every_row_of_a_box_however_many(
    app, remote_listing: Any
) -> None:
    """The answer holds every active row, and no more than 50 rows when fewer are
    active; the flags are set on every row of the box, also those the answer leaves out."""
    names = [f"flag-{n:03d}" for n in range(60)]
    remote_listing("".join(_remote_line(f"ts-{n}", n, f"/srv/{n}") for n in names))
    await _synced(app, REMOTE_BOX)
    remote_listing(_remote_line("ts-flag-000", "flag-000", "/srv/flag-000"))

    status, answered = await _synced(app, REMOTE_BOX)

    rows = await state.list_sessions_async(REMOTE_BOX, active_only=False, limit=1000)
    assert status == 200
    assert sorted(r.session_name for r in rows if r.active) == ["flag-000"]
    assert (len(answered), [r["session_name"] for r in answered if r["active"]]) == (
        50,
        ["flag-000"],
    )


@pytest.mark.asyncio
async def test_a_session_that_ends_before_its_row_is_made_is_inactive_at_the_next_sync(
    app, remote_listing: Any
) -> None:
    remote_listing(_remote_line("ts-flag-brief", "flag-brief", "/srv"))
    await _synced(app, REMOTE_BOX)
    remote_listing("")

    status, rows = await _synced(app, REMOTE_BOX)

    assert (status, _rows(rows)) == (200, [("flag-brief", False, "/srv", "ssh")])


# --- the snapshot loop and recovery ---------------------------------------------------


@pytest.fixture
def recovery_list() -> Iterator[None]:
    snapshot.set_recovery_sessions([])
    yield
    snapshot.set_recovery_sessions([])


async def _snapshotted(app: Any, workdir: Path, servers: Servers, name: str) -> int:
    """Session *name* was opened, its tab closed and its windows saved by one tick of
    the snapshot loop; the process id of its tmux server."""
    sock = await _open(app, workdir, name, servers)
    pid = servers.record(name)
    await _close(sock)
    assert await snapshot.snapshot_all_sessions() == 1
    return pid


async def _snapshotted_then_ended(
    app: Any, workdir: Path, servers: Servers, name: str, ending: str = "kill-session"
) -> None:
    """``_snapshotted``, and then the session ended: by ``kill-session`` in another
    terminal, or by its tmux server killed with SIGKILL."""
    pid = await _snapshotted(app, workdir, servers, name)
    if ending == "kill-session":
        assert _tmux(name, "kill-session", "-t", name).returncode == 0
    else:
        os.kill(pid, signal.SIGKILL)
    await _until(lambda: not _alive(pid), "the tmux server to end")


@pytest.mark.asyncio
async def test_a_session_that_ended_with_no_tab_is_not_offered_for_recovery(
    app: Any, workdir: Path, servers: Servers, recovery_list: None
) -> None:
    await _snapshotted_then_ended(app, workdir, servers, "flag-ended")

    assert await snapshot.snapshot_all_sessions() == 0

    assert snapshot.get_recovery_sessions() == []
    assert await _listed(app) == [("flag-ended", False)]


@pytest.mark.asyncio
async def test_a_snapshot_tick_while_the_tmux_of_a_session_does_not_answer_leaves_its_row_active(
    app: Any, workdir: Path, servers: Servers, recovery_list: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _snapshotted(app, workdir, servers, "flag-slow")
    _unanswering(monkeypatch, "flag-slow")

    assert await snapshot.snapshot_all_sessions() == 0

    assert snapshot.get_recovery_sessions() == []
    assert await _listed(app) == [("flag-slow", True)]


@pytest.mark.asyncio
@pytest.mark.parametrize("ending", ["kill-session", "sigkill"])
async def test_a_session_gone_when_the_server_starts_is_offered_for_recovery(
    app: Any, workdir: Path, servers: Servers, recovery_list: None, ending: str
) -> None:
    await _snapshotted_then_ended(app, workdir, servers, "flag-gone", ending)

    lost = await snapshot.reconcile_on_startup()

    assert [(row["session_name"], row["working_directory"]) for row in lost] == [
        ("flag-gone", str(workdir))
    ]
    assert await _listed(app) == [("flag-gone", False)]


@pytest.mark.asyncio
async def test_the_start_makes_an_inactive_row_with_no_snapshot_of_a_living_session_active(
    app: Any, workdir: Path, servers: Servers, recovery_list: None
) -> None:
    sock = await _open(app, workdir, "flag-start", servers)
    servers.record("flag-start")
    await _close(sock)
    row = await state.get_session_by_name_async("local", "flag-start")
    assert row is not None and not row.metadata.get("last_snapshot_at")
    await state.update_session_activity_async(row.id, active=False)

    lost = await snapshot.reconcile_on_startup()

    assert (lost, await _listed(app)) == ([], [("flag-start", True)])


async def _dismissed(app: Any, route: str) -> int:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        answer = await client.post(f"/api/v1/{route}", headers={"X-SSHLER-TOKEN": TOKEN})
    return answer.status_code


@pytest.mark.asyncio
@pytest.mark.parametrize("all_of_them", [False, True], ids=["one", "all"])
async def test_dismissing_a_lost_session_that_tmux_has_again_leaves_its_row_active(
    app: Any, workdir: Path, servers: Servers, recovery_list: None, all_of_them: bool
) -> None:
    # Lost at the start, then started again by hand before the dialog was answered.
    await _snapshotted_then_ended(app, workdir, servers, "flag-again")
    snapshot.set_recovery_sessions(await snapshot.reconcile_on_startup())
    [lost] = snapshot.get_recovery_sessions()
    _start("flag-again", "flag-again")
    servers.record("flag-again")

    route = "recovery/dismiss" if all_of_them else f"recovery/{lost['id']}/dismiss"

    assert await _dismissed(app, route) == 200
    assert snapshot.get_recovery_sessions() == []
    assert await _listed(app) == [("flag-again", True)]


# --- an end the running server sees, the start's lost list, and Restore layout ---------


async def _row(name: str, box: str = "local") -> state.Session:
    row = await state.get_session_by_name_async(box, name)
    assert row is not None
    return row


def _end_seen(row: state.Session) -> Any:
    return row.metadata.get(state.END_SEEN_AT)


async def _end(name: str, pid: int, ending: str) -> None:
    """Session *name* (its tmux server *pid*) ends: its shell exits, it is killed in
    another terminal, or its tmux server is killed with SIGKILL."""
    if ending == "exit":
        assert _tmux(name, "send-keys", "-t", name, "exit", "Enter").returncode == 0
    elif ending == "kill-session":
        assert _tmux(name, "kill-session", "-t", name).returncode == 0
    else:
        os.kill(pid, signal.SIGKILL)
    await _until(lambda: not _alive(pid), "the tmux server to end")


async def _seen_ended(
    app: Any, workdir: Path, servers: Servers, name: str, seen_by: str = "snapshot"
) -> None:
    """``_snapshotted_then_ended``, and the running server saw the end: by a tick of
    the snapshot loop, or by a sync."""
    await _snapshotted_then_ended(app, workdir, servers, name)
    if seen_by == "snapshot":
        assert await snapshot.snapshot_all_sessions() == 0
    else:
        assert (await _synced(app))[0] == 200


@pytest.mark.asyncio
@pytest.mark.parametrize("seen_by", ["snapshot", "sync"])
async def test_an_end_the_running_server_sees_is_recorded_with_its_time(
    app: Any, workdir: Path, servers: Servers, recovery_list: None, seen_by: str
) -> None:
    before = time.time()

    await _seen_ended(app, workdir, servers, "flag-seen", seen_by)

    row = await _row("flag-seen")
    assert (row.active, before <= (_end_seen(row) or 0) <= time.time()) == (False, True)


@pytest.mark.asyncio
async def test_an_end_a_terminal_socket_sees_is_recorded(
    app: Any, servers: Servers, workdir: Path
) -> None:
    sock = await _open(app, workdir, "flag-sock", servers)
    pid = servers.record("flag-sock")
    sock.send_bytes(b"exit\n")
    await sock.finished()
    await _until(lambda: not _alive(pid), "the tmux server to end")

    row = await _row("flag-sock")
    assert (row.active, _end_seen(row) is not None) == (False, True)


@pytest.mark.asyncio
async def test_a_session_found_gone_at_the_start_is_not_recorded_as_seen(
    app: Any, workdir: Path, servers: Servers, recovery_list: None
) -> None:
    await _snapshotted_then_ended(app, workdir, servers, "flag-start-gone")

    await snapshot.reconcile_on_startup()

    row = await _row("flag-start-gone")
    assert (row.active, _end_seen(row)) == (False, None)


@pytest.mark.asyncio
@pytest.mark.parametrize("again_by", ["sync", "terminal"])
async def test_a_session_tmux_has_again_loses_the_record(
    app: Any, workdir: Path, servers: Servers, recovery_list: None, again_by: str
) -> None:
    await _seen_ended(app, workdir, servers, "flag-again-seen")
    assert _end_seen(await _row("flag-again-seen")) is not None

    if again_by == "sync":
        _start("flag-again-seen", "flag-again-seen")
        assert (await _synced(app))[0] == 200
    else:
        await _close(await _open(app, workdir, "flag-again-seen", servers))
    servers.record("flag-again-seen")

    row = await _row("flag-again-seen")
    assert (row.active, _end_seen(row)) == (True, None)


@pytest.mark.asyncio
async def test_a_session_seen_ended_that_tmux_has_at_the_start_loses_the_record(
    app: Any, workdir: Path, servers: Servers, recovery_list: None
) -> None:
    await _seen_ended(app, workdir, servers, "flag-back")
    _start("flag-back", "flag-back")
    servers.record("flag-back")

    await snapshot.reconcile_on_startup()

    row = await _row("flag-back")
    assert (row.active, _end_seen(row), "recovered_at" in row.metadata) == (True, None, True)


@pytest.mark.asyncio
@pytest.mark.parametrize("ending", ["exit", "kill-session", "sigkill"])
async def test_a_session_that_ends_while_the_server_runs_is_not_lost_at_the_next_start(
    app: Any, workdir: Path, servers: Servers, recovery_list: None, ending: str
) -> None:
    pid = await _snapshotted(app, workdir, servers, "flag-ran")
    await _end("flag-ran", pid, ending)
    assert await snapshot.snapshot_all_sessions() == 0

    assert await snapshot.reconcile_on_startup() == []


@pytest.mark.asyncio
async def test_a_session_whose_end_a_sync_saw_is_not_lost_at_the_next_start(
    app: Any, workdir: Path, servers: Servers, recovery_list: None
) -> None:
    await _seen_ended(app, workdir, servers, "flag-synced", "sync")

    assert await snapshot.reconcile_on_startup() == []


async def _restorable(app: Any, box: str = "local") -> list[tuple[str, bool, bool]]:
    """(name, active, restorable) of each row the sync answers."""
    status, rows = await _synced(app, box)
    assert status == 200
    return sorted((r["session_name"], r["active"], r["restorable"]) for r in rows)


@pytest.mark.asyncio
async def test_the_row_of_a_session_that_ended_with_its_layout_kept_is_restorable(
    app: Any, workdir: Path, servers: Servers, recovery_list: None
) -> None:
    await _seen_ended(app, workdir, servers, "flag-kept")

    assert await _restorable(app) == [("flag-kept", False, True)]


@pytest.mark.asyncio
async def test_an_active_row_with_a_layout_is_not_restorable(
    app: Any, workdir: Path, servers: Servers, recovery_list: None
) -> None:
    await _snapshotted(app, workdir, servers, "flag-running")

    assert await _restorable(app) == [("flag-running", True, False)]


@pytest.mark.asyncio
async def test_an_ended_row_with_no_layout_is_not_restorable(
    app: Any, servers: Servers, workdir: Path
) -> None:
    sock = await _open(app, workdir, "flag-bare", servers)
    pid = servers.record("flag-bare")
    sock.send_bytes(b"exit\n")
    await sock.finished()
    await _until(lambda: not _alive(pid), "the tmux server to end")

    assert await _restorable(app) == [("flag-bare", False, False)]


async def _recreate(app: Any, session_id: str) -> tuple[int, Any]:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        answer = await client.post(
            f"/api/v1/recovery/{session_id}/recreate", headers={"X-SSHLER-TOKEN": TOKEN}
        )
    return answer.status_code, answer.json()


@pytest.mark.asyncio
async def test_restore_layout_brings_back_the_windows_and_runs_no_pane_command_again(
    app: Any, workdir: Path, servers: Servers, recovery_list: None
) -> None:
    name = "flag-restore"
    sock = await _open(app, workdir, name, servers)
    pid = servers.record(name)
    await _close(sock)
    assert _tmux(name, "new-window", "-t", name, "-c", str(workdir)).returncode == 0
    assert _tmux(name, "send-keys", "-t", f"{name}:1", "sleep 300", "Enter").returncode == 0
    await _until(
        lambda: "sleep" in _tmux(name, "list-panes", "-s", "-F", "#{pane_current_command}").stdout,
        "the sleep to run",
    )
    assert await snapshot.snapshot_all_sessions() == 1
    await _end(name, pid, "kill-session")
    assert await snapshot.snapshot_all_sessions() == 0
    row = await _row(name)

    status, _body = await _recreate(app, row.id)
    servers.record(name)
    assert status == 200

    # A new window's shell runs its start-up (on some hosts `locale`) before it idles, so wait for
    # both panes to show the bare shell. A restore that typed the old command again would show
    # `sleep` instead, and this wait would fail.
    def panes() -> list[str]:
        out = _tmux(name, "list-panes", "-s", "-F", "#{window_index} #{pane_current_command}")
        return out.stdout.split("\n")[:-1]

    await _until(lambda: panes() == ["0 sh", "1 sh"], "both panes to idle in sh")
    assert await _restorable(app) == [(name, True, False)]


@pytest.mark.asyncio
async def test_restore_layout_of_a_session_tmux_has_again_is_refused_and_leaves_it_running(
    app: Any, workdir: Path, servers: Servers, recovery_list: None
) -> None:
    await _seen_ended(app, workdir, servers, "flag-twice")
    _start("flag-twice", "flag-twice")
    pid = servers.record("flag-twice")
    row = await _row("flag-twice")

    status, body = await _recreate(app, row.id)

    assert (status, body["reason"], body["detail"]) == (400, "invalid", "Session is running")
    assert (servers._pid("flag-twice"), (await _row("flag-twice")).active) == (pid, True)


@pytest.mark.asyncio
async def test_restore_layout_while_tmux_does_not_answer_is_refused_and_leaves_it_running(
    app: Any, workdir: Path, servers: Servers, recovery_list: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _seen_ended(app, workdir, servers, "flag-wedged")
    _start("flag-wedged", "flag-wedged")
    pid = servers.record("flag-wedged")
    row = await _row("flag-wedged")
    _unanswering(monkeypatch, "flag-wedged")

    status, body = await _recreate(app, row.id)

    assert (status, body["reason"]) == (504, "timeout")
    assert servers._pid("flag-wedged") == pid


@pytest.mark.asyncio
async def test_restore_layout_of_an_active_row_is_refused(
    app: Any, workdir: Path, servers: Servers, recovery_list: None
) -> None:
    await _snapshotted(app, workdir, servers, "flag-busy")

    status, body = await _recreate(app, (await _row("flag-busy")).id)

    assert (status, body["reason"], body["detail"]) == (400, "invalid", "Session is running")


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["unknown", "no-layout", "remote"])
async def test_restore_layout_of_a_row_with_no_layout_of_this_box_is_refused(
    app: Any, recovery_list: None, kind: str
) -> None:
    session_id = "no-such-id"
    if kind != "unknown":
        box = REMOTE_BOX if kind == "remote" else "local"
        row = await state.create_or_update_session_async(
            box, "flag-forged", "/", metadata={"transport": "ssh" if box == REMOTE_BOX else "local"}
        )
        await state.update_session_activity_async(row.id, active=False)
        if kind == "remote":
            # A layout no snapshot wrote: the snapshot loop reads local sessions only.
            await state.update_session_snapshot_async(row.id, [{"index": 0, "name": "w"}])
        session_id = row.id

    status, body = await _recreate(app, session_id)

    assert (status, body["reason"], body["detail"]) == (
        404,
        "not_found",
        "Session has no saved layout",
    )
