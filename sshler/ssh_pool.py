"""SSH Connection Pool Manager

Manages a pool of SSH connections per box to avoid creating new connections
for every request. Dramatically improves performance for file operations.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING

import asyncssh

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from .config import Box


@dataclass
class PooledConnection:
    """Wrapper around an SSH connection with metadata."""

    connection: asyncssh.SSHClientConnection
    box_name: str
    created_at: float
    last_used_at: float
    use_count: int = 0
    is_healthy: bool = True


class SSHConnectionPool:
    """Pool of SSH connections per box with automatic cleanup and health checks."""

    def __init__(
        self,
        max_connections_per_box: int = 3,
        idle_timeout: int | None = 1800,  # 30 minutes (None = forever)
        max_lifetime: int | None = 3600,  # 1 hour (None = forever)
    ):
        """Initialize the connection pool.

        Args:
            max_connections_per_box: Maximum number of connections to maintain per box
            idle_timeout: Seconds before an idle connection is closed (None = never timeout)
            max_lifetime: Maximum lifetime of a connection in seconds (None = never expire)
        """
        self._pools: dict[str, list[PooledConnection]] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._max_connections_per_box = max_connections_per_box
        self._idle_timeout = idle_timeout
        self._max_lifetime = max_lifetime
        self._cleanup_task: asyncio.Task | None = None

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
        now = time.time()

        for box_name, pool in list(self._pools.items()):
            if box_name not in self._locks:
                self._locks[box_name] = asyncio.Lock()

            async with self._locks[box_name]:
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
                        # Close stale connection
                        try:
                            conn.connection.close()
                        except Exception as exc:
                            logger.debug(f"Error closing stale connection for {box_name}: {exc}")
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

        Args:
            box: Box configuration to connect to
            connect_func: Async function to create new connection

        Returns:
            SSH connection from pool or newly created

        Raises:
            Exception: If connection fails and no pooled connections available
        """
        box_name = box.name

        # Ensure lock exists for this box
        if box_name not in self._locks:
            self._locks[box_name] = asyncio.Lock()

        async with self._locks[box_name]:
            # Get or create pool for this box
            if box_name not in self._pools:
                self._pools[box_name] = []

            pool = self._pools[box_name]

            # Try to find a healthy connection from pool
            while pool:
                pooled_conn = pool.pop(0)

                # Check if connection is still healthy
                if await self._is_connection_healthy(pooled_conn.connection):
                    pooled_conn.last_used_at = time.time()
                    pooled_conn.use_count += 1
                    return pooled_conn.connection
                else:
                    # Connection is dead, close it
                    try:
                        pooled_conn.connection.close()
                    except Exception as exc:
                        logger.debug(f"Error closing dead connection for {box_name}: {exc}")

            # No healthy connection in pool, create a new one
            connection = await connect_func()

            # Don't add to pool yet - will be added on release
            return connection

    async def release(
        self,
        box_name: str,
        connection: asyncssh.SSHClientConnection,
    ):
        """Release a connection back to the pool.

        Args:
            box_name: Name of the box this connection belongs to
            connection: Connection to release back to pool
        """
        if box_name not in self._locks:
            self._locks[box_name] = asyncio.Lock()

        async with self._locks[box_name]:
            # Get or create pool for this box
            if box_name not in self._pools:
                self._pools[box_name] = []

            pool = self._pools[box_name]

            # Check if pool is full
            if len(pool) >= self._max_connections_per_box:
                # Pool is full, close this connection
                try:
                    connection.close()
                except Exception as exc:
                    logger.debug(f"Error closing connection (pool full) for {box_name}: {exc}")
                return

            # Check if connection is healthy before returning to pool
            if await self._is_connection_healthy(connection):
                # Add connection to pool
                pooled_conn = PooledConnection(
                    connection=connection,
                    box_name=box_name,
                    created_at=time.time(),
                    last_used_at=time.time(),
                    use_count=1,
                    is_healthy=True,
                )
                pool.append(pooled_conn)
            else:
                # Connection is not healthy, close it
                try:
                    connection.close()
                except Exception as exc:
                    logger.debug(f"Error closing unhealthy connection for {box_name}: {exc}")

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
        except (asyncio.TimeoutError, Exception):
            return False

    async def invalidate(self, box_name: str):
        """Close all connections for a box (e.g., after config change).

        Args:
            box_name: Name of the box to invalidate connections for
        """
        if box_name not in self._locks:
            return

        async with self._locks[box_name]:
            if box_name in self._pools:
                pool = self._pools[box_name]
                for conn in pool:
                    try:
                        conn.connection.close()
                    except Exception as exc:
                        logger.debug(f"Error closing connection during invalidate for {box_name}: {exc}")
                del self._pools[box_name]

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
        finally:
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
                    int(time.time() - min(conn.created_at for conn in pool))
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
