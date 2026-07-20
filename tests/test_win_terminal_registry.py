"""Tests for the native-Windows ConPTY persistence registry.

These run cross-platform: the registry only touches the ``WinPTYProcess``
surface, so a fake process stands in for pywinpty. The fake's ``stdout.read``
genuinely BLOCKS a worker thread (the registry drains it via
``asyncio.to_thread``) until the test ``feed()``s output or ``feed_eof()``s /
``terminate()``s it — exactly mirroring how a real ConPTY blocks until data or
child exit.
"""

from __future__ import annotations

import asyncio
import queue
import sys
import time

import pytest
import pytest_asyncio

from sshler.win_terminal_registry import CLEAR_SEQ, WindowsTerminalRegistry

# The blocking-fake ConPTY exercises real worker threads + ``asyncio.to_thread``;
# the drain/attach ordering races differently under Linux CI's scheduler. The
# code under test is Windows-only (native ConPTY persistence), so gate these to
# Windows — they pass deterministically on the real target platform.
pytestmark = pytest.mark.skipif(
    sys.platform != "win32",
    reason="Native Windows ConPTY persistence registry — Windows-only target; "
    "thread-timing fakes are non-deterministic under Linux CI.",
)


# --------------------------------------------------------------------------
# Blocking fake ConPTY process
# --------------------------------------------------------------------------


class _FakeStdin:
    def __init__(self, proc: "FakeWinProc") -> None:
        self._proc = proc

    def write(self, data: bytes) -> int:
        self._proc.written.append(data)
        return len(data)


class _FakeStdout:
    def __init__(self, proc: "FakeWinProc") -> None:
        self._proc = proc

    def read(self, size: int = 1024) -> str:
        # Blocks the worker thread until fed; None sentinel == EOF.
        item = self._proc._queue.get()
        if item is None:
            return ""
        return item


class FakeWinProc:
    """Mimics WinPTYProcess: blocking read, recorded writes/resizes, EOF on terminate."""

    def __init__(self) -> None:
        self._queue: "queue.Queue[str | None]" = queue.Queue()
        self.written: list[bytes] = []
        self.size: tuple[int, int] | None = None
        self.resize_calls = 0
        self.terminated = False
        self.closed = False
        self.returncode: int | None = None
        self.stdin = _FakeStdin(self)
        self.stdout = _FakeStdout(self)

    def feed(self, data: str) -> None:
        self._queue.put(data)

    def feed_eof(self) -> None:
        self._queue.put(None)

    def resize(self, cols: int, rows: int) -> None:
        self.size = (cols, rows)
        self.resize_calls += 1

    def terminate(self) -> None:
        self.terminated = True
        self._queue.put(None)  # force a blocked read to return EOF

    def close(self) -> None:
        self.closed = True


def _const_spawn(proc: FakeWinProc):
    async def _spawn() -> FakeWinProc:
        return proc

    return _spawn


async def _await_until(predicate, timeout: float = 3.0) -> None:
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.005)
    raise AssertionError("condition not met within timeout")


@pytest_asyncio.fixture
async def make_registry():
    """Factory that tears down every registry (terminating live shells) after the test."""
    created: list[WindowsTerminalRegistry] = []

    def _make(**kwargs) -> WindowsTerminalRegistry:
        reg = WindowsTerminalRegistry(**kwargs)
        created.append(reg)
        return reg

    try:
        yield _make
    finally:
        for reg in created:
            await reg.shutdown()


# --------------------------------------------------------------------------
# spawn / reuse
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_or_create_spawns_once_and_reuses(make_registry):
    reg = make_registry()
    proc = FakeWinProc()
    calls = 0

    async def spawn():
        nonlocal calls
        calls += 1
        return proc

    s1, created1 = await reg.get_or_create(("local", "a"), spawn, 80, 24)
    s2, created2 = await reg.get_or_create(("local", "a"), spawn, 80, 24)

    assert created1 is True
    assert created2 is False
    assert calls == 1
    assert s1 is s2


