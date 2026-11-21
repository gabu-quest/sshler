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


def _start(server: str | None, session: str) -> None:
    """A detached session *session* on *server*; no pipe for the server it forks to hold."""
    assert TMUX is not None
    words = [TMUX] if server is None else [TMUX, "-L", f"ts-{server}"]
    started = subprocess.run(
        [*words, "new-session", "-d", "-s", session],
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
            ["tmux", "-L", "ts-flag-own", "list-sessions", "-F", "#{session_name}"],
            ["tmux", "list-sessions", "-F", "#{session_name}"],
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

    def __init__(self, names: list[str], status: int = 0) -> None:
        super().__init__()
        self.names = names
        self.status = status

    async def run(self, command: str, *args: Any, **kwargs: Any) -> Any:
        if shlex.split(command)[:3] != ["sh", "-c", sessions_api.REMOTE_LIST_SESSIONS]:
            return await super().run(command, *args, **kwargs)
        self.commands.append(command)
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
    servers.expect("flag-again")
    _start("flag-again", "flag-again")

    route = "recovery/dismiss" if all_of_them else f"recovery/{lost['id']}/dismiss"

    assert await _dismissed(app, route) == 200
    assert snapshot.get_recovery_sessions() == []
    assert await _listed(app) == [("flag-again", True)]
