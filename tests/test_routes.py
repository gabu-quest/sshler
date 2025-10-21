import os
import tempfile

import yaml
from fastapi.testclient import TestClient

from sshler import state
from sshler.config import ensure_config
from sshler.ssh import SSHError
from sshler.webapp import ServerSettings, make_app

if "SSHLER_CONFIG_DIR" not in os.environ:
    os.environ["SSHLER_CONFIG_DIR"] = tempfile.mkdtemp(prefix="sshler_")


TEST_TOKEN = "test-token"


def build_client() -> TestClient:
    return TestClient(make_app(ServerSettings(csrf_token=TEST_TOKEN)))


def test_directory_listing_returns_error_message(monkeypatch):
    ensure_config()
    client = build_client()
    try:

        async def fake_connect(*_args, **_kwargs):
            raise SSHError("boom")

        monkeypatch.setattr("sshler.webapp.connect", fake_connect)

        response = client.get("/box/gabu-server/ls", params={"path": "/home/gabu"})
        if response.status_code != 200:
            print('RESPONSE', response.status_code, response.text)
            try:
                detail = response.json()
            except Exception:
                detail = response.text
            raise AssertionError(detail)
        assert response.status_code == 200
        assert "SSH connection failed: boom" in response.text
    finally:
        client.close()


def test_toggle_favorite_persists_for_imported_host(monkeypatch, tmp_path):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    monkeypatch.setenv("SSHLER_CONFIG_DIR", str(config_dir))
    (config_dir / "boxes.yaml").write_text(
        yaml.safe_dump({"boxes": []}, sort_keys=False), encoding="utf-8"
    )

    ssh_config = tmp_path / "ssh_config"
    ssh_config.write_text(
        """
Host demo-box
  HostName demo.internal
  User demo
""".strip(),
        encoding="utf-8",
    )
    monkeypatch.setenv("SSHLER_SSH_CONFIG", str(ssh_config))

    client = build_client()
    try:
        response = client.post(
            "/box/demo-box/fav",
            params={"path": "/tmp"},
            headers={"X-SSHLER-TOKEN": TEST_TOKEN},
        )
        if response.status_code != 200:
            try:
                detail = response.json()
            except Exception:
                detail = response.text
            raise AssertionError(detail)
        assert response.status_code == 200
        assert response.text == "ok"
    finally:
        client.close()

    assert state.list_favorites("demo-box") == ["/tmp"]
    stored = yaml.safe_load((config_dir / "boxes.yaml").read_text(encoding="utf-8"))
    assert stored["boxes"] == []


def test_toggle_favorite_normalizes_path(monkeypatch, tmp_path):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    monkeypatch.setenv("SSHLER_CONFIG_DIR", str(config_dir))
    (config_dir / "boxes.yaml").write_text(
        yaml.safe_dump({"boxes": []}, sort_keys=False), encoding="utf-8"
    )

    ssh_config = tmp_path / "ssh_config"
    ssh_config.write_text(
        """
Host demo-box
  HostName demo.internal
  User demo
""".strip(),
        encoding="utf-8",
    )
    monkeypatch.setenv("SSHLER_SSH_CONFIG", str(ssh_config))

    client = build_client()
    try:
        response = client.post(
            "/box/demo-box/fav",
            params={"path": "/home/demo/../logs/.."},
            headers={"X-SSHLER-TOKEN": TEST_TOKEN},
        )
        if response.status_code != 200:
            try:
                detail = response.json()
            except Exception:
                detail = response.text
            raise AssertionError(detail)
        assert response.status_code == 200
        assert response.text == "ok"
    finally:
        client.close()

    assert state.list_favorites("demo-box") == ["/home"]


def test_create_box_route_persists_data(monkeypatch, tmp_path):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    monkeypatch.setenv("SSHLER_CONFIG_DIR", str(config_dir))
    (config_dir / "boxes.yaml").write_text(
        yaml.safe_dump({"boxes": []}, sort_keys=False), encoding="utf-8"
    )

    client = build_client()
    try:
        response = client.post(
            "/boxes/new",
            data={
                "name": "custom",
                "host": "192.0.2.10",
                "user": "ubuntu",
                "port": "2200",
                "keyfile": "~/.ssh/custom",
                "favorites": "/srv\n/var/log",
                "default_dir": "/srv",
            },
            follow_redirects=False,
            headers={"X-SSHLER-TOKEN": TEST_TOKEN},
        )
        assert response.status_code == 303
        assert response.headers["location"] == "/boxes"
    finally:
        client.close()

    assert state.list_favorites("custom") == ["/srv", "/var/log"]
    stored = yaml.safe_load((config_dir / "boxes.yaml").read_text(encoding="utf-8"))
    assert stored["boxes"] == [
        {
            "name": "custom",
            "host": "192.0.2.10",
            "user": "ubuntu",
            "port": 2200,
            "keyfile": "~/.ssh/custom",
            "default_dir": "/srv",
        }
    ]


