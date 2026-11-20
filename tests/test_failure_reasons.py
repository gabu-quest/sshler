"""The reason of every failure of a file operation, the same on both kinds of box.

One table of rows (operation, the failure made to happen, the expected status and
reason), run against the local box and the loopback remote box of
``test_box_files_contract.py``. Paths are test data: every file and folder is made with
``pathlib`` inside the test's temporary folder; a folder made unreadable there gets its
mode back before the folder is removed.
"""

# ruff: noqa: F811  (the parameters named `kind` are the fixture imported below)
from __future__ import annotations

import asyncio
import contextlib
import os
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from types import SimpleNamespace

import asyncssh
import pytest
import pytest_asyncio
from fastapi import HTTPException
from test_box_files_contract import (  # noqa: F401  (`kind` is a fixture)
    CONFIG,
    Kind,
    _open_local,
    kind,
)

from sshler import ssh_pool
from sshler.api import remote as remote_api
from sshler.api.box_files import box_files
from sshler.config import Box
from sshler.ssh import connect
from sshler.ssh_pool import SSHConnectionPool

pytestmark = pytest.mark.posix_only("chmod, pipes and an SFTP server rooted in a temp folder")

LOCKED = "locked"  # a folder of mode 0 holding `x.txt`


@pytest.fixture
def locked(kind: Kind):
    """``locked/x.txt`` under the tree, the folder of mode 0 while the test runs."""
    folder = kind.root / LOCKED
    folder.mkdir()
    (folder / "x.txt").write_bytes(b"x")
    folder.chmod(0)
    try:
        try:
            os.listdir(folder)
        except PermissionError:
            pass
        else:
            pytest.skip("the system lets this user list a folder of mode 0 (root)")
        yield
    finally:
        folder.chmod(0o755)


async def _answer(kind: Kind, operation: Callable) -> tuple[int, str | None, str]:
    """The status, the reason and the text a ``box_files`` block answers the failure of
    ``operation`` with."""
    try:
        async with box_files(kind.deps, kind.box, CONFIG) as files:
            value = await operation(files, kind)
    except HTTPException as exc:
        return exc.status_code, getattr(exc, "reason", None), str(exc.detail)
    raise AssertionError(f"no failure: {value!r}")


async def _list(files, kind: Kind, rel: str):
    return await files.list_folder(await files.folder(kind.at(rel)))


async def _read(files, kind: Kind, rel: str):
    return await files.read_text(await files.path(kind.at(rel)), 1000)


async def _preview(files, kind: Kind, rel: str):
    return await files.read_bytes(await files.path(kind.at(rel)), 1000)


async def _write(files, kind: Kind, rel: str):
    return await files.write_text(await files.path(kind.at(rel)), "new")


async def _delete(files, kind: Kind, rel: str):
    return await files.delete(await files.path(kind.at(rel)))


async def _rename(files, kind: Kind, rel: str):
    return await files.rename(kind.at(rel), "renamed.txt", overwrite=False)


async def _stat(files, kind: Kind, rel: str):
    return await files.stat(await files.path(kind.at(rel)))


async def _chmod(files, kind: Kind, rel: str):
    return await files.chmod(await files.path(kind.at(rel)), 0o600)


async def _mkdir(files, kind: Kind, rel: str):
    return await files.make_folder(await files.path(kind.at(rel)))


async def _folder_size(files, kind: Kind, rel: str):
    return await files.folder_size(await files.path(kind.at(rel)))


async def _extract(files, kind: Kind, rel: str):
    return await files.extract_archive(kind.at(rel), kind.at(""))


def _path_as(sent: str) -> Callable:
    """An operation on a path sent as ``sent`` itself (not a path of the tree)."""

    async def read(files, kind: Kind):
        return await files.read_text(await files.path(sent), 1000)

    return read


@dataclass(frozen=True)
class Row:
    """One operation on one path of the tree, and the status and reason it answers;
    ``detail``, where set, is the exact text (``{at}`` is the path as the box names it)."""

    operation: Callable
    rel: str
    status: int
    reason: str
    detail: str | None = None
    locks: bool = False

    def run(self, files, kind: Kind):
        return self.operation(files, kind, self.rel)


