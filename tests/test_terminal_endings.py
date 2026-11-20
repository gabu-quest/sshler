"""However a terminal session ends, ``/ws/term`` leaves nothing behind.

Each test drives the real app over raw ASGI on the test's own event loop, so it can
count what is left once the handler has returned: tasks still running on the loop,
child processes of this process (a zombie counts), and this process's open file
descriptors. Each count is taken against the same moment before the socket opened,
and must be exactly zero for every ending: the client disconnects, the child or
channel ends, the terminal fails to open, and the handler is cancelled.
"""

from __future__ import annotations

import asyncio
import json
import os
import threading
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import anyio
import psutil
import pytest
import yaml
from terminal_fakes import FakeSshChannel, FakeSshConnection

from sshler import state
from sshler.webapp import ServerSettings, make_app

pytestmark = pytest.mark.posix_only("counts /proc/self/fd and runs PTY children")

TOKEN = "endings-token"
SESSION = "endings"
REMOTE_BOX = "demo-box"


@dataclass(frozen=True)
class Snapshot:
    tasks: frozenset[asyncio.Task[Any]]
    children: frozenset[int]
    fds: frozenset[int]


def _open_fds() -> frozenset[int]:
    """This process's open descriptors, except the state database's.

    The state store keeps one SQLite connection per worker thread that has used it,
    so which threads served a session decides how many it holds; none is the terminal's.
    """
    fds = set()
    for name in os.listdir("/proc/self/fd"):
        try:
            target = os.readlink(f"/proc/self/fd/{name}")
        except OSError:
            continue  # the descriptor listdir itself used, already closed
        if "state.sqlite" not in target:
            fds.add(int(name))
    return frozenset(fds)


def _snapshot() -> Snapshot:
    return Snapshot(
        tasks=frozenset(t for t in asyncio.all_tasks() if not t.done()),
        children=frozenset(p.pid for p in psutil.Process().children(recursive=True)),
        fds=_open_fds(),
    )


def _left_since(before: Snapshot) -> tuple[int, int, int]:
    """(tasks, children, descriptors) present now that were not present *before*."""
    now = _snapshot()
    return (
        len(now.tasks - before.tasks),
        len(now.children - before.children),
        len(now.fds - before.fds),
    )


@dataclass
class AsgiSocket:
    """One ``/ws/term`` connection driven by hand: what the client sends and receives."""

    app: Any
    query: str
    incoming: asyncio.Queue[dict[str, Any]] = field(default_factory=asyncio.Queue)
    sent: list[dict[str, Any]] = field(default_factory=list)
    changed: asyncio.Event = field(default_factory=asyncio.Event)
    task: asyncio.Task[None] | None = None
    # The app runs in an anyio cancel scope, as under Starlette's test client; cancelling
    # it cancels the handler at every await until it leaves the scope.
    scope: anyio.CancelScope = field(default_factory=anyio.CancelScope)

    def start(self) -> None:
        scope = {
            "type": "websocket",
            "asgi": {"version": "3.0"},
            "scheme": "ws",
            "http_version": "1.1",
            "path": "/ws/term",
            "raw_path": b"/ws/term",
            "root_path": "",
            "query_string": self.query.encode(),
            "headers": [(b"host", b"testserver")],
            "client": ("127.0.0.1", 50000),
            "server": ("testserver", 80),
            "subprotocols": [],
        }
        self.incoming.put_nowait({"type": "websocket.connect"})
        self.task = asyncio.create_task(self._run(scope))

    async def _run(self, scope: dict[str, Any]) -> None:
        with self.scope:
            await self.app(scope, self._receive, self._send)

    async def _receive(self) -> dict[str, Any]:
        return await self.incoming.get()

    async def _send(self, message: dict[str, Any]) -> None:
        self.sent.append(message)
        self.changed.set()

    def send_bytes(self, data: bytes) -> None:
        self.incoming.put_nowait({"type": "websocket.receive", "bytes": data})

    def send_text(self, text: str) -> None:
        self.incoming.put_nowait({"type": "websocket.receive", "text": text})

    def disconnect(self) -> None:
        self.incoming.put_nowait({"type": "websocket.disconnect", "code": 1001})

    def output(self) -> bytes:
        return b"".join(m.get("bytes") or b"" for m in self.sent if m["type"] == "websocket.send")

    def texts(self) -> list[str]:
        return [m["text"] for m in self.sent if m["type"] == "websocket.send" and m.get("text")]

    def closes(self) -> list[tuple[int, str]]:
        return [
            (m.get("code", 1000), m.get("reason") or "")
            for m in self.sent
            if m["type"] == "websocket.close"
        ]

    async def wait_output(self, marker: bytes, count: int = 1, seconds: float = 10.0) -> None:
        async with asyncio.timeout(seconds):
            while self.output().count(marker) < count:
                self.changed.clear()
                await self.changed.wait()

    async def finished(self, seconds: float = 15.0) -> None:
        """Wait for the handler to return; re-raise what it raised."""
        assert self.task is not None
        await asyncio.wait_for(asyncio.shield(self.task), seconds)


