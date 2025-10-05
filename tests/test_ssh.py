from types import SimpleNamespace

import pytest

from sshler.ssh import sftp_list_directory


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