ROWS = {
    # The thing is not there.
    "list a missing folder": Row(_list, "nope", 404, "not_found"),
    "read a missing file": Row(_read, "nope.txt", 404, "not_found"),
    "preview a missing file": Row(_preview, "nope.txt", 404, "not_found"),
    "write through a file": Row(_write, "a.txt/b.txt", 404, "not_found"),
    "delete a missing file": Row(_delete, "nope.txt", 404, "not_found"),
    "rename a missing file": Row(_rename, "nope.txt", 404, "not_found"),
    "chmod a missing file": Row(_chmod, "nope.txt", 404, "not_found"),
    "mkdir in a missing folder": Row(_mkdir, "nope/new", 404, "not_found"),
    "extract a missing archive": Row(_extract, "nope.zip", 404, "not_found"),
    # The system refuses access (a folder of mode 0 made by the test).
    "list a folder of mode 0": Row(_list, LOCKED, 403, "denied", locks=True),
    "read in a folder of mode 0": Row(_read, f"{LOCKED}/x.txt", 403, "denied", locks=True),
    "preview in a folder of mode 0": Row(_preview, f"{LOCKED}/x.txt", 403, "denied", locks=True),
    "write in a folder of mode 0": Row(_write, f"{LOCKED}/x.txt", 403, "denied", locks=True),
    "delete in a folder of mode 0": Row(_delete, f"{LOCKED}/x.txt", 403, "denied", locks=True),
    "rename in a folder of mode 0": Row(_rename, f"{LOCKED}/x.txt", 403, "denied", locks=True),
    "stat in a folder of mode 0": Row(_stat, f"{LOCKED}/x.txt", 403, "denied", locks=True),
    "chmod in a folder of mode 0": Row(_chmod, f"{LOCKED}/x.txt", 403, "denied", locks=True),
    "mkdir in a folder of mode 0": Row(_mkdir, f"{LOCKED}/new", 403, "denied", locks=True),
    "extract in a folder of mode 0": Row(_extract, f"{LOCKED}/a.zip", 403, "denied", locks=True),
    # A null byte, an empty path: refused by the path check before the box is asked.
    "read a path with a null byte": Row(
        _read, "a\x00b.txt", 400, "invalid", "Path cannot contain null bytes"
    ),
    "list a path with a null byte": Row(
        _list, "a\x00b", 400, "invalid", "Path cannot contain null bytes"
    ),
    "write a path with a null byte": Row(
        _write, "a\x00b.txt", 400, "invalid", "Path cannot contain null bytes"
    ),
    "delete a path with a null byte": Row(
        _delete, "a\x00b.txt", 400, "invalid", "Path cannot contain null bytes"
    ),
    "stat a path with a null byte": Row(
        _stat, "a\x00b.txt", 400, "invalid", "Path cannot contain null bytes"
    ),
    "folder size of a path with a null byte": Row(
        _folder_size, "a\x00b", 400, "invalid", "Path cannot contain null bytes"
    ),
    # A folder where a file is needed, and the reverse.
    "read a folder": Row(_read, "sub", 400, "invalid"),
    "preview a folder": Row(_preview, "sub", 400, "invalid"),
    "write over a folder": Row(_write, "sub", 400, "invalid"),
    "delete a folder as a file": Row(_delete, "sub", 400, "invalid"),
    "extract a folder": Row(_extract, "sub.zip", 400, "invalid"),
    "list a file": Row(_list, "a.txt", 400, "invalid", "Path is not a directory"),
}


