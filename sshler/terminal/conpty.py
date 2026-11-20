"""A persisted native Windows shell, shared through the terminal registry.

The registry owns the shell: it reads the ConPTY, keeps a ring buffer and fans each
chunk out to every attached tab. This transport is one tab: it attaches a sink on
open and detaches it on close; the shell keeps running for the next tab.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import Any

from ..win_terminal_registry import TerminalSession, WindowsTerminalRegistry

logger = logging.getLogger(__name__)

# Chunks held for one tab before the registry's fan-out waits for it to read.
QUEUE_CHUNKS = 8


class ConPtyTransport:
    """One tab of a persisted shell: the replay first, then live output."""

    tmux = None

    def __init__(
        self, registry: WindowsTerminalRegistry, session: TerminalSession, replay: bytes
    ) -> None:
        self._registry = registry
        self._session = session
        self._replay: bytes | None = replay
        self._queue: asyncio.Queue[bytes] = asyncio.Queue(maxsize=QUEUE_CHUNKS)
        self._closed = False

    @classmethod
    async def attach(
        cls, registry: WindowsTerminalRegistry, session: TerminalSession, cols: int, rows: int
    ) -> ConPtyTransport:
        """Attach a new tab; its first read is the replay (clear screen and the ring)."""
        transport = cls(registry, session, b"")
        transport._replay = await registry.attach(session, transport._sink, cols, rows)
        return transport

    async def _sink(self, chunk: bytes) -> None:
        # A detached tab never holds the fan-out up: it raises, and the registry drops it.
        if self._closed:
            raise ConnectionError("tab closed")
        await self._queue.put(chunk)

    async def read(self) -> bytes:
        if self._replay is not None:
            replay, self._replay = self._replay, None
            return replay
        if not self._queue.empty():
            return self._queue.get_nowait()
        if self._closed or self._session.exited_event.is_set():
            return b""
        return await self._next_chunk()

    async def _next_chunk(self) -> bytes:
        chunk: asyncio.Task[bytes] = asyncio.create_task(self._queue.get(), name="terminal-tab")
        exited: asyncio.Task[Any] = asyncio.create_task(
            self._session.exited_event.wait(), name="terminal-tab"
        )
        try:
            await asyncio.wait({chunk, exited}, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for task in (chunk, exited):
                task.cancel()
            await asyncio.gather(chunk, exited, return_exceptions=True)
        if chunk.done() and not chunk.cancelled():
            return chunk.result()
        return self._queue.get_nowait() if not self._queue.empty() else b""

    async def write(self, data: bytes) -> None:
        # Mirrored input: every tab of this shell writes to one stdin.
        await self._registry.write(self._session, data)

    async def resize(self, cols: int, rows: int) -> None:
        try:
            self._session.process.resize(cols, rows)
            logger.info(f"[Resize] Local PTY resized to {cols}x{rows}")
        except Exception as exc:
            logger.warning(f"Failed to resize local PTY: {exc}")

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        # Free a fan-out waiting on a full queue; the shell itself keeps running.
        while not self._queue.empty():
            self._queue.get_nowait()
        with contextlib.suppress(Exception):
            await self._registry.detach(self._session, self._sink)
