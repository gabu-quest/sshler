"""Per-app registry that keeps native Windows ConPTY shells alive across
websocket disconnects (session persistence for the local box on Windows).

Unlike the SSH / local-tmux paths — where a tmux server outlives the websocket
and the client simply re-attaches — a native ConPTY child (``WinPTYProcess``)
has nothing keeping it alive. Before this module, the ``/ws/term`` handler
terminated the shell on every disconnect, losing the cwd, history, env and any
running process. The registry fixes that by owning the process lifecycle
independently of any single websocket:

* One :class:`TerminalSession` per ``(box, session)`` key holds the live process,
  a **byte-bounded ring buffer** of recent output, and the set of currently
  attached browser tabs (*sinks*).
* A per-session **drain task** continuously reads the ConPTY's output into the
  ring buffer *regardless of whether a tab is attached*, so output produced while
  disconnected is captured. Each chunk is also fanned out to every attached sink.
* **Attach** adds a sink and returns a clear-screen sequence followed by the ring
  snapshot, so a (re)attaching tab's view becomes exactly the buffer — no
  duplicated tail on a transient reconnect, full context on a fresh tab.
* **Detach** (on websocket close) just removes the sink; the process keeps running.
* The shell is only torn down when it **exits on its own**, when it is explicitly
  **killed** (e.g. SessionSwitcher delete), on **idle TTL** (disabled by default —
  persist until restart), or on **app shutdown**.

Mirroring (tmux-style): multiple tabs may attach to the same key simultaneously;
they all see the same output and all write to the same stdin.

This module touches only the ``WinPTYProcess`` surface (``stdin.write(bytes)->int``,
``stdout.read(size)->str`` which blocks until data/EOF, ``resize``, ``terminate``
which forces EOF, ``close``, ``returncode``), so it is exercised cross-platform in
tests with a blocking fake — no real pywinpty required.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Awaitable, Callable

logger = logging.getLogger(__name__)

# --- configuration (env-overridable, read once) ---------------------------
RING_BYTES = int(os.getenv("SSHLER_WIN_TERM_RING_BYTES", str(768 * 1024)))
TTL_SECONDS = float(os.getenv("SSHLER_WIN_TERM_TTL", "0"))  # 0 = never idle-reap
REAP_INTERVAL = float(os.getenv("SSHLER_WIN_TERM_REAP_INTERVAL", "60"))
# Hard cap on concurrently-live native shells. A single user never needs dozens;
# the cap stops a runaway client (or a buggy/abusive script opening tabs in a
# loop) from spawning unbounded ConPTY children and exhausting the host. 0 = off.
MAX_SESSIONS = int(os.getenv("SSHLER_WIN_TERM_MAX_SESSIONS", "50"))
READ_SIZE = 32768  # matches the legacy reader loop
# Clear screen + clear scrollback + cursor home, so a (re)attaching tab shows
# exactly the replayed ring buffer with no duplicated output above it.
CLEAR_SEQ = b"\x1b[2J\x1b[3J\x1b[H"

Key = tuple[str, str]
Sink = Callable[[bytes], Awaitable[None]]
SpawnFn = Callable[[], Awaitable[Any]]
ReapedCallback = Callable[[Key, "str | None"], Awaitable[None]]


class TooManyTerminalsError(RuntimeError):
    """Raised when spawning a new shell would exceed the per-app session cap."""


class TerminalSession:
    """One live ConPTY shell plus its output ring buffer and attached tabs."""

    def __init__(self, key: Key, process: Any, cols: int, rows: int, ring_bytes: int) -> None:
        self.key = key
        self.process = process
        self.session_id: str | None = None
        self.cols = cols
        self.rows = rows
        self.sinks: set[Sink] = set()
        self.exited = False
        self.exited_event = asyncio.Event()
        self.detached_since: float | None = None  # monotonic; set when no tabs attached
        self.drain_task: asyncio.Task[None] | None = None
        self._ring: deque[bytes] = deque()
        self._ring_size = 0
        self._ring_bytes = ring_bytes
        self._lock = asyncio.Lock()

    def _append(self, chunk: bytes) -> None:
        """Append a chunk and trim oldest bytes past the ring cap.

        Keeps at least one chunk even if a single chunk exceeds the cap, so a
        lone huge write is never silently dropped.
        """
        self._ring.append(chunk)
        self._ring_size += len(chunk)
        while self._ring_size > self._ring_bytes and len(self._ring) > 1:
            self._ring_size -= len(self._ring.popleft())

    def snapshot(self) -> bytes:
        return b"".join(self._ring)


class WindowsTerminalRegistry:
    """Per-app singleton owning every persisted native Windows shell."""

    def __init__(
        self,
        *,
        ring_bytes: int = RING_BYTES,
        ttl_seconds: float = TTL_SECONDS,
        reap_interval: float = REAP_INTERVAL,
        max_sessions: int = MAX_SESSIONS,
    ) -> None:
        self._sessions: dict[Key, TerminalSession] = {}
        self._lock = asyncio.Lock()
        self._ring_bytes = ring_bytes
        self._ttl = ttl_seconds
        self._reap_interval = reap_interval
        self._max_sessions = max_sessions
        self._reaper_task: asyncio.Task[None] | None = None
        self._on_reaped: ReapedCallback | None = None
        # Blocking pywinpty I/O must NOT share asyncio's default to_thread pool.
        # Each live shell's drain task blocks one worker FOREVER in stdout.read
        # (an idle ConPTY never returns), so enough persisted shells would
        # saturate that shared pool and starve every other to_thread caller
        # app-wide — every SQLite write in state.py, every keystroke stdin.write,
        # every file op — freezing the whole server. Even teardown would deadlock,
        # since terminate() also needs a worker; that is why the user's kill and
        # page-refresh did nothing once the pool was full. Fix: reads get their
        # OWN pool with one permanent slot per allowed shell; transient
        # writes/terminates get a small SEPARATE pool, so a full read pool can
        # never block a keystroke or a kill.
        read_slots = (max_sessions + 4) if max_sessions > 0 else 256
        self._read_executor = ThreadPoolExecutor(
            max_workers=read_slots, thread_name_prefix="winpty-read"
        )
        self._io_executor = ThreadPoolExecutor(
            max_workers=16, thread_name_prefix="winpty-io"
        )
        if max_sessions <= 0:
            logger.warning(
                "[WinRegistry] session cap disabled (max_sessions=%d); unbounded "
                "ConPTY spawning permitted. Set SSHLER_WIN_TERM_MAX_SESSIONS > 0 to re-enable.",
                max_sessions,
            )

    # --- introspection -----------------------------------------------------
    def get(self, key: Key) -> TerminalSession | None:
        return self._sessions.get(key)

    def live_keys(self) -> list[Key]:
        return list(self._sessions.keys())

    def _live_count(self, exclude: Key | None = None) -> int:
        """Count non-exited sessions, optionally excluding one key.

        Exited-but-not-yet-reaped shells don't count toward the cap, and a key
        we're about to replace (its previous shell already exited) is excluded so
        re-attaching never trips the limit. Caller must hold ``self._lock``.
        """
        return sum(
            1 for k, s in self._sessions.items() if not s.exited and k != exclude
        )

    # --- lifecycle ---------------------------------------------------------
    async def get_or_create(
        self, key: Key, spawn: SpawnFn, cols: int, rows: int
    ) -> tuple[TerminalSession, bool]:
        """Return the live session for *key*, spawning a new shell if needed.

        Returns ``(session, created)``. *spawn* is an async factory returning a
        ``WinPTYProcess``; it runs OUTSIDE the registry lock (it blocks), and any
        exception it raises propagates without registering anything.

        Raises :class:`TooManyTerminalsError` when creating a *new* shell would
        push the live-session count past ``max_sessions``. Re-attaching to an
        existing live shell is always allowed, even at the cap.
        """
        async with self._lock:
            existing = self._sessions.get(key)
            if existing is not None and not existing.exited:
                return existing, False
            # Reject before paying for a spawn we'd only have to tear down.
            if self._max_sessions > 0 and self._live_count(exclude=key) >= self._max_sessions:
                raise TooManyTerminalsError(
                    f"terminal session limit reached ({self._max_sessions})"
                )

        # Spawn outside the lock; this can block / raise (e.g. WSL missing).
        process = await spawn()

        async with self._lock:
            existing = self._sessions.get(key)
            over_cap = (
                self._max_sessions > 0
                and (existing is None or existing.exited)
                and self._live_count(exclude=key) >= self._max_sessions
            )
            if not over_cap and (existing is None or existing.exited):
                session = TerminalSession(key, process, cols, rows, self._ring_bytes)
                session.drain_task = asyncio.create_task(self._drain(session))
                self._sessions[key] = session
                logger.info("[WinRegistry] spawned shell for %s", key)
                return session, True
            # Either we lost a concurrent race (existing won), or the last free
            # slot was taken while we were spawning. Tear down our redundant proc.
            winner = None if over_cap else existing

        with contextlib.suppress(Exception):
            await asyncio.get_running_loop().run_in_executor(
                self._io_executor, self._terminate_proc, process
            )
        if winner is None:
            raise TooManyTerminalsError(
                f"terminal session limit reached ({self._max_sessions})"
            )
        return winner, False

    async def attach(self, session: TerminalSession, sink: Sink, cols: int, rows: int) -> bytes:
        """Register *sink* and return the replay bytes (clear + ring snapshot).

        Snapshot + sink-registration happen atomically under the session lock,
        and the drain's append + sink-copy use the same lock, so every byte
        reaches the new tab exactly once: either via this snapshot OR live — never
        both, never neither.
        """
        async with session._lock:
            snap = session.snapshot()
            session.sinks.add(sink)
            session.detached_since = None
            if (cols, rows) != (session.cols, session.rows):
                session.cols, session.rows = cols, rows
                with contextlib.suppress(Exception):
                    session.process.resize(cols, rows)
        return CLEAR_SEQ + snap

    async def detach(self, session: TerminalSession, sink: Sink) -> None:
        """Remove *sink*; if no tabs remain, mark the session detached."""
        async with session._lock:
            session.sinks.discard(sink)
            if not session.sinks:
                session.detached_since = time.monotonic()

    async def write(self, session: TerminalSession, data: bytes) -> None:
        """Forward keystrokes to the shell's stdin (mirrored across all tabs)."""
        await asyncio.get_running_loop().run_in_executor(
            self._io_executor, session.process.stdin.write, data
        )

    async def kill(self, key: Key) -> bool:
        """Forcibly terminate and remove the session for *key* (delete action)."""
        session = self._sessions.get(key)
        if session is None:
            return False
        await self._remove(key, session)
        return True

    async def reap_once(self, now: float | None = None) -> list[Key]:
        """Remove exited sessions (and idle-detached ones if TTL > 0)."""
        now = now if now is not None else time.monotonic()
        async with self._lock:
            items = list(self._sessions.items())
        reaped: list[Key] = []
        for key, session in items:
            idle = (
                self._ttl > 0
                and session.detached_since is not None
                and (now - session.detached_since) >= self._ttl
            )
            if session.exited or idle:
                await self._remove(key, session)
                reaped.append(key)
        return reaped

    def start_reaper(self) -> None:
        if self._reaper_task is None or self._reaper_task.done():
            self._reaper_task = asyncio.create_task(self._reap_loop())

    async def shutdown(self) -> None:
        """Cancel the reaper and terminate every live shell (app shutdown)."""
        if self._reaper_task is not None:
            self._reaper_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._reaper_task
            self._reaper_task = None
        async with self._lock:
            items = list(self._sessions.items())
        for key, session in items:
            await self._remove(key, session)
        # Every shell is now terminated and its drain has returned, so the pools
        # hold only idle threads — safe to release. wait=False keeps app shutdown
        # from blocking on the worker threads winding down.
        self._read_executor.shutdown(wait=False)
        self._io_executor.shutdown(wait=False)

    # --- internals ---------------------------------------------------------
    async def _drain(self, session: TerminalSession) -> None:
        """Continuously read the ConPTY into the ring buffer + attached sinks."""
        proc = session.process
        try:
            loop = asyncio.get_running_loop()
            while True:
                data = await loop.run_in_executor(
                    self._read_executor, proc.stdout.read, READ_SIZE
                )
                if not data:
                    break  # EOF: shell exited or was terminated
                chunk = data.encode("utf-8") if isinstance(data, str) else data
                async with session._lock:
                    session._append(chunk)
                    targets = list(session.sinks)
                failed: list[Sink] = []
                for sink in targets:
                    try:
                        await sink(chunk)
                    except Exception:
                        failed.append(sink)
                if failed:
                    async with session._lock:
                        for sink in failed:
                            session.sinks.discard(sink)
        except Exception as exc:  # pragma: no cover - defensive
            logger.debug("[WinRegistry] drain error for %s: %s", session.key, exc)
        finally:
            async with session._lock:
                session.exited = True
                session.exited_event.set()

    async def _remove(self, key: Key, session: TerminalSession) -> None:
        """Terminate the process, await the drain, drop the entry, notify."""
        await asyncio.get_running_loop().run_in_executor(
            self._io_executor, self._terminate_proc, session.process
        )
        if session.drain_task is not None:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await session.drain_task
        async with self._lock:
            self._sessions.pop(key, None)
        if self._on_reaped is not None:
            with contextlib.suppress(Exception):
                await self._on_reaped(key, session.session_id)
        logger.info("[WinRegistry] removed shell for %s", key)

    @staticmethod
    def _terminate_proc(process: Any) -> None:
        with contextlib.suppress(Exception):
            process.terminate()
        with contextlib.suppress(Exception):
            process.close()

    async def _reap_loop(self) -> None:
        try:
            while True:
                await asyncio.sleep(self._reap_interval)
                with contextlib.suppress(Exception):
                    await self.reap_once()
        except asyncio.CancelledError:  # pragma: no cover - shutdown path
            pass
