"""Remote folder zip: ``dir-size`` and ``download-dir`` at ``~``, ``~/sub`` and ``/``.

Field report (2026-10-08): the toolbar download failed on remote boxes at ``~``
(the literal ``~`` was shell-quoted, so ``du`` and ``zip`` never saw the home
directory, and the failed ``du`` was reported as size 0, passing the 500 MB
guard) and at ``/`` (no folder name, so ``cd / && zip ... download`` failed).

A fake connection records the exact commands. Each test names its mutation.
"""

from __future__ import annotations

import asyncio
import tempfile
import tracemalloc
from pathlib import Path
from types import SimpleNamespace

import asyncssh
import pytest
import yaml
from fastapi.testclient import TestClient

from sshler import ssh_pool, state
from sshler.api import files as files_api
from sshler.api.dependencies import APIDependencies
from sshler.ssh_pool import SSHConnectionPool
from sshler.webapp import ServerSettings, make_app

TOKEN = "zip-token"
BOX = "remote1"
HEADERS = {"X-SSHLER-TOKEN": TOKEN}
HOME_CMD = 'printf %s "$HOME"'


class FakeStream:
    """``stdout``/``stderr`` of a fake process: hands out ``data`` in reads of at most
    ``n`` bytes and records every requested size. An unbounded read (``n < 0``) fails
    the test, since it would buffer the whole output."""

    def __init__(self, data: bytes) -> None:
        self.data = data
        self.requested: list[int] = []

    async def read(self, n: int = -1) -> bytes:
        self.requested.append(n)
        if n < 0:
            chunk, self.data = self.data, b""
            return chunk
        chunk, self.data = self.data[:n], self.data[n:]
        return chunk


class EndlessStream(FakeStream):
    """Never reaches EOF: every read returns ``n`` fresh bytes. Fails the test after
    ``max_reads`` reads so a route that never aborts cannot hang the suite."""

    def __init__(self, max_reads: int) -> None:
        super().__init__(b"")
        self.max_reads = max_reads

    async def read(self, n: int = -1) -> bytes:
        self.requested.append(n)
        if n < 0:
            raise AssertionError("unbounded read of an endless stream")
        if len(self.requested) > self.max_reads:
            raise AssertionError(f"still reading after {self.max_reads} reads")
        return b"z" * n


class FakeProcess:
    def __init__(self, stdout: FakeStream, stderr: bytes = b"", status: int = 0) -> None:
        self.stdout = stdout
        self.stderr = FakeStream(stderr)
        self.status = status
        self.signals: list[str] = []
        self.closed = False

    def kill(self) -> None:
        self.signals.append("KILL")

    def close(self) -> None:
        self.closed = True

    async def wait(self, check: bool = False, timeout: float | None = None):
        return SimpleNamespace(exit_status=self.status, returncode=self.status)


class FakeRemoteFile:
    """An open SFTP file: records ``("write", path, data)``; ``read`` returns ``data``.
    ``target`` is where the bytes land (the link target when ``path`` is a symlink)."""

    def __init__(self, sftp: FakeSFTP, path: str, target: str | None = None) -> None:
        self.sftp = sftp
        self.path = path
        self.target = target if target is not None else path

    async def __aenter__(self) -> FakeRemoteFile:
        return self

    async def __aexit__(self, *exc) -> None:
        return None

    async def read(self) -> bytes:
        return self.sftp.contents[self.target]

    async def write(self, data) -> None:
        self.sftp.calls.append(("write", self.path, data))
        self.sftp.contents[self.target] = data if isinstance(data, bytes) else data.encode()


class FakeSFTP:
    """Records every SFTP call with its exact path. ``contents`` holds the regular files
    that exist (``stat`` on any other path is ``FileNotFoundError``); ``symlinks`` maps a
    link to its target (which may not exist); a path in ``denied`` answers every
    ``stat``/``lstat`` with permission denied. ``stat`` follows links, ``lstat`` does
    not, and an open for write on a link writes through to the target, like a server."""

    def __init__(self, calls: list[tuple], contents: dict[str, bytes]) -> None:
        self.calls = calls
        self.contents = contents
        self.symlinks: dict[str, str] = {}
        self.denied: set[str] = set()

    async def stat(self, path: str):
        self.calls.append(("stat", path))
        if path in self.denied:
            raise asyncssh.SFTPPermissionDenied("Permission denied")
        target = self.symlinks.get(path, path)
        if target not in self.contents:
            raise FileNotFoundError(path)
        return SimpleNamespace(permissions=0o100644)

    async def lstat(self, path: str):
        self.calls.append(("lstat", path))
        if path in self.denied:
            raise asyncssh.SFTPPermissionDenied("Permission denied")
        if path in self.symlinks:
            return SimpleNamespace(permissions=0o120777)
        if path in self.contents:
            return SimpleNamespace(permissions=0o100644)
        raise asyncssh.SFTPNoSuchFile("No such file")

    async def open(self, path: str, mode: str, encoding: str | None = None):
        self.calls.append(("open", path, mode))
        if mode in ("x", "xb"):
            # O_CREAT|O_EXCL: refused when the name exists, a dangling link included
            # (OpenSSH answers SFTPv3 FX_FAILURE).
            if path in self.contents or path in self.symlinks:
                raise asyncssh.SFTPFailure("Failure")
            self.contents[path] = b""
            return FakeRemoteFile(self, path)
        target = self.symlinks.get(path, path)
        if mode in ("w", "wb"):
            self.contents[target] = b""  # SFTP opens for write truncate an existing file
        return FakeRemoteFile(self, path, target)

    async def mkdir(self, path: str) -> None:
        self.calls.append(("mkdir", path))

    async def rename(self, source: str, target: str) -> None:
        self.calls.append(("rename", source, target))

    async def remove(self, path: str) -> None:
        self.calls.append(("remove", path))

    async def exit(self) -> None:
        return None


class FakeConnection:
    """Answers each command from ``replies``; an unknown command fails the test.

    ``create_process`` streams a reply's bytes stdout through a ``FakeProcess``, or
    uses ``processes[command]`` when a test supplies its own process."""

    def __init__(
        self,
        replies: dict[str, SimpleNamespace],
        log: list[str],
        processes: dict[str, FakeProcess] | None = None,
        sftp: FakeSFTP | None = None,
    ) -> None:
        self.replies = replies
        self.log = log
        self.processes = processes if processes is not None else {}
        self.sftp = sftp
        self.closed = False

    async def start_sftp_client(self) -> FakeSFTP:
        if self.sftp is None:
            raise AssertionError("unexpected SFTP session")
        return self.sftp

    async def create_process(self, command: str, encoding: str | None = "utf-8"):
        assert encoding is None
        self.log.append(command)
        if command in self.processes:
            return self.processes[command]
        if command not in self.replies:
            raise AssertionError(f"unexpected command: {command}")
        r = self.replies[command]
        return FakeProcess(FakeStream(r.stdout), r.stderr or b"", r.exit_status)

    async def run(self, command: str, check: bool = False, encoding: str | None = "utf-8"):
        if command == "echo test":
            return SimpleNamespace(exit_status=0, returncode=0, stdout="test\n", stderr="")
        self.log.append(command)
        if command not in self.replies:
            raise AssertionError(f"unexpected command: {command}")
        return self.replies[command]

    def close(self) -> None:
        self.closed = True


