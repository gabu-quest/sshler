"""Servers, fixtures and helpers shared by the tests of the existing-name rule."""

from __future__ import annotations

import asyncio
import os
import posixpath
import shlex
import tarfile
import threading
import zipfile
from collections.abc import Awaitable, Callable, Iterator
from dataclasses import dataclass
from pathlib import Path

import asyncssh
import pytest
import yaml
from fastapi.testclient import TestClient
from transfer_events import transfer_events

from sshler import ssh_pool, state
from sshler.ssh_pool import SSHConnectionPool
from sshler.webapp import ServerSettings, make_app

TOKEN = "rule-token"
HEADERS = {"X-SSHLER-TOKEN": TOKEN}
REMOTE = "lb"
EXISTS = {"detail": "File already exists", "reason": "exists"}
TAKEN = {"detail": "File already exists", "reason": "cannot_replace"}
NOT_REGULAR = {"detail": "Only a regular file can be replaced", "reason": "cannot_replace"}
SAME_FILE = {"detail": "Source and destination are the same file", "reason": "cannot_replace"}
HOME_CMD = 'printf %s "$HOME"'


# --- servers ------------------------------------------------------------------------


class ReplacingRenameServer(asyncssh.SFTPServer):
    """An SFTP server whose plain rename replaces an existing name and that refuses hard
    links, as some servers do."""

    def rename(self, oldpath: bytes, newpath: bytes) -> None:
        os.replace(self.map_path(oldpath), self.map_path(newpath))

    def link(self, oldpath: bytes, newpath: bytes) -> None:
        raise asyncssh.SFTPOpUnsupported("hard links not supported")


class NoPosixRenameServer(asyncssh.SFTPServer):
    """An SFTP server without posix-rename, as some servers are: only the plain rename,
    which refuses an existing name."""

    def posix_rename(self, oldpath: bytes, newpath: bytes) -> None:
        raise asyncssh.SFTPOpUnsupported("posix-rename not supported")


class TempRenameFailsServer(NoPosixRenameServer):
    """No posix-rename, and renaming a finished temp file fails (a quota, a full disk):
    the second rename of a replace."""

    def rename(self, oldpath: bytes, newpath: bytes) -> None:
        if oldpath.endswith(b".sshler"):
            raise asyncssh.SFTPFailure("rename refused")
        super().rename(oldpath, newpath)


class NoRenameBackServer(NoPosixRenameServer):
    """No posix-rename, and no rename of sshler's temp or backup file works, so a backup
    cannot be put back either."""

    def rename(self, oldpath: bytes, newpath: bytes) -> None:
        if b".sshler" in posixpath.basename(oldpath):
            raise asyncssh.SFTPFailure("rename refused")
        super().rename(oldpath, newpath)


class CaseBlindServer(asyncssh.SFTPServer):
    """An SFTP server that looks names up without regard to letter case, as macOS does
    by default: `README.md` finds `Readme.md`; a rename onto the same file by another
    case changes the case."""

    def _blind(self, mapped: bytes) -> bytes:
        root = self._chroot
        rel = os.path.relpath(mapped, root)
        if rel == b".":
            return mapped
        current = root
        for part in rel.split(b"/"):
            candidate = os.path.join(current, part)
            if not os.path.lexists(candidate) and os.path.isdir(current):
                for name in os.listdir(current):
                    if name.lower() == part.lower():
                        candidate = os.path.join(current, name)
                        break
            current = candidate
        return current

    def map_path(self, path: bytes) -> bytes:
        return self._blind(super().map_path(path))

    def _rename_target(self, old: bytes, newpath: bytes) -> bytes:
        given = super().map_path(newpath)
        parent, name = os.path.split(given)
        new = os.path.join(self._blind(parent), name)
        found = self._blind(new)
        if os.path.lexists(found) and os.path.samestat(os.lstat(found), os.lstat(old)):
            return new  # the same file by another case: the case changes
        return found

    def rename(self, oldpath: bytes, newpath: bytes) -> None:
        old = self.map_path(oldpath)
        new = self._rename_target(old, newpath)
        if os.path.lexists(new) and not os.path.samestat(os.lstat(new), os.lstat(old)):
            raise asyncssh.SFTPFailure("File already exists")
        os.rename(old, new)

    def posix_rename(self, oldpath: bytes, newpath: bytes) -> None:
        old = self.map_path(oldpath)
        os.replace(old, self._rename_target(old, newpath))


