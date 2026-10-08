"""``~`` in the remote routes outside ``files.py`` (wave 5c, WS-AF item 1).

WS-AC carried: "~ is not resolved in remote routes outside files.py: archive create and
extract, batch delete/move/copy, excel, grep. They pass a literal `~/...` to SFTP or the
shell." Each route now resolves ``~`` and ``~/x`` with the same ``$HOME`` lookup as
``files.py``. A fake connection records the exact command or SFTP path; every test names
its mutation. Shared mutation: drop the route's ``_remote_path`` call, so the literal
``~/...`` reaches the remote and the recorded value differs.
"""

from __future__ import annotations

import io
from pathlib import Path
from types import SimpleNamespace

import openpyxl
import pytest
import yaml
from fastapi import HTTPException
from fastapi.testclient import TestClient
from starlette.requests import Request

from sshler import ssh_pool, state
from sshler.api.dependencies import APIDependencies
from sshler.api.rate_limiting import create_rate_limit_dependency
from sshler.settings import reset_settings
from sshler.ssh_pool import SSHConnectionPool
from sshler.webapp import ServerSettings, make_app

TOKEN = "tilde-token"
BOX = "remote1"
HEADERS = {"X-SSHLER-TOKEN": TOKEN}
HOME_CMD = 'printf %s "$HOME"'
HOME = "/home/tester"


def _reply(stdout: str = "", status: int = 0) -> SimpleNamespace:
    return SimpleNamespace(exit_status=status, returncode=status, stdout=stdout, stderr="")


class FakeFile:
    def __init__(self, sftp: FakeSFTP, path: str) -> None:
        self.sftp = sftp
        self.path = path

    async def __aenter__(self) -> FakeFile:
        return self

    async def __aexit__(self, *exc) -> None:
        return None

    async def read(self, n: int = -1) -> bytes:
        return self.sftp.content


class FakeSFTP:
    def __init__(self, calls: list[tuple], content: bytes = b"") -> None:
        self.calls = calls
        self.content = content

    async def stat(self, path: str):
        self.calls.append(("stat", path))
        return SimpleNamespace(permissions=0o100644)

    async def remove(self, path: str) -> None:
        self.calls.append(("remove", path))

    async def rename(self, source: str, target: str) -> None:
        self.calls.append(("rename", source, target))

    async def open(self, path: str, mode: str):
        self.calls.append(("open", path, mode))
        return FakeFile(self, path)

    async def exit(self) -> None:
        return None


class FakeConnection:
    def __init__(self, replies: dict, log: list[str], sftp: FakeSFTP) -> None:
        self.replies = replies
        self.log = log
        self.sftp = sftp

    async def start_sftp_client(self) -> FakeSFTP:
        return self.sftp

    async def run(self, command: str, check: bool = False, encoding: str | None = "utf-8"):
        if command == "echo test":
            return _reply("test\n")
        self.log.append(command)
        if command not in self.replies:
            raise AssertionError(f"unexpected command: {command}")
        return self.replies[command]

    def close(self) -> None:
        return None


@pytest.fixture
def remote(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
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
    replies: dict[str, SimpleNamespace] = {HOME_CMD: _reply(HOME)}
    log: list[str] = []
    sftp = FakeSFTP([])

    async def fake_connect(self, box, application_config):
        return FakeConnection(replies, log, sftp)

    monkeypatch.setattr(APIDependencies, "connect_for_box", fake_connect)
    monkeypatch.setattr(ssh_pool, "_global_pool", SSHConnectionPool())
    client = TestClient(make_app(ServerSettings(csrf_token=TOKEN)))
    try:
        yield SimpleNamespace(client=client, replies=replies, log=log, sftp=sftp)
    finally:
        client.close()
        state.reset_state()


def _post(remote, route: str, body: dict):
    return remote.client.post(f"/api/v1/boxes/{BOX}/{route}", json=body, headers=HEADERS)


# --- archive -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("fmt", "name", "command"),
    [
        ("tar.gz", "out.tar.gz", f"tar czf {HOME}/out.tar.gz -C / {HOME}/proj"),
        ("zip", "out.zip", f"cd / && zip -r {HOME}/out.zip {HOME}/proj"),
    ],
)
def test_archive_create_resolves_tilde_in_destination_and_sources(remote, fmt, name, command):
    """Mutation: skip `_remote_path` in archive create; the command carries `~/out...`
    and `~/proj` (the shell quotes them, so they are never expanded)."""
    remote.replies[command] = _reply()
    resp = _post(
        remote,
        "archive/create",
        {"paths": ["~/proj"], "destination": "~", "archive_name": name, "format": fmt},
    )
    assert resp.status_code == 200
    assert resp.json()["path"] == f"{HOME}/{name}"
    assert remote.log == [HOME_CMD, HOME_CMD, command]


def test_archive_create_with_a_failed_home_lookup_is_502_and_runs_nothing(remote):
    """Mutation: let the failed lookup fall through as a literal path; the tar command
    would run (the fake raises on it) instead of a clean 502."""
    remote.replies[HOME_CMD] = _reply("", 1)
    resp = _post(
        remote,
        "archive/create",
        {
            "paths": ["~/proj"],
            "destination": "/srv",
            "archive_name": "o.tar.gz",
            "format": "tar.gz",
        },
    )
    assert (resp.status_code, resp.json()) == (
        502,
        {"detail": "Could not resolve the remote home directory"},
    )
    assert remote.log == [HOME_CMD]