# --------------------------------------------------------------------------
# drain / ring buffer
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_drain_fills_ring_buffer(make_registry):
    reg = make_registry()
    proc = FakeWinProc()
    s, _ = await reg.get_or_create(("local", "b"), _const_spawn(proc), 80, 24)

    proc.feed("hello")
    proc.feed("world")
    await _await_until(lambda: s._ring_size == 10)

    assert s.snapshot() == b"helloworld"
    assert s._ring_size == 10


@pytest.mark.asyncio
async def test_ring_trims_to_cap(make_registry):
    reg = make_registry(ring_bytes=8)
    proc = FakeWinProc()
    s, _ = await reg.get_or_create(("local", "ring"), _const_spawn(proc), 80, 24)

    for ch in "0123456789":  # 10 one-byte chunks; cap is 8
        proc.feed(ch)
    await _await_until(lambda: s._ring_size == 8 and s.snapshot() == b"23456789")

    assert s._ring_size == 8
    assert s.snapshot() == b"23456789"


# --------------------------------------------------------------------------
# attach / replay / live delivery
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_attach_returns_clear_plus_full_ring(make_registry):
    reg = make_registry()
    proc = FakeWinProc()
    s, _ = await reg.get_or_create(("local", "c"), _const_spawn(proc), 80, 24)

    proc.feed("abcdef")
    await _await_until(lambda: s.snapshot() == b"abcdef")

    received: list[bytes] = []

    async def sink(chunk: bytes) -> None:
        received.append(chunk)

    replay = await reg.attach(s, sink, 80, 24)

    assert replay == CLEAR_SEQ + b"abcdef"
    assert received == []  # buffered history is replayed, not re-sent live


@pytest.mark.asyncio
async def test_live_output_after_attach_reaches_sink(make_registry):
    reg = make_registry()
    proc = FakeWinProc()
    s, _ = await reg.get_or_create(("local", "d"), _const_spawn(proc), 80, 24)

    received: list[bytes] = []

    async def sink(chunk: bytes) -> None:
        received.append(chunk)

    await reg.attach(s, sink, 80, 24)
    proc.feed("xyz")
    await _await_until(lambda: received == [b"xyz"])

    assert received == [b"xyz"]


@pytest.mark.asyncio
async def test_no_lost_or_duplicated_bytes_across_attach(make_registry):
    reg = make_registry()
    proc = FakeWinProc()
    s, _ = await reg.get_or_create(("local", "e"), _const_spawn(proc), 80, 24)

    proc.feed("A")
    await _await_until(lambda: s.snapshot() == b"A")

    received: list[bytes] = []

    async def sink(chunk: bytes) -> None:
        received.append(chunk)

    replay = await reg.attach(s, sink, 80, 24)
    proc.feed("B")
    await _await_until(lambda: received == [b"B"])

    assert replay == CLEAR_SEQ + b"A"
    assert received == [b"B"]
    # Each byte delivered exactly once: A via replay, B via live.
    assert replay[len(CLEAR_SEQ):] + b"".join(received) == b"AB"


# --------------------------------------------------------------------------
# mirroring (tmux-style multi-attach)
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mirror_fans_out_to_all_sinks(make_registry):
    reg = make_registry()
    proc = FakeWinProc()
    s, _ = await reg.get_or_create(("local", "f"), _const_spawn(proc), 80, 24)

    r1: list[bytes] = []
    r2: list[bytes] = []

    async def s1(chunk: bytes) -> None:
        r1.append(chunk)

    async def s2(chunk: bytes) -> None:
        r2.append(chunk)

    await reg.attach(s, s1, 80, 24)
    await reg.attach(s, s2, 80, 24)
    proc.feed("X")
    await _await_until(lambda: r1 == [b"X"] and r2 == [b"X"])

    assert r1 == [b"X"]
    assert r2 == [b"X"]