def reply(stdout="", stderr="", status=0) -> SimpleNamespace:
    return SimpleNamespace(exit_status=status, returncode=status, stdout=stdout, stderr=stderr)


class Remote(SimpleNamespace):
    """What the ``remote`` fixture gives a test; unpacks as ``client, replies, log``."""

    def __init__(self, client, replies, log, processes, connections, spool, sftp) -> None:
        super().__init__(
            client=client,
            replies=replies,
            log=log,
            processes=processes,
            connections=connections,
            spool=spool,
            sftp=sftp,
        )

    def __iter__(self):
        return iter((self.client, self.replies, self.log))


@pytest.fixture
def remote(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Yields a ``Remote``: set ``replies`` (or ``processes``) per test, read ``log``
    after. Temp files go to ``spool``, so a test can check none is left behind."""
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "boxes.yaml").write_text(yaml.safe_dump({"boxes": []}), encoding="utf-8")
    ssh_config = tmp_path / "ssh_config"
    ssh_config.write_text(
        f"Host {BOX}\n  HostName example.invalid\n  User tester\n", encoding="utf-8"
    )
    monkeypatch.setenv("SSHLER_CONFIG_DIR", str(config_dir))
    monkeypatch.setenv("SSHLER_SSH_CONFIG", str(ssh_config))
    state.initialize(config_dir)
    replies: dict[str, SimpleNamespace] = {}
    log: list[str] = []
    processes: dict[str, FakeProcess] = {}
    connections: list[FakeConnection] = []
    sftp = FakeSFTP([], {})
    spool = tmp_path / "spool"
    spool.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(spool))

    async def fake_connect(self, box, application_config):
        conn = FakeConnection(replies, log, processes, sftp)
        connections.append(conn)
        return conn

    monkeypatch.setattr(APIDependencies, "connect_for_box", fake_connect)
    monkeypatch.setattr(ssh_pool, "_global_pool", SSHConnectionPool())
    client = TestClient(make_app(ServerSettings(csrf_token=TOKEN)))
    try:
        yield Remote(client, replies, log, processes, connections, spool, sftp)
    finally:
        client.close()
        state.reset_state()


def dir_size(client, path):
    return client.get(f"/api/v1/boxes/{BOX}/dir-size", params={"path": path}, headers=HEADERS)


def download(client, path):
    return client.get(f"/api/v1/boxes/{BOX}/download-dir", params={"path": path}, headers=HEADERS)


# --- dir-size ---------------------------------------------------------------


def test_dir_size_at_tilde_runs_du_on_the_resolved_home(remote):
    """Mutation: skip `_resolve_remote_tilde` and `du -sb '~'` runs (and the replies
    dict raises on the unknown command, giving a 500)."""
    client, replies, log = remote
    replies[HOME_CMD] = reply("/home/tester")
    replies["du -sb /home/tester"] = reply("4096\t/home/tester\n")
    resp = dir_size(client, "~")
    assert (resp.status_code, resp.json()) == (200, {"size_bytes": 4096})
    assert log == [HOME_CMD, "du -sb /home/tester"]


def test_dir_size_at_tilde_subfolder_resolves_and_quotes_the_rest(remote):
    """Mutation: resolve only the bare `~` (not `~/sub dir`); the command would keep `~/`."""
    client, replies, log = remote
    replies[HOME_CMD] = reply("/home/tester")
    replies["du -sb '/home/tester/sub dir'"] = reply("7\t/home/tester/sub dir\n")
    resp = dir_size(client, "~/sub dir")
    assert (resp.status_code, resp.json()) == (200, {"size_bytes": 7})
    assert log == [HOME_CMD, "du -sb '/home/tester/sub dir'"]


def test_dir_size_at_root_runs_du_on_slash(remote):
    """Mutation: treat `/` specially (e.g. send an empty path); the exact command differs."""
    client, replies, log = remote
    replies["du -sb /"] = reply("999\t/\n")
    resp = dir_size(client, "/")
    assert (resp.status_code, resp.json()) == (200, {"size_bytes": 999})
    assert log == ["du -sb /"]


def test_both_du_forms_failing_on_an_existing_folder_gives_unknown_size_not_zero(remote):
    """Mutation: report 0 (or a 502) when both `du` forms fail on a folder that exists;
    the UI could not ask the user, and a 0 would pass the 500 MB guard."""
    client, replies, log = remote
    replies["du -sb /odd"] = reply("", "du: unknown failure\n", 1)
    replies["du -sk /odd"] = reply("", "du: unknown failure\n", 1)
    replies["test -d /odd"] = reply("", "", 0)
    resp = dir_size(client, "/odd")
    assert (resp.status_code, resp.json()) == (200, {"size_bytes": None})
    assert log == ["du -sb /odd", "du -sk /odd", "test -d /odd"]


def test_bsd_du_without_b_falls_back_to_kilobytes(remote):
    """Mutation: drop the `du -sk` fallback; a macOS/BSD remote (no `-b`) would get
    size null instead of 5 * 1024 = 5120."""
    client, replies, log = remote
    replies["du -sb /srv"] = reply("", "du: illegal option -- b\n", 64)
    replies["du -sk /srv"] = reply("5\t/srv\n")
    resp = dir_size(client, "/srv")
    assert (resp.status_code, resp.json()) == (200, {"size_bytes": 5120})
    assert log == ["du -sb /srv", "du -sk /srv"]


def test_du_b_success_does_not_run_the_fallback(remote):
    """Mutation: always run `du -sk` too; the unexpected-command assertion in the fake
    connection fails the request."""
    client, replies, log = remote
    replies["du -sb /srv"] = reply("10\t/srv\n")
    assert dir_size(client, "/srv").json() == {"size_bytes": 10}
    assert log == ["du -sb /srv"]


def test_du_with_an_unreadable_subfolder_still_reports_the_total(remote):
    """Mutation: treat any non-zero exit as an error; a folder with one unreadable
    child would then be impossible to download."""
    client, replies, log = remote
    replies["du -sb /srv"] = reply("123\t/srv\n", "du: cannot read directory '/srv/x'\n", 1)
    resp = dir_size(client, "/srv")
    assert (resp.status_code, resp.json()) == (200, {"size_bytes": 123})


def test_unresolvable_home_is_an_error(remote):
    """Mutation: use an empty `$HOME` as the home; du would run on '' / the sub path."""
    client, replies, log = remote
    replies[HOME_CMD] = reply("", "", 0)
    resp = dir_size(client, "~")
    assert (resp.status_code, resp.json()) == (
        502,
        {"detail": "Could not resolve the remote home directory"},
    )
    assert log == [HOME_CMD]


# --- download-dir -----------------------------------------------------------


def test_download_at_tilde_zips_the_home_folder_under_its_real_name(remote):
    """Mutation: skip tilde resolution; the command would be `cd . && zip ... '~'`
    and the file `~.zip`."""
    client, replies, log = remote
    replies[HOME_CMD] = reply("/home/tester")
    replies["cd /home && zip -r -q - tester"] = reply(b"ZIPDATA")
    resp = download(client, "~")
    assert resp.status_code == 200
    assert resp.content == b"ZIPDATA"
    assert resp.headers["content-disposition"] == (
        "attachment; filename=\"tester.zip\"; filename*=UTF-8''tester.zip"
    )
    assert log == [HOME_CMD, "cd /home && zip -r -q - tester"]


def test_download_at_tilde_subfolder(remote):
    """Mutation: resolve `~` only when the path is exactly `~`."""
    client, replies, log = remote
    replies[HOME_CMD] = reply("/home/tester")
    replies["cd /home/tester && zip -r -q - proj"] = reply(b"Z")
    resp = download(client, "~/proj")
    assert resp.status_code == 200
    assert resp.headers["content-disposition"] == (
        "attachment; filename=\"proj.zip\"; filename*=UTF-8''proj.zip"
    )
    assert log == [HOME_CMD, "cd /home/tester && zip -r -q - proj"]


def test_download_at_root_zips_the_contents_as_root(remote):
    """Mutation: restore `dirname = ... or "download"` with the folder name as the zip
    target; the command becomes `cd / && zip -r -q - download`."""
    client, replies, log = remote
    replies["cd / && zip -r -q - ."] = reply(b"ROOTZIP")
    resp = download(client, "/")
    assert resp.status_code == 200
    assert resp.content == b"ROOTZIP"
    assert resp.headers["content-disposition"] == (
        "attachment; filename=\"root.zip\"; filename*=UTF-8''root.zip"
    )
    assert log == ["cd / && zip -r -q - ."]


def test_download_failure_reports_the_zip_error(remote):
    """Mutation: ignore a non-zero zip status and return the (empty) stdout as a 200."""
    client, replies, log = remote
    replies["cd / && zip -r -q - gone"] = reply(b"", b"zip error: Nothing to do!", 12)
    resp = download(client, "/gone")
    assert (resp.status_code, resp.json()) == (
        500,
        {"detail": "zip failed: zip error: Nothing to do!"},
    )


# --- `~` resolution ---------------------------------------------------------


@pytest.mark.parametrize(
    ("sent", "resolved"),
    [
        ("~", "/home/tester"),
        ("~/a", "/home/tester/a"),
        ("~//etc", "/home/tester/etc"),
        ("~/../..", "/"),
    ],
)
def test_tilde_paths_resolve_to_exact_paths(remote, sent, resolved):
    """Mutation: `posixpath.join(home, path[2:])` (pre-fix) resolves `~//etc` to `/etc`;
    skipping `normpath` leaves `~/../..` as `/home/tester/../..`."""
    client, replies, log = remote
    replies[HOME_CMD] = reply("/home/tester")
    replies[f"du -sb {resolved}"] = reply("1\tx\n")
    resp = dir_size(client, sent)
    assert (resp.status_code, resp.json()) == (200, {"size_bytes": 1})
    assert log == [HOME_CMD, f"du -sb {resolved}"]


# --- unreachable box --------------------------------------------------------


@pytest.mark.parametrize("call", [dir_size, download])
def test_unreachable_box_is_a_502_with_the_pool_message(remote, monkeypatch, call):
    """Mutation: catch only SSHError; the pool's ConnectionError (an OSError) falls into
    `except Exception` and the response is a 500."""
    client, replies, log = remote

    async def down(self, box, application_config):
        raise ConnectionError("remote1: unreachable (cached, retry in 42s)")

    monkeypatch.setattr(APIDependencies, "connect_for_box", down)
    resp = call(client, "/srv")
    assert (resp.status_code, resp.json()) == (
        502,
        {"detail": "remote1: unreachable (cached, retry in 42s)"},
    )


# --- Content-Disposition ----------------------------------------------------


@pytest.mark.parametrize(
    ("folder", "header"),
    [
        ("proj", "attachment; filename=\"proj.zip\"; filename*=UTF-8''proj.zip"),
        (
            "日本語",
            "attachment; filename=\"___.zip\"; filename*=UTF-8''%E6%97%A5%E6%9C%AC%E8%AA%9E.zip",
        ),
        (
            'a"b',
            "attachment; filename=\"a_b.zip\"; filename*=UTF-8''a%22b.zip",
        ),
    ],
)
def test_remote_zip_header_is_exact_for_any_folder_name(remote, folder, header):
    """Mutation: restore `filename="{dirname}.zip"`; a Japanese name raises
    UnicodeEncodeError (500) and a `"` ends the quoted string early."""
    client, replies, log = remote
    from shlex import quote as q

    replies[f"cd /srv && zip -r -q - {q(folder)}"] = reply(b"Z")
    resp = download(client, f"/srv/{folder}")
    assert resp.status_code == 200
    assert resp.headers["content-disposition"] == header


def test_remote_file_download_header_is_exact_for_a_japanese_name(remote, monkeypatch):
    """Mutation: keep the unencoded `filename="{filename}"` in /download; the header
    cannot be built for a non-Latin-1 name."""
    client, replies, log = remote

    async def fake_read(connection, path, limit):
        assert path == "/srv/日本語.txt"
        return b"hi", False

    monkeypatch.setattr("sshler.api.files._read_file_bytes", fake_read)
    resp = client.get(
        f"/api/v1/boxes/{BOX}/download", params={"path": "/srv/日本語.txt"}, headers=HEADERS
    )
    assert resp.status_code == 200
    assert resp.headers["content-disposition"] == (
        "attachment; filename=\"___.txt\"; filename*=UTF-8''%E6%97%A5%E6%9C%AC%E8%AA%9E.txt"
    )


def test_local_zip_header_is_exact_for_a_japanese_folder(remote, tmp_path):
    """Mutation: pass `filename=` to FileResponse again for a name with `"`; the quoted
    string is broken (the local box is a different code path from the remote one)."""
    client, replies, log = remote
    folder = tmp_path / 'ドキュメント"x'
    folder.mkdir()
    (folder / "a.txt").write_text("a", encoding="utf-8")
    resp = client.get(
        "/api/v1/boxes/local/download-dir", params={"path": str(folder)}, headers=HEADERS
    )
    assert resp.status_code == 200
    assert resp.headers["content-disposition"] == (
        "attachment; filename=\"_______x.zip\"; "
        "filename*=UTF-8''%E3%83%89%E3%82%AD%E3%83%A5%E3%83%A1%E3%83%B3%E3%83%88%22x.zip"
    )


# --- download-dir streams with a size cap ------------------------------------

CHUNK = 64 * 1024
ZIP_BIG = "cd /srv && zip -r -q - big"


def _pool_idle(box: str) -> int:
    stats = ssh_pool.get_pool().stats().get(box, {"active_connections": 0})
    return int(stats["active_connections"])


@pytest.fixture
def small_cap(monkeypatch: pytest.MonkeyPatch) -> int:
    """Cap at 10 chunks so a test streams kilobytes, not 500 MB. Returns the cap."""
    monkeypatch.setattr(files_api, "ZIP_STREAM_CHUNK_BYTES", CHUNK)
    monkeypatch.setattr(files_api, "MAX_DIR_DOWNLOAD_BYTES", 10 * CHUNK)
    return 10 * CHUNK


def test_zip_past_the_cap_is_aborted_one_chunk_past_it(remote, small_cap):
    """Wave 4e review: the zip was collected whole (`connection.run(..., encoding=None)`)
    before the size check, so an unknown-size folder could exhaust memory.

    Mutations: restore the buffered `connection.run` (the fake has no reply for the zip
    command: 500); drop the in-loop cap check (the endless stream fails the test after
    50 reads); skip `process.kill()` (signals empty); release instead of discard (the
    connection stays open and pooled); leave the spool file behind (spool not empty).
    """
    proc = FakeProcess(EndlessStream(max_reads=50))
    remote.processes[ZIP_BIG] = proc
    resp = download(remote.client, "/srv/big")
    assert (resp.status_code, resp.json()) == (
        413,
        {"detail": "Directory too large to download"},
    )
    # 10 chunks reach the cap exactly; the 11th passes it and stops the read.
    assert proc.stdout.requested == [CHUNK] * 11
    assert proc.signals == ["KILL"]
    assert proc.closed is True
    assert len(remote.connections) == 1
    assert remote.connections[0].closed is True
    assert _pool_idle(BOX) == 0
    assert list(remote.spool.iterdir()) == []


def test_zip_past_the_cap_holds_at_most_a_chunk_in_memory(remote, monkeypatch):
    """Mutation: append the chunks to a list (or `b"".join` them) instead of spooling to a
    temp file; the traced peak then reaches the 16 MiB read before the abort. Spooled,
    the peak stays near one 64 KiB chunk (bound: 2 MiB, an eighth of the cap)."""
    cap = 256 * CHUNK  # 16 MiB
    monkeypatch.setattr(files_api, "ZIP_STREAM_CHUNK_BYTES", CHUNK)
    monkeypatch.setattr(files_api, "MAX_DIR_DOWNLOAD_BYTES", cap)
    remote.processes[ZIP_BIG] = FakeProcess(EndlessStream(max_reads=300))
    tracemalloc.start()
    try:
        tracemalloc.reset_peak()
        resp = download(remote.client, "/srv/big")
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert resp.status_code == 413
    assert peak < 2 * 1024 * 1024


def test_zip_under_the_cap_is_streamed_whole_and_the_connection_reused(remote, small_cap):
    """Mutations: return only the first chunk (content differs); discard the connection
    on success (idle count 0); drop the `_TempFileResponse` unlink (spool not empty
    after the response)."""
    data = bytes(range(256)) * 800  # 204800 bytes: 3 full chunks and one of 8192
    remote.replies[ZIP_BIG] = reply(data)
    resp = download(remote.client, "/srv/big")
    assert resp.status_code == 200
    assert resp.content == data
    assert resp.headers["content-disposition"] == (
        "attachment; filename=\"big.zip\"; filename*=UTF-8''big.zip"
    )
    assert _pool_idle(BOX) == 1
    assert remote.connections[0].closed is False
    assert list(remote.spool.iterdir()) == []


def test_zip_exactly_at_the_cap_is_allowed(remote, small_cap):
    """Mutation: `>=` instead of `>` in the cap check; a zip of exactly the cap is 413."""
    data = b"q" * small_cap
    remote.replies[ZIP_BIG] = reply(data)
    resp = download(remote.client, "/srv/big")
    assert resp.status_code == 200
    assert len(resp.content) == small_cap


# --- dir-size of a missing folder ------------------------------------------


def test_dir_size_of_a_missing_folder_is_404(remote):
    """Wave 4e review: a folder that doesn't exist returned `size_bytes: null`.

    Mutation: skip the `test -d` check after both `du` forms fail; the response is
    `200 {"size_bytes": null}`."""
    client, replies, log = remote
    err = "du: cannot access '/nope': No such file or directory\n"
    replies["du -sb /nope"] = reply("", err, 1)
    replies["du -sk /nope"] = reply("", err, 1)
    replies["test -d /nope"] = reply("", "", 1)
    resp = dir_size(client, "/nope")
    assert (resp.status_code, resp.json()) == (404, {"detail": "Directory not found"})
    assert log == ["du -sb /nope", "du -sk /nope", "test -d /nope"]


# --- /ls, /download and /view at `~` ------------------------------------------


@pytest.fixture
def listed(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Replaces the SFTP listing; returns the paths it was asked to list."""
    paths: list[str] = []

    async def fake_list(connection, path):
        paths.append(path)
        return [
            {"name": "a.txt", "is_directory": False, "size": 3, "modified": 1.0, "mode": 420}
        ]

    monkeypatch.setattr(files_api, "sftp_list_directory", fake_list)
    return paths


@pytest.mark.parametrize(
    ("sent", "resolved"),
    [("~", "/home/tester"), ("~/sub dir", "/home/tester/sub dir")],
)
def test_remote_listing_at_tilde_lists_the_remote_home(remote, listed, sent, resolved):
    """WS-Z carry-over: `_normalize_directory_path` mapped `~` to `/`, so the remote
    listing at `~` showed `/` while the toolbar zip at `~` zipped the home folder.

    Mutation: list `_normalize_directory_path(directory)` without resolving `~` first;
    the listed path is `/` (or `/~/sub dir`)."""
    client, replies, log = remote
    replies[HOME_CMD] = reply("/home/tester")
    resp = client.get(f"/api/v1/boxes/{BOX}/ls", params={"directory": sent}, headers=HEADERS)
    assert resp.status_code == 200
    body = resp.json()
    assert body["directory"] == resolved
    assert [e["path"] for e in body["entries"]] == [f"{resolved}/a.txt"]
    assert listed == [resolved]
    assert log == [HOME_CMD]


def test_remote_listing_of_an_absolute_path_runs_no_home_lookup(remote, listed):
    """Mutation: always ask the remote for `$HOME`; the fake then logs the extra command."""
    client, replies, log = remote
    resp = client.get(f"/api/v1/boxes/{BOX}/ls", params={"directory": "/srv"}, headers=HEADERS)
    assert resp.status_code == 200
    assert listed == ["/srv"]
    assert log == []


@pytest.mark.parametrize("route", ["download", "view"])
def test_file_routes_resolve_tilde_like_the_folder_zip(remote, monkeypatch, route):
    """Wave 4e review: `/download` and `/view` read the literal `~/notes.txt`.

    Mutation: drop `_resolve_remote_tilde` from the route; SFTP is asked for
    `~/notes.txt`."""
    client, replies, log = remote
    replies[HOME_CMD] = reply("/home/tester")
    read_paths: list[str] = []

    async def fake_read(connection, path, limit):
        read_paths.append(path)
        return b"hello", False

    monkeypatch.setattr(files_api, "_read_file_bytes", fake_read)
    resp = client.get(
        f"/api/v1/boxes/{BOX}/{route}", params={"path": "~/notes.txt"}, headers=HEADERS
    )
    assert resp.status_code == 200
    assert resp.content == b"hello"
    assert read_paths == ["/home/tester/notes.txt"]
    assert log == [HOME_CMD]


@pytest.mark.parametrize(
    ("route", "detail"),
    [
        ("download", "File too large to download via API"),
        ("view", "File too large to view"),
    ],
)
def test_file_routes_too_large_is_413_not_500(remote, monkeypatch, route, detail):
    """Mutation: drop `except HTTPException: raise`; the route's own 413 is caught by
    `except Exception` and answered as `500 "413: ..."`."""
    client, replies, log = remote

    async def too_big(connection, path, limit):
        return b"", True

    monkeypatch.setattr(files_api, "_read_file_bytes", too_big)
    resp = client.get(f"/api/v1/boxes/{BOX}/{route}", params={"path": "/srv/x"}, headers=HEADERS)
    assert (resp.status_code, resp.json()) == (413, {"detail": detail})


@pytest.mark.parametrize(
    ("route", "params"),
    [
        ("download", {"path": "/srv/x"}),
        ("view", {"path": "/srv/x"}),
        ("ls", {"directory": "/srv"}),
    ],
)
def test_file_routes_unreachable_box_is_502(remote, monkeypatch, route, params):
    """Wave 4e review: `/download` sent the pool's `ConnectionError` to the generic
    `except Exception` (500). Mutation: catch only `SSHError` again."""
    client, replies, log = remote

    async def down(self, box, application_config):
        raise ConnectionError("remote1: unreachable (cached, retry in 42s)")

    monkeypatch.setattr(APIDependencies, "connect_for_box", down)
    resp = client.get(f"/api/v1/boxes/{BOX}/{route}", params=params, headers=HEADERS)
    assert (resp.status_code, resp.json()) == (
        502,
        {"detail": "remote1: unreachable (cached, retry in 42s)"},
    )


# --- writes at `~` (wave 5b, WS-AC item 1) ------------------------------------
#
# Review: "touch, mkdir and upload still send `~` through `_normalize_directory_path`,
# which maps it to `/` ... The user sees `/home/u`'s files, clicks New File "a.txt",
# and the server writes `/a.txt`." Every remote route in files.py that takes a folder
# or a path resolves `~` with the same `$HOME` lookup as the listing. Shared mutation
# (the pre-fix routes): pass the raw `~` path on to SFTP; the recorded calls then name
# `/a.txt` (folder routes) or `~/a.txt` (path routes) and no `$HOME` lookup is logged.

HOME = "/home/tester"


def _post(remote, route: str, body: dict):
    return remote.client.post(f"/api/v1/boxes/{BOX}/{route}", json=body, headers=HEADERS)


@pytest.mark.parametrize(
    ("directory", "created"),
    [("~", f"{HOME}/a.txt"), ("~/sub dir", f"{HOME}/sub dir/a.txt")],
)
def test_touch_at_tilde_creates_the_file_in_the_remote_home(remote, directory, created):
    """Mutation: `_normalize_directory_path(payload.directory)` without the `~` lookup;
    the file is created at `/a.txt` (or `/~/sub dir/a.txt`)."""
    remote.replies[HOME_CMD] = reply(HOME)
    resp = _post(remote, "touch", {"directory": directory, "filename": "a.txt"})
    assert (resp.status_code, resp.json()["path"]) == (200, created)
    assert remote.sftp.calls == [
        ("lstat", created),
        ("open", created, "xb"),
        ("write", created, b""),
    ]
    assert remote.log == [HOME_CMD]


def test_touch_at_an_absolute_folder_runs_no_home_lookup(remote):
    """Mutation: always ask the remote for `$HOME`; the fake logs the extra command."""
    resp = _post(remote, "touch", {"directory": "/srv", "filename": "a.txt"})
    assert (resp.status_code, resp.json()["path"]) == (200, "/srv/a.txt")
    assert remote.sftp.calls[1] == ("open", "/srv/a.txt", "xb")
    assert remote.log == []


def test_mkdir_at_tilde_creates_the_folder_in_the_remote_home(remote):
    """Mutation: drop the `~` lookup in /mkdir; SFTP is asked for `/newdir`."""
    remote.replies[HOME_CMD] = reply(HOME)
    resp = _post(remote, "mkdir", {"directory": "~", "filename": "newdir"})
    assert (resp.status_code, resp.json()["path"]) == (200, f"{HOME}/newdir")
    assert remote.sftp.calls == [("stat", f"{HOME}/newdir"), ("mkdir", f"{HOME}/newdir")]
    assert remote.log == [HOME_CMD]


def test_upload_at_tilde_writes_into_the_remote_home(remote):
    """Mutation: drop the `~` lookup in /upload; the bytes are written to `/up.bin`."""
    remote.replies[HOME_CMD] = reply(HOME)
    resp = remote.client.post(
        f"/api/v1/boxes/{BOX}/upload",
        data={"directory": "~"},
        files={"file": ("up.bin", b"\x00\x01payload", "application/octet-stream")},
        headers=HEADERS,
    )
    assert (resp.status_code, resp.json()["path"]) == (200, f"{HOME}/up.bin")
    assert remote.sftp.calls == [
        ("lstat", f"{HOME}/up.bin"),
        ("open", f"{HOME}/up.bin", "xb"),
        ("write", f"{HOME}/up.bin", b"\x00\x01payload"),
    ]
    assert remote.log == [HOME_CMD]


@pytest.mark.parametrize(
    ("route", "body", "calls", "path"),
    [
        (
            "rename",
            {"path": "~/old.txt", "new_name": "new.txt"},
            [("rename", f"{HOME}/old.txt", f"{HOME}/new.txt")],
            f"{HOME}/new.txt",
        ),
        (
            "delete",
            {"path": "~/a.txt"},
            [("remove", f"{HOME}/a.txt")],
            f"{HOME}/a.txt",
        ),
        (
            "move",
            {"source": "~/a.txt", "destination": "~/archive"},
            [("rename", f"{HOME}/a.txt", f"{HOME}/archive/a.txt")],
            f"{HOME}/archive/a.txt",
        ),
        (
            "copy",
            {"source": "~/a.txt", "destination": "~/bak"},
            [
                ("open", f"{HOME}/a.txt", "rb"),
                ("open", f"{HOME}/bak/a.txt", "wb"),
                ("write", f"{HOME}/bak/a.txt", b"original"),
            ],
            f"{HOME}/bak/a.txt",
        ),
        (
            "write",
            {"path": "~/notes.md", "content": "hi"},
            [("open", f"{HOME}/notes.md", "w"), ("write", f"{HOME}/notes.md", "hi")],
            f"{HOME}/notes.md",
        ),
    ],
)
def test_path_routes_at_tilde_act_on_the_remote_home(remote, route, body, calls, path):
    """Mutation: drop `_remote_path` from the route (validate the raw path only); SFTP
    is asked for the literal `~/...`, which it reads as a folder named `~`."""
    remote.replies[HOME_CMD] = reply(HOME)
    remote.sftp.contents[f"{HOME}/a.txt"] = b"original"
    resp = _post(remote, route, body)
    assert resp.status_code == 200, resp.text
    assert resp.json()["path"] == path
    assert remote.sftp.calls == calls
    # One lookup per `~` path in the request.
    assert remote.log == [HOME_CMD] * sum(str(v).startswith("~") for v in body.values())


def test_chmod_at_tilde_changes_the_file_in_the_remote_home(remote, monkeypatch):
    """Mutation: drop `_remote_path` from /chmod; `sftp_chmod` gets `~/run.sh`."""
    remote.replies[HOME_CMD] = reply(HOME)
    seen: list[tuple[str, int]] = []

    async def fake_chmod(connection, path, mode):
        seen.append((path, mode))

    monkeypatch.setattr(files_api, "sftp_chmod", fake_chmod)
    resp = _post(remote, "chmod", {"path": "~/run.sh", "mode": "755"})
    assert (resp.status_code, resp.json()["path"]) == (200, f"{HOME}/run.sh")
    assert seen == [(f"{HOME}/run.sh", 0o755)]
    assert remote.log == [HOME_CMD]


def test_stat_at_tilde_checks_the_file_in_the_remote_home(remote):
    """Mutation: drop `_remote_path` from /stat; SFTP stats `~/a.txt`, which the fake
    does not have, and the answer is `exists: false`."""
    remote.replies[HOME_CMD] = reply(HOME)
    remote.sftp.contents[f"{HOME}/a.txt"] = b"x"
    resp = remote.client.get(
        f"/api/v1/boxes/{BOX}/stat", params={"path": "~/a.txt"}, headers=HEADERS
    )
    assert (resp.status_code, resp.json()) == (
        200,
        {"exists": True, "is_directory": False, "is_file": True},
    )
    assert remote.sftp.calls == [("stat", f"{HOME}/a.txt")]
    assert remote.log == [HOME_CMD]


def test_file_preview_at_tilde_reads_the_remote_home(remote, monkeypatch):
    """Mutation: drop `_remote_path` from /file; the text is read from `~/a.md` and the
    parent reported as `~`."""
    remote.replies[HOME_CMD] = reply(HOME)
    read: list[str] = []

    async def fake_text(connection, path, limit):
        read.append(path)
        return "# hi"

    monkeypatch.setattr(files_api, "_read_remote_text", fake_text)
    resp = remote.client.get(
        f"/api/v1/boxes/{BOX}/file", params={"path": "~/a.md"}, headers=HEADERS
    )
    assert resp.status_code == 200
    body = resp.json()
    assert (body["path"], body["parent"], body["content"]) == (f"{HOME}/a.md", HOME, "# hi")
    assert read == [f"{HOME}/a.md"]
    assert remote.log == [HOME_CMD]


def test_write_route_with_an_unresolvable_home_is_502_and_writes_nothing(remote):
    """Mutation: drop `except HTTPException: raise` from /write; the lookup's 502 falls
    into `except Exception` and is answered as `500 "502: ..."`."""
    remote.replies[HOME_CMD] = reply("", "", 1)
    resp = _post(remote, "write", {"path": "~/a.txt", "content": "x"})
    assert (resp.status_code, resp.json()) == (
        502,
        {"detail": "Could not resolve the remote home directory"},
    )
    assert remote.sftp.calls == []


# --- the temp zip is deleted on every outcome (WS-AC item 2) -------------------

ZIP_SRV = "cd /srv && zip -r -q - big"


async def _asgi_get(app, path: str, query: dict[str, str], send) -> None:
    """One GET straight through the ASGI app, so a test controls `send` and can
    cancel the request task."""
    from urllib.parse import urlencode

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "root_path": "",
        "query_string": urlencode(query).encode(),
        "headers": [(b"host", b"testserver"), (b"x-sshler-token", TOKEN.encode())],
        "client": ("127.0.0.1", 50000),
        "server": ("testserver", 80),
    }

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    await app(scope, receive, send)