@pytest.mark.asyncio
@pytest.mark.parametrize("row", ROWS.values(), ids=list(ROWS))
async def test_a_file_failure_has_one_reason_on_both_kinds(kind: Kind, row: Row, request):
    if row.locks:
        request.getfixturevalue("locked")
    (kind.root / "sub.zip").mkdir()
    status, reason, detail = await _answer(kind, row.run)
    assert (status, reason) == (row.status, row.reason)
    if row.detail is not None:
        assert detail == row.detail


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("sent", "detail"),
    [("", "Path cannot be empty"), ("a\x00b", "Path cannot contain null bytes")],
    ids=["empty", "null byte"],
)
async def test_a_path_refused_by_its_check_is_invalid_on_both_kinds(kind: Kind, sent, detail):
    status, reason, text = await _answer(kind, _path_as(sent))
    assert (status, reason, text) == (400, "invalid", detail)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("sent", "detail"),
    [("", "Path cannot be empty"), ("a\x00b", "Path cannot contain null bytes")],
    ids=["empty", "null byte"],
)
async def test_a_folder_refused_by_its_check_is_invalid_on_both_kinds(kind: Kind, sent, detail):
    async def folder(files, kind: Kind):
        return await files.folder(sent)

    assert await _answer(kind, folder) == (400, "invalid", detail)


@pytest.mark.asyncio
async def test_the_system_words_name_the_path_as_the_client_sent_it_on_the_local_box(kind: Kind):
    """The local box passes on the system's message and the path as sent; the remote
    box's SFTP server sends its words and no path."""
    status, reason, text = await _answer(kind, lambda f, k: _read(f, k, "sub/../nope.txt"))
    expected = (
        f"No such file or directory: {kind.at('sub/../nope.txt')}"
        if kind.name == "local"
        else "No such file or directory"
    )
    assert (status, reason, text) == (404, "not_found", expected)


# --- the local kind: a thing that is not a file --------------------------------------------


@pytest_asyncio.fixture
async def local(tmp_path) -> AsyncIterator[Kind]:
    opened = _open_local(tmp_path)
    yield await anext(opened)
    with contextlib.suppress(StopAsyncIteration):
        await anext(opened)


def _a_pipe(root) -> None:
    os.mkfifo(root / "thing")


def _a_link_to_a_device(root) -> None:
    (root / "thing").symlink_to("/dev/null")


@pytest.mark.asyncio
@pytest.mark.parametrize("make", [_a_pipe, _a_link_to_a_device], ids=["a pipe", "a device"])
async def test_a_thing_that_is_not_a_file_is_refused_before_it_is_opened(local: Kind, make):
    """Opening a pipe to read waits for a writer, so a download of one held a worker
    thread for ever; a device was served as a file."""
    make(local.root)

    async def serve(files, kind: Kind):
        return await files.serve(await files.path(kind.at("thing")), inline=False, limit=1000)

    assert await _answer(local, serve) == (400, "invalid", "Not a regular file")


# --- the remote kind: a box that cannot be reached, a dropped connection, a lookup past
# its limit ------------------------------------------------------------------------------


def _remote_box() -> Box:
    return Box(name="rb", connect_host="127.0.0.1", display_host="rb", user="tester")


async def _remote_answer(connect_for_box, operation: Callable) -> tuple[int, str | None, str]:
    previous, ssh_pool._global_pool = ssh_pool._global_pool, SSHConnectionPool()
    try:
        deps = SimpleNamespace(connect_for_box=connect_for_box)
        try:
            async with box_files(deps, _remote_box(), CONFIG) as files:
                value = await operation(files)
        except HTTPException as exc:
            return exc.status_code, getattr(exc, "reason", None), str(exc.detail)
        raise AssertionError(f"no failure: {value!r}")
    finally:
        await ssh_pool._global_pool.close_all()
        ssh_pool._global_pool = previous


async def _read_abs(files):
    return await files.read_text(await files.path("/srv/a.txt"), 100)


@pytest.mark.asyncio
async def test_a_remote_box_whose_failure_the_pool_remembers_is_unreachable():
    async def cached(box, application_config):
        raise ConnectionError("rb: unreachable (cached, retry in 42s)")

    answered = await _remote_answer(cached, _read_abs)
    assert answered == (502, "unreachable", "rb: unreachable (cached, retry in 42s)")


