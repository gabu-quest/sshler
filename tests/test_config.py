import yaml

from sshler import state
from sshler.config import Box, StoredBox, load_config


def test_merge_includes_ssh_config_hosts(monkeypatch, tmp_path):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    monkeypatch.setenv("SSHLER_CONFIG_DIR", str(config_dir))

    boxes_yaml = config_dir / "boxes.yaml"
    boxes_yaml.write_text(
        yaml.safe_dump(
            {
                "boxes": [
                    {
                        "name": "custom-only",
                        "host": "10.0.0.5",
                        "user": "ubuntu",
                        "favorites": ["/var/www"],
                        "default_dir": "/var/www",
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
Host merged-box
  HostName merged.example
  User deploy
  Port 2201
  IdentityFile ~/.ssh/merged_key
""".strip(),
        encoding="utf-8",
    )
    monkeypatch.setenv("SSHLER_SSH_CONFIG", str(ssh_config))

    config = load_config()
    names = {box.name: box for box in config.boxes}

    assert {"local", "custom-only", "merged-box"}.issubset(set(names))
    merged = names["merged-box"]
    assert isinstance(merged, Box)
    assert merged.display_host == "merged-box"
    assert merged.connect_host == "merged.example"
    assert merged.user == "deploy"
    assert merged.port == 2201
    assert merged.source == "ssh_config"
    assert merged.ssh_alias == "merged-box"
    assert merged.resolved_host == "merged.example"
    custom = names["custom-only"]
    assert custom.display_host == "10.0.0.5"
    assert custom.connect_host == "10.0.0.5"
    assert custom.source == "custom"
    assert custom.favorites == ["/var/www"]
    assert state.list_favorites("custom-only") == ["/var/www"]
    assert custom.ssh_alias == "custom-only"
    assert custom.resolved_host is None

    local = names["local"]
    assert isinstance(local, Box)
    assert local.transport == "local"


def test_legacy_seed_favorites_are_cleared(tmp_path, monkeypatch):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    monkeypatch.setenv("SSHLER_CONFIG_DIR", str(config_dir))

    (config_dir / "boxes.yaml").write_text(
        yaml.safe_dump(
            {
                "boxes": [
                    {
                        "name": "gabu-server",
                        "host": "example.tailnet.ts.net",
                        "user": "gabu",
                        "favorites": ["/home/gabu"],
                    }
                ]
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    monkeypatch.setenv("SSHLER_SSH_CONFIG", "")

    config = load_config()
    stored = config.stored.get("gabu-server")
    assert isinstance(stored, StoredBox)
    assert stored.favorites == []
    assert state.list_favorites("gabu-server") == ["/home/gabu"]

    data = yaml.safe_load((config_dir / "boxes.yaml").read_text(encoding="utf-8"))
    assert data["boxes"][0].get("favorites") is None
