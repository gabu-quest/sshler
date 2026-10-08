"""SSH connection accounting: sshler must not pile up connections to a box.

Field report (2026-10-08): "also why do i sometimes ahve like 800 sshelr ssh
connections active?"

Every test here counts connections through a fake ``connect_for_box``: each
call opens one ``FakeConnection`` and a connection is *live* until something
calls ``close()`` on it. A fake box can be told to hang: every command except
the pool's ``echo test`` health check then blocks until the connection is
closed, the way a remote command on a stuck host never returns.

The stats stream is driven at the ASGI level (``StreamClient``) with the same
``spec_version`` uvicorn 0.37 sends ("2.3"), so a client disconnect reaches the
app the way it does in production.

Each test names the mutation it kills in its docstring.
"""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
import yaml

from sshler import ssh_pool, state
from sshler.api import dependencies as deps_module
from sshler.api import stats_stream
from sshler.api import tunnels as tunnels_module
from sshler.api.dependencies import APIDependencies
from sshler.ssh import SSHError
from sshler.ssh_pool import SSHConnectionPool
from sshler.webapp import ServerSettings, make_app

TOKEN = "leak-token"
BOX = "remote1"
EMPTY_POOL = {BOX: {"active_connections": 0, "total_uses": 0, "oldest_connection_age": 0}}
# What a polled route reports when the command hangs past a 0.05 s probe limit.
HUNG_COMMAND_ERROR = "command on remote1 timed out (0.05 s limit for connect plus command)"

# Hand-computed stats for the canned /proc output in ``Ledger.reply``:
# cpu deltas total 1200-1000=200, active 300-200=100 -> 50.0 %;
# MemTotal 2048000 kB = 2000.0 MB, MemAvailable 1024000 kB = 1000.0 MB used.
PROC_STAT = "cpu  100 0 100 800 0\ncpu  150 0 150 900 0\n"
PROC_MEMINFO = "MemTotal:       2048000 kB\nMemAvailable:   1024000 kB\n"
PROC_UPTIME = "3600.50 100.00\n"


def _result(returncode: int, stdout: str = "") -> SimpleNamespace:
    return SimpleNamespace(exit_status=returncode, returncode=returncode, stdout=stdout, stderr="")


class FakeConnection:
    def __init__(self, ledger: Ledger) -> None:
        self.ledger = ledger
        self.closed = False
        self._closed_event = asyncio.Event()
        self.forward_error: Exception | None = None

    async def run(self, command: str, check: bool = False):
        if self.closed:
            raise ConnectionError("connection is closed")
        if command == "echo test":
            return _result(0, "test\n")
        if self.ledger.hang:
            self.ledger.hung_commands += 1
            await self._closed_event.wait()
            raise ConnectionError("connection closed while the command was running")
        return self.ledger.reply(command)

    async def forward_local_port(self, **kwargs):
        if self.ledger.forward_error is not None:
            raise self.ledger.forward_error
        return FakeListener()

    def close(self) -> None:
        self.closed = True
        self._closed_event.set()


class FakeListener:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


class Ledger:
    """Records every connection the fake ``connect_for_box`` hands out."""

    def __init__(self) -> None:
        self.opened: list[FakeConnection] = []
        self.hang = False
        self.hung_commands = 0
        self.forward_error: Exception | None = None
        self.fail_connect = False
        self.hang_connect = False
        self.connect_attempts = 0

    def live(self) -> int:
        return sum(1 for conn in self.opened if not conn.closed)

    @staticmethod
    def reply(command: str) -> SimpleNamespace:
        if command.startswith("cat /proc/stat"):
            return _result(0, PROC_STAT)
        if command.startswith("cat /proc/meminfo"):
            return _result(0, PROC_MEMINFO)
        if command == "cat /proc/uptime":
            return _result(0, PROC_UPTIME)
        if command.startswith("tmux list-sessions"):
            return _result(0, "alpha\n")
        if command == "true":
            return _result(0)
        if "rev-parse --abbrev-ref" in command:
            return _result(0, "main\n")
        if "rev-parse --short" in command:
            return _result(0, "abc1234\n")
        if "status --porcelain" in command:
            return _result(0, "")
        raise AssertionError(f"unexpected command: {command}")


async def wait_until(predicate: Callable[[], bool], timeout: float = 5.0) -> bool:
    """Yield to the loop until ``predicate()`` holds; return its final value.

    Callers assert the exact count afterwards, so a timeout shows the real
    number instead of a bare TimeoutError.
    """

    async def _poll() -> None:
        while not predicate():
            await asyncio.sleep(0)

    try:
        await asyncio.wait_for(_poll(), timeout)
    except TimeoutError:
        pass
    return predicate()


@pytest.fixture
def ledger(monkeypatch: pytest.MonkeyPatch) -> Ledger:
    book = Ledger()

    async def fake_connect(self, box, application_config):
        book.connect_attempts += 1
        if book.hang_connect:
            await asyncio.Event().wait()  # never set: the handshake never finishes
        if book.fail_connect:
            raise SSHError(f"{box.name}: connection refused")
        conn = FakeConnection(book)
        book.opened.append(conn)
        return conn

    monkeypatch.setattr(APIDependencies, "connect_for_box", fake_connect)
    # A fresh pool per test: the real one is a process-wide singleton.
    monkeypatch.setattr(ssh_pool, "_global_pool", SSHConnectionPool())
    return book


