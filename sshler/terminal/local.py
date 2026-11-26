"""A tmux client on a local PTY."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import sys
from collections.abc import Coroutine, Iterable, Mapping
from typing import Any, Protocol

from ..tmux import WINDOW_LIST_FORMAT, _run_local_tmux_command, parse_window_list, run_local_tmux
from .transport import READ_SIZE, WindowList

logger = logging.getLogger(__name__)

# The shell of last resort, as for tmux.
FALLBACK_SHELL = "/bin/sh"

# How long closing waits for an in-flight read or write and for the child to be
# reaped. A child that ignores the hangup is killed well within it.
CLOSE_TIMEOUT = 10.0


def _usable_shell(path: str | None) -> bool:
    return (
        path is not None
        and os.path.isabs(path)
        and os.path.isfile(path)
        and os.access(path, os.X_OK)
    )


def _passwd_shell() -> str | None:
    """The login shell the password database names for this user, or None."""
    if sys.platform == "win32":
        return None
    import pwd

    try:
        return pwd.getpwuid(os.getuid()).pw_shell or None
    except KeyError:
        return None


def login_shell(environ: Mapping[str, str]) -> str:
    """The shell tmux would start for this user: ``$SHELL``, else the password
    database's, else ``/bin/sh``; each only when it is an absolute path to a program
    that can run."""
    for candidate in (environ.get("SHELL"), _passwd_shell()):
        if candidate is not None and _usable_shell(candidate):
            return candidate
    return FALLBACK_SHELL


class PtyProcess(Protocol):
    """A child on a PTY whose master is read and written with blocking calls."""

    def read(self, size: int) -> bytes: ...

    def write(self, data: bytes) -> None: ...

    def resize(self, cols: int, rows: int) -> None: ...

    def close(self) -> None: ...

    async def wait(self) -> int: ...


async def list_local_tmux_windows(session: str) -> WindowList | None:
    """The windows of local tmux session *session*, or None when tmux fails."""
    args = ["list-windows", "-F", WINDOW_LIST_FORMAT, "-t", session]
    try:
        returncode, stdout, _ = await run_local_tmux(session, args)
    except Exception as exc:
        logger.debug(f"Failed to list local tmux windows for session {session}: {exc}")
        return None
    if returncode != 0:
        return None
    return parse_window_list(stdout.decode("utf-8", errors="replace"))


class LocalTmuxWindows:
    """The windows of a tmux session on this machine (argv, no shell)."""

    def __init__(self, session: str) -> None:
        self._session = session

    async def list_windows(self) -> WindowList | None:
        return await list_local_tmux_windows(self._session)

    async def select(self, target: object) -> None:
        await _run_local_tmux_command(
            self._session, ["select-window", "-t", f"{self._session}:{target}"]
        )

    async def rename(self, name: str) -> None:
        await _run_local_tmux_command(self._session, ["rename-window", "-t", self._session, name])


class LocalPtyTransport:
    """A PTY child, with the background work that belongs to it.

    Blocking reads and writes run in threads. Each is tracked until it returns, even
    when its awaiter is cancelled, so ``close`` can wait for them: the PTY's
    descriptor closes only once none is in flight.
    """

    def __init__(
        self,
        process: PtyProcess,
        tmux: LocalTmuxWindows | None,
        background: Iterable[Coroutine[Any, Any, None]] = (),
    ) -> None:
        self._process = process
        self.tmux = tmux
        self._background = {asyncio.create_task(work, name="terminal-setup") for work in background}
        self._in_flight: set[asyncio.Future[Any]] = set()
        self._closed = False

    async def _blocking(self, call: Any, *args: Any) -> Any:
        future = asyncio.get_running_loop().run_in_executor(None, call, *args)
        self._in_flight.add(future)
        future.add_done_callback(self._in_flight.discard)
        return await asyncio.shield(future)

    async def read(self) -> bytes:
        if self._closed:
            return b""
        try:
            data: bytes = await self._blocking(self._process.read, READ_SIZE)
        except OSError:
            # EIO: the child has exited and the PTY is hung up.
            return b""
        return data

    async def write(self, data: bytes) -> None:
        await self._blocking(self._process.write, data)

    async def resize(self, cols: int, rows: int) -> None:
        try:
            self._process.resize(cols, rows)
            logger.info(f"[Resize] Local PTY resized to {cols}x{rows}")
        except Exception as exc:
            logger.warning(f"Failed to resize local PTY: {exc}")

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        for task in self._background:
            task.cancel()
        await asyncio.gather(*self._background, return_exceptions=True)
        # Hang up the client (the tmux session keeps running in its server), close
        # the descriptor once no read or write holds it, and reap the child.
        self._process.close()
        in_flight = list(self._in_flight)
        if in_flight:
            await asyncio.wait(in_flight, timeout=CLOSE_TIMEOUT)
            for future in in_flight:
                if future.done() and not future.cancelled():
                    future.exception()  # retrieved: a read ended by the hangup may raise
        with contextlib.suppress(Exception):
            await asyncio.wait_for(self._process.wait(), timeout=CLOSE_TIMEOUT)
