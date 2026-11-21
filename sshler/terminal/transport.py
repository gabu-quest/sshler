"""The interface every terminal transport keeps, and what opening one returns."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Protocol

import anyio

# The most output one read returns.
READ_SIZE = 32768

WindowList = list[dict[str, str | bool]]


class TmuxWindows(Protocol):
    """The tmux windows of one session."""

    async def list_windows(self) -> WindowList | None:
        """The session's windows, or None when they cannot be listed."""
        ...

    async def select(self, target: object) -> None:
        """Make window *target* the current one."""
        ...

    async def rename(self, name: str) -> None:
        """Rename the current window to *name*."""
        ...


class Transport(Protocol):
    """One running terminal.

    * ``read`` returns the next output, never ``b""`` while the terminal runs; ``b""``
      means it has ended for good. It raises when the transport fails (a dropped
      connection), which also ends it.
    * ``write`` sends input; it raises when the terminal cannot take it.
    * ``resize`` never raises: a size the terminal refuses is logged and dropped.
    * ``close`` may be called more than once and from any state. When it returns,
      every task, child process, channel and file descriptor of the transport is
      gone (a persisted shell is detached from, not ended), and ``read`` returns
      ``b""``.
    * ``tmux`` is the session's tmux windows, or None when there is no tmux.
    """

    @property
    def tmux(self) -> TmuxWindows | None: ...

    async def read(self) -> bytes: ...

    async def write(self, data: bytes) -> None: ...

    async def resize(self, cols: int, rows: int) -> None: ...

    async def close(self) -> None: ...


@dataclass(frozen=True)
class OpenedTerminal:
    """A transport that opened, and what the socket needs to know about it.

    ``directory`` is the working directory the terminal really started in (a
    requested directory that does not exist falls back to the box's default).
    ``on_tracked``, when set, takes the id of the session row: the transport's owner
    then marks the row inactive itself, so the socket must not when it closes.
    ``fallback`` is true when tmux was wanted and the box has none, so the transport
    is a plain shell opened in its place. ``connection`` is the SSH connection a remote
    terminal runs on (the transport owns and closes it; None on the local box): the
    socket's end asks tmux on it which sessions it has, so nothing new connects.
    """

    transport: Transport
    directory: str
    on_tracked: Callable[[str], None] | None = None
    fallback: bool = False
    connection: Any = None


class TerminalOpenError(Exception):
    """A terminal that could not open: the socket sends *message*, then closes.

    Raised only before anything is left open, so the socket has nothing to tear down.
    """

    def __init__(self, message: str, code: int = 1000, reason: str = "") -> None:
        super().__init__(message)
        self.message = message
        self.code = code
        self.reason = reason


async def open_or_close_on_cancel[T](
    opening: Awaitable[T], close: Callable[[T], Awaitable[None]]
) -> T:
    """Await *opening*; if the caller is cancelled meanwhile, close what it opens.

    For an open that runs on in a thread after its awaiter is cancelled (a PTY
    spawn): the open is allowed to finish, its result is closed, and the
    cancellation goes on.
    """
    task = asyncio.ensure_future(opening)
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        with teardown_scope():
            await asyncio.wait([task])
            if not task.cancelled() and task.exception() is None:
                await close(task.result())
        raise


def teardown_scope() -> anyio.CancelScope:
    """The scope every teardown runs in, so it always runs to its end.

    Under Starlette the handler runs in an anyio cancel scope, whose cancel is
    delivered again at every await until the handler returns; a teardown outside
    this scope would stop at its first await and leave the rest open. A cancel
    still takes effect once the teardown is done.
    """
    return anyio.CancelScope(shield=True)