@pytest.mark.parametrize(
    ("range_header", "status"),
    [("bytes=abc", 400), ("bytes=5000-", 416)],
)
def test_bad_range_on_a_folder_zip_deletes_the_temp_zip(remote, range_header, status):
    """Review: "A `Range` header that is malformed or unsatisfiable takes an early
    `return` before `background()`." Mutation: go back to `FileResponse(...,
    background=_cleanup_temp(...))`; the zip stays in the spool folder."""
    remote.replies[ZIP_SRV] = reply(b"Z" * 100)
    resp = remote.client.get(
        f"/api/v1/boxes/{BOX}/download-dir",
        params={"path": "/srv/big"},
        headers={**HEADERS, "Range": range_header},
    )
    assert resp.status_code == status
    assert list(remote.spool.iterdir()) == []


def test_client_disconnect_mid_download_deletes_the_temp_zip(remote):
    """Mutation: rely on the `background` task again; `send` raising on the body (the
    client went away) skips it and the zip stays."""
    remote.replies[ZIP_SRV] = reply(b"Z" * 100)

    async def send(message):
        if message["type"] == "http.response.body":
            raise OSError("client went away")

    with pytest.raises(OSError, match="client went away"):
        asyncio.run(
            _asgi_get(
                remote.client.app,
                f"/api/v1/boxes/{BOX}/download-dir",
                {"path": "/srv/big"},
                send,
            )
        )
    assert list(remote.spool.iterdir()) == []