COUNTED_REQUESTS: list[str] = []


class CountingServer(asyncssh.SFTPServer):
    """An SFTP server that records the name of each request it receives, in order."""


def _counted(name: str):
    def method(self, *args, **kwargs):
        COUNTED_REQUESTS.append(name)
        return getattr(asyncssh.SFTPServer, name)(self, *args, **kwargs)

    return method


for _name in ("lstat", "stat", "link", "remove", "realpath", "mkdir", "rmdir", "rename"):
    setattr(CountingServer, _name, _counted(_name))
CountingServer.posix_rename = _counted("posix_rename")


class NoSetstatServer(asyncssh.SFTPServer):
    """An SFTP server that cannot change a file's attributes, as some gateways are: a new
    file has the bits its open asked for, narrowed by the server's umask."""

    def setstat(self, path: bytes, attrs: asyncssh.SFTPAttrs) -> None:
        raise asyncssh.SFTPOpUnsupported("setstat not supported")

    def fsetstat(self, file_obj: object, attrs: asyncssh.SFTPAttrs) -> None:
        raise asyncssh.SFTPOpUnsupported("fsetstat not supported")


class SetstatFailsServer(asyncssh.SFTPServer):
    """An SFTP server that has the operation and fails it (a file system that refuses)."""

    def fsetstat(self, file_obj: object, attrs: asyncssh.SFTPAttrs) -> None:
        raise asyncssh.SFTPFailure("fsetstat refused")


SERVERS = {"refusing-rename": asyncssh.SFTPServer, "replacing-rename": ReplacingRenameServer}
# Servers that behave differently again, for the tests that name them.
ODD_SERVERS = {
    "no-posix-rename": NoPosixRenameServer,
    "temp-rename-fails": TempRenameFailsServer,
    "no-rename-back": NoRenameBackServer,
    "case-blind": CaseBlindServer,
    "counting": CountingServer,
    "no-setstat": NoSetstatServer,
    "setstat-fails": SetstatFailsServer,
}


CommandStandIn = Callable[[str], Awaitable[int]]