@pytest.fixture
def app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Any]:
    config_dir = Path(os.environ["SSHLER_CONFIG_DIR"])
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "boxes.yaml").write_text(yaml.safe_dump({"boxes": []}), encoding="utf-8")
    ssh_config = tmp_path / "ssh_config"
    ssh_config.write_text(f"Host {REMOTE_BOX}\n  HostName demo.internal\n  User demo\n")
    monkeypatch.setenv("SSHLER_SSH_CONFIG", str(ssh_config))
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


def _local_query(workdir: Path, session: str = SESSION) -> str:
    return f"host=local&dir={workdir}&session={session}&cols=80&rows=24&token={TOKEN}"


def _remote_query() -> str:
    return f"host={REMOTE_BOX}&dir=/srv&session={SESSION}&cols=80&rows=24&token={TOKEN}"


async def _warm(app: Any, workdir: Path) -> None:
    """Run one whole session first, so whatever opens once per process predates the count."""
    await state.create_or_update_session_async("local", "warm-up", str(workdir))
    sock = AsgiSocket(app, _local_query(workdir, session="warm-up"))
    sock.start()
    sock.send_bytes(b"warm\n")
    await sock.wait_output(b"warm", count=2)
    sock.disconnect()
    await sock.finished()


@dataclass
class Remote:
    connection: FakeSshConnection
    channel: FakeSshChannel
    opened: asyncio.Event


@pytest.fixture
def remote(monkeypatch: pytest.MonkeyPatch) -> Remote:
    fake = Remote(FakeSshConnection(), FakeSshChannel(), asyncio.Event())

    async def connect(*_args: Any, **_kwargs: Any) -> FakeSshConnection:
        return fake.connection

    async def is_directory(_connection: Any, _path: str) -> bool:
        return True

    async def open_tmux(_connection: Any, **_kwargs: Any) -> FakeSshChannel:
        fake.opened.set()
        return fake.channel

    monkeypatch.setattr("sshler.webapp.connect", connect)
    monkeypatch.setattr("sshler.webapp.sftp_is_directory", is_directory)
    monkeypatch.setattr("sshler.webapp.open_tmux", open_tmux)
    return fake


# --- local PTY ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_local_client_disconnect_leaves_nothing(app, fake_tmux, workdir: Path) -> None:
    await _warm(app, workdir)
    before = _snapshot()
    sock = AsgiSocket(app, _local_query(workdir))
    sock.start()
    sock.send_bytes(b"ready\n")
    await sock.wait_output(b"ready", count=2)

    sock.disconnect()
    await sock.finished()

    assert _left_since(before) == (0, 0, 0)


@pytest.mark.asyncio
async def test_local_child_exit_closes_the_socket_and_leaves_nothing(
    app, fake_tmux, workdir: Path
) -> None:
    await _warm(app, workdir)
    before = _snapshot()
    sock = AsgiSocket(app, _local_query(workdir))
    sock.start()
    sock.send_bytes(b"ready\n")
    await sock.wait_output(b"ready", count=2)

    sock.send_bytes(b"\x04")  # end of file at the start of a line: `cat` exits
    await sock.finished()

    assert sock.closes() == [(1000, "shell exited")]
    assert _left_since(before) == (0, 0, 0)