def test_cancel_during_release_deletes_the_temp_zip(remote, monkeypatch):
    """Review: "`await ssh_pool.release(...)` runs outside the cleanup `try`. If the
    request is cancelled during its health check ... the spooled zip is never deleted."

    Mutation: move `release` back after the `try`; the zip is still in the spool after
    the cancel."""
    remote.replies[ZIP_SRV] = reply(b"Z" * 100)
    entered: list[asyncio.Event] = []

    async def hang(self, connection):
        entered[0].set()
        await asyncio.Event().wait()  # until cancelled

    monkeypatch.setattr(SSHConnectionPool, "_is_connection_healthy", hang)
    sent: list[dict] = []

    async def send(message):
        sent.append(message)

    async def run() -> int:
        entered.append(asyncio.Event())
        task = asyncio.create_task(
            _asgi_get(
                remote.client.app,
                f"/api/v1/boxes/{BOX}/download-dir",
                {"path": "/srv/big"},
                send,
            )
        )
        await entered[0].wait()
        spooled = len(list(remote.spool.iterdir()))
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        return spooled

    assert asyncio.run(run()) == 1  # the zip was on disk when the cancel arrived
    assert sent == []
    assert list(remote.spool.iterdir()) == []
    assert len(remote.connections) == 1
    assert remote.connections[0].closed is True