def test_refresh_box_clears_overrides(monkeypatch, tmp_path):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    monkeypatch.setenv("SSHLER_CONFIG_DIR", str(config_dir))
    (config_dir / "boxes.yaml").write_text(
        yaml.safe_dump(
            {
                "boxes": [
                    {
                        "name": "demo-box",
                        "host": "stale-host",
                        "user": "override",
                        "ssh_alias": "override-alias",
                    }
                ]
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    ssh_config = tmp_path / "ssh_config"
    ssh_config.write_text(
        """
Host demo-box
  HostName fresh.example
  User deploy
""".strip(),
        encoding="utf-8",
    )
    monkeypatch.setenv("SSHLER_SSH_CONFIG", str(ssh_config))

    client = build_client()
    try:
        response = client.post(
            "/box/demo-box/refresh",
            headers={"X-SSHLER-TOKEN": TEST_TOKEN},
        )
        assert response.status_code == 200
        assert response.text == "ok"
    finally:
        client.close()

    assert state.list_favorites("demo-box") == []
    stored = yaml.safe_load((config_dir / "boxes.yaml").read_text(encoding="utf-8"))
    assert stored["boxes"] == []


def test_file_preview_route(monkeypatch, tmp_path):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    monkeypatch.setenv("SSHLER_CONFIG_DIR", str(config_dir))
    (config_dir / "boxes.yaml").write_text(
        yaml.safe_dump({"boxes": []}, sort_keys=False), encoding="utf-8"
    )

    ssh_config = tmp_path / "ssh_config"
    ssh_config.write_text(
        """
Host demo
  HostName demo.internal
  User demo
""".strip(),
        encoding="utf-8",
    )
    monkeypatch.setenv("SSHLER_SSH_CONFIG", str(ssh_config))

    class FakeConnection:
        def close(self):
            pass

    async def fake_connect(*_args, **_kwargs):
        return FakeConnection()

    async def fake_read(_connection, path):
        return f"content-{path}"

    monkeypatch.setattr("sshler.webapp.connect", fake_connect)
    monkeypatch.setattr("sshler.webapp.sftp_read_file", fake_read)
    monkeypatch.setattr("sshler.ssh.sftp_read_file", fake_read)

    client = build_client()
    try:
        response = client.get("/box/demo/cat", params={"path": "/etc/os-release"})
        assert response.status_code == 200
        assert "content-/etc/os-release" in response.text
    finally:
        client.close()


def test_edit_file_get(monkeypatch, tmp_path):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    monkeypatch.setenv("SSHLER_CONFIG_DIR", str(config_dir))
    (config_dir / "boxes.yaml").write_text(
        yaml.safe_dump({"boxes": []}, sort_keys=False), encoding="utf-8"
    )

    ssh_config = tmp_path / "ssh_config"
    ssh_config.write_text(
        """
Host demo
  HostName demo.internal
  User demo
""".strip(),
        encoding="utf-8",
    )
    monkeypatch.setenv("SSHLER_SSH_CONFIG", str(ssh_config))

    class FakeReader:
        def __init__(self, data: str):
            self.data = data.encode("utf-8")

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def read(self, _size: int):
            return self.data

    class FakeSFTP:
        async def open(self, path: str, mode: str, encoding: str | None = None):
            return FakeReader(f"existing-{path}")

        async def exit(self):
            pass

    class FakeConnection:
        def close(self):
            pass

        async def start_sftp_client(self):
            return FakeSFTP()

    async def fake_connect(*_args, **_kwargs):
        return FakeConnection()

    monkeypatch.setattr("sshler.webapp.connect", fake_connect)

    client = TestClient(make_app(ServerSettings(csrf_token=None)))
    try:
        response = client.get("/box/demo/edit", params={"path": "/etc/hosts"})
        assert response.status_code == 200
        assert "existing-/etc/hosts" in response.text
    finally:
        client.close()


def test_edit_file_post(monkeypatch, tmp_path):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    monkeypatch.setenv("SSHLER_CONFIG_DIR", str(config_dir))
    (config_dir / "boxes.yaml").write_text(
        yaml.safe_dump({"boxes": []}, sort_keys=False), encoding="utf-8"
    )

    ssh_config = tmp_path / "ssh_config"
    ssh_config.write_text(
        """
Host demo
  HostName demo.internal
  User demo
""".strip(),
        encoding="utf-8",
    )
    monkeypatch.setenv("SSHLER_SSH_CONFIG", str(ssh_config))

    class FakeWriter:
        def __init__(self, sink, path):
            self.sink = sink
            self.path = path

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def write(self, data):
            self.sink.append(self.path)
            self.sink.append(data)

    class FakeSFTPSave:
        def __init__(self, sink):
            self.sink = sink

        async def open(self, path, mode, encoding: str | None = None):
            return FakeWriter(self.sink, path)

        async def exit(self):
            pass

    records = []

    class FakeConnectionSave:
        def close(self):
            pass

        async def start_sftp_client(self):
            return FakeSFTPSave(records)

    async def fake_connect_save(*_args, **_kwargs):
        return FakeConnectionSave()

    monkeypatch.setattr("sshler.webapp.connect", fake_connect_save)

    client = TestClient(make_app(ServerSettings(csrf_token=None)))
    try:
        response = client.post(
            "/box/demo/edit",
            params={"path": "/etc/hosts"},
            json={"path": "/etc/hosts", "content": "hello"},
        )
        assert response.status_code == 200
        assert records == ["/etc/hosts", "hello"]
    finally:
        client.close()
