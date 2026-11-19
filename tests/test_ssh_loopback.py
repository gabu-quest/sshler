"""The SSH layer against a real asyncssh server on the loopback interface.

Every other SSH test hands ``sshler.ssh`` a fake connection, so none of them
would notice asyncssh changing under it. These start an in-process asyncssh
server (public-key login, an SFTP subsystem rooted in a temp folder, and a
command handler that reports what it was asked to run) and drive the module's
own functions through a real connection.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path

import asyncssh
import pytest
import pytest_asyncio

from sshler.api.helpers import _read_file_bytes
from sshler.ssh import (
    SSHError,
    connect,
    open_tmux,
    sftp_chmod,
    sftp_is_directory,
    sftp_list_directory,
    sftp_read_file,
)

pytestmark = pytest.mark.posix_only("an SFTP server rooted in a temp folder with POSIX file modes")


@dataclass
class LoopbackServer:
    port: int
    root: Path
    client_key: Path
    known_hosts: Path
    wrong_known_hosts: Path


def _report_request(process: asyncssh.SSHServerProcess) -> None:
    """Answer any command with the command, terminal type and terminal size it came with."""
    width, height, _, _ = process.term_size
    process.stdout.write(f"{process.command}|{process.term_type}|{width}x{height}")
    process.exit(0)


@pytest_asyncio.fixture
async def loopback(tmp_path: Path) -> AsyncIterator[LoopbackServer]:
    root = tmp_path / "root"
    (root / "dir_a").mkdir(parents=True)
    (root / "b.txt").write_text("hello\n", encoding="utf-8")
    (root / "b.txt").chmod(0o644)
    (root / "Z.txt").write_text("café\n", encoding="utf-8")
    (root / "dir_a" / "latin1.txt").write_bytes(b"caf\xe9 au lait\n")

    host_key = asyncssh.generate_private_key("ssh-ed25519")
    other_key = asyncssh.generate_private_key("ssh-ed25519")
    client_key = asyncssh.generate_private_key("ssh-ed25519")
    client_key_path = tmp_path / "id_client"
    client_key.write_private_key(str(client_key_path))
    client_key_path.chmod(0o600)
    authorized = tmp_path / "authorized_keys"
    client_key.write_public_key(str(authorized))

    server = await asyncssh.listen(
        "127.0.0.1",
        0,
        server_host_keys=[host_key],
        authorized_client_keys=str(authorized),
        sftp_factory=lambda chan: asyncssh.SFTPServer(chan, chroot=os.fsencode(root)),
        process_factory=_report_request,
    )
    port = server.get_port()

    def known_hosts_for(key: asyncssh.SSHKey, name: str) -> Path:
        path = tmp_path / name
        public = key.export_public_key("openssh").decode().strip()
        path.write_text(f"[127.0.0.1]:{port} {public}\n", encoding="utf-8")
        return path

    try:
        yield LoopbackServer(
            port=port,
            root=root,
            client_key=client_key_path,
            known_hosts=known_hosts_for(host_key, "known_hosts"),
            wrong_known_hosts=known_hosts_for(other_key, "known_hosts_wrong"),
        )
    finally:
        server.close()
        await server.wait_closed()


async def _connect(loopback: LoopbackServer) -> asyncssh.SSHClientConnection:
    return await connect(
        "127.0.0.1",
        "tester",
        port=loopback.port,
        keyfile=str(loopback.client_key),
        known_hosts=str(loopback.known_hosts),
        allow_alias=False,
    )


@pytest.mark.asyncio
async def test_lists_a_remote_directory_with_directories_first(loopback: LoopbackServer):
    connection = await _connect(loopback)
    try:
        entries = await sftp_list_directory(connection, "/")
    finally:
        connection.close()
        await connection.wait_closed()

    assert [(e["name"], e["is_directory"]) for e in entries] == [
        ("dir_a", True),
        ("b.txt", False),
        ("Z.txt", False),
    ]
    assert [e["size"] for e in entries[1:]] == [6, 6]
    assert entries[1]["mode"] == 0o644
    assert entries[1]["modified"] == int((loopback.root / "b.txt").stat().st_mtime)


@pytest.mark.asyncio
async def test_tells_a_directory_from_a_file(loopback: LoopbackServer):
    connection = await _connect(loopback)
    try:
        assert await sftp_is_directory(connection, "/dir_a") is True
        assert await sftp_is_directory(connection, "/b.txt") is False
    finally:
        connection.close()
        await connection.wait_closed()


@pytest.mark.asyncio
async def test_reads_a_text_file_up_to_the_limit(loopback: LoopbackServer):
    connection = await _connect(loopback)
    try:
        whole = await sftp_read_file(connection, "/b.txt")
        cut = await sftp_read_file(connection, "/b.txt", max_bytes=2)
        accented = await sftp_read_file(connection, "/Z.txt")
    finally:
        connection.close()
        await connection.wait_closed()

    assert whole == "hello\n"
    assert cut == "he"
    assert accented == "café\n"


@pytest.mark.asyncio
async def test_bytes_that_are_not_utf8_are_read_as_replacement_characters(
    loopback: LoopbackServer,
):
    connection = await _connect(loopback)
    try:
        text = await sftp_read_file(connection, "/dir_a/latin1.txt")
        cut_inside_a_character = await sftp_read_file(connection, "/Z.txt", max_bytes=4)
    finally:
        connection.close()
        await connection.wait_closed()

    assert text == "caf\ufffd au lait\n"
    assert cut_inside_a_character == "caf\ufffd"


@pytest.mark.asyncio
async def test_a_file_larger_than_one_sftp_block_is_read_whole(loopback: LoopbackServer):
    """One `read` call must give the whole file, not only the first block the server sends."""
    size = 1024 * 1024 + 17
    payload = bytes(i * 31 % 251 for i in range(size))
    (loopback.root / "big.bin").write_bytes(payload)
    text = "".join(f"line {i:07d}\n" for i in range(90_000))
    (loopback.root / "big.txt").write_text(text, encoding="utf-8")

    connection = await _connect(loopback)
    try:
        data, too_large = await _read_file_bytes(connection, "/big.bin", 2 * 1024 * 1024)
        over_limit = await _read_file_bytes(connection, "/big.bin", size - 1)
        read_text = await sftp_read_file(connection, "/big.txt", max_bytes=2 * 1024 * 1024)
    finally:
        connection.close()
        await connection.wait_closed()

    assert too_large is False
    assert len(data) == size
    assert data == payload
    assert over_limit == (b"", True)
    assert len(read_text) == 90_000 * 13
    assert read_text == text


@pytest.mark.asyncio
async def test_many_sftp_calls_share_one_connection(loopback: LoopbackServer):
    """Each call opens and exits its own SFTP session; the connection must survive them."""
    connection = await _connect(loopback)
    try:
        results = [await sftp_is_directory(connection, "/dir_a") for _ in range(25)]
        after = await sftp_read_file(connection, "/b.txt")
    finally:
        connection.close()
        await connection.wait_closed()

    assert results == [True] * 25
    assert after == "hello\n"


@pytest.mark.asyncio
async def test_changes_a_remote_file_mode(loopback: LoopbackServer):
    connection = await _connect(loopback)
    try:
        await sftp_chmod(connection, "/b.txt", 0o600)
    finally:
        connection.close()
        await connection.wait_closed()

    assert (loopback.root / "b.txt").stat().st_mode & 0o7777 == 0o600


@pytest.mark.asyncio
async def test_open_tmux_requests_a_terminal_of_the_given_size(loopback: LoopbackServer):
    connection = await _connect(loopback)
    try:
        process = await open_tmux(connection, "/srv/my project", "my.proj", columns=100, rows=40)
        output = await process.stdout.read()
        await process.wait()
    finally:
        connection.close()
        await connection.wait_closed()

    command = b"tmux -L ts-my.proj new -As my.proj -c '/srv/my project'"
    assert output == command + b"|xterm-256color|100x40"
    assert process.exit_status == 0


@pytest.mark.asyncio
async def test_a_host_key_that_is_not_the_known_one_is_refused(loopback: LoopbackServer):
    with pytest.raises(SSHError, match="[Hh]ost key"):
        await connect(
            "127.0.0.1",
            "tester",
            port=loopback.port,
            keyfile=str(loopback.client_key),
            known_hosts=str(loopback.wrong_known_hosts),
            allow_alias=False,
        )


@pytest.mark.asyncio
async def test_a_client_key_the_server_does_not_know_is_refused(
    loopback: LoopbackServer, tmp_path: Path
):
    stranger = tmp_path / "id_stranger"
    asyncssh.generate_private_key("ssh-ed25519").write_private_key(str(stranger))
    stranger.chmod(0o600)

    with pytest.raises(SSHError, match="[Pp]ermission denied"):
        await connect(
            "127.0.0.1",
            "tester",
            port=loopback.port,
            keyfile=str(stranger),
            known_hosts=str(loopback.known_hosts),
            allow_alias=False,
        )