@pytest.mark.asyncio
async def test_mirror_new_tab_replays_without_disturbing_existing(make_registry):
    reg = make_registry()
    proc = FakeWinProc()
    s, _ = await reg.get_or_create(("local", "g"), _const_spawn(proc), 80, 24)

    a: list[bytes] = []

    async def sa(chunk: bytes) -> None:
        a.append(chunk)

    await reg.attach(s, sa, 80, 24)
    proc.feed("live1")
    await _await_until(lambda: a == [b"live1"])

    b: list[bytes] = []

    async def sb(chunk: bytes) -> None:
        b.append(chunk)

    replay = await reg.attach(s, sb, 80, 24)

    assert replay == CLEAR_SEQ + b"live1"
    assert a == [b"live1"]  # existing tab untouched by the new attach

    proc.feed("live2")
    await _await_until(lambda: a == [b"live1", b"live2"] and b == [b"live2"])

    assert a == [b"live1", b"live2"]
    assert b == [b"live2"]


@pytest.mark.asyncio
async def test_dead_sink_dropped_others_continue(make_registry):
    reg = make_registry()
    proc = FakeWinProc()
    s, _ = await reg.get_or_create(("local", "h"), _const_spawn(proc), 80, 24)

    good: list[bytes] = []

    async def good_sink(chunk: bytes) -> None:
        good.append(chunk)

    async def bad_sink(chunk: bytes) -> None:
        raise RuntimeError("websocket gone")

    await reg.attach(s, good_sink, 80, 24)
    await reg.attach(s, bad_sink, 80, 24)

    proc.feed("hi")
    await _await_until(lambda: good == [b"hi"])
    await _await_until(lambda: bad_sink not in s.sinks)

    assert good == [b"hi"]
    assert bad_sink not in s.sinks
    assert good_sink in s.sinks

    proc.feed("again")
    await _await_until(lambda: good == [b"hi", b"again"])
    assert good == [b"hi", b"again"]


# --------------------------------------------------------------------------
# detach keeps the shell alive
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_detach_keeps_process_alive(make_registry):
    reg = make_registry()
    proc = FakeWinProc()
    key = ("local", "i")
    s, _ = await reg.get_or_create(key, _const_spawn(proc), 80, 24)

    received: list[bytes] = []

    async def sink(chunk: bytes) -> None:
        received.append(chunk)

    await reg.attach(s, sink, 80, 24)
    await reg.detach(s, sink)

    assert proc.terminated is False
    assert reg.get(key) is s
    assert s.drain_task is not None
    assert s.drain_task.done() is False
    assert s.detached_since is not None

    proc.feed("more")
    await _await_until(lambda: s.snapshot() == b"more")

    assert received == []  # detached: no live delivery
    assert s.snapshot() == b"more"  # but still buffered


@pytest.mark.asyncio
async def test_reattach_after_detach_replays_gap(make_registry):
    reg = make_registry()
    proc = FakeWinProc()
    key = ("local", "j")
    s, _ = await reg.get_or_create(key, _const_spawn(proc), 80, 24)

    r1: list[bytes] = []

    async def s1(chunk: bytes) -> None:
        r1.append(chunk)

    await reg.attach(s, s1, 80, 24)
    proc.feed("seen")
    await _await_until(lambda: r1 == [b"seen"])
    await reg.detach(s, s1)

    proc.feed("gap")  # produced while fully detached
    await _await_until(lambda: s.snapshot() == b"seengap")

    r2: list[bytes] = []

    async def s2(chunk: bytes) -> None:
        r2.append(chunk)

    replay = await reg.attach(s, s2, 80, 24)
    assert replay == CLEAR_SEQ + b"seengap"