class ServerThread:
    """An asyncssh server on its own event loop, so the sync test client can call it.

    It answers the pool's health check and the `~` lookup itself. Any other command is
    never run: it is recorded in ``ran`` and handed to ``stand_in``, which does in
    the temp folder what the program would have done and returns its exit status; with
    no stand-in the status is 127."""

    async def _answer(self, process: asyncssh.SSHServerProcess) -> None:
        if process.command == "echo test":
            process.stdout.write("test\n")
            process.exit(0)
        elif process.command == HOME_CMD:
            process.stdout.write("/home")
            process.exit(0)
        elif self.stand_in is not None:
            self.ran.append(process.command)
            running = asyncio.ensure_future(self.stand_in(process.command))
            self.running.add(running)
            process.exit(await running)
        else:
            process.stderr.write(f"not run: {process.command}\n")
            process.exit(127)

    def __init__(self, root: Path, keys: Path, server_class: type[asyncssh.SFTPServer]) -> None:
        self.stand_in: CommandStandIn | None = None
        self.ran: list[str] = []
        self.running: set[asyncio.Future[int]] = set()
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
                connections.append(conn)

        self.server = self._run(
            asyncssh.listen(
                "127.0.0.1",
                0,
                server_factory=Tracked,
                server_host_keys=[host_key],
                authorized_client_keys=str(authorized),
                sftp_factory=lambda chan: server_class(chan, chroot=os.fsencode(root)),
                process_factory=self._answer,
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
            # The app pools its connections; close them here or `wait_closed` waits.
            for conn in self.connections:
                conn.close()
            for running in self.running:  # a stand-in still waiting when the test ended
                running.cancel()
            await asyncio.gather(*self.running, return_exceptions=True)
            self.server.close()
            await self.server.wait_closed()

        self._run(_close())
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.thread.join(timeout=30)
        self.loop.close()


@dataclass
class Env:
    client: TestClient
    local: Path  # a work folder on the local box
    root: Path  # the remote server's `/`
    server_kind: str
    server: ServerThread | None = None


# A test names its box ("local" or a remote one on either server) as `box`, or the
# server alone as `server`; the fixture starts a server only when one is named.
BOXES = ["local", "lb-refusing", "lb-replacing"]
SERVER_OF_BOX = {
    "lb-refusing": "refusing-rename",
    "lb-replacing": "replacing-rename",
    "lb-no-posix": "no-posix-rename",
    "lb-temp-rename-fails": "temp-rename-fails",
    "lb-no-rename-back": "no-rename-back",
    "lb-case-blind": "case-blind",
    "lb-counting": "counting",
    "lb-no-setstat": "no-setstat",
    "lb-setstat-fails": "setstat-fails",
}


@pytest.fixture
def env(request, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Env]:
    callspec = getattr(request.node, "callspec", None)
    params = callspec.params if callspec is not None else {}
    kind = params.get("server") or SERVER_OF_BOX.get(params.get("box", "local"))
    if kind is None:
        yield from _local_only(tmp_path, monkeypatch)
        return
    root = tmp_path / "remote"
    (root / "home").mkdir(parents=True)
    keys = tmp_path / "keys"
    keys.mkdir()
    server = ServerThread(root, keys, {**SERVERS, **ODD_SERVERS}[kind])
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    box = {
        "name": REMOTE,
        "host": "127.0.0.1",
        "user": "tester",
        "port": server.port,
        "keyfile": str(server.client_key),
        "known_hosts": str(server.known_hosts),
    }
    (config_dir / "boxes.yaml").write_text(yaml.safe_dump({"boxes": [box]}), encoding="utf-8")
    ssh_config = tmp_path / "ssh_config"
    ssh_config.write_text("", encoding="utf-8")
    monkeypatch.setenv("SSHLER_CONFIG_DIR", str(config_dir))
    monkeypatch.setenv("SSHLER_SSH_CONFIG", str(ssh_config))
    monkeypatch.setattr(ssh_pool, "_global_pool", SSHConnectionPool())
    state.initialize(config_dir)
    local = tmp_path / "local"
    local.mkdir()
    client = TestClient(make_app(ServerSettings(csrf_token=TOKEN)))
    try:
        server.stand_in = _packing_in(root)
        yield Env(client=client, local=local, root=root, server_kind=kind, server=server)
    finally:
        client.close()
        state.reset_state()
        server.close()


@pytest.fixture
def local_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Env]:
    """The local box only (no server), for tests with nothing remote to say."""
    yield from _local_only(tmp_path, monkeypatch)


def _local_only(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Env]:
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "boxes.yaml").write_text(yaml.safe_dump({"boxes": []}), encoding="utf-8")
    monkeypatch.setenv("SSHLER_CONFIG_DIR", str(config_dir))
    local = tmp_path / "local"
    local.mkdir()
    client = TestClient(make_app(ServerSettings(csrf_token=TOKEN)))
    try:
        yield Env(client=client, local=local, root=tmp_path / "none", server_kind="none")
    finally:
        client.close()
        state.reset_state()


# --- helpers ------------------------------------------------------------------------


class Side:
    """One box as a test sees it: ``base`` is where files are made on disk, ``path``
    what the API is told (the local path, or the path under the server's `/`)."""

    def __init__(self, env: Env, box: str) -> None:
        self.env = env
        self.box = "local" if box == "local" else REMOTE
        self.base = env.local if box == "local" else env.root

    def path(self, rel: str) -> str:
        return str(self.base / rel) if self.box == "local" else "/" + rel

    def disk(self, rel: str) -> Path:
        return self.base / rel

    def post(self, route: str, body: dict):
        return self.env.client.post(f"/api/v1/boxes/{self.box}/{route}", json=body, headers=HEADERS)


def side(env: Env, box: str) -> Side:
    return Side(env, box)


