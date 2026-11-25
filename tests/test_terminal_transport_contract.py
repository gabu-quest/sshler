"""Every terminal transport keeps one contract, proved by one script.

The script opens a transport, runs ``echo`` and reads the answer back, resizes and
reads the new size back, then closes. It runs against the local transport on a real
PTY running ``sh``, against SSH over a fake remote channel, and against a tab of a
persisted Windows shell kept by the real registry over a fake ConPTY. A transport
that breaks the interface fails here.
"""

from __future__ import annotations

import asyncio
import errno
import os
import sys
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path

import pytest
import pytest_asyncio
from terminal_fakes import FakeConPtyProcess, FakeSshChannel, FakeSshConnection

from sshler.terminal.conpty import ConPtyTransport
from sshler.terminal.local import LocalPtyTransport
from sshler.terminal.remote import SshTransport
from sshler.terminal.transport import Transport
from sshler.win_terminal_registry import WindowsTerminalRegistry

ECHO_WORD = "contract-7a1"


@dataclass
class Opened:
    transport: Transport
    # Checks what closing must have left behind, specific to the transport.
    after_close: Callable[[], Awaitable[None]]


async def _read_until(
    transport: Transport, marker: bytes, count: int = 1, seconds: float = 10.0
) -> bytes:
    received = b""
    async with asyncio.timeout(seconds):
        while received.count(marker) < count:
            chunk = await transport.read()
            assert chunk != b"", f"output ended before {marker!r}; got {received!r}"
            received += chunk
    return received


async def _open_local(tmp_path: Path) -> Opened:
    from sshler.ptyspawn import spawn_on_pty
    from sshler.webapp import LocalPTYProcess

    env = {"PATH": os.environ["PATH"], "HOME": str(tmp_path), "PS1": "$ ", "TERM": "dumb"}
    master_fd, pid = spawn_on_pty(["sh"], env=env, rows=24, cols=80)
    stdin = os.fdopen(master_fd, "wb", buffering=0, closefd=False)
    stdout = os.fdopen(master_fd, "rb", buffering=0, closefd=False)
    process = LocalPTYProcess(master_fd, pid, stdin, stdout)

    async def after_close() -> None:
        with pytest.raises(ChildProcessError):
            os.waitpid(pid, os.WNOHANG)  # reaped: no zombie left
        with pytest.raises(OSError) as closed:
            os.fstat(master_fd)
        assert closed.value.errno == errno.EBADF

    return Opened(LocalPtyTransport(process, None), after_close)


async def _open_remote(tmp_path: Path) -> Opened:
    connection = FakeSshConnection()
    channel = FakeSshChannel(cols=80, rows=24)
    transport = SshTransport(connection, channel, None)  # type: ignore[arg-type]

    async def after_close() -> None:
        assert (channel.stdin.eof, channel.closed) == (True, True)
        assert (connection.closed, connection.wait_closed_calls) == (True, 1)

    return Opened(transport, after_close)


async def _open_conpty(tmp_path: Path) -> Opened:
    registry = WindowsTerminalRegistry(max_sessions=4)
    process = FakeConPtyProcess(cols=80, rows=24)

    async def spawn() -> FakeConPtyProcess:
        return process

    session, created = await registry.get_or_create(("local", "contract"), spawn, 80, 24)
    assert created is True
    transport = await ConPtyTransport.attach(registry, session, 80, 24)

    async def after_close() -> None:
        # One tab closed: detached, while the persisted shell keeps running.
        assert (len(session.sinks), process.terminated) == (0, False)
        assert session.detached_since is not None
        await registry.shutdown()
        assert process.terminated is True

    return Opened(transport, after_close)


OPENERS = {"local-pty": _open_local, "ssh": _open_remote, "conpty": _open_conpty}


@pytest_asyncio.fixture(params=list(OPENERS))
async def opened(request: pytest.FixtureRequest, tmp_path: Path) -> AsyncIterator[Opened]:
    if request.param == "local-pty" and sys.platform == "win32":
        pytest.skip("a POSIX PTY running sh")
    result = await OPENERS[request.param](tmp_path)
    try:
        yield result
    finally:
        await result.transport.close()


@pytest.mark.asyncio
async def test_every_transport_echoes_resizes_and_closes(opened: Opened) -> None:
    transport = opened.transport

    await transport.write(f"echo {ECHO_WORD}\n".encode())
    # Once in the echoed command line, once as the answer.
    echoed = await _read_until(transport, f"{ECHO_WORD}\r\n".encode(), count=2)
    assert echoed.count(f"{ECHO_WORD}\r\n".encode()) == 2

    await transport.resize(100, 40)
    await transport.write(b"stty size\n")
    await _read_until(transport, b"40 100\r\n")

    await transport.close()
    assert await transport.read() == b""
    await transport.close()  # a second close is a no-op
    await opened.after_close()