@pytest.fixture
def app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, ledger: Ledger):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "boxes.yaml").write_text(
        yaml.safe_dump({"boxes": []}, sort_keys=False), encoding="utf-8"
    )
    # The box comes from a private SSH config (never the user's ~/.ssh/config),
    # so ``/refresh`` keeps it: it only drops boxes.yaml overrides.
    ssh_config = tmp_path / "ssh_config"
    ssh_config.write_text(
        f"Host {BOX}\n  HostName example.invalid\n  User tester\n", encoding="utf-8"
    )
    monkeypatch.setenv("SSHLER_CONFIG_DIR", str(config_dir))
    monkeypatch.setenv("SSHLER_SSH_CONFIG", str(ssh_config))
    state.initialize(config_dir)
    try:
        yield make_app(ServerSettings(csrf_token=TOKEN))
    finally:
        state.reset_state()


def _client(app) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
        headers={"X-SSHLER-TOKEN": TOKEN},
    )


class StreamClient:
    """One browser tab's EventSource on ``/api/v1/boxes/stats/stream``."""

    def __init__(self, app, boxes: str = BOX) -> None:
        self.app = app
        query = f"boxes={boxes}&token={TOKEN}".encode()
        self.scope = {
            "type": "http",
            "asgi": {"version": "3.0", "spec_version": "2.3"},
            "http_version": "1.1",
            "method": "GET",
            "scheme": "http",
            "path": "/api/v1/boxes/stats/stream",
            "raw_path": b"/api/v1/boxes/stats/stream",
            "query_string": query,
            "root_path": "",
            "headers": [(b"host", b"127.0.0.1:8822"), (b"accept", b"text/event-stream")],
            "client": ("127.0.0.1", 50000),
            "server": ("127.0.0.1", 8822),
            "state": {},
        }
        self._request_sent = False
        self._disconnect = asyncio.Event()
        self._buffer = ""
        self.status: int | None = None
        self.events: asyncio.Queue[dict] = asyncio.Queue()
        self.task: asyncio.Task | None = None

    async def _receive(self) -> dict:
        if not self._request_sent:
            self._request_sent = True
            return {"type": "http.request", "body": b"", "more_body": False}
        await self._disconnect.wait()
        return {"type": "http.disconnect"}

    async def _send(self, message: dict) -> None:
        if message["type"] == "http.response.start":
            self.status = message["status"]
        elif message["type"] == "http.response.body":
            self._buffer += message.get("body", b"").decode()
            while "\n\n" in self._buffer:
                block, self._buffer = self._buffer.split("\n\n", 1)
                for line in block.splitlines():
                    if line.startswith("data: "):
                        self.events.put_nowait(json.loads(line[len("data: "):]))

    def open(self) -> StreamClient:
        self.task = asyncio.create_task(self.app(self.scope, self._receive, self._send))
        return self

    async def next_event(self, timeout: float = 5.0) -> dict:
        return await asyncio.wait_for(self.events.get(), timeout)

    async def close(self) -> bool:
        """Disconnect the client; True when the app finished its response."""
        self._disconnect.set()
        assert self.task is not None
        done, _ = await asyncio.wait({self.task}, timeout=5.0)
        return self.task in done


# --- the stats stream ------------------------------------------------------


@pytest.mark.asyncio
async def test_page_reloads_on_a_hung_box_do_not_accumulate_connections(app, ledger):
    """Reproduces the field report: five reloads of the overview page while a
    box's stats command hangs. Before the fix every closed stream left its
    probe running, so each reload added one live connection (5 after 5).

    Mutation killed: a closed stream leaving its in-flight probe running
    (no cancel when the last subscriber leaves), or the pool returning a
    cancelled probe's connection instead of closing it.
    """
    ledger.hang = True
    reloads = 5
    for n in range(1, reloads + 1):
        stream = StreamClient(app).open()
        assert await wait_until(lambda n=n: len(ledger.opened) == n), (
            f"reload {n}: the stream never connected (opened={len(ledger.opened)})"
        )
        assert await stream.close() is True, f"reload {n}: the stream ignored the disconnect"

    await wait_until(lambda: ledger.live() == 0)
    assert len(ledger.opened) == reloads
    assert ledger.live() == 0


