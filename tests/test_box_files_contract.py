"""The files of a box: one interface, one implementation per kind of box.

The table of operations runs against ``LocalFiles`` on a temp folder and against
``RemoteFiles`` on the same tree through a loopback asyncssh SFTP server; each row
asserts the one visible result. Where the two kinds answer differently on purpose, the
row names the difference as its expectation. A third kind of box joins by adding an
opener to ``OPENERS``.

The loopback server only serves SFTP on the temp folder, the pool's health check and
the home lookup; it never runs a command built from a path. The folder size and the
folder zip run commands, so their rows use a fake connection that records each command
line and never runs it; a command line is checked by splitting it with ``shlex``.

The last part counts, per request, the commands run and the SFTP sessions opened by a
remote listing, a rename and a preview.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import shlex
import threading
import zipfile
from collections.abc import AsyncIterator, Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace

import asyncssh
import pytest
import pytest_asyncio
import yaml
from fastapi import HTTPException
from fastapi.responses import FileResponse
from fastapi.testclient import TestClient
from transfer_events import transfer_events

import sshler.api
from sshler import ssh_pool, state
from sshler.api import archive as archive_api
from sshler.api import remote_files as remote_files_api
from sshler.api.box_files import DEFAULT_ANSWERS, Answers, box_files
from sshler.api.dependencies import APIDependencies
from sshler.api.placement import DestinationRefused
from sshler.config import Box
from sshler.ssh import SSHError, connect
from sshler.ssh_pool import SSHConnectionPool
from sshler.webapp import ServerSettings, make_app

pytestmark = pytest.mark.posix_only("an SFTP server rooted in a temp folder with POSIX file modes")

TOKEN = "contract-token"
HEADERS = {"X-SSHLER-TOKEN": TOKEN}
HOME_CMD = 'printf %s "$HOME"'
HEALTH_CMD = "echo test"


# --- a loopback server that counts ---------------------------------------------------


@dataclass
class Counts:
    """What the server saw: each command line it was asked to run, each SFTP session."""

    commands: list[str] = field(default_factory=list)
    sftp_sessions: int = 0
    connections: int = 0


def _answer_factory(counts: Counts, home: str) -> Callable[[asyncssh.SSHServerProcess], None]:
    def answer(process: asyncssh.SSHServerProcess) -> None:
        """Only the pool's health check and the home lookup are answered; any other
        command line is recorded and refused, never run."""
        counts.commands.append(str(process.command))
        if process.command == HEALTH_CMD:
            process.stdout.write("test\n")
            process.exit(0)
        elif process.command == HOME_CMD:
            process.stdout.write(home)
            process.exit(0)
        else:
            process.stderr.write("not run\n")
            process.exit(127)

    return answer


class ServerThread:
    """An asyncssh server on its own event loop, so the sync test client can reach it."""

    def __init__(self, root: Path, keys: Path, counts: Counts, home: str) -> None:
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self.loop.run_forever, daemon=True)
        self.thread.start()
        host_key = asyncssh.generate_private_key("ssh-ed25519")
        client_key = asyncssh.generate_private_key("ssh-ed25519")
        self.client_key = keys / "id_client"
        client_key.write_private_key(str(self.client_key))
        self.client_key.chmod(0o600)
        authorized = keys / "authorized_keys"
        client_key.write_public_key(str(authorized))
        self.connections: list[asyncssh.SSHServerConnection] = []
        connections = self.connections

        class Tracked(asyncssh.SSHServer):
            def connection_made(self, conn: asyncssh.SSHServerConnection) -> None:
                counts.connections += 1
                connections.append(conn)

        def sftp_factory(chan: asyncssh.SSHServerChannel) -> asyncssh.SFTPServer:
            counts.sftp_sessions += 1
            return asyncssh.SFTPServer(chan, chroot=os.fsencode(root))

        self.server = self._run(
            asyncssh.listen(
                "127.0.0.1",
                0,
                server_factory=Tracked,
                server_host_keys=[host_key],
                authorized_client_keys=str(authorized),
                sftp_factory=sftp_factory,
                process_factory=_answer_factory(counts, home),
            )
        )
        self.port = self.server.get_port()
        self.known_hosts = keys / "known_hosts"
        public = host_key.export_public_key("openssh").decode().strip()
        self.known_hosts.write_text(f"[127.0.0.1]:{self.port} {public}\n", encoding="utf-8")

    def _run(self, awaitable):
        async def _await():
            return await awaitable

        return asyncio.run_coroutine_threadsafe(_await(), self.loop).result(timeout=30)

    def close(self) -> None:
        async def _close() -> None:
            for conn in self.connections:
                conn.close()
            self.server.close()
            await self.server.wait_closed()

        self._run(_close())
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.thread.join(timeout=30)
        self.loop.close()


# --- one table of operations, run against each kind of box ---------------------------

CONFIG = SimpleNamespace()  # `box_files` only hands it to `connect_for_box`
SERVER_HOME = "/home"
# A name with a space, a quote and a newline.
ODD_NAME = "it's a\nname.txt"


def _build_tree(root: Path, outside: Path) -> None:
    """The same tree for every kind: six bytes, an empty file, a folder with a file, a
    file the rule finds taken, and a link that leaves the tree."""
    root.mkdir(parents=True)
    (root / "a.txt").write_bytes(b"hello\n")
    (root / "empty.txt").write_bytes(b"")
    (root / "sub").mkdir()
    (root / "sub" / "inner.txt").write_bytes(b"in\n")
    (root / "taken.txt").write_bytes(b"old\n")
    outside.mkdir()
    (outside / "secret.txt").write_bytes(b"secret\n")
    (root / "link_out").symlink_to(outside / "secret.txt")


@dataclass
class Kind:
    """One kind of box, opened on the tree: ``at(rel)`` is how that box names a path
    of the tree; ``home`` is what ``~`` resolves to on it."""

    name: str
    box: Box
    deps: object
    root: Path
    outside: Path
    home: str
    at: Callable[[str], str]


async def _open_local(tmp_path: Path) -> AsyncIterator[Kind]:
    root = (tmp_path / "tree").resolve()
    outside = (tmp_path / "outside").resolve()
    _build_tree(root, outside)
    box = Box(name="local", connect_host="localhost", display_host="localhost", user="me")
    box.transport = "local"
    yield Kind(
        name="local",
        box=box,
        deps=SimpleNamespace(),
        root=root,
        outside=outside,
        home=str(Path.home().resolve()),
        at=lambda rel: str(root / rel) if rel else str(root),
    )


async def _open_remote(tmp_path: Path) -> AsyncIterator[Kind]:
    root = tmp_path / "tree"
    outside = tmp_path / "outside"
    _build_tree(root, outside)
    keys = tmp_path / "keys"
    keys.mkdir()
    host_key = asyncssh.generate_private_key("ssh-ed25519")
    client_key = asyncssh.generate_private_key("ssh-ed25519")
    client_key.write_private_key(str(keys / "id_client"))
    (keys / "id_client").chmod(0o600)
    client_key.write_public_key(str(keys / "authorized_keys"))
    counts = Counts()
    server = await asyncssh.listen(
        "127.0.0.1",
        0,
        server_host_keys=[host_key],
        authorized_client_keys=str(keys / "authorized_keys"),
        sftp_factory=lambda chan: asyncssh.SFTPServer(chan, chroot=os.fsencode(root)),
        process_factory=_answer_factory(counts, SERVER_HOME),
    )
    port = server.get_port()
    public = host_key.export_public_key("openssh").decode().strip()
    (keys / "known_hosts").write_text(f"[127.0.0.1]:{port} {public}\n", encoding="utf-8")

    async def connect_for_box(box: Box, application_config: object):
        return await connect(
            "127.0.0.1",
            "tester",
            port=port,
            keyfile=str(keys / "id_client"),
            known_hosts=str(keys / "known_hosts"),
            allow_alias=False,
        )

    pool = SSHConnectionPool()
    previous, ssh_pool._global_pool = ssh_pool._global_pool, pool
    # A remote listing records the folder visit.
    (tmp_path / "config").mkdir()
    state.initialize(tmp_path / "config")
    box = Box(name="lb", connect_host="127.0.0.1", display_host="127.0.0.1", user="tester")
    try:
        yield Kind(
            name="remote",
            box=box,
            deps=SimpleNamespace(connect_for_box=connect_for_box),
            root=root,
            outside=outside,
            home=SERVER_HOME,
            at=lambda rel: f"/{rel}",
        )
        # Only the health check and the home lookup ever reach the server.
        assert set(counts.commands) <= {HEALTH_CMD, HOME_CMD}
    finally:
        await pool.close_all()
        ssh_pool._global_pool = previous
        state.reset_state()
        server.close()
        await server.wait_closed()


OPENERS = {"local": _open_local, "remote": _open_remote}


@pytest_asyncio.fixture(params=list(OPENERS))
async def kind(request: pytest.FixtureRequest, tmp_path: Path) -> AsyncIterator[Kind]:
    opened = OPENERS[request.param](tmp_path)
    yield await anext(opened)
    with contextlib.suppress(StopAsyncIteration):
        await anext(opened)


async def _outcome(kind: Kind, operation, answers: Answers | None = DEFAULT_ANSWERS) -> object:
    """What ``operation(files, kind)`` gives inside a ``box_files`` block: its value, a
    refusal (status, text, reason), an answer (status, text), or the type of a failure
    that goes on unanswered."""
    try:
        async with box_files(kind.deps, kind.box, CONFIG, answers) as files:
            return await operation(files, kind)
    except DestinationRefused as exc:
        return ("refused", exc.status_code, exc.detail, exc.reason)
    except HTTPException as exc:
        return ("answered", exc.status_code, exc.detail)
    except Exception as exc:
        return ("unanswered", type(exc).__name__)


@dataclass(frozen=True)
class Row:
    """One operation and its one expected result: ``same(kind)`` on every kind, or
    ``differs[kind.name](kind)`` where the kinds answer differently on purpose."""

    name: str
    operation: Callable
    same: Callable[[Kind], object] | None = None
    differs: dict[str, Callable[[Kind], object]] = field(default_factory=dict)

    def expected(self, kind: Kind) -> object:
        return self.same(kind) if self.same is not None else self.differs[kind.name](kind)


async def _names(files, kind: Kind, rel: str = "") -> list[tuple[str, bool, int | None]]:
    folder = await files.folder(kind.at(rel))
    return [(e.name, e.is_directory, e.size) for e in await files.list_folder(folder)]


async def _rename_taken(files, kind: Kind):
    return await files.rename(kind.at("a.txt"), "taken.txt", overwrite=False)


async def _rename_replacing(files, kind: Kind):
    target = await files.rename(kind.at("a.txt"), "taken.txt", overwrite=True)
    return target, (kind.root / "taken.txt").read_bytes(), (kind.root / "a.txt").exists()


async def _folder_into_itself(files, kind: Kind):
    return await files.place_into(
        kind.at("sub"), kind.at("sub"), None, move=True, overwrite=False, allow_dirs=True
    )


async def _gone_before_the_read(files, kind: Kind):
    target = await files.path(kind.at("a.txt"))
    found = await files.stat(target)
    (kind.root / "a.txt").unlink()
    return found, await files.read_text(target, 100)


async def _odd_name(files, kind: Kind):
    target = await files.child(kind.at(""), ODD_NAME)
    await files.touch(target)
    return target, await files.stat(target), (kind.root / ODD_NAME).read_bytes()


async def _upload_replacing(files, kind: Kind):
    target = await files.child(kind.at(""), "taken.txt")
    await files.upload(target, b"new", overwrite=True)
    return (kind.root / "taken.txt").read_bytes()


async def _chmod(files, kind: Kind):
    await files.chmod(await files.path(kind.at("a.txt")), 0o600)
    return (kind.root / "a.txt").stat().st_mode & 0o7777


async def _write_and_read(files, kind: Kind):
    target = await files.path(kind.at("new.md"))
    await files.write_text(target, "# hi\n")
    return await files.read_text(target, 100)


async def _serve(files, kind: Kind, limit: int):
    response = await files.serve(await files.path(kind.at("a.txt")), inline=False, limit=limit)
    if isinstance(response, FileResponse):
        return ("streamed from", response.path)
    return ("in memory", response.body, response.headers["content-disposition"])


async def _delete_many(files, kind: Kind):
    # A folder is removed with a command on a remote box: see the recorded test below.
    outcome = await files.delete_many([kind.at("a.txt"), kind.at("nope")])
    left = sorted(p.name for p in kind.root.iterdir())
    return outcome.succeeded, [f["path"] for f in outcome.failed], left


async def _copy_many(files, kind: Kind):
    (kind.root / "sub" / "taken.txt").write_bytes(b"inner old\n")
    outcome = await files.place_many(
        [kind.at("a.txt"), kind.at("taken.txt")], kind.at("sub"), move=False, overwrite=False
    )
    return outcome.succeeded, outcome.failed


async def _read_both(files, kind: Kind, rel: str, limit: int):
    target = await files.path(kind.at(rel))
    return await files.read_bytes(target, limit), await files.read_text(target, limit)


async def _read_bytes(files, kind: Kind, rel: str, limit: int):
    return await files.read_bytes(await files.path(kind.at(rel)), limit)


async def _read_empty(files, kind: Kind):
    target = await files.path(kind.at("empty.txt"))
    return (
        await files.read_bytes(target, 10),
        await files.read_text(target, 10),
        await files.stat(target),
    )


async def _stat(files, kind: Kind, rel: str):
    return await files.stat(await files.path(kind.at(rel)))


async def _link_out(files, kind: Kind):
    target = await files.path(kind.at("link_out"))
    return target, await files.read_text(target, 100)


async def _touch(files, kind: Kind, rel: str):
    await files.touch(await files.child(kind.at(""), rel))


async def _make_folder(files, kind: Kind, rel: str):
    await files.make_folder(await files.child(kind.at(""), rel))


async def _delete(files, kind: Kind, rel: str):
    await files.delete(await files.path(kind.at(rel)))


# --- the operations a transfer is built from -----------------------------------------

CHUNK = 256 * 1024
# Larger than two chunks, so a read in chunks gives two full ones and a short one.
BIG = bytes(range(256)) * (2 * CHUNK // 256 + 100)


async def _source(files, kind: Kind, rel: str):
    found = await files.source(await files.path(kind.at(rel)))
    return found.mode, found.size, found.fingerprint


def _fingerprint(kind: Kind, rel: str) -> tuple:
    """The fingerprint of a file of the tree, from ``os.stat`` and not from the box:
    size, time in nanoseconds and inode on the local box; size and whole-second time
    over SFTP version 3, which sends no finer time."""
    st = (kind.root / rel).stat()
    if kind.name == "local":
        return (st.st_size, st.st_mtime_ns, st.st_ino)
    return (st.st_size, int(st.st_mtime), None)


async def _chunks(files, kind: Kind, rel: str, size: int) -> list[bytes]:
    if rel == "big.bin":
        (kind.root / rel).write_bytes(BIG)
    chunks = []
    async with files.open_read(await files.path(kind.at(rel))) as read:
        while chunk := await read(size):
            chunks.append(chunk)
    return chunks


def _folder_names(kind: Kind, rel: str = "") -> list[str]:
    return sorted(p.name for p in (kind.root / rel).iterdir())


async def _land_new(files, kind: Kind):
    """Three writes into a free name in a folder that does not exist yet."""
    folder = await files.path(kind.at("in"))
    target = files.join(folder, ODD_NAME)
    landing = await files.land(target, source_mode=0o100640, overwrite=False)
    for part in (b"ab", b"", b"c"):
        await landing.write(part)
    await landing.commit()
    found = (kind.root / "in" / ODD_NAME).stat()
    return (
        files.name(target),
        (kind.root / "in" / ODD_NAME).read_bytes(),
        found.st_mode & 0o777,
        _folder_names(kind, "in"),
    )


async def _land_replacing(files, kind: Kind):
    (kind.root / "taken.txt").chmod(0o660)
    target = await files.path(kind.at("taken.txt"))
    landing = await files.land(target, source_mode=0o100600, overwrite=True)
    await landing.write(BIG)
    await landing.commit()
    return (
        (kind.root / "taken.txt").read_bytes() == BIG,
        (kind.root / "taken.txt").stat().st_mode & 0o777,
        _folder_names(kind),
    )


async def _land_aborted(files, kind: Kind, rel: str, overwrite: bool):
    target = await files.path(kind.at(rel))
    landing = await files.land(target, source_mode=0o100644, overwrite=overwrite)
    await landing.write(b"partial")
    await landing.abort()
    return _folder_names(kind), (kind.root / "taken.txt").read_bytes()


async def _land(files, kind: Kind, rel: str, source_rel: str, overwrite: bool):
    found = await files.source(await files.path(kind.at(source_rel)))
    await files.land(await files.path(kind.at(rel)), source_mode=found.mode, overwrite=overwrite)


async def _remove_if_unchanged(files, kind: Kind, changed: bool):
    target = await files.path(kind.at("a.txt"))
    found = await files.source(target)
    if changed:
        (kind.root / "a.txt").write_bytes(b"written meanwhile\n")
    try:
        await files.remove_if_unchanged(target, found.fingerprint, "elsewhere/a.txt")
    except HTTPException as exc:
        return exc.status_code, exc.detail, (kind.root / "a.txt").exists()
    return "removed", (kind.root / "a.txt").exists()


async def _checked_then_resolved(files, kind: Kind, path: str):
    checked = await asyncio.to_thread(files.check_path, path)
    return checked, await files.resolve(checked)


async def _name_of_joined(files, kind: Kind):
    return files.name(files.join(kind.at("sub"), ODD_NAME))


async def _prepared(files, kind: Kind):
    await files.prepare()
    return await _source(files, kind, "a.txt")


async def _archive_of(files, kind: Kind, sources: list[str]):
    return await files.create_archive(sources, kind.at(""), "o.zip", "zip")


def _listed(folder_size: int | None) -> list[tuple[str, bool, int | None]]:
    return [
        ("sub", True, folder_size),
        ("a.txt", False, 6),
        ("empty.txt", False, 0),
        ("link_out", False, 7),
        ("taken.txt", False, 4),
    ]


ATTACHMENT = "attachment; filename=\"a.txt\"; filename*=UTF-8''a.txt"

ROWS = [
    Row(
        # A local listing gives a folder no size; a remote one gives the size SFTP
        # reports for it.
        "a folder is listed folders first, then by name; a link is listed as what it names",
        _names,
        differs={
            "local": lambda k: _listed(None),
            "remote": lambda k: _listed((k.root / "sub").stat().st_size),
        },
    ),
    Row(
        "a file is read as bytes and as text",
        lambda f, k: _read_both(f, k, "a.txt", 100),
        same=lambda k: ((b"hello\n", False), "hello\n"),
    ),
    Row(
        "a 0-byte file reads as nothing and is a file",
        _read_empty,
        same=lambda k: ((b"", False), "", (True, False, True)),
    ),
    Row(
        # The bytes of a read past the limit are never used; only the flag is.
        "a file past the limit is flagged too large",
        lambda f, k: _read_bytes(f, k, "a.txt", 3),
        differs={"local": lambda k: (b"hel", True), "remote": lambda k: (b"", True)},
    ),
    Row(
        "a folder is a folder",
        lambda f, k: _stat(f, k, "sub"),
        same=lambda k: (True, True, False),
    ),
    Row(
        "a missing path",
        lambda f, k: _stat(f, k, "nope"),
        same=lambda k: (False, False, False),
    ),
    Row(
        "a name that exists is refused",
        _rename_taken,
        same=lambda k: ("refused", 409, "File already exists", "exists"),
    ),
    Row(
        "a name that exists is replaced when told to",
        _rename_replacing,
        same=lambda k: (k.at("taken.txt"), b"hello\n", False),
    ),
    Row(
        "a folder moved into itself is refused",
        _folder_into_itself,
        same=lambda k: (
            "refused",
            400,
            "A folder cannot be placed inside itself",
            "cannot_replace",
        ),
    ),
    Row(
        "a path with .. is normalised",
        lambda f, k: f.path(k.at("sub/../a.txt")),
        same=lambda k: k.at("a.txt"),
    ),
    Row(
        # A local path is resolved through its links; a remote one is kept as given.
        "a link that leaves the folder",
        lambda f, k: _link_out(f, k),
        differs={
            "local": lambda k: (str(k.outside / "secret.txt"), "secret\n"),
            "remote": lambda k: ("/link_out", "secret\n"),
        },
    ),
    Row(
        # Both answer 404 with the system's words; the local box names the path as sent.
        "a file gone between the stat and the read",
        _gone_before_the_read,
        differs={
            "local": lambda k: ("answered", 404, f"No such file or directory: {k.at('a.txt')}"),
            "remote": lambda k: ("answered", 404, "No such file or directory"),
        },
    ),
    Row(
        "a name with a space, a quote and a newline",
        _odd_name,
        same=lambda k: (k.at(ODD_NAME), (True, False, True), b""),
    ),
    Row("~ is the home", lambda f, k: f.path("~"), same=lambda k: k.home),
    Row("~/x is in the home", lambda f, k: f.path("~/x"), same=lambda k: f"{k.home}/x"),
    Row(
        # A new file on a taken name: the local /touch answers 400, the remote one 409.
        "a new file on a name that exists is refused",
        lambda f, k: _touch(f, k, "taken.txt"),
        differs={
            "local": lambda k: ("refused", 400, "File already exists", "exists"),
            "remote": lambda k: ("refused", 409, "File already exists", "exists"),
        },
    ),
    Row("an upload replaces when told to", _upload_replacing, same=lambda k: b"new"),
    Row(
        "a folder that exists is not made again",
        lambda f, k: _make_folder(f, k, "sub"),
        same=lambda k: ("answered", 400, "Directory already exists"),
    ),
    Row(
        # Both answer 404 with the system's words (the /delete route gives its own text,
        # "File not found"); the local box names the path as sent.
        "a missing file is not deleted",
        lambda f, k: _delete(f, k, "nope"),
        differs={
            "local": lambda k: ("answered", 404, f"No such file or directory: {k.at('nope')}"),
            "remote": lambda k: ("answered", 404, "No such file or directory"),
        },
    ),
    Row("a mode is set", _chmod, same=lambda k: 0o600),
    Row("text is written and read back", _write_and_read, same=lambda k: "# hi\n"),
    Row(
        # A local download streams from disk; a remote one is read into memory.
        "a download",
        lambda f, k: _serve(f, k, 100),
        differs={
            "local": lambda k: ("streamed from", Path(k.at("a.txt"))),
            "remote": lambda k: ("in memory", b"hello\n", ATTACHMENT),
        },
    ),
    Row(
        # Streamed from disk, a local download has no limit.
        "a download past the limit",
        lambda f, k: _serve(f, k, 3),
        differs={
            "local": lambda k: ("streamed from", Path(k.at("a.txt"))),
            "remote": lambda k: ("answered", 413, "File too large to download via API"),
        },
    ),
    Row(
        "a source's mode, size and fingerprint",
        lambda f, k: _source(f, k, "a.txt"),
        same=lambda k: ((k.root / "a.txt").stat().st_mode, 6, _fingerprint(k, "a.txt")),
    ),
    Row(
        "a link as a source is what it names",
        lambda f, k: _source(f, k, "link_out"),
        same=lambda k: ((k.outside / "secret.txt").stat().st_mode, 7, _fingerprint(k, "link_out")),
    ),
    Row(
        "a box made ready still answers the same",
        _prepared,
        same=lambda k: ((k.root / "a.txt").stat().st_mode, 6, _fingerprint(k, "a.txt")),
    ),
    Row(
        "a file read in chunks to its end is its bytes",
        lambda f, k: _chunks(f, k, "a.txt", 4),
        same=lambda k: [b"hell", b"o\n"],
    ),
    Row(
        "a file larger than one chunk is read in full chunks and a short one",
        lambda f, k: _chunks(f, k, "big.bin", CHUNK),
        same=lambda k: [BIG[:CHUNK], BIG[CHUNK : 2 * CHUNK], BIG[2 * CHUNK :]],
    ),
    Row(
        "a 0-byte file read in chunks gives none",
        lambda f, k: _chunks(f, k, "empty.txt", CHUNK),
        same=lambda k: [],
    ),
    Row(
        "a committed landing is the file, with the source's bits, its folder made",
        _land_new,
        same=lambda k: (ODD_NAME, b"abc", 0o640, [ODD_NAME]),
    ),
    Row(
        "a committed landing replaces a file and keeps its bits",
        _land_replacing,
        same=lambda k: (True, 0o660, ["a.txt", "empty.txt", "link_out", "sub", "taken.txt"]),
    ),
    Row(
        "an aborted landing on a free name leaves nothing behind",
        lambda f, k: _land_aborted(f, k, "new.txt", False),
        same=lambda k: (["a.txt", "empty.txt", "link_out", "sub", "taken.txt"], b"old\n"),
    ),
    Row(
        "an aborted replace leaves the old file and no temp name",
        lambda f, k: _land_aborted(f, k, "taken.txt", True),
        same=lambda k: (["a.txt", "empty.txt", "link_out", "sub", "taken.txt"], b"old\n"),
    ),
    Row(
        "a landing on a name that exists is refused",
        lambda f, k: _land(f, k, "taken.txt", "a.txt", False),
        same=lambda k: ("refused", 409, "File already exists", "exists"),
    ),
    Row(
        "a landing for a folder is refused",
        lambda f, k: _land(f, k, "new/sub", "sub", False),
        same=lambda k: ("answered", 400, "Only files can be transferred"),
    ),
    Row(
        "an unchanged source is removed",
        lambda f, k: _remove_if_unchanged(f, k, False),
        same=lambda k: ("removed", False),
    ),
    Row(
        "a source that changed since its fingerprint is kept",
        lambda f, k: _remove_if_unchanged(f, k, True),
        same=lambda k: (
            500,
            "Copied to elsewhere/a.txt, but the source changed during the copy and was kept",
            True,
        ),
    ),
    Row(
        # A remote path is checked and kept as spelled; its `~` is resolved on the box.
        "a path is checked before the box is reached and resolved after",
        lambda f, k: _checked_then_resolved(f, k, "~/x"),
        differs={
            "local": lambda k: (f"{k.home}/x", f"{k.home}/x"),
            "remote": lambda k: ("~/x", f"{k.home}/x"),
        },
    ),
    Row(
        "a name joined to a folder and taken back",
        _name_of_joined,
        same=lambda k: ODD_NAME,
    ),
    Row(
        "a name that is no archive is not unpacked",
        lambda f, k: f.extract_archive(k.at("a.txt"), k.at("sub")),
        same=lambda k: ("answered", 400, "Unsupported archive format"),
    ),
    Row(
        # A remote box checks every source before it is reached and names the bad one;
        # the local box refuses it with the same check's text.
        "an archive with a source path holding a null byte",
        lambda f, k: _archive_of(f, k, [k.at("a.txt"), "/x\x00y"]),
        differs={
            "local": lambda k: ("answered", 400, "Path cannot contain null bytes"),
            "remote": lambda k: (
                "answered",
                400,
                "Invalid path /x\x00y: Path cannot contain null bytes",
            ),
        },
    ),
    Row(
        "an archive into a destination holding a null byte",
        lambda f, k: f.create_archive([k.at("a.txt")], "/x\x00y", "o.zip", "zip"),
        same=lambda k: ("answered", 400, "Path cannot contain null bytes"),
    ),
    Row(
        "a batch delete removes a file and fails a missing path",
        _delete_many,
        same=lambda k: (
            [k.at("a.txt")],
            [k.at("nope")],
            ["empty.txt", "link_out", "sub", "taken.txt"],
        ),
    ),
    Row(
        "a batch copy places a free name and refuses a taken one",
        _copy_many,
        same=lambda k: (
            [k.at("a.txt")],
            [{"path": k.at("taken.txt"), "error": "File already exists", "reason": "exists"}],
        ),
    ),
]


@pytest.mark.asyncio
@pytest.mark.parametrize("row", ROWS, ids=[row.name for row in ROWS])
async def test_every_kind_of_box_keeps_the_contract(kind: Kind, row: Row) -> None:
    assert await _outcome(kind, row.operation) == row.expected(kind)


# --- the folder size and the folder zip: commands recorded, never run ----------------


class _Result(SimpleNamespace):
    pass


class RecordingConnection:
    """A connection whose commands are recorded and answered from ``replies``; nothing
    is run. Its SFTP client is ``sftp``."""

    def __init__(self, replies: dict[str, _Result], sftp: object = None) -> None:
        self.replies = replies
        self.commands: list[str] = []
        self.sftp = sftp
        self.sftp_opened = 0

    async def run(self, cmd: str, check: bool = False, encoding: str | None = "utf-8") -> _Result:
        self.commands.append(cmd)
        if cmd == HEALTH_CMD:
            return _Result(stdout="test\n", stderr="", exit_status=0)
        return self.replies[cmd]

    async def start_sftp_client(self):
        self.sftp_opened += 1
        if isinstance(self.sftp, BaseException):
            raise self.sftp
        return self.sftp

    def close(self) -> None:
        pass


class _StatSFTP:
    """An SFTP client that finds every path with ``mode``."""

    def __init__(self, mode: int) -> None:
        self.mode = mode

    async def stat(self, path: str) -> SimpleNamespace:
        return SimpleNamespace(permissions=self.mode)

    def exit(self) -> None:
        pass


def _remote_box() -> Box:
    return Box(name="rec", connect_host="example.invalid", display_host="rec", user="tester")


def _recording(monkeypatch: pytest.MonkeyPatch, connection: RecordingConnection) -> object:
    monkeypatch.setattr(ssh_pool, "_global_pool", SSHConnectionPool())

    async def connect_for_box(box: Box, application_config: object):
        return connection

    return SimpleNamespace(connect_for_box=connect_for_box)


@pytest.mark.asyncio
async def test_a_remote_folder_size_runs_du_on_the_quoted_path(monkeypatch):
    path = f"/srv/{ODD_NAME}"
    du = shlex.join(["du", "-sb", path])
    connection = RecordingConnection({du: _Result(stdout=f"4096\t{path}\n", exit_status=0)})
    deps = _recording(monkeypatch, connection)
    async with box_files(deps, _remote_box(), CONFIG) as files:
        size = await files.folder_size(await files.path(path))
    assert size == 4096
    assert shlex.split(connection.commands[0]) == ["du", "-sb", path]
    assert connection.commands[1:] == [HEALTH_CMD]
    assert connection.sftp_opened == 0


@pytest.mark.asyncio
async def test_a_local_folder_size_counts_the_bytes_of_its_files(tmp_path):
    root = tmp_path / "tree"
    _build_tree(root, tmp_path / "outside")
    async with box_files(SimpleNamespace(), _local_box(), CONFIG) as files:
        size = await files.folder_size(await files.path(str(root)))
    # 6 + 0 + 3 + 4 bytes, and the 7 of the file the link names (a link to a file is
    # counted as the file).
    assert size == 20


class _Stream:
    def __init__(self, chunks: list[bytes]) -> None:
        self.chunks = list(chunks)

    async def read(self, n: int) -> bytes:
        return self.chunks.pop(0) if self.chunks else b""


class _Process:
    def __init__(self, stdout: bytes) -> None:
        self.stdout = _Stream([stdout])
        self.stderr = _Stream([])

    async def wait(self) -> _Result:
        return _Result(exit_status=0)

    def kill(self) -> None:
        pass

    def close(self) -> None:
        pass


@pytest.mark.asyncio
async def test_a_remote_folder_zip_runs_zip_in_the_parent_on_the_quoted_name(monkeypatch):
    connection = RecordingConnection({})
    started: list[str] = []

    async def create_process(cmd: str, encoding=None) -> _Process:
        started.append(cmd)
        return _Process(b"PK-zip-bytes")

    connection.create_process = create_process  # type: ignore[attr-defined]
    deps = _recording(monkeypatch, connection)
    async with box_files(deps, _remote_box(), CONFIG) as files:
        zipped = await files.zip_folder(f"/srv/{ODD_NAME}")
    try:
        assert zipped.name == ODD_NAME
        assert Path(zipped.path).read_bytes() == b"PK-zip-bytes"
    finally:
        Path(zipped.path).unlink()
    words = shlex.split(started[0])
    assert words == ["cd", "/srv", "&&", "zip", "-r", "-q", "-", ODD_NAME]
    assert connection.commands == [HEALTH_CMD]


class _FolderSFTP:
    """An SFTP client that finds a folder at every path."""

    async def stat(self, path: str) -> SimpleNamespace:
        return SimpleNamespace(permissions=0o040755)

    def exit(self) -> None:
        pass


@pytest.mark.asyncio
async def test_a_remote_batch_delete_removes_a_folder_with_rm_on_the_quoted_path(monkeypatch):
    path = f"/srv/{ODD_NAME}"
    rm = shlex.join(["rm", "-rf", path])
    connection = RecordingConnection({rm: _Result(stdout="", exit_status=0)}, _FolderSFTP())
    deps = _recording(monkeypatch, connection)
    async with box_files(deps, _remote_box(), CONFIG, answers=None) as files:
        outcome = await files.delete_many([path])
    assert (outcome.succeeded, outcome.failed) == ([path], [])
    assert shlex.split(connection.commands[0]) == ["rm", "-rf", path]
    assert connection.commands[1:] == [HEALTH_CMD]


def _local_box() -> Box:
    box = Box(name="local", connect_host="localhost", display_host="localhost", user="me")
    box.transport = "local"
    return box


@pytest.mark.asyncio
async def test_a_local_folder_zip_holds_each_file_under_its_relative_name(tmp_path):
    root = tmp_path / "tree"
    _build_tree(root, tmp_path / "outside")
    async with box_files(SimpleNamespace(), _local_box(), CONFIG) as files:
        zipped = await files.zip_folder(str(root))
    try:
        with zipfile.ZipFile(zipped.path) as archive:
            names = sorted(archive.namelist())
    finally:
        Path(zipped.path).unlink()
    assert (zipped.name, names) == (
        "tree",
        ["a.txt", "empty.txt", "link_out", "sub/inner.txt", "taken.txt"],
    )


# --- archives: a remote command recorded, never run; a local one made and unpacked ---


class _PlacingSFTP:
    """An SFTP client for an archive: the temp file is there, the archive's name is free,
    and what it is asked to do is recorded."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, ...]] = []

    async def lstat(self, path: str) -> SimpleNamespace:
        self.calls.append(("lstat", path))
        if not path.endswith(".t.sshler"):
            raise asyncssh.SFTPNoSuchFile("No such file")
        return SimpleNamespace(permissions=0o100644, size=0, mtime=0, mtime_ns=0)

    async def link(self, source: str, target: str) -> None:
        self.calls.append(("link", source, target))

    async def remove(self, path: str) -> None:
        self.calls.append(("remove", path))

    def exit(self) -> None:
        pass