def test_temp_file_response_deletes_its_file_when_send_fails(tmp_path):
    """The route-level disconnect test above goes through the middleware stack; this
    one calls the response directly. Mutation: unlink after `super().__call__` instead
    of in `finally`; the failing `send` skips it and the file stays."""
    zip_file = tmp_path / "z.zip"
    zip_file.write_bytes(b"Z" * 10)
    response = files_api._TempFileResponse(str(zip_file), media_type="application/zip")
    scope = {"type": "http", "method": "GET", "headers": []}

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        if message["type"] == "http.response.body":
            raise OSError("client went away")

    with pytest.raises(OSError, match="client went away"):
        asyncio.run(response(scope, receive, send))
    assert zip_file.exists() is False


# --- stderr is drained alongside stdout (WS-AC item 3) ---------------------------


class _WindowedStdout(FakeStream):
    """stdout that, like an SSH channel whose shared window is full of unread stderr,
    gives EOF only after stderr has been read to the end."""

    def __init__(self, data: bytes, stderr_done: asyncio.Event) -> None:
        super().__init__(data)
        self.stderr_done = stderr_done

    async def read(self, n: int = -1) -> bytes:
        if not self.data:
            await self.stderr_done.wait()
        return await super().read(n)


class _SignallingStderr(FakeStream):
    """stderr that sets ``done`` when it is read to EOF."""

    def __init__(self, data: bytes, done: asyncio.Event) -> None:
        super().__init__(data)
        self.done = done

    async def read(self, n: int = -1) -> bytes:
        chunk = await super().read(n)
        if not chunk:
            self.done.set()
        return chunk


