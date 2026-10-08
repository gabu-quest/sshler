"""Pool bookkeeping the leak suite does not reach: connection age and two races.

``SSHConnectionPool`` takes an injected ``clock`` so a connection can be aged
without sleeping. Each test names the mutation it kills in its docstring.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from sshler.ssh_pool import SSHConnectionPool

BOX = "age-box"
OPENED_AT = 1_000_000.0
MAX_LIFETIME = 3600


class Clock:
    def __init__(self, now: float) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


class Conn:
    """Health check passes at once unless ``gate`` has been cleared."""

    def __init__(self) -> None:
        self.closed = False
        self.gate = asyncio.Event()
        self.gate.set()
        self.checking = asyncio.Event()

    async def run(self, command: str, check: bool = False):
        self.checking.set()
        await self.gate.wait()
        return SimpleNamespace(exit_status=0)

    def close(self) -> None:
        self.closed = True


def _box():
    return SimpleNamespace(name=BOX)


def _connector(*conns: Conn):
    opened = iter(conns)

    async def connect():
        return next(opened)

    return connect


@pytest.mark.asyncio
async def test_a_connection_in_regular_use_is_retired_at_max_lifetime():
    """A connection released and re-acquired keeps the time it was opened, so
    the cleanup pass closes it once that is older than ``max_lifetime`` even
    though it was last used moments ago.

    Mutation killed: ``_place`` stamping ``created_at`` with the release time
    (the age after the second release reads 0 instead of 3000, and cleanup
    at 3700 s keeps the connection: closed False).
    """
    clock = Clock(OPENED_AT)
    pool = SSHConnectionPool(max_lifetime=MAX_LIFETIME, clock=clock)
    conn = Conn()
    connect = _connector(conn)

    first = await pool.acquire(_box(), connect)
    await pool.release(BOX, first)  # pooled at OPENED_AT
    clock.now = OPENED_AT + 3000
    again = await pool.acquire(_box(), connect)  # the same connection, reused
    await pool.release(BOX, again)  # released 3000 s after it was opened

    assert again is conn
    assert pool.stats()[BOX]["oldest_connection_age"] == 3000

    clock.now = OPENED_AT + 3700  # used 700 s ago, opened 3700 s ago
    await pool._cleanup_stale_connections()

    assert conn.closed is True
    assert pool.stats() == {}


@pytest.mark.asyncio
async def test_release_closes_a_connection_older_than_max_lifetime():
    """A connection past ``max_lifetime`` is closed when it is released, not
    pooled for the next caller; one inside the limit is pooled.

    Mutation killed: ``_may_keep`` without the lifetime check (the old
    connection is pooled: closed False, one pooled).
    """
    clock = Clock(OPENED_AT)
    pool = SSHConnectionPool(max_lifetime=MAX_LIFETIME, clock=clock)
    old, young = Conn(), Conn()
    connect = _connector(old, young)

    first = await pool.acquire(_box(), connect)
    clock.now = OPENED_AT + MAX_LIFETIME + 1
    await pool.release(BOX, first)
    second = await pool.acquire(_box(), connect)
    await pool.release(BOX, second)

    assert (first, second) == (old, young)
    assert old.closed is True
    assert young.closed is False
    assert pool.stats()[BOX]["active_connections"] == 1


@pytest.mark.asyncio
async def test_a_handoff_that_lands_with_the_timeout_is_returned_not_dropped():
    """A release hands a connection to a queued caller in the same loop turn
    that its wait times out. ``_wait`` returns that connection; returning None
    would leak it (it is neither pooled nor closed).

    Mutation killed: ``_wait`` ending in ``return None`` (the handed
    connection is lost: the result is None).
    """
    pool = SSHConnectionPool(clock=Clock(OPENED_AT))
    conn = Conn()
    waiting = asyncio.create_task(pool._wait(BOX, 0))  # timeout fires on the next turn
    await asyncio.sleep(0)  # the waiter queues; the timeout callback is scheduled
    assert len(pool._waiters[BOX]) == 1

    assert pool._place(BOX, conn, 0, OPENED_AT) is True  # wake-up now queued behind the timeout
    handed = await waiting

    assert handed is conn
    assert conn.closed is False


@pytest.mark.asyncio
async def test_an_invalidate_during_a_release_health_check_closes_instead_of_handing_off():
    """A release with a caller queued starts its health check; a box refresh
    (``invalidate``) lands during it. The connection is closed, not handed to
    the queued caller: it was opened with the old settings.

    Mutation killed: ``_place`` without its generation re-check (the queued
    caller is handed the stale connection: closed False).
    """
    pool = SSHConnectionPool(max_connections_per_box=1, wait_timeout=30.0)
    conn = Conn()
    connect_gate = asyncio.Event()

    async def connect():
        await connect_gate.wait()
        return conn

    first = asyncio.create_task(pool.acquire(_box(), connect))
    waiter = asyncio.create_task(pool.acquire(_box(), connect))
    while not (BOX in pool._connecting and len(pool._waiters.get(BOX, ())) == 1):
        await asyncio.sleep(0)
    connect_gate.set()
    held = await first
    # The finished connect woke the waiter; it queues again for a release.
    while len(pool._waiters.get(BOX, ())) != 1:
        await asyncio.sleep(0)

    conn.gate.clear()
    conn.checking.clear()
    releasing = asyncio.create_task(pool.release(BOX, held))
    await conn.checking.wait()
    await pool.invalidate(BOX)
    conn.gate.set()
    await releasing

    try:
        assert conn.closed is True
        assert waiter.done() is False
    finally:
        waiter.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiter


@pytest.mark.asyncio
async def test_a_cancelled_waiter_does_not_re_pool_an_expired_connection():
    """A release hands a connection to a queued caller; the caller is cancelled
    after the connection has passed ``max_lifetime``. The hand-back closes it
    instead of pooling it.

    Mutation killed: ``_place`` without the ``max_lifetime`` check (the
    expired connection goes back into the pool: closed False, one pooled).
    """
    clock = Clock(OPENED_AT)
    pool = SSHConnectionPool(
        max_connections_per_box=1, max_lifetime=MAX_LIFETIME, wait_timeout=30.0, clock=clock
    )
    conn = Conn()
    connect_gate = asyncio.Event()

    async def connect():
        await connect_gate.wait()
        return conn

    first = asyncio.create_task(pool.acquire(_box(), connect))
    waiter = asyncio.create_task(pool.acquire(_box(), connect))
    while not (BOX in pool._connecting and len(pool._waiters.get(BOX, ())) == 1):
        await asyncio.sleep(0)
    connect_gate.set()
    held = await first
    while len(pool._waiters.get(BOX, ())) != 1:
        await asyncio.sleep(0)

    await pool.release(BOX, held)  # young: handed to the queued caller
    clock.now = OPENED_AT + MAX_LIFETIME + 1
    waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter

    assert conn.closed is True
    assert pool.stats()[BOX]["active_connections"] == 0
