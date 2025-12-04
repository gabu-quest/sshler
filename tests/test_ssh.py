from types import SimpleNamespace

import pytest

from sshler import ssh as ssh_module
from sshler.ssh import connect, sftp_list_directory


@pytest.mark.asyncio
async def test_sftp_list_directory_skips_entries_on_stat_failure():
    class FakeSFTP:
        def __init__(self) -> None:
            self.exit_called = False

        async def listdir(self, path: str):
            return ["ok", "broken"]

        async def stat(self, path: str):
            if path.endswith("broken"):
                raise OSError("boom")
            return SimpleNamespace(permissions=0o40000, size=123)

        async def exit(self):
            self.exit_called = True

    class FakeConnection:
        def __init__(self) -> None:
            self.client = FakeSFTP()

        async def start_sftp_client(self):
            return self.client

    connection = FakeConnection()
    entries = await sftp_list_directory(connection, "/tmp")

    assert entries == [{"name": "ok", "is_directory": True, "size": 123}]
    assert connection.client.exit_called is True


@pytest.mark.asyncio
async def test_connect_uses_alias_when_dns_fails(monkeypatch):
    calls = {}

    async def fake_asyncssh_connect(**kwargs):
        calls.update(kwargs)
        return "connection"

    async def fake_expand(alias: str):
        return {
            "hostname": "1.2.3.4",
            "user": "alias-user",
            "port": "2200",
            "identityfile": "/keys/alias",
        }

    monkeypatch.setattr(ssh_module.asyncssh, "connect", fake_asyncssh_connect)
    monkeypatch.setattr(ssh_module, "_expand_alias", fake_expand)
    monkeypatch.setattr(ssh_module, "_is_resolvable", lambda name: False)

    result = await connect(
        host="gabu-server",
        user="gabu",
        port=22,
        keyfile=None,
        known_hosts=None,
        ssh_config_path=None,
        ssh_alias="gabu-server",
    )

    assert result == "connection"
    assert calls["host"] == "1.2.3.4"
    assert calls["username"] == "gabu"
    assert calls["port"] == 2200
    assert calls["client_keys"] == ["/keys/alias"]


@pytest.mark.asyncio
async def test_connect_skips_alias_when_disabled(monkeypatch):
    calls = {}

    async def fake_asyncssh_connect(**kwargs):
        calls.update(kwargs)
        return "connection"

    expand_called = False

    async def fake_expand(alias: str):
        nonlocal expand_called
        expand_called = True
        return {"hostname": "1.2.3.4"}

    monkeypatch.setattr(ssh_module.asyncssh, "connect", fake_asyncssh_connect)
    monkeypatch.setattr(ssh_module, "_expand_alias", fake_expand)
    monkeypatch.setattr(ssh_module, "_is_resolvable", lambda name: False)

    result = await connect(
        host="gabu-server",
        user="gabu",
        port=22,
        keyfile=None,
        known_hosts=None,
        ssh_config_path=None,
        ssh_alias="gabu-server",
        allow_alias=False,
    )

    assert result == "connection"
    assert expand_called is False
    assert calls["host"] == "gabu-server"
