"""SSH Connection Pool Manager

Manages a pool of SSH connections per box to avoid creating new connections
for every request. Dramatically improves performance for file operations.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING

import asyncssh

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from .config import Box

# Upper bound on one connect. ``ssh.connect`` caps the handshake at 10 s; this
# also covers the ``ssh -G`` alias expansion before it. Kept below the 15 s
# probe timeout (``api.dependencies.PROBE_TIMEOUT_SECONDS``) so a hung box is
# put in the failure cache by the pool before a probe gives up on it.
CONNECT_TIMEOUT_SECONDS = 12.0

# How long a caller queued for a connection waits for one to be released
# before it opens its own, counted from when it queued or from the box's last
# successful connect, whichever is later.
RELEASE_WAIT_SECONDS = 2.0


@dataclass
class PooledConnection:
    """Wrapper around an SSH connection with metadata."""

    connection: asyncssh.SSHClientConnection
    box_name: str
    created_at: float
    last_used_at: float
    use_count: int = 0
    is_healthy: bool = True
    # The box's ``invalidate`` count when the connection was opened.
    generation: int = 0


class SSHConnectionPool:
    """Pool of SSH connections per box with automatic cleanup and health checks.

    Every operation does its bookkeeping without awaiting, so there is no
    per-box lock for a slow connect or health check to hold. Bounds per box:
    at most ``max_connections_per_box`` idle connections and at most
    ``max_connections_per_box`` connects in flight; checked-out connections
    are one per running operation.

    A caller that finds no idle connection connects if a connect slot is
    free and nobody is queued; otherwise it queues. A release hands its
    healthy connection straight to the first queued caller, so a burst of
    callers behind slow connects is served by the connections those connects
    open. A queued caller opens its own connection only after waiting
    ``wait_timeout`` (from when it queued, or from the box's last successful
    connect), and every wait is bounded.
    """

    def __init__(
        self,
        max_connections_per_box: int = 3,
        idle_timeout: int | None = 1800,  # 30 minutes (None = forever)
        max_lifetime: int | None = 3600,  # 1 hour (None = forever)
        connect_timeout: float = CONNECT_TIMEOUT_SECONDS,
        wait_timeout: float = RELEASE_WAIT_SECONDS,
        clock: Callable[[], float] = time.time,
    ):
        """Initialize the connection pool.

        Args:
            max_connections_per_box: Maximum number of connections to maintain per box
            idle_timeout: Seconds before an idle connection is closed (None = never timeout)
            max_lifetime: Maximum lifetime of a connection in seconds (None = never expire)
            connect_timeout: Seconds before a connect is abandoned and the box
                put in the failure cache
            wait_timeout: Seconds a queued caller waits for a released
                connection before it opens its own
            clock: Wall-clock seconds; replaced in tests to age connections
        """
        self._clock = clock
        self._pools: dict[str, list[PooledConnection]] = {}
        self._connect_timeout = connect_timeout
        self._wait_timeout = wait_timeout
        # Connects in flight per box.
        self._connecting: dict[str, int] = {}
        # Queued callers per box, oldest first. A future resolves to a
        # released connection handed to that caller, or to None to make it
        # look again (a connect finished).
        self._waiters: dict[str, deque[asyncio.Future[asyncssh.SSHClientConnection | None]]] = {}
        # Successful connects and health checks per box. A failed connect
        # marks the box down only if nothing succeeded while it ran.
        self._successes: dict[str, int] = {}
        # ``invalidate`` count per box; checked-out connections by id() with
        # the count they were opened under and the time they were opened, so
        # a connection keeps its age across releases.
        self._generations: dict[str, int] = {}
        self._issued: dict[int, tuple[int, float]] = {}
        self._max_connections_per_box = max_connections_per_box
        self._idle_timeout = idle_timeout
        self._max_lifetime = max_lifetime
        self._cleanup_task: asyncio.Task | None = None
        self._fail_cache: dict[str, float] = {}  # box_name -> timestamp of last failure
        self._fail_cooldown = 60  # seconds to skip retries after a connection failure

    async def start_cleanup_task(self):
        """Start background task to cleanup old connections."""
        if self._cleanup_task is None or self._cleanup_task.done():
            self._cleanup_task = asyncio.create_task(self._cleanup_loop())

    async def _cleanup_loop(self):
        """Background task to periodically clean up stale connections."""
        while True:
            try:
                await asyncio.sleep(60)  # Check every minute
                await self._cleanup_stale_connections()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                # Log error but continue cleanup loop to ensure pool maintenance continues
                logger.error(f"Error during connection pool cleanup: {exc}", exc_info=True)

    async def _cleanup_stale_connections(self):
        """Remove connections that are idle or past their lifetime."""
        now = self._clock()

        for box_name, pool in list(self._pools.items()):
            # Filter out stale connections
            healthy_connections = []
            for conn in pool:
                # Check if connection is stale
                is_idle_too_long = (
                    self._idle_timeout is not None
                    and (now - conn.last_used_at) > self._idle_timeout
                )
                is_too_old = (
                    self._max_lifetime is not None
                    and (now - conn.created_at) > self._max_lifetime
                )

                if is_idle_too_long or is_too_old or not conn.is_healthy:
                    _close_quietly(conn.connection, box_name, "stale")
                else:
                    healthy_connections.append(conn)

            if healthy_connections:
                self._pools[box_name] = healthy_connections
            else:
                # Remove empty pool
                del self._pools[box_name]

    async def acquire(
        self,
        box: Box,
        connect_func: Callable[[], Awaitable[asyncssh.SSHClientConnection]],
    ) -> asyncssh.SSHClientConnection:
        """Acquire a connection from the pool or create a new one.

        No lock is held across an await: a slow health check or a hung
        connect for a box never makes other callers for that box wait. At
        most ``max_connections_per_box`` connects run at once per box; a
        caller beyond that waits for one of them to finish, then takes an
        idle connection or connects itself. A connect is bounded by the
        pool's connect timeout, and a failed or timed-out connect puts the
        box in the failure cache, so later callers fail fast instead of
        connecting again. A caller cancelled during its connect does not mark
        the box failed (a closed tab says nothing about the box). An idle
        healthy connection is handed out even while the box is in the
        failure cache.

        Args:
            box: Box configuration to connect to
            connect_func: Async function to create new connection

        Returns:
            SSH connection from pool or newly created

        Raises:
            ConnectionError: The box failed recently, or the connect timed out.
            Exception: Whatever ``connect_func`` raised.
        """
        box_name = box.name
        loop = asyncio.get_running_loop()
        deadline: float | None = None  # set once this caller has queued
        while True:
            connection = await self._take_idle(box_name)
            if connection is not None:
                return connection
            self._raise_if_recently_failed(box_name)
            slot_free = self._connecting.get(box_name, 0) < self._max_connections_per_box
            if deadline is None:
                may_connect = slot_free and not self._waiters.get(box_name)
            else:
                may_connect = slot_free and loop.time() >= deadline
            if may_connect:
                return await self._connect(box_name, connect_func)
            if deadline is None:
                deadline = loop.time() + self._wait_timeout
            successes = self._successes.get(box_name, 0)
            remaining = deadline - loop.time()
            # Past the deadline with every slot busy: wait for a connect to
            # finish, which takes at most the connect timeout.
            timeout = remaining if remaining > 0 else self._connect_timeout
            connection = await self._wait(box_name, timeout)
            if connection is not None:
                return connection
            if self._successes.get(box_name, 0) != successes:
                # A connect just succeeded: its connection is likely to come
                # back soon, so wait for it rather than open another.
                deadline = loop.time() + self._wait_timeout

    async def _wait(self, box_name: str, timeout: float) -> asyncssh.SSHClientConnection | None:
        """Queue for a released connection; None on a wake-up or a timeout."""
        future: asyncio.Future[asyncssh.SSHClientConnection | None] = (
            asyncio.get_running_loop().create_future()
        )
        waiters = self._waiters.setdefault(box_name, deque())
        waiters.append(future)
        try:
            async with asyncio.timeout(timeout):
                await future
        except TimeoutError:
            pass
        except BaseException:
            # Cancelled after a release handed this caller a connection:
            # pass it on so it is not lost.
            handed = future.result() if future.done() and not future.cancelled() else None
            if handed is not None:
                generation, created_at = self._issued.pop(id(handed), (-1, self._clock()))
                if not self._place(box_name, handed, generation, created_at):
                    _close_quietly(handed, box_name, "cancelled waiter")
            raise
        finally:
            if future in waiters:
                waiters.remove(future)
        # A handoff that raced the timeout still counts.
        if future.done() and not future.cancelled():
            return future.result()
        return None

    def _wake_all(self, box_name: str) -> None:
        """Make every queued caller look again (a connect finished)."""
        waiters = self._waiters.get(box_name)
        while waiters:
            future = waiters.popleft()
            if not future.done():
                future.set_result(None)

    def _place(
        self,
        box_name: str,
        connection: asyncssh.SSHClientConnection,
        generation: int,
        created_at: float,
    ) -> bool:
        """Hand a healthy connection to the first queued caller, or pool it.

        False when it cannot be kept (stale settings, older than
        ``max_lifetime``, or nobody queued and the idle pool full); the
        caller then closes it.
        """
        if generation != self._generations.get(box_name, 0):
            return False
        if self._max_lifetime is not None and self._clock() - created_at > self._max_lifetime:
            return False
        waiters = self._waiters.get(box_name)
        while waiters:
            future = waiters.popleft()
            if not future.done():
                self._issued[id(connection)] = (generation, created_at)
                future.set_result(connection)
                return True
        pool = self._pools.setdefault(box_name, [])
        if len(pool) >= self._max_connections_per_box:
            return False
        now = self._clock()
        pool.append(
            PooledConnection(
                connection=connection,
                box_name=box_name,
                created_at=created_at,
                last_used_at=now,
                use_count=1,
                is_healthy=True,
                generation=generation,
            )
        )
        return True

    def _raise_if_recently_failed(self, box_name: str) -> None:
        last_fail = self._fail_cache.get(box_name)
        if last_fail and (self._clock() - last_fail) < self._fail_cooldown:
            retry_in = int(self._fail_cooldown - (self._clock() - last_fail))
            raise ConnectionError(f"{box_name}: unreachable (cached, retry in {retry_in}s)")

    async def _take_idle(self, box_name: str) -> asyncssh.SSHClientConnection | None:
        """Pop idle connections until one passes the health check."""
        pool = self._pools.setdefault(box_name, [])
        while pool:
            pooled_conn = pool.pop(0)
            # The connection has already left the pool, so a cancel during
            # the check must close it or nothing ever will.
            try:
                healthy = await self._is_connection_healthy(pooled_conn.connection)
            except BaseException:
                _close_quietly(pooled_conn.connection, box_name, "cancelled health check")
                raise
            if healthy:
                pooled_conn.last_used_at = self._clock()
                pooled_conn.use_count += 1
                # Clear any previous failure since we have a working connection
                self._fail_cache.pop(box_name, None)
                self._successes[box_name] = self._successes.get(box_name, 0) + 1
                self._issued[id(pooled_conn.connection)] = (
                    pooled_conn.generation,
                    pooled_conn.created_at,
                )
                return pooled_conn.connection
            _close_quietly(pooled_conn.connection, box_name, "dead connection")
            # An invalidate during the check replaces the list.
            pool = self._pools.setdefault(box_name, [])
        return None

    async def _connect(
        self,
        box_name: str,
        connect_func: Callable[[], Awaitable[asyncssh.SSHClientConnection]],
    ) -> asyncssh.SSHClientConnection:
        # Both read before the connect starts: it runs with this
        # generation's settings, and only a success after this point
        # contradicts its failure.
        generation = self._generations.get(box_name, 0)
        successes = self._successes.get(box_name, 0)
        self._connecting[box_name] = self._connecting.get(box_name, 0) + 1
        deadline = asyncio.timeout(self._connect_timeout)
        try:
            try:
                async with deadline:
                    connection = await connect_func()
            except Exception:
                # A failure with settings an invalidate has since replaced,
                # or one another caller's success contradicts, says nothing
                # about the box as it is now. (A cancel is not an Exception
                # and never marks the box.)
                if generation == self._generations.get(
                    box_name, 0
                ) and successes == self._successes.get(box_name, 0):
                    self._fail_cache[box_name] = self._clock()
                if deadline.expired():
                    raise ConnectionError(
                        f"connect to {box_name} timed out after {self._connect_timeout:g} s"
                    ) from None
                raise
            # Connection succeeded — clear failure cache
            self._successes[box_name] = self._successes.get(box_name, 0) + 1
            self._fail_cache.pop(box_name, None)
            # Don't add to pool yet - will be added on release
            self._issued[id(connection)] = (generation, self._clock())
            return connection
        finally:
            self._connecting[box_name] -= 1
            self._wake_all(box_name)

    async def release(
        self,
        box_name: str,
        connection: asyncssh.SSHClientConnection,
    ):
        """Release a connection back to the pool.

        The connection ends up handed to a queued caller, pooled or closed
        on every path, including a cancel during the health check. A
        connection checked out before an ``invalidate`` is closed, not
        pooled: it was opened with the old settings.

        Args:
            box_name: Name of the box this connection belongs to
            connection: Connection to release back to pool
        """
        generation, created_at = self._issued.pop(
            id(connection), (self._generations.get(box_name, 0), self._clock())
        )
        self._pools.setdefault(box_name, [])
        try:
            healthy = (
                self._may_keep(box_name, generation, created_at)
                and await self._is_connection_healthy(connection)
            )
        except BaseException:
            _close_quietly(connection, box_name, "cancelled release")
            raise
        # ``_place`` re-checks after the await: an invalidate or other
        # releases may have happened during the health check.
        if not (healthy and self._place(box_name, connection, generation, created_at)):
            _close_quietly(connection, box_name, "not pooled on release")

    def _may_keep(self, box_name: str, generation: int, created_at: float) -> bool:
        """Current settings, within ``max_lifetime``, and a queued caller or room."""
        if self._max_lifetime is not None and self._clock() - created_at > self._max_lifetime:
            return False
        return generation == self._generations.get(box_name, 0) and (
            bool(self._waiters.get(box_name))
            or len(self._pools.get(box_name, [])) < self._max_connections_per_box
        )

    def discard(self, box_name: str, connection: asyncssh.SSHClientConnection) -> None:
        """Close a checked-out connection instead of returning it (after a
        timeout or a cancel, when a command may still run on it)."""
        self._issued.pop(id(connection), None)
        _close_quietly(connection, box_name, "discarded")

    async def _is_connection_healthy(
        self,
        connection: asyncssh.SSHClientConnection,
    ) -> bool:
        """Check if a connection is still healthy.

        Args:
            connection: Connection to check

        Returns:
            True if connection is healthy, False otherwise
        """
        try:
            # Try to run a simple command to check if connection is alive
            result = await asyncio.wait_for(
                connection.run("echo test", check=False),
                timeout=2.0,
            )
            return result.exit_status == 0
        except (TimeoutError, Exception):
            return False

    async def invalidate(self, box_name: str):
        """Close all connections for a box (e.g., after config change).

        Also clears the failure cache so the next attempt will try to connect.

        Args:
            box_name: Name of the box to invalidate connections for
        """
        self._fail_cache.pop(box_name, None)
        # Connections checked out now were opened with the old settings:
        # ``release`` closes them instead of pooling them.
        self._generations[box_name] = self._generations.get(box_name, 0) + 1
        for conn in self._pools.pop(box_name, []):
            _close_quietly(conn.connection, box_name, "invalidated")

    async def close_all(self):
        """Close all connections in all pools."""
        for box_name in list(self._pools.keys()):
            await self.invalidate(box_name)

        if self._cleanup_task and not self._cleanup_task.done():
            self._cleanup_task.cancel()
            try:
                await self._cleanup_task
            except asyncio.CancelledError:
                pass

    @asynccontextmanager
    async def connection(
        self,
        box: Box,
        connect_func,
    ) -> AsyncIterator[asyncssh.SSHClientConnection]:
        """Context manager for automatic acquire/release.

        Args:
            box: Box configuration to connect to
            connect_func: Async function to create new connection

        Yields:
            SSH connection from pool or newly created

        Example:
            async with ssh_pool.connection(box, connect_func) as conn:
                # Use connection
                ...
        """
        connection = await self.acquire(box, connect_func)
        try:
            yield connection
        except BaseException as exc:
            # A timeout or a cancel can leave a command running on the
            # connection (its channel stays open on the remote), so close it
            # instead of handing it to the next caller. Ordinary errors (a
            # missing file, a bad path) say nothing about the connection.
            if isinstance(exc, TimeoutError) or not isinstance(exc, Exception):
                self.discard(box.name, connection)
            else:
                await self.release(box.name, connection)
            raise
        else:
            await self.release(box.name, connection)

    def stats(self) -> dict[str, dict]:
        """Get statistics about the connection pool.

        Returns:
            Dictionary mapping box names to pool statistics
        """
        stats = {}
        for box_name, pool in self._pools.items():
            stats[box_name] = {
                "active_connections": len(pool),
                "total_uses": sum(conn.use_count for conn in pool),
                "oldest_connection_age": (
                    int(self._clock() - min(conn.created_at for conn in pool))
                    if pool
                    else 0
                ),
            }
        return stats

    def get_config(self) -> dict[str, int | None]:
        """Get current pool configuration.

        Returns:
            Dictionary with idle_timeout, max_lifetime, and max_connections_per_box
        """
        return {
            "idle_timeout": self._idle_timeout,
            "max_lifetime": self._max_lifetime,
            "max_connections_per_box": self._max_connections_per_box,
        }

    def update_config(
        self,
        idle_timeout: int | None = None,
        max_lifetime: int | None = None,
        max_connections_per_box: int | None = None,
    ) -> None:
        """Update pool configuration dynamically.

        Args:
            idle_timeout: New idle timeout (None = keep current)
            max_lifetime: New max lifetime (None = keep current)
            max_connections_per_box: New max connections (None = keep current)
        """
        if idle_timeout is not None:
            self._idle_timeout = idle_timeout
        if max_lifetime is not None:
            self._max_lifetime = max_lifetime
        if max_connections_per_box is not None:
            self._max_connections_per_box = max_connections_per_box


def _close_quietly(connection: asyncssh.SSHClientConnection, box_name: str, reason: str) -> None:
    """Close a connection that must not go back to the pool."""
    try:
        connection.close()
    except Exception as exc:
        logger.debug(f"Error closing connection ({reason}) for {box_name}: {exc}")


# Global connection pool instance
_global_pool: SSHConnectionPool | None = None


def get_pool() -> SSHConnectionPool:
    """Get the global SSH connection pool instance."""
    global _global_pool
    if _global_pool is None:
        import os

        # Read configuration from environment variables
        idle_timeout_str = os.getenv("SSHLER_POOL_IDLE_TIMEOUT", "1800")
        max_lifetime_str = os.getenv("SSHLER_POOL_MAX_LIFETIME", "3600")
        max_connections = int(os.getenv("SSHLER_POOL_MAX_CONNECTIONS", "3"))

        # Parse timeout values: "forever" or "none" -> None, otherwise int
        idle_timeout: int | None = None
        if idle_timeout_str.lower() not in ("forever", "none"):
            idle_timeout = int(idle_timeout_str)

        max_lifetime: int | None = None
        if max_lifetime_str.lower() not in ("forever", "none"):
            max_lifetime = int(max_lifetime_str)

        _global_pool = SSHConnectionPool(
            max_connections_per_box=max_connections,
            idle_timeout=idle_timeout,
            max_lifetime=max_lifetime,
        )
    return _global_pool


async def initialize_pool():
    """Initialize the global connection pool and start cleanup task."""
    pool = get_pool()
    await pool.start_cleanup_task()


async def shutdown_pool():
    """Shutdown the global connection pool."""
    global _global_pool
    if _global_pool:
        await _global_pool.close_all()
        _global_pool = None