@pytest.mark.asyncio
async def test_a_remote_box_that_refuses_a_fresh_connect_is_unreachable(tmp_path):
    """A port nothing listens on: the connect is refused."""
    server = await asyncio.start_server(lambda r, w: None, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    server.close()
    await server.wait_closed()

    async def fresh(box, application_config):
        return await connect(
            "127.0.0.1",
            "tester",
            port=port,
            known_hosts=str(tmp_path / "known_hosts"),
            allow_alias=False,
        )

    (tmp_path / "known_hosts").write_text("", encoding="utf-8")
    status, reason, _ = await _remote_answer(fresh, _read_abs)
    assert (status, reason) == (502, "unreachable")


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["delete", "copy"])
async def test_a_batch_on_a_box_that_cannot_be_reached_fails_each_item_as_unreachable(operation):
    async def refused(box, application_config):
        raise ConnectionError("Connection refused")

    previous, ssh_pool._global_pool = ssh_pool._global_pool, SSHConnectionPool()
    try:
        deps = SimpleNamespace(connect_for_box=refused)
        async with box_files(deps, _remote_box(), CONFIG) as files:
            if operation == "delete":
                outcome = await files.delete_many(["/srv/a.txt", "/srv/b.txt"])
            else:
                outcome = await files.place_many(
                    ["/srv/a.txt", "/srv/b.txt"], "/srv/d", move=False, overwrite=False
                )
    finally:
        await ssh_pool._global_pool.close_all()
        ssh_pool._global_pool = previous

    assert outcome.succeeded == []
    assert outcome.failed == [
        {"path": "/srv/a.txt", "error": "Connection refused", "reason": "unreachable"},
        {"path": "/srv/b.txt", "error": "Connection refused", "reason": "unreachable"},
    ]


class _Connection:
    """A reached box whose SFTP client is ``sftp`` and whose home lookup takes
    ``home_delay`` seconds."""

    def __init__(self, sftp: object = None, home_delay: float = 0.0) -> None:
        self.sftp = sftp
        self.home_delay = home_delay

    async def run(self, cmd: str, check: bool = False, encoding: str | None = "utf-8"):
        if cmd == 'printf %s "$HOME"':
            await asyncio.sleep(self.home_delay)
            return SimpleNamespace(stdout="/home", stderr="", exit_status=0)
        return SimpleNamespace(stdout="test\n", stderr="", exit_status=0)

    async def start_sftp_client(self):
        return self.sftp

    def close(self) -> None:
        pass


class _DroppingSFTP:
    """An SFTP client whose connection drops at the first request."""

    async def stat(self, path: str):
        raise asyncssh.ConnectionLost("Connection lost")

    def exit(self) -> None:
        pass


@pytest.mark.asyncio
async def test_a_remote_connection_that_drops_mid_request_is_unreachable():
    connection = _Connection(_DroppingSFTP())

    async def reached(box, application_config):
        return connection

    async def stat(files):
        return await files.stat(await files.path("/srv/a.txt"))

    answered = await _remote_answer(reached, stat)
    assert answered == (502, "unreachable", "Connection lost")


@pytest.mark.asyncio
async def test_a_remote_home_lookup_past_its_limit_is_timeout(monkeypatch):
    monkeypatch.setattr(remote_api, "HOME_LOOKUP_TIMEOUT", 0.05)
    connection = _Connection(home_delay=1.0)

    async def reached(box, application_config):
        return connection

    async def read_home(files):
        return await files.read_text(await files.path("~/a.txt"), 100)

    answered = await _remote_answer(reached, read_home)
    assert answered == (504, "timeout", "The remote home directory lookup timed out")


def test_the_table_holds_every_operation_the_contract_names():
    names = {row.operation.__name__ for row in ROWS.values()}
    assert names == {
        "_list",
        "_read",
        "_preview",
        "_write",
        "_delete",
        "_rename",
        "_stat",
        "_chmod",
        "_mkdir",
        "_folder_size",
        "_extract",
    }