def _fixed_temp_name(monkeypatch: pytest.MonkeyPatch) -> None:
    def temp(target: str, suffix: str = ".sshler") -> str:
        parent, name = target.rsplit("/", 1)
        return f"{parent}/.{name}.t.sshler"

    monkeypatch.setattr(remote_files_api, "_remote_temp_beside", temp)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("fmt", "name", "words"),
    [
        (
            "tar.gz",
            "o.tar.gz",
            [
                "tar",
                "czf",
                "/home/out/.o.tar.gz.t.sshler",
                "-C",
                "/",
                f"/srv/{ODD_NAME}",
                "/home/x",
            ],
        ),
        (
            "zip",
            "o.zip",
            [
                "cd",
                "/",
                "&&",
                "zip",
                "-r",
                "/home/out/.o.zip.t.sshler",
                f"/srv/{ODD_NAME}",
                "/home/x",
            ],
        ),
    ],
)
async def test_a_remote_archive_is_made_by_one_command_on_the_quoted_paths(
    monkeypatch, fmt, name, words
):
    _fixed_temp_name(monkeypatch)
    home = 'printf %s "$HOME"'
    sftp = _PlacingSFTP()
    connection = RecordingConnection({home: _Result(stdout="/home", exit_status=0)}, sftp)
    # The zip runs after a `cd /`: its `&&` is the one word left unquoted.
    made = shlex.join(words) if fmt == "tar.gz" else "cd / && " + shlex.join(words[3:])
    connection.replies[made] = _Result(stdout="", exit_status=0)
    deps = _recording(monkeypatch, connection)
    async with box_files(deps, _remote_box(), CONFIG) as files:
        target = await files.create_archive([f"/srv/{ODD_NAME}", "~/x"], "~/out", name, fmt)
    assert target == f"/home/out/{name}"
    assert connection.commands == [home, made, HEALTH_CMD]
    assert shlex.split(made) == words
    # The archive takes its name only now: the temp file is linked to it and removed.
    assert sftp.calls[-2:] == [
        ("link", f"/home/out/.{name}.t.sshler", f"/home/out/{name}"),
        ("remove", f"/home/out/.{name}.t.sshler"),
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("archive", "words"),
    [
        (
            f"/srv/{ODD_NAME}.TGZ",
            ["tar", "xzf", f"/srv/{ODD_NAME}.TGZ", "-C", f"/home/d/.{ODD_NAME}.TGZ.t.sshler"],
        ),
        ("-a.zip", ["unzip", "-o", "./-a.zip", "-d", "/home/d/.-a.zip.t.sshler"]),
    ],
)
async def test_a_remote_archive_is_unpacked_by_one_command_on_the_quoted_paths(
    monkeypatch, archive, words
):
    _fixed_temp_name(monkeypatch)
    home = 'printf %s "$HOME"'
    unpack = shlex.join(words)
    sftp = _UnpackingSFTP()
    connection = RecordingConnection(
        {home: _Result(stdout="/home", exit_status=0), unpack: _Result(stdout="", exit_status=0)},
        sftp,
    )
    deps = _recording(monkeypatch, connection)
    async with box_files(deps, _remote_box(), CONFIG) as files:
        destination = await files.extract_archive(archive, "~/d")
    assert destination == "/home/d"
    assert connection.commands == [home, unpack, HEALTH_CMD]
    # The command unpacked into a temp folder of its own, which is gone at the end.
    temp = words[-1]
    assert sftp.calls == [("mkdir", temp), ("readdir", temp), ("readdir", temp), ("rmdir", temp)]