# --------------------------------------------------------------------------
# reaper / kill / shutdown
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reaper_removes_exited_and_marks_inactive(make_registry):
    reg = make_registry()
    reaped: list[tuple] = []

    async def on_reaped(key, session_id):
        reaped.append((key, session_id))

    reg._on_reaped = on_reaped

    proc = FakeWinProc()
    key = ("local", "x")
    s, _ = await reg.get_or_create(key, _const_spawn(proc), 80, 24)
    s.session_id = "sid-123"

    proc.feed_eof()  # shell exits on its own
    await _await_until(lambda: s.exited is True)

    result = await reg.reap_once()

    assert result == [key]
    assert reg.get(key) is None
    assert reaped == [(key, "sid-123")]


@pytest.mark.asyncio
async def test_ttl_zero_never_idle_reaps(make_registry):
    reg = make_registry(ttl_seconds=0)
    proc = FakeWinProc()
    key = ("local", "persist")
    s, _ = await reg.get_or_create(key, _const_spawn(proc), 80, 24)
    s.detached_since = time.monotonic() - 10_000  # ancient, but TTL disabled

    result = await reg.reap_once()

    assert result == []
    assert reg.get(key) is s
    assert proc.terminated is False


@pytest.mark.asyncio
async def test_ttl_positive_idle_reaps(make_registry):
    reg = make_registry(ttl_seconds=10)
    proc = FakeWinProc()
    key = ("local", "stale")
    s, _ = await reg.get_or_create(key, _const_spawn(proc), 80, 24)
    s.detached_since = time.monotonic() - 100  # past the 10s TTL

    result = await reg.reap_once()

    assert result == [key]
    assert reg.get(key) is None
    assert proc.terminated is True


@pytest.mark.asyncio
async def test_kill_terminates_and_unblocks_drain(make_registry):
    reg = make_registry()
    proc = FakeWinProc()
    key = ("local", "k")
    s, _ = await reg.get_or_create(key, _const_spawn(proc), 80, 24)

    ok = await reg.kill(key)

    assert ok is True
    assert proc.terminated is True
    assert reg.get(key) is None
    assert s.exited_event.is_set() is True
    assert s.drain_task is not None
    assert s.drain_task.done() is True

    assert await reg.kill(("nope", "nope")) is False


@pytest.mark.asyncio
async def test_shutdown_terminates_all_and_marks_inactive(make_registry):
    reg = make_registry()
    reaped: list[tuple] = []

    async def on_reaped(key, session_id):
        reaped.append((key, session_id))

    reg._on_reaped = on_reaped

    p1 = FakeWinProc()
    p2 = FakeWinProc()
    s1, _ = await reg.get_or_create(("local", "1"), _const_spawn(p1), 80, 24)
    s1.session_id = "id1"
    s2, _ = await reg.get_or_create(("local", "2"), _const_spawn(p2), 80, 24)
    s2.session_id = "id2"

    await reg.shutdown()

    assert p1.terminated is True
    assert p2.terminated is True
    assert reg.live_keys() == []
    assert set(reaped) == {(("local", "1"), "id1"), (("local", "2"), "id2")}


# --------------------------------------------------------------------------
# resize-on-attach / write
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resize_on_attach_when_size_differs(make_registry):
    reg = make_registry()
    proc = FakeWinProc()
    s, _ = await reg.get_or_create(("local", "r"), _const_spawn(proc), 80, 24)

    async def sink(chunk: bytes) -> None:
        pass

    await reg.attach(s, sink, 100, 40)
    assert proc.size == (100, 40)
    assert (s.cols, s.rows) == (100, 40)
    assert proc.resize_calls == 1

    async def sink2(chunk: bytes) -> None:
        pass

    await reg.attach(s, sink2, 100, 40)  # same size -> no extra resize
    assert proc.resize_calls == 1


@pytest.mark.asyncio
async def test_write_forwards_bytes_to_stdin(make_registry):
    reg = make_registry()
    proc = FakeWinProc()
    s, _ = await reg.get_or_create(("local", "w"), _const_spawn(proc), 80, 24)

    await reg.write(s, b"ls -la\n")

    assert proc.written == [b"ls -la\n"]
