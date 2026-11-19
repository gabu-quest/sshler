import os

import pytest
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
                        "name": "test-server",
                        "host": "example.tailnet.ts.net",
                        "user": "alice",
                        "favorites": ["/home/alice"],
                    }
                ]
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    monkeypatch.setenv("SSHLER_SSH_CONFIG", "")

    config = load_config()
    stored = config.stored.get("test-server")
    assert isinstance(stored, StoredBox)
    assert stored.favorites == []
    assert state.list_favorites("test-server") == ["/home/alice"]

    data = yaml.safe_load((config_dir / "boxes.yaml").read_text(encoding="utf-8"))
    assert data["boxes"][0].get("favorites") is None


def _stored_app_config(*names: str):
    from sshler.config import AppConfig

    return AppConfig(
        boxes=[],
        stored={n: StoredBox(name=n, host=f"{n}.example", user="u") for n in names},
        ssh_config_path=None,
    )


def test_save_config_failed_dump_keeps_old_file_and_leaves_no_temp(monkeypatch, tmp_path):
    from sshler import config as config_module

    monkeypatch.setenv("SSHLER_CONFIG_DIR", str(tmp_path))
    boxes_yaml = tmp_path / "boxes.yaml"
    boxes_yaml.write_text("boxes: []\n", encoding="utf-8")

    def half_dump(data, stream, **kwargs):
        stream.write("boxes:\n- name: par")
        raise OSError("disk full")

    monkeypatch.setattr(config_module.yaml, "safe_dump", half_dump)
    with pytest.raises(OSError, match="disk full"):
        config_module.save_config(_stored_app_config("a"))

    assert boxes_yaml.read_text(encoding="utf-8") == "boxes: []\n"
    assert [p.name for p in tmp_path.iterdir() if p.name.endswith(".tmp")] == []


def test_save_config_creates_missing_config_folder(monkeypatch, tmp_path):
    from sshler.config import save_config

    monkeypatch.setenv("SSHLER_CONFIG_DIR", str(tmp_path / "new" / "dir"))
    save_config(_stored_app_config("a"))
    data = yaml.safe_load((tmp_path / "new" / "dir" / "boxes.yaml").read_text(encoding="utf-8"))
    assert [b["name"] for b in data["boxes"]] == ["a"]


def test_save_config_keeps_existing_mode(monkeypatch, tmp_path):
    from sshler.config import save_config

    monkeypatch.setenv("SSHLER_CONFIG_DIR", str(tmp_path))
    boxes_yaml = tmp_path / "boxes.yaml"
    boxes_yaml.write_text("boxes: []\n", encoding="utf-8")
    boxes_yaml.chmod(0o600)
    save_config(_stored_app_config("a"))
    assert oct(boxes_yaml.stat().st_mode & 0o777) == "0o600"


def test_save_config_new_file_gets_umask_mode(monkeypatch, tmp_path):
    from sshler.config import save_config

    monkeypatch.setenv("SSHLER_CONFIG_DIR", str(tmp_path))
    old = os.umask(0o022)
    try:
        save_config(_stored_app_config("a"))
    finally:
        os.umask(old)
    assert oct((tmp_path / "boxes.yaml").stat().st_mode & 0o777) == "0o644"


def test_save_config_writes_through_symlink(monkeypatch, tmp_path):
    from sshler.config import save_config

    cfg = tmp_path / "cfg"
    cfg.mkdir()
    dotfiles = tmp_path / "dotfiles"
    dotfiles.mkdir()
    target = dotfiles / "boxes.yaml"
    target.write_text("boxes: []\n", encoding="utf-8")
    (cfg / "boxes.yaml").symlink_to(target)
    monkeypatch.setenv("SSHLER_CONFIG_DIR", str(cfg))

    save_config(_stored_app_config("a"))

    assert (cfg / "boxes.yaml").is_symlink()
    assert [b["name"] for b in yaml.safe_load(target.read_text(encoding="utf-8"))["boxes"]] == ["a"]
    assert sorted(p.name for p in dotfiles.iterdir()) == ["boxes.yaml"]


def test_save_config_two_saves_keep_last(monkeypatch, tmp_path):
    from sshler.config import save_config

    monkeypatch.setenv("SSHLER_CONFIG_DIR", str(tmp_path))
    save_config(_stored_app_config("a"))
    save_config(_stored_app_config("b"))
    data = yaml.safe_load((tmp_path / "boxes.yaml").read_text(encoding="utf-8"))
    assert [b["name"] for b in data["boxes"]] == ["b"]


def test_missing_default_ssh_config_path_is_returned_as_is(monkeypatch, tmp_path):
    from sshler.config import _resolve_ssh_config_path

    monkeypatch.delenv("SSHLER_SSH_CONFIG", raising=False)
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    assert _resolve_ssh_config_path() == tmp_path / ".ssh" / "config"