class _UnpackingSFTP(_StatSFTP):
    """An SFTP client for an extract: the archive is a file, ``/home/d`` a folder, and
    the unpacked folder stays empty (the command is not run)."""

    def __init__(self) -> None:
        super().__init__(0o100644)
        self.calls: list[tuple[str, str]] = []

    async def lstat(self, path: str) -> SimpleNamespace:
        if path != "/home/d":
            raise asyncssh.SFTPNoSuchFile("No such file")
        return SimpleNamespace(permissions=0o040755)

    async def mkdir(self, path: str, attrs: object = None) -> None:
        self.calls.append(("mkdir", path))

    async def readdir(self, path: str) -> list:
        self.calls.append(("readdir", path))
        return []

    async def rmdir(self, path: str) -> None:
        self.calls.append(("rmdir", path))


@pytest.mark.asyncio
async def test_a_remote_archive_past_its_limit_is_504(monkeypatch):
    class Slow(RecordingConnection):
        async def run(self, cmd: str, check: bool = False, encoding: str | None = "utf-8"):
            if cmd.startswith("tar "):
                await asyncio.sleep(5)
            return await super().run(cmd, check, encoding)

    _fixed_temp_name(monkeypatch)
    sftp = _PlacingSFTP()
    connection = Slow({}, sftp)
    monkeypatch.setattr(remote_files_api, "ARCHIVE_TIMEOUT", 0.05)
    deps = _recording(monkeypatch, connection)
    with pytest.raises(HTTPException) as answered:
        async with box_files(deps, _remote_box(), CONFIG) as files:
            await files.create_archive(["/srv/a"], "/srv", "o.tgz", "tgz")
    assert (answered.value.status_code, answered.value.detail) == (
        504,
        "Archive creation timed out",
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(("fmt", "name"), [("tgz", "o.tgz"), ("zip", "o.zip")])
async def test_a_local_archive_packs_each_path_under_its_name_and_unpacks_it(tmp_path, fmt, name):
    root = tmp_path / "tree"
    _build_tree(root, tmp_path / "outside")
    out = tmp_path / "out"
    out.mkdir()
    async with box_files(SimpleNamespace(), _local_box(), CONFIG) as files:
        made = await files.create_archive(
            [str(root / "a.txt"), str(root / "sub")], str(out), name, fmt
        )
        unpacked = await files.extract_archive(made, str(out / "x"))
    assert (made, unpacked) == (str(out.resolve() / name), str(out.resolve() / "x"))
    found = sorted(str(p.relative_to(out / "x")) for p in (out / "x").rglob("*"))
    assert found == ["a.txt", "sub", "sub/inner.txt"]
    assert (out / "x" / "sub" / "inner.txt").read_bytes() == b"in\n"


@pytest.mark.asyncio
async def test_a_local_archive_that_does_not_exist_is_404(tmp_path):
    with pytest.raises(HTTPException) as answered:
        async with box_files(
            SimpleNamespace(), _local_box(), CONFIG, archive_api._ARCHIVE
        ) as files:
            await files.extract_archive(str(tmp_path / "nope.zip"), str(tmp_path))
    assert (answered.value.status_code, answered.value.detail) == (404, "Archive not found")


# --- remote only: a connection that drops, an SFTP session that will not open --------


class _DroppingSFTP:
    """An SFTP client whose connection drops at the first request."""

    def __init__(self) -> None:
        self.exits = 0

    async def remove(self, path: str) -> None:
        raise SSHError("connection lost")

    def exit(self) -> None:
        self.exits += 1


@pytest.mark.asyncio
async def test_a_connection_that_drops_in_the_block_is_502_and_the_session_closes_once(
    monkeypatch,
):
    sftp = _DroppingSFTP()
    connection = RecordingConnection({}, sftp)
    deps = _recording(monkeypatch, connection)
    with pytest.raises(HTTPException) as answered:
        async with box_files(deps, _remote_box(), CONFIG) as files:
            await files.delete(await files.path("/srv/a.txt"))
    assert (answered.value.status_code, answered.value.detail) == (502, "connection lost")
    assert (connection.sftp_opened, sftp.exits) == (1, 1)


@pytest.mark.asyncio
async def test_an_sftp_session_that_fails_to_open_is_a_500_and_nothing_is_written(monkeypatch):
    refused = asyncssh.ChannelOpenError(asyncssh.OPEN_ADMINISTRATIVELY_PROHIBITED, "no sftp")
    connection = RecordingConnection({}, refused)
    deps = _recording(monkeypatch, connection)
    with pytest.raises(HTTPException) as answered:
        async with box_files(deps, _remote_box(), CONFIG) as files:
            await files.write_text(await files.path("/srv/a.txt"), "x")
    assert (answered.value.status_code, answered.value.detail) == (500, "no sftp")
    assert connection.sftp_opened == 1
    # The connection went back to the pool through its health check.
    assert connection.commands == [HEALTH_CMD]


# --- no route asks which kind of box it has ------------------------------------------


def test_the_file_routes_hold_no_test_of_the_kind_of_box():
    """Only ``box_files`` tells the kinds apart; the Excel preview and the archives
    read and write through it too. search.py keeps one in ``search_box`` (zoxide and a
    local walk on this machine, the frecency table and ``find`` over SSH on a remote
    box). The command routes run through
    ``box_runner``, and git.py and the stats stream hold no test; grep.py keeps one for
    the in-process search of a Windows local box (named in its module's docstring);
    boxes.py keeps one in ``box_stats`` (psutil on this machine, ``/proc`` through the
    runner on a remote box), and names the field once more when it lists a box. The
    session routes run tmux through the runner; sessions.py keeps two: the live
    sessions of a box (the local box scans its own sockets, a remote box runs one
    script) and the force kill, which is built for the local box only."""
    api = Path(sshler.api.__file__).parent

    def tests_of_the_kind(name: str) -> int:
        text = (api / name).read_text(encoding="utf-8")
        return text.count("box.transport") + text.count("is_local(")

    found = {
        name: tests_of_the_kind(name)
        for name in (
            "files.py",
            "batch.py",
            "excel.py",
            "archive.py",
            "search.py",
            "transfer.py",
            "git.py",
            "grep.py",
            "boxes.py",
            "stats_stream.py",
            "sessions.py",
        )
    }
    assert found == {
        "files.py": 0,
        "batch.py": 0,
        "excel.py": 0,
        "archive.py": 0,
        "search.py": 1,
        "transfer.py": 0,
        "git.py": 0,
        "grep.py": 1,
        "boxes.py": 2,
        "stats_stream.py": 0,
        "sessions.py": 2,
    }


def test_a_transfer_holds_no_sftp_client():
    """A transfer reads and writes through the files of each box only."""
    text = (Path(sshler.api.__file__).parent / "transfer.py").read_text(encoding="utf-8")
    assert text.lower().count("sftp") == 0


# --- round trips of a remote listing, a rename and a preview -------------------------


@dataclass
class Counted:
    client: TestClient
    root: Path
    counts: Counts


@pytest.fixture
def counted(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Counted]:
    root = tmp_path / "remote"
    (root / "home").mkdir(parents=True)
    (root / "home" / "a.txt").write_text("hello\n", encoding="utf-8")
    (root / "home" / "dot.png").write_bytes(b"\x89PNG small")
    (root / "home" / "big.png").write_bytes(b"\x89PNG" + b"x" * (2 * 1024 * 1024))
    keys = tmp_path / "keys"
    keys.mkdir()
    counts = Counts()
    server = ServerThread(root, keys, counts, "/home")
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    box = {
        "name": "lb",
        "host": "127.0.0.1",
        "user": "tester",
        "port": server.port,
        "keyfile": str(server.client_key),
        "known_hosts": str(server.known_hosts),
    }
    # A second name for the same server: a transfer between two boxes that are one.
    twin = {**box, "name": "lb2"}
    (config_dir / "boxes.yaml").write_text(yaml.safe_dump({"boxes": [box, twin]}), encoding="utf-8")
    ssh_config = tmp_path / "ssh_config"
    ssh_config.write_text("", encoding="utf-8")
    monkeypatch.setenv("SSHLER_CONFIG_DIR", str(config_dir))
    monkeypatch.setenv("SSHLER_SSH_CONFIG", str(ssh_config))
    monkeypatch.setattr(ssh_pool, "_global_pool", SSHConnectionPool())
    state.initialize(config_dir)
    client = TestClient(make_app(ServerSettings(csrf_token=TOKEN)))
    try:
        yield Counted(client=client, root=root, counts=counts)
    finally:
        client.close()
        state.reset_state()
        server.close()


def _get(counted: Counted, route: str, **params: str):
    return counted.client.get(f"/api/v1/boxes/lb/{route}", params=params, headers=HEADERS)


def test_a_remote_upload_that_cannot_connect_is_not_answered_as_a_bad_name(counted, monkeypatch):
    """The upload route's 400 is for a name the local box refuses. A remote box whose
    connection fails with a ``ValueError`` (not a name's fault) is a fault nobody named:
    a 500 ``internal`` that names the type and the text."""

    async def refuse(*args: object, **kwargs: object) -> None:
        raise ValueError("host name cannot be encoded")

    monkeypatch.setattr(asyncssh, "connect", refuse)
    resp = counted.client.post(
        "/api/v1/boxes/lb/upload",
        data={"directory": "/home"},
        files={"file": ("n.txt", b"x")},
        headers=HEADERS,
    )
    assert (resp.status_code, resp.json()) == (
        500,
        {"detail": "ValueError: host name cannot be encoded", "reason": "internal"},
    )
    assert not (counted.root / "home" / "n.txt").exists()


def test_a_remote_listing_opens_one_session_and_runs_only_the_health_check(counted):
    resp = _get(counted, "ls", directory="/home")
    assert resp.status_code == 200
    assert [e["name"] for e in resp.json()["entries"]] == ["a.txt", "big.png", "dot.png"]
    assert (counted.counts.connections, counted.counts.sftp_sessions) == (1, 1)
    assert counted.counts.commands == [HEALTH_CMD]


def test_a_remote_listing_at_the_home_asks_for_the_home_once(counted):
    resp = _get(counted, "ls", directory="~")
    assert (resp.status_code, resp.json()["directory"]) == (200, "/home")
    assert (counted.counts.connections, counted.counts.sftp_sessions) == (1, 1)
    assert counted.counts.commands == [HOME_CMD, HEALTH_CMD]


def test_a_remote_rename_opens_one_session(counted):
    resp = counted.client.post(
        "/api/v1/boxes/lb/rename",
        json={"path": "~/a.txt", "new_name": "b.txt"},
        headers=HEADERS,
    )
    assert (resp.status_code, resp.json()["path"]) == (200, "/home/b.txt")
    assert (counted.root / "home" / "b.txt").read_text(encoding="utf-8") == "hello\n"
    assert (counted.counts.connections, counted.counts.sftp_sessions) == (1, 1)
    assert counted.counts.commands == [HOME_CMD, HEALTH_CMD]


@pytest.mark.parametrize(
    ("name", "sessions"),
    [
        ("a.txt", 1),  # the text
        ("dot.png", 1),  # the image's bytes
        # An image past the preview limit is read as bytes, then as text: two sessions,
        # one per read, as each read opens its own.
        ("big.png", 2),
    ],
)
def test_a_remote_preview_opens_one_session_per_read(counted, name, sessions):
    resp = _get(counted, "file", path=f"/home/{name}")
    assert (resp.status_code, resp.json()["path"]) == (200, f"/home/{name}")
    assert (counted.counts.connections, counted.counts.sftp_sessions) == (1, sessions)
    assert counted.counts.commands == [HEALTH_CMD]


def test_a_remote_listing_records_the_visit_and_a_failed_one_does_not(counted):
    listed = _get(counted, "ls", directory="/home")
    missing = _get(counted, "ls", directory="/nope")
    assert (listed.status_code, missing.status_code) == (200, 404)
    assert [path for path, _ in state.search_directories("lb", "")] == ["/home"]


# --- round trips of a transfer, and where a box that cannot be reached fails ----------


def _transfer(counted: Counted, src: str, dest: str, paths: list[str], destination: str):
    resp = counted.client.post(
        "/api/v1/transfer",
        json={"src_box": src, "dest_box": dest, "paths": paths, "destination": destination},
        headers=HEADERS,
    )
    assert resp.status_code == 200, resp.text
    return transfer_events(resp.text)


def test_a_transfer_between_two_remote_boxes_takes_one_connection_and_one_session_each(counted):
    """Two names for one server: each box has its own pooled connection and session."""
    events = _transfer(counted, "lb", "lb2", ["/home/a.txt", "/home/dot.png"], "/home/in")
    assert events[-1] == ("done", {"succeeded": ["/home/a.txt", "/home/dot.png"], "failed": []})
    assert (counted.root / "home" / "in" / "a.txt").read_bytes() == b"hello\n"
    assert (counted.root / "home" / "in" / "dot.png").read_bytes() == b"\x89PNG small"
    assert (counted.counts.connections, counted.counts.sftp_sessions) == (2, 2)
    assert counted.counts.commands == [HEALTH_CMD, HEALTH_CMD]


def test_a_transfer_from_a_remote_box_to_the_local_box_takes_one_connection_and_session(
    counted, tmp_path
):
    landing = tmp_path / "landing"
    events = _transfer(counted, "lb", "local", ["/home/a.txt", "/home/dot.png"], str(landing))
    assert events[-1] == ("done", {"succeeded": ["/home/a.txt", "/home/dot.png"], "failed": []})
    assert sorted(p.name for p in landing.iterdir()) == ["a.txt", "dot.png"]
    assert (counted.counts.connections, counted.counts.sftp_sessions) == (1, 1)
    assert counted.counts.commands == [HEALTH_CMD]


@pytest.mark.parametrize("remote_side", ["source", "destination"])
def test_a_transfer_whose_sftp_session_cannot_open_is_one_error_before_any_item(
    counted, tmp_path, monkeypatch, remote_side
):
    """The remote side's session is opened before the first item, so its failure is
    the one event of the request and no item is tried."""
    local = tmp_path / "local_side"
    local.mkdir()
    (local / "x.txt").write_bytes(b"x\n")

    async def refuse(self):
        raise asyncssh.ChannelOpenError(asyncssh.OPEN_ADMINISTRATIVELY_PROHIBITED, "no sftp")

    monkeypatch.setattr(asyncssh.SSHClientConnection, "start_sftp_client", refuse)
    if remote_side == "source":
        events = _transfer(counted, "lb", "local", ["/home/a.txt", "/home/dot.png"], str(local))
    else:
        events = _transfer(counted, "local", "lb", [str(local / "x.txt")], "/home/in")
    assert events == [("error", {"detail": "no sftp", "reason": "failed"})]
    assert sorted(p.name for p in local.iterdir()) == ["x.txt"]
    assert not (counted.root / "home" / "in").exists()


@pytest.mark.parametrize(
    ("raised", "body"),
    [
        (
            SSHError("lb: connection refused"),
            {"detail": "lb: connection refused", "reason": "unreachable"},
        ),
        (
            ConnectionError("lb: unreachable (cached)"),
            {"detail": "lb: unreachable (cached)", "reason": "unreachable"},
        ),
        (
            TimeoutError("lb: the connect timed out"),
            {"detail": "lb: the connect timed out", "reason": "timeout"},
        ),
    ],
    ids=["ssh-error", "connection-error", "timeout"],
)
def test_a_transfer_whose_source_box_cannot_be_reached_ends_with_its_reason(
    counted, tmp_path, monkeypatch, raised, body
):
    """The stream has begun when the source box is reached, so its failure is the one
    ``error`` event, with the text and reason a failed request would answer."""
    landing = tmp_path / "landing"
    landing.mkdir()

    async def unreachable(self, box, application_config):
        raise raised

    monkeypatch.setattr(APIDependencies, "connect_for_box", unreachable)
    events = _transfer(counted, "lb", "local", ["/home/a.txt"], str(landing))
    assert events == [("error", body)]
    assert sorted(landing.iterdir()) == []


@pytest.mark.parametrize(
    ("src", "dest", "paths", "destination", "detail"),
    [
        (
            "lb",
            "local",
            ["/home/a.txt", "/home/a\x00b"],
            "",
            "Invalid path /home/a\x00b: Path cannot contain null bytes",
        ),
        ("local", "lb", ["/tmp/x"], "", "Invalid destination: Path cannot be empty"),
    ],
)
def test_a_bad_transfer_path_is_a_400_before_any_connection(
    counted, src, dest, paths, destination, detail
):
    resp = counted.client.post(
        "/api/v1/transfer",
        json={"src_box": src, "dest_box": dest, "paths": paths, "destination": destination},
        headers=HEADERS,
    )
    assert (resp.status_code, resp.json()) == (400, {"detail": detail, "reason": "invalid"})
    assert (counted.counts.connections, counted.counts.sftp_sessions) == (0, 0)


@pytest.mark.parametrize(
    ("change", "status", "detail"),
    [
        ({"dest_box": "lb"}, 400, "Use batch copy/move for same-box transfers"),
        ({"paths": []}, 400, "No paths provided"),
        ({"mode": "swap"}, 400, "mode must be 'copy' or 'move'"),
        (
            {"paths": [f"/home/{n}.txt" for n in range(101)]},
            422,
            "body.paths: List should have at most 100 items after validation, not 101",
        ),
    ],
    ids=["same-box", "no-paths", "bad-mode", "too-many-paths"],
)
def test_a_refused_transfer_answers_invalid_with_its_text_before_any_connection(
    counted, change, status, detail
):
    body = {"src_box": "lb", "dest_box": "local", "paths": ["/home/a.txt"], "destination": "/x"}
    resp = counted.client.post("/api/v1/transfer", json={**body, **change}, headers=HEADERS)
    assert (resp.status_code, resp.json()) == (status, {"detail": detail, "reason": "invalid"})
    assert (counted.counts.connections, counted.counts.sftp_sessions) == (0, 0)


@pytest.mark.parametrize("overwrite", [False, True])
def test_a_remote_write_that_fails_half_way_leaves_no_file_and_no_temp_name(
    counted, tmp_path, monkeypatch, overwrite
):
    local = tmp_path / "local_side"
    local.mkdir()
    (local / "big.bin").write_bytes(BIG)
    (counted.root / "home" / "in").mkdir()
    if overwrite:
        (counted.root / "home" / "in" / "big.bin").write_bytes(b"old")
    real_write = remote_files_api._RemoteLanding.write
    writes = [0]

    async def write(self, data):
        writes[0] += 1
        if writes[0] == 2:
            raise OSError("disk full")
        await real_write(self, data)

    monkeypatch.setattr(remote_files_api._RemoteLanding, "write", write)
    sent = str(local / "big.bin")
    resp = counted.client.post(
        "/api/v1/transfer",
        json={
            "src_box": "local",
            "dest_box": "lb",
            "paths": [sent],
            "destination": "/home/in",
            "overwrite": overwrite,
        },
        headers=HEADERS,
    )
    entry = {"path": sent, "error": "disk full", "reason": "failed"}
    progress = {"file": sent, "bytes_done": CHUNK, "bytes_total": len(BIG), "index": 0, "total": 1}
    transfer_events(resp.text)
    assert resp.text == (
        f"event: progress\ndata: {json.dumps(progress)}\n\n"
        f"event: failed\ndata: {json.dumps(entry)}\n\n"
        f"event: done\ndata: {json.dumps({'succeeded': [], 'failed': [entry]})}\n\n"
    )
    left = {p.name: p.read_bytes() for p in (counted.root / "home" / "in").iterdir()}
    assert left == ({"big.bin": b"old"} if overwrite else {})
