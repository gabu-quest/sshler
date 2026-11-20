"""Stand-ins for the far end of a terminal: a remote SSH channel and a Windows ConPTY.

Both run ``ShellEmulator``, which answers like ``sh`` on a terminal for the two
commands the transport tests use, so one script can drive a real PTY and the fakes.
"""

from __future__ import annotations

import asyncio
import queue
import threading
from types import SimpleNamespace


class ShellEmulator:
    """``sh`` on a terminal, for ``echo WORD`` and ``stty size``.

    Input is echoed with each newline turned into CRLF, as a tty in cooked mode does;
    each complete line is then answered, followed by the ``$ `` prompt.
    """

    def __init__(self, cols: int, rows: int) -> None:
        self.cols = cols
        self.rows = rows
        self._line = b""

    def feed(self, data: bytes) -> bytes:
        out = b""
        for byte in data:
            char = bytes([byte])
            if char != b"\n":
                self._line += char
                out += char
                continue
            out += b"\r\n" + self._answer(self._line.decode()) + b"$ "
            self._line = b""
        return out

    def _answer(self, line: str) -> bytes:
        if line.startswith("echo "):
            return line[len("echo ") :].encode() + b"\r\n"
        if line == "stty size":
            return f"{self.rows} {self.cols}\r\n".encode()
        return b""


class _ChannelStdout:
    def __init__(self, channel: FakeSshChannel) -> None:
        self._channel = channel

    async def read(self, size: int) -> bytes:
        item = await self._channel.output.get()
        if isinstance(item, BaseException):
            raise item
        return item


class _ChannelStdin:
    def __init__(self, channel: FakeSshChannel) -> None:
        self._channel = channel
        self.eof = False

    def write(self, data: bytes) -> None:
        if self._channel.closed:
            raise BrokenPipeError("channel closed")
        self._channel.written.append(data)
        self._channel.output.put_nowait(self._channel.shell.feed(data))

    def write_eof(self) -> None:
        self.eof = True


class FakeSshChannel:
    """An asyncssh process running ``sh`` on a remote PTY (binary mode)."""

    def __init__(self, cols: int = 80, rows: int = 24) -> None:
        self.shell = ShellEmulator(cols, rows)
        self.output: asyncio.Queue[bytes | BaseException] = asyncio.Queue()
        self.written: list[bytes] = []
        self.sizes: list[tuple[int, int]] = []
        self.closed = False
        self.stdin = _ChannelStdin(self)
        self.stdout = _ChannelStdout(self)

    def change_terminal_size(self, cols: int, rows: int) -> None:
        self.sizes.append((cols, rows))
        self.shell.cols, self.shell.rows = cols, rows

    def exit(self) -> None:
        """The remote program ends: its output reaches end of file."""
        self.output.put_nowait(b"")

    def drop(self) -> None:
        """The connection under the channel is lost mid-stream."""
        self.output.put_nowait(ConnectionResetError("connection lost"))

    def close(self) -> None:
        self.closed = True
        self.output.put_nowait(b"")


class FakeSshConnection:
    """An asyncssh connection: tmux commands fail, so no window list is sent."""

    def __init__(self) -> None:
        self.closed = False
        self.wait_closed_calls = 0
        self.commands: list[str] = []

    def close(self) -> None:
        self.closed = True

    async def wait_closed(self) -> None:
        self.wait_closed_calls += 1

    async def run(self, command: str, check: bool = False) -> SimpleNamespace:
        self.commands.append(command)
        return SimpleNamespace(returncode=1, stdout="")


class _ConPtyStdout:
    def __init__(self, process: FakeConPtyProcess) -> None:
        self._process = process

    def read(self, size: int) -> str:
        item = self._process.output.get()
        return "" if item is None else item


class _ConPtyStdin:
    def __init__(self, process: FakeConPtyProcess) -> None:
        self._process = process

    def write(self, data: bytes) -> None:
        with self._process.lock:
            answer = self._process.shell.feed(data)
        self._process.output.put(answer.decode())


class FakeConPtyProcess:
    """A ``WinPTYProcess`` running ``sh``: blocking reads, end of file on terminate."""

    def __init__(self, cols: int = 80, rows: int = 24) -> None:
        self.shell = ShellEmulator(cols, rows)
        self.lock = threading.Lock()
        self.output: queue.Queue[str | None] = queue.Queue()
        self.terminated = False
        self.stdin = _ConPtyStdin(self)
        self.stdout = _ConPtyStdout(self)

    def resize(self, cols: int, rows: int) -> None:
        with self.lock:
            self.shell.cols, self.shell.rows = cols, rows

    def terminate(self) -> None:
        self.terminated = True
        self.output.put(None)

    def close(self) -> None:
        pass