@pytest.mark.parametrize(
    ("archive", "command"),
    [
        ("~/a b.tar.gz", f"tar xzf '{HOME}/a b.tar.gz' -C {HOME}/dest"),
        ("~/a.zip", f"unzip -o {HOME}/a.zip -d {HOME}/dest"),
    ],
)
def test_archive_extract_resolves_tilde_in_archive_and_destination(remote, archive, command):
    """Mutation: skip `_remote_path` in extract; the command names `~/a...` and `~/dest`."""
    remote.replies[command] = _reply()
    resp = _post(remote, "archive/extract", {"archive_path": archive, "destination": "~/dest"})
    assert (resp.status_code, resp.json()["path"]) == (200, f"{HOME}/dest")
    assert remote.log == [HOME_CMD, HOME_CMD, command]


# --- batch ---------------------------------------------------------------------


def test_batch_delete_resolves_tilde_paths(remote):
    """Mutation: skip `_resolve_tilde_paths` in batch delete; SFTP is asked about `~/f`."""
    resp = _post(remote, "batch/delete", {"paths": ["~/f"]})
    assert (resp.status_code, resp.json()) == (
        200,
        {"status": "ok", "succeeded": ["~/f"], "failed": []},
    )
    assert remote.sftp.calls == [("stat", f"{HOME}/f"), ("remove", f"{HOME}/f")]


def test_batch_move_resolves_tilde_destination_and_sources(remote):
    """Mutation: skip the `~` resolution in batch move; SFTP renames `~/f` to `~/d/f`."""
    resp = _post(remote, "batch/move", {"paths": ["~/f"], "destination": "~/d"})
    assert (resp.status_code, resp.json()) == (
        200,
        {"status": "ok", "succeeded": ["~/f"], "failed": []},
    )
    assert remote.sftp.calls == [("rename", f"{HOME}/f", f"{HOME}/d/f")]


def test_batch_copy_resolves_tilde_destination_and_sources(remote):
    """Mutation: skip the `~` resolution in batch copy; `cp -r '~/f' '~/d/f'` runs."""
    command = f"cp -r {HOME}/f {HOME}/d/f"
    remote.replies[command] = _reply()
    resp = _post(remote, "batch/copy", {"paths": ["~/f"], "destination": "~/d"})
    assert (resp.status_code, resp.json()) == (
        200,
        {"status": "ok", "succeeded": ["~/f"], "failed": []},
    )
    assert remote.log == [HOME_CMD, HOME_CMD, command]


# --- excel and grep ------------------------------------------------------------


def test_excel_preview_opens_the_resolved_home_path(remote):
    """Mutation: skip `_remote_path` in excel; SFTP opens the literal `~/book.xlsx`."""
    workbook = openpyxl.Workbook()
    buffer = io.BytesIO()
    workbook.save(buffer)
    remote.sftp.content = buffer.getvalue()
    resp = remote.client.get(
        f"/api/v1/boxes/{BOX}/excel", params={"path": "~/book.xlsx"}, headers=HEADERS
    )
    assert resp.status_code == 200
    assert remote.sftp.calls == [("open", f"{HOME}/book.xlsx", "rb")]


def test_grep_searches_the_resolved_home_directory(remote):
    """Mutation: skip `_remote_path` in grep; the command searches `'~/proj'`, which
    the shell never expands, and the response names `~/proj`."""
    command = f"grep -rn -i -m 100 -- needle {HOME}/proj 2>/dev/null"
    remote.replies[command] = _reply(f"{HOME}/proj/a.txt:3:needle\n")
    resp = remote.client.get(
        f"/api/v1/boxes/{BOX}/grep",
        params={"pattern": "needle", "directory": "~/proj"},
        headers=HEADERS,
    )
    assert resp.status_code == 200
    assert resp.json()["directory"] == f"{HOME}/proj"
    assert remote.log == [HOME_CMD, command]


# --- rate limit address (item 2) ----------------------------------------------


def _request(peer: str, real_ip: str | None) -> Request:
    headers = [(b"x-real-ip", real_ip.encode())] if real_ip else []
    return Request({"type": "http", "headers": headers, "client": (peer, 5000)})


@pytest.mark.asyncio
async def test_rate_limit_buckets_by_the_forwarded_client_address(
    monkeypatch: pytest.MonkeyPatch,
):
    """Mutation: key on `request.client.host`; behind the proxy (SSHLER_TRUST_PROXY_HEADERS
    on) every client shares 127.0.0.1, so the second client is refused after the first
    one's request."""
    monkeypatch.setenv("SSHLER_TRUST_PROXY_HEADERS", "true")
    reset_settings()
    limit = create_rate_limit_dependency(
        "test_ws_af_real_ip", rate=1, per=60, capacity_multiplier=1
    )
    await limit(_request("127.0.0.1", "203.0.113.9"))
    with pytest.raises(HTTPException) as refused:
        await limit(_request("127.0.0.1", "203.0.113.9"))
    assert refused.value.status_code == 429
    await limit(_request("127.0.0.1", "203.0.113.10"))