class RecordingProbes(stats_stream.SharedStatsProbes):
    """``SharedStatsProbes`` that counts joins and remembers its probe tasks."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.acquires = 0
        self.tasks: set[asyncio.Task] = set()

    def acquire(self, box, application_config):
        self.acquires += 1
        task = super().acquire(box, application_config)
        self.tasks.add(task)
        return task


def _app_with_recorded_probes(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[object, list[RecordingProbes]]:
    """A fresh app whose stats stream uses ``RecordingProbes`` (one per app)."""
    probes: list[RecordingProbes] = []

    class Recording(RecordingProbes):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            probes.append(self)

    monkeypatch.setattr(stats_stream, "SharedStatsProbes", Recording)
    return make_app(ServerSettings(csrf_token=TOKEN)), probes


@pytest.mark.asyncio
async def test_open_tabs_share_one_probe_per_box(app, ledger, monkeypatch):
    """Four tabs open on the overview at once while the box hangs hold one
    connection between them, not four.

    Mutation killed: every stream starting its own probe (no single-flight
    in ``SharedStatsProbes``).
    """
    local_app, probes = _app_with_recorded_probes(monkeypatch)
    assert len(probes) == 1
    ledger.hang = True

    tabs = [StreamClient(local_app).open() for _ in range(4)]
    # Settled: every tab has joined, and every distinct probe task is blocked
    # on its hung command (so each one has opened whatever it opens).
    assert await wait_until(
        lambda: probes[0].acquires == 4 and ledger.hung_commands == len(probes[0].tasks)
    )
    assert len(ledger.opened) == 1
    assert ledger.live() == 1
    assert probes[0].subscriber_count(BOX) == 4

    for tab in tabs:
        assert await tab.close() is True
    await wait_until(lambda: ledger.live() == 0)
    assert ledger.live() == 0
    assert probes[0].subscriber_count(BOX) == 0


@pytest.mark.asyncio
async def test_a_box_named_twice_in_one_stream_is_probed_once(app, ledger, monkeypatch):
    """``?boxes=remote1,remote1`` joins the box's probe once, so closing the
    stream leaves no subscriber and the hung probe's connection is closed.

    Mutation killed: no de-duplication of ``box_names`` (the stream joins
    twice and leaves once: subscriber_count stays 1 after close, and the
    probe and its connection live on, live == 1).
    """
    local_app, probes = _app_with_recorded_probes(monkeypatch)
    ledger.hang = True

    stream = StreamClient(local_app, boxes=f"{BOX},{BOX}").open()
    assert await wait_until(lambda: ledger.hung_commands == 1)
    assert probes[0].subscriber_count(BOX) == 1

    assert await stream.close() is True
    await wait_until(lambda: ledger.live() == 0)
    assert probes[0].subscriber_count(BOX) == 0
    assert len(ledger.opened) == 1
    assert ledger.live() == 0


@pytest.mark.asyncio
async def test_hung_probe_times_out_and_its_connection_is_closed(app, ledger, monkeypatch):
    """A probe on a hung box ends at the probe timeout with an error event,
    and the connection it held is closed rather than parked in the pool.

    Mutations killed: no timeout around the probe (no event ever arrives);
    the pool releasing a timed-out connection back to the pool (live == 1).
    """
    monkeypatch.setattr(deps_module, "PROBE_TIMEOUT_SECONDS", 0.05, raising=False)
    ledger.hang = True
    stream = StreamClient(app).open()
    try:
        event = await stream.next_event()
    except TimeoutError:
        pytest.fail(f"no stats event within 5 s; live connections: {ledger.live()}")
    finally:
        await stream.close()

    assert stream.status == 200
    assert event["name"] == BOX
    assert event["error"] == HUNG_COMMAND_ERROR
    assert event["cpu_percent"] is None
    await wait_until(lambda: ledger.live() == 0)
    assert len(ledger.opened) == 1
    assert ledger.live() == 0


@pytest.mark.asyncio
async def test_stream_emits_parsed_stats_and_keeps_one_pooled_connection(app, ledger):
    """A healthy box: the first event carries the hand-computed values and
    the probe's connection goes back to the pool (one live, reusable).

    Mutation killed: the probe closing its connection instead of returning it
    to the pool (live == 0 and the next cycle reconnects), or wrong parsing.
    """
    stream = StreamClient(app).open()
    try:
        event = await stream.next_event()
    finally:
        assert await stream.close() is True

    assert event == {
        "name": BOX,
        "cpu_percent": 50.0,
        "memory_used_mb": 1000.0,
        "memory_total_mb": 2000.0,
        "memory_percent": 50.0,
        "uptime_seconds": 3600,
        "error": None,
    }
    assert len(ledger.opened) == 1
    assert ledger.live() == 1


# --- polled REST endpoints ---------------------------------------------------


@pytest.mark.asyncio
async def test_session_sync_polling_reuses_one_connection(app, ledger):
    """The overview polls ``sessions/sync`` every 30 s per box. Before the fix
    the route never closed its connection: ten polls left ten live
    connections, kept alive forever by the 30 s keepalive.

    Mutation killed: the sync route opening a connection with
    ``connect_for_box`` and not closing it (opened == live == 10).
    """
    async with _client(app) as client:
        for _ in range(10):
            resp = await client.post(f"/api/v1/boxes/{BOX}/sessions/sync")
            assert resp.status_code == 200
            assert resp.json() == []

    assert len(ledger.opened) == 1
    assert ledger.live() == 1


@pytest.mark.asyncio
async def test_status_stats_and_git_polls_reuse_one_connection(app, ledger):
    """Status, stats and git badges are polled (git every 15 s per terminal
    tab); three rounds of all three run on one pooled connection.

    Mutation killed: any of the three routes opening its own connection per
    request instead of using the pool (opened == 9).
    """
    async with _client(app) as client:
        for _ in range(3):
            status = await client.get(f"/api/v1/boxes/{BOX}/status")
            stats = await client.get(f"/api/v1/boxes/{BOX}/stats")
            git = await client.get(f"/api/v1/boxes/{BOX}/git", params={"directory": "/srv"})
            assert status.json()["status"] == "online"
            assert stats.json()["cpu_percent"] == 50.0
            assert git.json() == {
                "branch": "main",
                "commit": "abc1234",
                "is_repo": True,
                "dirty": False,
                "error": None,
            }

    assert len(ledger.opened) == 1
    assert ledger.live() == 1


@pytest.mark.parametrize(
    ("method", "path", "params", "expected"),
    [
        ("GET", "/status", None, {"name": BOX, "status": "offline", "latency_ms": None}),
        ("GET", "/stats", None, {"name": BOX, "error": HUNG_COMMAND_ERROR}),
        ("GET", "/git", {"directory": "/srv"}, {"error": HUNG_COMMAND_ERROR}),
        ("POST", "/sessions/sync", None, []),
    ],
)
@pytest.mark.asyncio
async def test_polled_route_on_a_hung_box_times_out_and_closes(
    app, ledger, monkeypatch, method, path, params, expected
):
    """A hung box answers each polled route within the probe timeout and the
    connection the request held is closed.

    Mutations killed: a route without the probe timeout (the request never
    returns); a timed-out connection returned to the pool (live == 1).
    """
    monkeypatch.setattr(deps_module, "PROBE_TIMEOUT_SECONDS", 0.05, raising=False)
    ledger.hang = True
    async with _client(app) as client:
        try:
            resp = await asyncio.wait_for(
                client.request(method, f"/api/v1/boxes/{BOX}{path}", params=params), 5.0
            )
        except TimeoutError:
            pytest.fail(f"{method} {path} did not return within 5 s on a hung box")

    assert resp.status_code == 200
    body = resp.json()
    if isinstance(expected, dict):
        assert {key: body[key] for key in expected} == expected
    else:
        assert body == expected
    await wait_until(lambda: ledger.live() == 0)
    assert len(ledger.opened) == 1
    assert ledger.live() == 0


@pytest.mark.parametrize(
    ("probe_limit", "connect_limit", "expected"),
    [
        # The probe limit runs out while the connect is still going.
        (0.05, 5.0, "connect to remote1 timed out after 0.05 s"),
        # The pool's own connect limit runs out first.
        (5.0, 0.05, "connect to remote1 timed out after 0.05 s"),
    ],
    ids=["probe-limit", "pool-connect-limit"],
)
@pytest.mark.asyncio
async def test_a_hung_connect_is_reported_as_a_connect_timeout(
    app, ledger, monkeypatch, probe_limit, connect_limit, expected
):
    """When the handshake never finishes, ``/stats`` says the connect timed
    out, with the limit that actually ran out, not a generic "timed out".

    Mutations killed: one message for every step (the connect is reported
    as a command timeout); the pool's connect without a time limit (the
    pool-connect-limit case runs to the 5 s probe limit instead).
    """
    monkeypatch.setattr(deps_module, "PROBE_TIMEOUT_SECONDS", probe_limit, raising=False)
    monkeypatch.setattr(ssh_pool, "_global_pool", SSHConnectionPool(connect_timeout=connect_limit))
    ledger.hang_connect = True
    async with _client(app) as client:
        resp = await asyncio.wait_for(client.get(f"/api/v1/boxes/{BOX}/stats"), 10.0)

    assert resp.status_code == 200
    assert resp.json()["error"] == expected
    assert ledger.connect_attempts == 1
    assert len(ledger.opened) == 0


class ReleaseGateConnection:
    """Answers commands at once without awaiting; only the pool's health
    check (``echo test``) can block, until ``gate`` is set."""

    def __init__(self) -> None:
        self.closed = False
        self.gate = asyncio.Event()
        self.checking = asyncio.Event()

    async def run(self, command: str, check: bool = False):
        if command == "echo test":
            self.checking.set()
            await self.gate.wait()
        return _result(0, "ok\n")

    def close(self) -> None:
        self.closed = True


@pytest.mark.asyncio
async def test_run_pooled_does_not_count_the_release_against_the_probe_limit(monkeypatch):
    """A probe whose command finished is not a timeout because returning the
    connection to the pool is slow: the result comes back and the healthy
    connection is pooled.

    A 0 s limit expires at the first await inside the bound. The fake connect
    and command never await, so only the release's health check suspends.

    Mutation killed: the probe limit also bounding ``release`` (the health
    check is cancelled, the call raises a timeout and the good connection is
    closed).
    """
    monkeypatch.setattr(deps_module, "PROBE_TIMEOUT_SECONDS", 0.0, raising=False)
    pool = SSHConnectionPool()
    monkeypatch.setattr(ssh_pool, "_global_pool", pool)
    conn = ReleaseGateConnection()

    async def connect(self, box, application_config):
        return conn

    monkeypatch.setattr(APIDependencies, "connect_for_box", connect)
    deps = APIDependencies(ServerSettings(csrf_token=TOKEN))

    async def operation(c):
        result = await c.run("uptime")
        return result.stdout

    task = asyncio.create_task(deps.run_pooled(_box(), SimpleNamespace(), operation))
    await asyncio.wait_for(conn.checking.wait(), 5.0)
    conn.gate.set()

    assert await asyncio.wait_for(task, 5.0) == "ok\n"
    assert conn.closed is False
    assert pool.stats()[BOX]["active_connections"] == 1


@pytest.mark.asyncio
async def test_refresh_lets_the_next_probe_reconnect_at_once(app, ledger):
    """Refreshing a box clears the pool's failure cache, so a box that was
    down is probed again immediately instead of 60 s later.

    Mutation killed: ``/refresh`` clearing only the dependency-level failure
    cache (the pool keeps answering "unreachable (cached)", status offline).
    """
    ledger.fail_connect = True
    async with _client(app) as client:
        down = await client.get(f"/api/v1/boxes/{BOX}/status")
        ledger.fail_connect = False
        refreshed = await client.post(f"/api/v1/boxes/{BOX}/refresh")
        up = await client.get(f"/api/v1/boxes/{BOX}/status")

    assert down.json() == {"name": BOX, "status": "offline", "latency_ms": None}
    assert refreshed.json() == {"name": BOX, "refreshed": True}
    assert up.status_code == 200, up.text
    assert up.json()["status"] == "online"
    assert ledger.connect_attempts == 2
    assert ledger.live() == 1


# --- the pool itself -------------------------------------------------------


def _box(name: str = BOX):
    return SimpleNamespace(name=name)


class GateConnection:
    """Pool-level fake whose health check blocks until ``gate`` is set."""

    def __init__(self) -> None:
        self.closed = False
        self.gate = asyncio.Event()
        self.checking = asyncio.Event()

    async def run(self, command: str, check: bool = False):
        self.checking.set()
        await self.gate.wait()
        return _result(0, "test\n")

    def close(self) -> None:
        self.closed = True


@pytest.mark.asyncio
async def test_pool_closes_a_pooled_connection_when_acquire_is_cancelled():
    """Cancelling ``acquire`` while it health-checks a pooled connection
    closes that connection; it has already left the pool, so nothing else
    would.

    Mutation killed: ``acquire`` without the cancel guard around the health
    check (the popped connection stays open and unreferenced).
    """
    pool = SSHConnectionPool()
    conn = GateConnection()
    conn.gate.set()
    await pool.release(BOX, conn)
    assert pool.stats()[BOX]["active_connections"] == 1
    conn.gate.clear()
    conn.checking.clear()

    async def never_connect():
        raise AssertionError("the pooled connection should be tried first")

    task = asyncio.create_task(pool.acquire(_box(), never_connect))
    await conn.checking.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert conn.closed is True
    assert pool.stats() == EMPTY_POOL


@pytest.mark.asyncio
async def test_pool_closes_a_connection_when_release_is_cancelled():
    """Cancelling ``release`` during its health check closes the connection.

    Mutation killed: ``release`` without the cancel guard (the connection is
    neither pooled nor closed).
    """
    pool = SSHConnectionPool()
    conn = GateConnection()
    task = asyncio.create_task(pool.release(BOX, conn))
    await conn.checking.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert conn.closed is True
    assert pool.stats() == EMPTY_POOL


class HungConnect:
    """A ``connect_func`` whose handshake never finishes."""

    def __init__(self) -> None:
        self.attempts = 0
        self.started = asyncio.Event()

    async def __call__(self):
        self.attempts += 1
        self.started.set()
        await asyncio.Event().wait()


@pytest.mark.asyncio
async def test_release_cancelled_while_a_connect_to_the_box_hangs_closes_the_connection():
    """Another caller's connect to the box hangs; a ``release`` started now
    and then cancelled still closes its connection.

    Mutation killed: ``release`` waiting on a per-box lock that the hung
    connect holds, outside any cancel handling (the cancel lands at the lock:
    the connection is neither pooled nor closed, closed is False).
    """
    pool = SSHConnectionPool()
    hung = HungConnect()
    connecting = asyncio.create_task(pool.acquire(_box(), hung))
    await asyncio.wait_for(hung.started.wait(), 5.0)

    conn = GateConnection()  # its health check blocks until the gate opens
    releasing = asyncio.create_task(pool.release(BOX, conn))
    # Let the release reach whatever it waits on (the health check here).
    await wait_until(conn.checking.is_set, timeout=0.5)
    releasing.cancel()
    with pytest.raises(asyncio.CancelledError):
        await releasing

    assert conn.closed is True
    assert pool.stats() == EMPTY_POOL
    connecting.cancel()
    with pytest.raises(asyncio.CancelledError):
        await connecting


@pytest.mark.asyncio
async def test_a_hung_connect_does_not_hold_up_other_callers_for_the_box():
    """While one caller's connect to the box hangs, another caller can give
    its connection back and a third can take it, at once.

    Mutation killed: a per-box lock held across ``connect_func`` (the release
    waits for the hung connect: no return within 1 s).
    """
    pool = SSHConnectionPool()
    spare = GateConnection()
    spare.gate.set()

    async def connect_spare():
        return spare

    held = await pool.acquire(_box(), connect_spare)
    hung = HungConnect()
    connecting = asyncio.create_task(pool.acquire(_box(), hung))
    await asyncio.wait_for(hung.started.wait(), 5.0)

    async def never_connect():
        raise AssertionError("the released connection should be reused")

    try:
        await asyncio.wait_for(pool.release(BOX, held), 1.0)
        again = await asyncio.wait_for(pool.acquire(_box(), never_connect), 1.0)
    except TimeoutError:
        pytest.fail("release/acquire waited behind another caller's hung connect")
    finally:
        connecting.cancel()

    assert again is spare
    assert spare.closed is False
    assert hung.attempts == 1


@pytest.mark.asyncio
async def test_a_hung_box_costs_three_connects_then_fails_fast():
    """Ten callers hit a box whose handshake hangs. At most three connects run
    at once (``max_connections_per_box``); when they time out the box goes
    into the failure cache, and every other caller, then and later, fails
    fast without connecting.

    Mutations killed: no cap on connects in flight (attempts == 10); a
    timed-out connect not entering the failure cache (the waiting callers
    connect too: attempts == 10); no connect time limit (nothing returns).
    """
    pool = SSHConnectionPool(max_connections_per_box=3, connect_timeout=0.05)
    hung = HungConnect()
    try:
        results = await asyncio.wait_for(
            asyncio.gather(
                *(pool.acquire(_box(), hung) for _ in range(10)), return_exceptions=True
            ),
            5.0,
        )
    except TimeoutError:
        pytest.fail(f"acquire never gave up on the hung box ({hung.attempts} connects)")

    assert hung.attempts == 3
    assert [type(result) for result in results] == [ConnectionError] * 10
    messages = [str(result) for result in results]
    assert messages.count("connect to remote1 timed out after 0.05 s") == 3
    cached = r"remote1: unreachable \(cached, retry in \d+s\)"
    assert sum(1 for message in messages if re.fullmatch(cached, message)) == 7

    with pytest.raises(ConnectionError, match=cached):
        await pool.acquire(_box(), hung)
    assert hung.attempts == 3


@pytest.mark.asyncio
async def test_a_connection_checked_out_before_invalidate_is_closed_on_release():
    """``invalidate`` (box refresh) closes a connection that was checked out
    at the time when it comes back, since it was opened with the old
    settings; one opened after the invalidate is pooled as usual.

    Mutation killed: ``release`` ignoring the invalidate (the old connection
    goes back to the pool: closed is False, two pooled).
    """
    pool = SSHConnectionPool()
    old, new = GateConnection(), GateConnection()
    old.gate.set()
    new.gate.set()
    opened = iter([old, new])

    async def connect():
        return next(opened)

    first = await pool.acquire(_box(), connect)
    await pool.invalidate(BOX)
    second = await pool.acquire(_box(), connect)
    await pool.release(BOX, first)
    await pool.release(BOX, second)

    assert (first, second) == (old, new)
    assert old.closed is True
    assert new.closed is False
    assert pool.stats()[BOX]["active_connections"] == 1


class GatedConnects:
    """A ``connect_func`` whose connects each block on their own gate.

    ``open(n)`` lets the n-th connect (0-based) finish with a new
    ``GateConnection`` (health check passes at once); ``fail(n)`` makes it
    raise ``SSHError`` instead. With ``auto_open`` set, later connects
    finish at once.
    """

    def __init__(self) -> None:
        self.attempts = 0
        self.opened: list[GateConnection] = []
        self.auto_open = False
        self._gates: list[asyncio.Future] = []

    async def __call__(self):
        self.attempts += 1
        gate = asyncio.get_running_loop().create_future()
        self._gates.append(gate)
        error = None if self.auto_open else await gate
        if error is not None:
            raise error
        conn = GateConnection()
        conn.gate.set()
        self.opened.append(conn)
        return conn

    def open(self, n: int) -> None:
        self._gates[n].set_result(None)

    def fail(self, n: int) -> None:
        self._gates[n].set_result(SSHError(f"{BOX}: connection refused"))


@pytest.mark.asyncio
async def test_a_failed_connect_that_started_before_a_success_does_not_mark_the_box_down():
    """Three callers connect at once; two succeed, then the third fails. The
    box is evidently up, so the next caller connects instead of being told
    "unreachable (cached)" for 60 s.

    Mutation killed: a failed connect always entering the failure cache,
    whatever succeeded while it ran (the fourth acquire raises
    ConnectionError "unreachable (cached ...)", attempts stays 3).
    """
    pool = SSHConnectionPool(max_connections_per_box=3)
    connects = GatedConnects()
    callers = [asyncio.create_task(pool.acquire(_box(), connects)) for _ in range(3)]
    assert await wait_until(lambda: connects.attempts == 3)
    connects.open(0)
    connects.open(1)
    first, second = await asyncio.wait_for(asyncio.gather(callers[0], callers[1]), 5.0)
    connects.fail(2)
    with pytest.raises(SSHError, match="remote1: connection refused"):
        await asyncio.wait_for(callers[2], 5.0)

    fourth = asyncio.create_task(pool.acquire(_box(), connects))
    assert await wait_until(lambda: connects.attempts == 4)
    connects.open(3)
    third_conn = await asyncio.wait_for(fourth, 5.0)

    assert [first, second, third_conn] == connects.opened
    assert connects.attempts == 4


@pytest.mark.asyncio
async def test_an_idle_healthy_connection_is_handed_out_while_the_box_is_in_the_fail_cache():
    """Connection A is checked out; a later connect fails, so the box is in
    the failure cache. A then comes back to the pool healthy, and the next
    caller gets A instead of "unreachable (cached)".

    Mutation killed: the failure cache consulted before the idle pool
    (the last acquire raises ConnectionError "unreachable (cached ...)").
    """
    pool = SSHConnectionPool()
    connects = GatedConnects()
    first = asyncio.create_task(pool.acquire(_box(), connects))
    assert await wait_until(lambda: connects.attempts == 1)
    connects.open(0)
    conn_a = await asyncio.wait_for(first, 5.0)

    second = asyncio.create_task(pool.acquire(_box(), connects))
    assert await wait_until(lambda: connects.attempts == 2)
    connects.fail(1)
    with pytest.raises(SSHError, match="remote1: connection refused"):
        await asyncio.wait_for(second, 5.0)
    # The failure is cached: with nothing idle, the box fails fast.
    with pytest.raises(ConnectionError, match=r"remote1: unreachable \(cached"):
        await pool.acquire(_box(), connects)

    await pool.release(BOX, conn_a)

    async def never_connect():
        raise AssertionError("the idle connection should be handed out")

    assert await pool.acquire(_box(), never_connect) is conn_a
    assert conn_a.closed is False
    assert connects.attempts == 2


@pytest.mark.asyncio
async def test_a_cancelled_connect_does_not_enter_the_fail_cache():
    """A caller whose connect hangs is cancelled (a closed tab). That says
    nothing about the box: the next caller connects with a working connect
    and gets that connection.

    Mutation killed: the connect's ``except Exception`` widened to
    ``except BaseException`` (the cancel fills the failure cache and the
    second acquire raises "unreachable (cached ...)", attempts == 1).
    """
    pool = SSHConnectionPool()
    attempts = 0
    started = asyncio.Event()
    working = GateConnection()
    working.gate.set()

    async def connect():
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            started.set()
            await asyncio.Event().wait()  # never set: the first handshake hangs
        return working

    hung = asyncio.create_task(pool.acquire(_box(), connect))
    await asyncio.wait_for(started.wait(), 5.0)
    hung.cancel()
    with pytest.raises(asyncio.CancelledError):
        await hung

    assert await asyncio.wait_for(pool.acquire(_box(), connect), 5.0) is working
    assert attempts == 2


@pytest.mark.asyncio
async def test_three_slow_connects_serve_seven_waiting_callers():
    """Ten callers arrive at once on an empty pool. Three connects start (the
    per-box limit) and seven callers wait. Each served caller works for a
    while, then releases, and every release hands its connection to a
    waiting caller, so the ten are served by exactly three connections.

    The wait deadline is 30 s, so within this test a waiter is served only
    by a handoff. The settled conditions read the pool's private
    ``_waiters`` to know who is queued.

    Mutations killed: waiters woken only when a connect finishes, never by
    a release (the WS-Y acquire: each wakes, finds the pool empty and
    connects: attempts == 10); a release that pools the connection without
    handing it to a waiter (the waiters sit out their deadline: nothing
    finishes within 5 s).
    """
    pool = SSHConnectionPool(max_connections_per_box=3, wait_timeout=30.0)
    connects = GatedConnects()
    served: list[GateConnection] = []
    work = asyncio.Event()

    async def caller():
        conn = await pool.acquire(_box(), connects)
        served.append(conn)
        await work.wait()
        await pool.release(BOX, conn)

    def waiting() -> int:
        return len(pool._waiters.get(BOX, ()))

    tasks = [asyncio.create_task(caller()) for _ in range(10)]
    assert await wait_until(lambda: connects.attempts == 3 and waiting() == 7)
    # Any further connect finishes at once, so a pool that opens more shows
    # the count instead of hanging.
    connects.auto_open = True
    for n in range(3):
        connects.open(n)
    # Settled: every caller is either served (and working) or queued.
    await wait_until(lambda: len(served) + waiting() == 10)
    work.set()
    try:
        await asyncio.wait_for(asyncio.gather(*tasks), 5.0)
    except TimeoutError:
        pytest.fail(f"{len(served)} of 10 callers served within 5 s")

    assert connects.attempts == 3
    assert len(connects.opened) == 3
    assert len(served) == 10
    assert {id(conn) for conn in served} == {id(conn) for conn in connects.opened}
    assert [conn.closed for conn in connects.opened] == [False, False, False]
    assert pool.stats()[BOX]["active_connections"] == 3


@pytest.mark.asyncio
async def test_a_waiting_caller_connects_itself_after_its_wait_deadline():
    """Three callers hold the only three connections and never release. A
    fourth caller, queued behind the connects, waits for a release up to
    the wait deadline and then opens its own connection.

    Mutation killed: a wait without a deadline (the fourth acquire never
    returns: no connection within 5 s).
    """
    pool = SSHConnectionPool(max_connections_per_box=3, wait_timeout=0.05)
    connects = GatedConnects()
    holders = [asyncio.create_task(pool.acquire(_box(), connects)) for _ in range(3)]
    fourth = asyncio.create_task(pool.acquire(_box(), connects))
    assert await wait_until(lambda: connects.attempts == 3 and len(pool._waiters.get(BOX, ())) == 1)
    for n in range(3):
        connects.open(n)
    held = await asyncio.wait_for(asyncio.gather(*holders), 5.0)

    assert await wait_until(lambda: connects.attempts == 4)
    connects.open(3)
    try:
        own = await asyncio.wait_for(fourth, 5.0)
    except TimeoutError:
        pytest.fail(f"the waiting caller never connected ({connects.attempts} connects)")

    assert held == connects.opened[:3]
    assert own is connects.opened[3]
    assert connects.attempts == 4


@pytest.mark.asyncio
async def test_a_waiter_cancelled_after_a_handoff_passes_the_connection_on():
    """A release hands its connection to a queued caller, and that caller is
    cancelled before it runs (a closed tab). The connection goes back to the
    pool instead of being lost.

    Mutation killed: ``_wait`` dropping a handed-over connection on cancel
    (the connection is neither pooled nor closed: pool empty, closed False).
    """
    pool = SSHConnectionPool(max_connections_per_box=1, wait_timeout=30.0)
    connects = GatedConnects()
    first = asyncio.create_task(pool.acquire(_box(), connects))
    waiter = asyncio.create_task(pool.acquire(_box(), connects))
    assert await wait_until(lambda: connects.attempts == 1 and len(pool._waiters.get(BOX, ())) == 1)
    connects.open(0)
    conn = await asyncio.wait_for(first, 5.0)
    # The finished connect woke the waiter; it queues again for a release.
    assert await wait_until(lambda: len(pool._waiters.get(BOX, ())) == 1)

    await pool.release(BOX, conn)  # health check passes without suspending
    waiter.cancel()  # before the waiter has run with the handed connection
    with pytest.raises(asyncio.CancelledError):
        await waiter

    assert conn.closed is False
    assert pool.stats()[BOX]["active_connections"] == 1
    assert connects.attempts == 1


@pytest.mark.parametrize("outcome", ["succeeds", "fails"])
@pytest.mark.asyncio
async def test_a_connect_started_before_invalidate_counts_under_the_old_settings(outcome):
    """A box refresh (``invalidate``) lands while a connect is in flight. That
    connect used the old settings: if it succeeds, its connection is closed
    on release rather than pooled; if it fails, the failure does not put the
    box (as configured now) in the failure cache.

    Mutation killed: ``_connect`` reading the box's generation after
    ``await connect_func()`` instead of before (succeeds: the old
    connection is pooled, closed is False; fails: the next acquire raises
    "unreachable (cached ...)").
    """
    pool = SSHConnectionPool()
    connects = GatedConnects()
    first = asyncio.create_task(pool.acquire(_box(), connects))
    assert await wait_until(lambda: connects.attempts == 1)
    await pool.invalidate(BOX)

    if outcome == "succeeds":
        connects.open(0)
        old = await asyncio.wait_for(first, 5.0)
        await pool.release(BOX, old)
        assert old.closed is True
        assert pool.stats() == EMPTY_POOL
    else:
        connects.fail(0)
        with pytest.raises(SSHError, match="remote1: connection refused"):
            await asyncio.wait_for(first, 5.0)
        second = asyncio.create_task(pool.acquire(_box(), connects))
        assert await wait_until(lambda: connects.attempts == 2)
        connects.open(1)
        assert await asyncio.wait_for(second, 5.0) is connects.opened[0]


@pytest.mark.asyncio
async def test_four_releases_finishing_together_pool_three_and_close_one():
    """Four connections come back at once; their health checks all start on
    an empty pool and finish together. The idle pool holds three
    (``max_connections_per_box``), so exactly one is closed.

    Mutation killed: no room re-check after the health check (all four are
    pooled: active_connections == 4, nothing closed).
    """
    pool = SSHConnectionPool(max_connections_per_box=3)
    conns = [GateConnection() for _ in range(4)]
    releases = [asyncio.create_task(pool.release(BOX, conn)) for conn in conns]
    assert await wait_until(lambda: all(conn.checking.is_set() for conn in conns))
    for conn in conns:
        conn.gate.set()
    await asyncio.wait_for(asyncio.gather(*releases), 5.0)

    assert pool.stats()[BOX]["active_connections"] == 3
    assert [conn.closed for conn in conns] == [False, False, False, True]


@pytest.mark.parametrize("error", [TimeoutError(), asyncio.CancelledError()])
@pytest.mark.asyncio
async def test_pool_context_closes_the_connection_on_timeout_or_cancel(error):
    """A block that times out or is cancelled may have left a command running
    on the connection, so ``connection()`` closes it instead of pooling it.

    Mutation killed: ``connection()`` releasing on every exit path
    (the connection is pooled: closed is False, one pooled entry).
    """
    pool = SSHConnectionPool()
    conn = GateConnection()
    conn.gate.set()

    async def connect():
        return conn

    with pytest.raises(type(error)):
        async with pool.connection(_box(), connect):
            raise error

    assert conn.closed is True
    assert pool.stats() == EMPTY_POOL


@pytest.mark.asyncio
async def test_pool_context_keeps_the_connection_on_an_ordinary_error():
    """An ordinary error (a missing file, a bad path) says nothing about the
    connection, so it goes back to the pool.

    Mutation killed: discarding the connection on every exception
    (closed is True, nothing pooled).
    """
    pool = SSHConnectionPool()
    conn = GateConnection()
    conn.gate.set()

    async def connect():
        return conn

    with pytest.raises(FileNotFoundError):
        async with pool.connection(_box(), connect):
            raise FileNotFoundError("/srv/missing")

    assert conn.closed is False
    assert pool.stats()[BOX]["active_connections"] == 1


# --- tunnels ---------------------------------------------------------------


@pytest.fixture
def no_tunnels():
    tunnels_module._active_tunnels.clear()
    yield
    tunnels_module._active_tunnels.clear()


TUNNEL = {
    "tunnel_type": "local",
    "local_host": "127.0.0.1",
    "local_port": 18080,
    "remote_host": "127.0.0.1",
    "remote_port": 80,
}


@pytest.mark.asyncio
async def test_failed_tunnel_closes_its_connection(app, ledger, no_tunnels):
    """A port forward that fails leaves no SSH connection behind.

    Mutation killed: the create route not closing the connection when the
    forward raises (live == 1).
    """
    ledger.forward_error = OSError("address already in use")
    async with _client(app) as client:
        resp = await client.post(f"/api/v1/boxes/{BOX}/tunnels", json=TUNNEL)

    assert resp.status_code == 500
    assert len(ledger.opened) == 1
    assert ledger.live() == 0


@pytest.mark.asyncio
async def test_deleting_a_tunnel_closes_its_connection(app, ledger, no_tunnels):
    """A tunnel holds one connection for its lifetime and gives it back when
    deleted.

    Mutation killed: delete closing only the listener (live stays 1).
    """
    async with _client(app) as client:
        created = await client.post(f"/api/v1/boxes/{BOX}/tunnels", json=TUNNEL)
        assert created.status_code == 200
        assert ledger.live() == 1
        tunnel_id = created.json()["id"]
        listener = tunnels_module._active_tunnels[tunnel_id].listener
        deleted = await client.delete(f"/api/v1/boxes/{BOX}/tunnels/{tunnel_id}")

    assert deleted.status_code == 200
    assert listener.closed is True
    assert len(ledger.opened) == 1
    assert ledger.live() == 0