def files_under(base: Path) -> dict[str, object]:
    """Every entry under ``base``: bytes for a file, "dir" for a folder, "-> target" for
    a link (not followed)."""
    found: dict[str, object] = {}
    for path in sorted(base.rglob("*")):
        rel = str(path.relative_to(base))
        if path.is_symlink():
            found[rel] = f"-> {os.readlink(path)}"
        elif path.is_dir():
            found[rel] = "dir"
        else:
            found[rel] = path.read_bytes()
    return found


def single(s: Side, op: str, source: str, dest_dir: str = "d", **extra):
    """Rename ``source`` to ``dest_dir`` (a name), or move/copy it into folder ``dest_dir``."""
    if op == "rename":
        return s.post("rename", {"path": s.path(source), "new_name": dest_dir, **extra})
    return s.post(op, {"source": s.path(source), "destination": s.path(dest_dir), **extra})


def target_of(op: str, source: str, dest_dir: str = "d") -> str:
    return dest_dir if op == "rename" else f"{dest_dir}/{Path(source).name}"


def transfer(env: Env, src: str, dest: str, paths: list[str], destination: str, **extra):
    resp = env.client.post(
        "/api/v1/transfer",
        json={
            "src_box": src,
            "dest_box": dest,
            "paths": paths,
            "destination": destination,
            **extra,
        },
        headers=HEADERS,
    )
    assert resp.status_code == 200, resp.text
    return transfer_events(resp.text)


# --- item 2: single rename, move and copy -----------------------------------------


def _unpack_tar(archive: Path, into: Path) -> int:
    """``tar xzf archive -C into``: a missing ``into`` is an error, as for GNU tar; each
    entry is written as it is read, so an archive cut short leaves what came before the
    cut. tarfile's ``data`` filter stands in for GNU tar's own guard: both strip a
    leading `/` and fail an entry with `..`; a link pointing outside is refused here,
    where GNU tar delays it and fails a later entry written through it, so an archive
    that writes through such a link fails on both."""
    if not into.is_dir():
        return 2
    try:
        with tarfile.open(archive, "r:gz") as opened:
            opened.extractall(into, filter="data")
    except (tarfile.TarError, OSError, EOFError):
        return 2
    return 0


def _unpack_zip(archive: Path, into: Path) -> int:
    """``unzip -o archive -d into``: ``into`` is made when missing, and a later entry of
    a name replaces an earlier one (``-o``)."""
    try:
        into.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(archive) as opened:
            opened.extractall(into)
    except (zipfile.BadZipFile, OSError, EOFError):
        return 9
    return 0


def _packing_in(root: Path) -> CommandStandIn:
    """What ``tar czf`` and ``zip -r`` do, on the temp folder that is the server's `/`:
    a tar is made new, a zip adds to the zip that is there (as the program does), and
    a source that is not there is the program's error after it began to write. The
    archive being written is never packed into itself. ``tar xzf`` and ``unzip -o``
    unpack as ``_unpack_tar`` and ``_unpack_zip`` say."""

    async def run(command: str) -> int:
        words = shlex.split(command)
        if words[:2] == ["tar", "xzf"] and words[3] == "-C":
            return _unpack_tar(root / words[2].lstrip("/"), root / words[4].lstrip("/"))
        if words[:2] == ["unzip", "-o"] and words[3] == "-d":
            return _unpack_zip(root / words[2].lstrip("/"), root / words[4].lstrip("/"))
        zipping = command.startswith("cd / && ")
        words = shlex.split(command.removeprefix("cd / && "))
        if words[:2] == ["tar", "czf"]:
            target, sources = words[2], words[5:]
        elif words[:2] == ["zip", "-r"]:
            target, sources = words[2], words[3:]
        else:
            return 127
        written = root / target.lstrip("/")
        if not zipping:
            written.write_bytes(b"")
        packed = [root / source.lstrip("/") for source in sources]
        if any(not p.exists() for p in packed):
            written.write_bytes(b"partial")
            return 2
        members = [f for p in packed for f in [p, *sorted(p.rglob("*"))] if f != written]
        if zipping:
            with zipfile.ZipFile(written, "a") as archive:
                for member in members:
                    archive.write(member, member.relative_to(root).as_posix())
            return 0
        with tarfile.open(written, "w:gz") as archive:
            for member in members:
                archive.add(member, arcname=member.relative_to(root).as_posix(), recursive=False)
        return 0

    return run