def _windowed_process(stdout: bytes, stderr: bytes, status: int) -> FakeProcess:
    # An Event binds to the loop on first use (Python 3.10+), here the request's loop.
    done = asyncio.Event()
    proc = FakeProcess(_WindowedStdout(stdout, done), status=status)
    proc.stderr = _SignallingStderr(stderr, done)
    return proc


def test_a_megabyte_of_stderr_does_not_stall_the_zip(remote, small_cap, monkeypatch):
    """Review: "`_spool_remote_zip` reads stderr only after stdout hits EOF ... a zip
    that writes a lot of warnings to stderr can stall until the 300 s timeout."

    Mutation: read stderr after the stdout loop again; stdout never reaches EOF and the
    route times out (504 at the lowered 2 s limit)."""
    monkeypatch.setattr(files_api, "ZIP_STREAM_TIMEOUT_SECONDS", 2)
    proc = _windowed_process(b"PKDATA", b"w" * (1024 * 1024), 0)
    remote.processes[ZIP_SRV] = proc
    resp = download(remote.client, "/srv/big")
    assert (resp.status_code, resp.content) == (200, b"PKDATA")
    # 1 MiB in 64 KiB reads: 16 full reads and the EOF read, none unbounded.
    assert proc.stderr.requested == [CHUNK] * 17