@pytest.mark.asyncio
async def test_local_child_that_exits_at_once_closes_the_socket(
    app, fake_tmux, workdir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _warm(app, workdir)
    monkeypatch.setattr("sshler.webapp.local_tmux_command", lambda _session: ["true"])
    before = _snapshot()
    sock = AsgiSocket(app, _local_query(workdir))
    sock.start()

    await sock.finished()

    assert sock.closes() == [(1000, "shell exited")]
    assert _left_since(before) == (0, 0, 0)


@pytest.mark.asyncio
async def test_local_open_error_reports_it_and_leaves_nothing(
    app, fake_tmux, workdir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _warm(app, workdir)

    def no_pty(*_args: Any, **_kwargs: Any) -> tuple[int, int]:
        raise OSError("out of ptys")

    monkeypatch.setattr("sshler.webapp.spawn_on_pty", no_pty)
    before = _snapshot()
    sock = AsgiSocket(app, _local_query(workdir))
    sock.start()

    await sock.finished()

    assert sock.texts() == ["Connection failed: out of ptys\r\n"]
    assert sock.closes() == [(1000, "")]
    assert _left_since(before) == (0, 0, 0)


@pytest.mark.asyncio
async def test_local_cancelled_handler_leaves_nothing(app, fake_tmux, workdir: Path) -> None:
    await _warm(app, workdir)
    before = _snapshot()
    sock = AsgiSocket(app, _local_query(workdir))
    sock.start()
    sock.send_bytes(b"ready\n")
    await sock.wait_output(b"ready", count=2)
    assert sock.task is not None

    sock.task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await sock.finished()

    assert _left_since(before) == (0, 0, 0)


@pytest.mark.asyncio
async def test_local_handler_cancelled_at_every_await_still_leaves_nothing(
    app, fake_tmux, workdir: Path
) -> None:
    await _warm(app, workdir)
    before = _snapshot()
    sock = AsgiSocket(app, _local_query(workdir))
    sock.start()
    sock.send_bytes(b"ready\n")
    await sock.wait_output(b"ready", count=2)

    # A cancel delivered again at each await until the handler returns, as anyio
    # does (Starlette's test client cancels this way as a client leaves).
    sock.scope.cancel()
    await sock.finished()

    assert _left_since(before) == (0, 0, 0)


def _slow_spawn(monkeypatch: pytest.MonkeyPatch) -> tuple[threading.Event, threading.Event]:
    """Make the PTY spawn take half a second; the events mark its start and its return."""
    from sshler import ptyspawn

    entered = threading.Event()
    returned = threading.Event()
    real = ptyspawn.spawn_on_pty

    def slow(*args: Any, **kwargs: Any) -> tuple[int, int]:
        entered.set()
        threading.Event().wait(0.5)
        try:
            return real(*args, **kwargs)
        finally:
            returned.set()

    monkeypatch.setattr("sshler.webapp.spawn_on_pty", slow)
    return entered, returned


@pytest.mark.asyncio
async def test_handler_cancelled_while_the_pty_spawns_leaves_nothing(
    app, fake_tmux, workdir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _warm(app, workdir)
    entered, returned = _slow_spawn(monkeypatch)
    before = _snapshot()
    sock = AsgiSocket(app, _local_query(workdir))
    sock.start()
    assert await asyncio.to_thread(entered.wait, 5.0)
    assert sock.task is not None

    sock.task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await sock.finished()
    # Counted only once the spawn has returned, so a child it started late is seen.
    assert await asyncio.to_thread(returned.wait, 5.0)

    # The spawn finished after the cancel; its child was hung up and reaped, its PTY closed.
    assert _left_since(before) == (0, 0, 0)


@pytest.mark.asyncio
async def test_client_that_leaves_while_the_pty_spawns_leaves_nothing(
    app, fake_tmux, workdir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _warm(app, workdir)
    entered, returned = _slow_spawn(monkeypatch)
    before = _snapshot()
    sock = AsgiSocket(app, _local_query(workdir))
    sock.start()
    assert await asyncio.to_thread(entered.wait, 5.0)

    sock.disconnect()
    await sock.finished()
    assert await asyncio.to_thread(returned.wait, 5.0)

    assert sock.closes() == []
    assert _left_since(before) == (0, 0, 0)


@pytest.mark.asyncio
async def test_two_sockets_on_one_session_run_and_end_apart(app, fake_tmux, workdir: Path) -> None:
    await _warm(app, workdir)
    before = _snapshot()
    first = AsgiSocket(app, _local_query(workdir))
    second = AsgiSocket(app, _local_query(workdir))
    first.start()
    second.start()
    first.send_bytes(b"first-1\n")
    second.send_bytes(b"second-2\n")
    await first.wait_output(b"first-1", count=2)
    await second.wait_output(b"second-2", count=2)

    first.disconnect()
    await first.finished()
    second.send_bytes(b"still-3\n")
    await second.wait_output(b"still-3", count=2)
    second.disconnect()
    await second.finished()

    assert (first.output().count(b"second-2"), second.output().count(b"first-1")) == (0, 0)
    assert [c for c in fake_tmux.calls() if c.startswith("new ")] == [
        f"new -As warm-up -c {workdir}",
        f"new -As {SESSION} -c {workdir}",
        f"new -As {SESSION} -c {workdir}",
    ]
    assert _left_since(before) == (0, 0, 0)


# --- remote over SSH ----------------------------------------------------------------


async def _warm_state() -> None:
    """Open the state DB first, so its file predates the count."""
    await state.create_or_update_session_async(REMOTE_BOX, "warm-up", "/srv")


async def _open_remote(app: Any, remote: Remote) -> AsgiSocket:
    sock = AsgiSocket(app, _remote_query())
    sock.start()
    sock.send_bytes(b"echo ready\n")
    await sock.wait_output(b"ready\r\n", count=2)
    return sock


def _channel_and_connection_closed(remote: Remote) -> tuple[bool, bool, bool]:
    return remote.channel.stdin.eof, remote.channel.closed, remote.connection.closed


@pytest.mark.asyncio
async def test_remote_client_disconnect_closes_the_channel(app, remote: Remote) -> None:
    await _warm_state()
    before = _snapshot()
    sock = await _open_remote(app, remote)

    sock.disconnect()
    await sock.finished()

    assert _channel_and_connection_closed(remote) == (True, True, True)
    assert _left_since(before) == (0, 0, 0)


@pytest.mark.asyncio
async def test_remote_channel_end_closes_the_socket_and_the_channel(app, remote: Remote) -> None:
    await _warm_state()
    before = _snapshot()
    sock = await _open_remote(app, remote)

    remote.channel.exit()
    await sock.finished()

    assert sock.closes() == [(1000, "shell exited")]
    assert _channel_and_connection_closed(remote) == (True, True, True)
    assert _left_since(before) == (0, 0, 0)


@pytest.mark.asyncio
async def test_remote_connection_drop_closes_the_socket_for_a_reconnect(
    app, remote: Remote
) -> None:
    await _warm_state()
    before = _snapshot()
    sock = await _open_remote(app, remote)

    remote.channel.drop()
    await sock.finished()

    assert sock.closes() == [(1011, "terminal connection lost")]
    assert _channel_and_connection_closed(remote) == (True, True, True)
    assert _left_since(before) == (0, 0, 0)


@pytest.mark.asyncio
async def test_remote_open_error_closes_the_connection(
    app, remote: Remote, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def open_tmux_fails(_connection: Any, **_kwargs: Any) -> FakeSshChannel:
        raise RuntimeError("no tmux on the box")

    monkeypatch.setattr("sshler.webapp.open_tmux", open_tmux_fails)
    await _warm_state()
    before = _snapshot()
    sock = AsgiSocket(app, _remote_query())
    sock.start()

    with pytest.raises(RuntimeError, match="no tmux on the box"):
        await sock.finished()

    assert (remote.connection.closed, remote.connection.wait_closed_calls) == (True, 1)
    assert _left_since(before) == (0, 0, 0)


@pytest.mark.asyncio
async def test_remote_cancelled_handler_closes_the_channel(app, remote: Remote) -> None:
    await _warm_state()
    before = _snapshot()
    sock = await _open_remote(app, remote)
    assert sock.task is not None

    sock.task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await sock.finished()

    assert _channel_and_connection_closed(remote) == (True, True, True)
    assert _left_since(before) == (0, 0, 0)


# --- hostile input ------------------------------------------------------------------


async def _sync(sock: AsgiSocket, word: str) -> None:
    """Every message sent before this one has been handled once its echo is back."""
    sock.send_bytes(f"echo {word}\n".encode())
    await sock.wait_output(f"{word}\r\n".encode(), count=2)


@pytest.mark.asyncio
async def test_resize_drops_sizes_that_are_not_both_positive(app, remote: Remote) -> None:
    sock = await _open_remote(app, remote)
    for cols, rows in [(0, 10), (10, 0), (-5, -5), (None, 40), (10**9, 10**9), ("100", "40")]:
        sock.send_text(json.dumps({"op": "resize", "cols": cols, "rows": rows}))
    await _sync(sock, "sized")

    assert remote.channel.sizes == [(10**9, 10**9), (100, 40)]
    sock.disconnect()
    await sock.finished()
    assert sock.closes() == []


@pytest.mark.asyncio
async def test_text_that_is_not_json_is_ignored(app, remote: Remote) -> None:
    sock = await _open_remote(app, remote)
    sock.send_text("not json {")
    sock.send_text("")
    await _sync(sock, "after-junk")

    assert remote.channel.written == [b"echo ready\n", b"echo after-junk\n"]
    sock.disconnect()
    await sock.finished()


@pytest.mark.asyncio
async def test_binary_frame_is_input_even_when_it_reads_as_a_control_message(
    app, remote: Remote
) -> None:
    sock = await _open_remote(app, remote)
    resize = json.dumps({"op": "resize", "cols": 99, "rows": 33}).encode()
    sock.send_bytes(resize)
    sock.send_text(json.dumps({"op": "send", "data": "typed\n"}))
    await _sync(sock, "framed")

    assert remote.channel.sizes == []
    assert remote.channel.written == [b"echo ready\n", resize, b"typed\n", b"echo framed\n"]
    sock.disconnect()
    await sock.finished()
