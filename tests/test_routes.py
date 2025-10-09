import os
import tempfile

import yaml
from fastapi.testclient import TestClient

if "SSHLER_CONFIG_DIR" not in os.environ:
    os.environ["SSHLER_CONFIG_DIR"] = tempfile.mkdtemp(prefix="sshler_")

from sshler.config import ensure_config
from sshler.ssh import SSHError
from sshler.webapp import make_app


def test_directory_listing_returns_error_message(monkeypatch):
    ensure_config()
    client = TestClient(make_app())
    try:

        async def fake_connect(*_args, **_kwargs):
            raise SSHError("boom")

        monkeypatch.setattr("sshler.webapp.connect", fake_connect)

        response = client.get("/box/gabu-server/ls", params={"path": "/home/gabu"})
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

    client = TestClient(make_app())
    try:
        response = client.post("/box/demo-box/fav", params={"path": "/tmp"})
        assert response.status_code == 200
        assert response.text == "ok"
    finally:
        client.close()

    stored = yaml.safe_load((config_dir / "boxes.yaml").read_text(encoding="utf-8"))
    assert stored["boxes"] == [{"name": "demo-box", "favorites": ["/tmp"]}]


def test_create_box_route_persists_data(monkeypatch, tmp_path):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    monkeypatch.setenv("SSHLER_CONFIG_DIR", str(config_dir))
    (config_dir / "boxes.yaml").write_text(
        yaml.safe_dump({"boxes": []}, sort_keys=False), encoding="utf-8"
    )

    client = TestClient(make_app())
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
        )
        assert response.status_code == 303
        assert response.headers["location"] == "/boxes"
    finally:
        client.close()

    stored = yaml.safe_load((config_dir / "boxes.yaml").read_text(encoding="utf-8"))
    assert stored["boxes"] == [
        {
            "name": "custom",
            "host": "192.0.2.10",
            "user": "ubuntu",
            "port": 2200,
            "keyfile": "~/.ssh/custom",
            "favorites": ["/srv", "/var/log"],
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

    client = TestClient(make_app())
    try:
        response = client.post("/box/demo-box/refresh")
        assert response.status_code == 200
        assert response.text == "ok"
    finally:
        client.close()

    stored = yaml.safe_load((config_dir / "boxes.yaml").read_text(encoding="utf-8"))
    assert stored["boxes"] == []