def test_a_failed_zip_reports_the_tail_of_a_long_stderr(remote, small_cap, monkeypatch):
    """Mutations: keep the head of stderr (the message is all `w`); keep all of it
    (the detail is a megabyte long); read stderr after stdout (504 at the 2 s limit)."""
    monkeypatch.setattr(files_api, "ZIP_STREAM_TIMEOUT_SECONDS", 2)
    last = b"zip error: Nothing to do!"
    proc = _windowed_process(b"", b"w" * (1024 * 1024) + last, 12)
    remote.processes[ZIP_SRV] = proc
    resp = download(remote.client, "/srv/big")
    keep = 64 * 1024
    expected = "zip failed: " + ("w" * (keep - len(last))) + last.decode()
    assert (resp.status_code, resp.json()) == (500, {"detail": expected})


# --- existing remote files are never overwritten (wave 5c, WS-AE) ----------------
#
# WS-AC carried: remote touch and upload raised "File already exists" inside a
# `try/except Exception: pass`, so the check was swallowed and an existing file was
# truncated or overwritten. The fake SFTP truncates on a write-mode open and stores
# written data, so "bytes unchanged" is read from `contents`, not inferred from calls.


def test_touch_on_an_existing_remote_file_answers_409_and_keeps_its_bytes(remote):
    """Mutation: wrap the stat in `try/except Exception: pass` again (no HTTPException
    passthrough); the route answers 200, opens the file "w" and its bytes become b""."""
    remote.sftp.contents["/srv/a.txt"] = b"precious"
    resp = _post(remote, "touch", {"directory": "/srv", "filename": "a.txt"})
    assert (resp.status_code, resp.json()) == (409, {"detail": "File already exists"})
    assert remote.sftp.contents == {"/srv/a.txt": b"precious"}
    assert remote.sftp.calls == [("lstat", "/srv/a.txt")]


def test_upload_onto_an_existing_remote_file_answers_409_and_keeps_its_bytes(remote):
    """Mutation: same swallowed check in /upload; the route answers 200 and replaces
    b"precious" with the uploaded bytes."""
    remote.sftp.contents["/srv/a.bin"] = b"precious"
    resp = remote.client.post(
        f"/api/v1/boxes/{BOX}/upload",
        data={"directory": "/srv"},
        files={"file": ("a.bin", b"replacement", "application/octet-stream")},
        headers=HEADERS,
    )
    assert (resp.status_code, resp.json()) == (409, {"detail": "File already exists"})
    assert remote.sftp.contents == {"/srv/a.bin": b"precious"}
    assert remote.sftp.calls == [("lstat", "/srv/a.bin")]


