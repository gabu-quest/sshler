"""tmux on a remote box, over its own SSH connection."""

from __future__ import annotations

import asyncio
import contextlib
import logging

import asyncssh

from ..tmux import WINDOW_LIST_FORMAT, parse_window_list, remote_tmux_command
from .transport import READ_SIZE, WindowList

logger = logging.getLogger(__name__)

# How long closing waits for the SSH connection to finish closing.
CLOSE_TIMEOUT = 5.0


async def list_remote_tmux_windows(
    connection: asyncssh.SSHClientConnection, session: str
) -> WindowList | None:
    """The windows of remote tmux session *session*, or None when tmux fails."""
    try:
        result = await connection.run(
            remote_tmux_command(session, ["list-windows", "-F", WINDOW_LIST_FORMAT, "-t", session]),
            check=False,
        )
    except Exception as exc:
        logger.debug(f"Failed to list tmux windows for session {session}: {exc}")
        return None
    if result.returncode != 0 or result.stdout is None:
        return None
    stdout = result.stdout
    text = stdout if isinstance(stdout, str) else stdout.decode("utf-8", errors="replace")
    return parse_window_list(text)


class RemoteTmuxWindows:
    """The windows of a tmux session on a remote box (each value shell-quoted)."""

    def __init__(self, connection: asyncssh.SSHClientConnection, session: str) -> None:
        self._connection = connection
        self._session = session

    async def list_windows(self) -> WindowList | None:
        return await list_remote_tmux_windows(self._connection, self._session)

    async def select(self, target: object) -> None:
        await self._run(["select-window", "-t", f"{self._session}:{target}"], "select window")

    async def rename(self, name: str) -> None:
        await self._run(["rename-window", "-t", self._session, name], "rename window")

    async def _run(self, args: list[str], what: str) -> None:
        try:
            await self._connection.run(remote_tmux_command(self._session, args), check=False)
        except Exception as exc:
            logger.debug(f"Failed to {what}: {exc}")


class SshTransport:
    """A tmux client on a remote PTY; the transport owns the connection under it."""

    def __init__(
        self,
        connection: asyncssh.SSHClientConnection,
        process: asyncssh.SSHClientProcess,
        tmux: RemoteTmuxWindows | None,
    ) -> None:
        self._connection = connection
        self._process = process
        self.tmux = tmux
        self._closed = False

    async def read(self) -> bytes:
        if self._closed:
            return b""
        data = await self._process.stdout.read(READ_SIZE)
        return data.encode("utf-8") if isinstance(data, str) else data

    async def write(self, data: bytes) -> None:
        if self._process.stdin is not None:
            self._process.stdin.write(data)

    async def resize(self, cols: int, rows: int) -> None:
        # asyncssh sends the window-change request; the remote PTY delivers SIGWINCH.
        try:
            self._process.change_terminal_size(cols, rows)
            logger.info(f"[Resize] SSH PTY resized to {cols}x{rows}")
        except Exception as exc:
            logger.warning(f"Failed to resize SSH PTY: {exc}")

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            # End of input, then close the channel.
            if self._process.stdin is not None:
                self._process.stdin.write_eof()
            self._process.close()
        except Exception as exc:
            logger.debug(f"Error during process cleanup: {exc}")
        await close_connection(self._connection)


async def close_connection(connection: asyncssh.SSHClientConnection) -> None:
    """Close *connection* and wait, within a bound, until it has closed."""
    try:
        connection.close()
    except Exception as exc:
        logger.debug(f"Error closing SSH connection: {exc}")
        return
    wait_closed = getattr(connection, "wait_closed", None)
    if wait_closed is not None:
        with contextlib.suppress(Exception):
            await asyncio.wait_for(wait_closed(), timeout=CLOSE_TIMEOUT)