def test_touch_and_upload_still_create_a_new_remote_file(remote):
    """Mutation: answer 409 whenever stat is called (reject every name); a name that
    does not exist must still be created, with the exact bytes."""
    resp = _post(remote, "touch", {"directory": "/srv", "filename": "new.txt"})
    assert (resp.status_code, resp.json()["path"]) == (200, "/srv/new.txt")
    resp = remote.client.post(
        f"/api/v1/boxes/{BOX}/upload",
        data={"directory": "/srv"},
        files={"file": ("new.bin", b"data", "application/octet-stream")},
        headers=HEADERS,
    )
    assert (resp.status_code, resp.json()["path"]) == (200, "/srv/new.bin")
    assert remote.sftp.contents == {"/srv/new.txt": b"", "/srv/new.bin": b"data"}


# --- only "no such file" counts as free; replace needs the flag (wave 5d, WS-AG) ----
#
# Review: "The 409 check still counts any failed `stat` as 'the name is free', and
# `stat` follows symlinks ... Touch or upload then opens 'w'/'wb' and writes through the
# link, or truncates the file." User: "it shoudl offer to replace it, default choice is
# no." The fake writes through a link on a "w"/"wb" open, as a server does.


def _upload(remote, name: str, data: bytes, overwrite: str | None = None):
    form = {"directory": "/srv"}
    if overwrite is not None:
        form["overwrite"] = overwrite
    return remote.client.post(
        f"/api/v1/boxes/{BOX}/upload",
        data=form,
        files={"file": (name, data, "application/octet-stream")},
        headers=HEADERS,
    )


def test_touch_and_upload_refuse_a_dangling_symlink_and_write_nothing(remote):
    """Mutation: check with `stat` (follows the link) instead of `lstat`; the dangling
    link reads as free and the open writes through it, creating `/etc/target`."""
    remote.sftp.symlinks["/srv/link"] = "/etc/target"
    resp = _post(remote, "touch", {"directory": "/srv", "filename": "link"})
    assert (resp.status_code, resp.json()) == (409, {"detail": "File already exists"})
    resp = _upload(remote, "link", b"payload")
    assert (resp.status_code, resp.json()) == (409, {"detail": "File already exists"})
    assert remote.sftp.contents == {}
    assert remote.sftp.calls == [("lstat", "/srv/link"), ("lstat", "/srv/link")]


def test_touch_and_upload_refuse_a_target_whose_stat_is_denied(remote):
    """Mutation: treat every failed lstat as "the name is free" (`except Exception:
    pass`); the route opens the file and answers 200."""
    remote.sftp.denied.add("/srv/a.txt")
    expected = {"detail": "Cannot check whether /srv/a.txt exists: Permission denied"}
    resp = _post(remote, "touch", {"directory": "/srv", "filename": "a.txt"})
    assert (resp.status_code, resp.json()) == (400, expected)
    resp = _upload(remote, "a.txt", b"payload")
    assert (resp.status_code, resp.json()) == (400, expected)
    assert remote.sftp.contents == {}
    assert remote.sftp.calls == [("lstat", "/srv/a.txt"), ("lstat", "/srv/a.txt")]


def test_a_file_that_appears_after_the_check_is_not_truncated(remote, monkeypatch):
    """Mutation: open the new name with "w"/"wb" instead of the exclusive "x"/"xb"; the
    file created between the check and the open is truncated (touch) or replaced
    (upload) and the route answers 200."""
    remote.sftp.contents["/srv/a.txt"] = b"precious"

    async def lstat_before_it_appeared(path: str):
        remote.sftp.calls.append(("lstat", path))
        raise asyncssh.SFTPNoSuchFile("No such file")

    monkeypatch.setattr(remote.sftp, "lstat", lstat_before_it_appeared)
    resp = _post(remote, "touch", {"directory": "/srv", "filename": "a.txt"})
    assert (resp.status_code, resp.json()) == (409, {"detail": "File already exists"})
    resp = _upload(remote, "a.txt", b"replacement")
    assert (resp.status_code, resp.json()) == (409, {"detail": "File already exists"})
    assert remote.sftp.contents == {"/srv/a.txt": b"precious"}


def test_upload_with_overwrite_replaces_an_existing_remote_file(remote):
    """Mutation: ignore the `overwrite` form field; the upload answers 409 and the old
    bytes stay."""
    remote.sftp.contents["/srv/a.bin"] = b"precious"
    resp = _upload(remote, "a.bin", b"replacement", overwrite="true")
    assert (resp.status_code, resp.json()["path"]) == (200, "/srv/a.bin")
    assert remote.sftp.contents == {"/srv/a.bin": b"replacement"}
    assert remote.sftp.calls == [
        ("lstat", "/srv/a.bin"),
        ("open", "/srv/a.bin", "wb"),
        ("write", "/srv/a.bin", b"replacement"),
    ]


def test_upload_with_overwrite_false_still_refuses(remote):
    """Mutation: replace whenever the field is present (`overwrite is not None`); an
    explicit "false" would replace the file."""
    remote.sftp.contents["/srv/a.bin"] = b"precious"
    resp = _upload(remote, "a.bin", b"replacement", overwrite="false")
    assert (resp.status_code, resp.json()) == (409, {"detail": "File already exists"})
    assert remote.sftp.contents == {"/srv/a.bin": b"precious"}


def test_upload_with_overwrite_never_writes_through_a_symlink(remote):
    """Mutation: let overwrite replace any existing name (drop the regular-file check);
    the bytes are written through the link into `/etc/passwd`."""
    remote.sftp.symlinks["/srv/a.bin"] = "/etc/passwd"
    remote.sftp.contents["/etc/passwd"] = b"root:x:0:0"
    resp = _upload(remote, "a.bin", b"replacement", overwrite="true")
    assert (resp.status_code, resp.json()) == (
        400,
        {"detail": "Only a regular file can be replaced"},
    )
    assert remote.sftp.contents == {"/etc/passwd": b"root:x:0:0"}
    assert remote.sftp.calls == [("lstat", "/srv/a.bin")]


def test_upload_with_overwrite_creates_a_missing_name_exclusively(remote):
    """Mutation: with the flag, open "wb" whatever lstat said; the new file is opened
    non-exclusively (the call log shows "wb", not "xb")."""
    resp = _upload(remote, "new.bin", b"data", overwrite="true")
    assert (resp.status_code, resp.json()["path"]) == (200, "/srv/new.bin")
    assert remote.sftp.contents == {"/srv/new.bin": b"data"}
    assert remote.sftp.calls == [
        ("lstat", "/srv/new.bin"),
        ("open", "/srv/new.bin", "xb"),
        ("write", "/srv/new.bin", b"data"),
    ]


def test_touch_has_no_overwrite_option(remote):
    """Mutation: give /touch the upload's overwrite branch (honour an `overwrite` key);
    the existing file is truncated to b""."""
    remote.sftp.contents["/srv/a.txt"] = b"precious"
    resp = _post(remote, "touch", {"directory": "/srv", "filename": "a.txt", "overwrite": True})
    assert (resp.status_code, resp.json()) == (409, {"detail": "File already exists"})
    assert remote.sftp.contents == {"/srv/a.txt": b"precious"}
