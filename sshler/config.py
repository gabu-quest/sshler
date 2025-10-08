from __future__ import annotations

import getpass
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from platformdirs import user_config_dir

from .ssh_config import HostConfig, load_ssh_config

ENV_CONFIG_DIR = "SSHLER_CONFIG_DIR"


@dataclass
class StoredBox:
    """User-defined overrides and custom boxes persisted in YAML."""

    name: str
    host: str | None = None
    user: str | None = None
    port: int | None = None
    keyfile: str | None = None
    agent: bool = False
    favorites: list[str] = field(default_factory=list)
    default_dir: str | None = None
    known_hosts: str | None = None


@dataclass
class Box:
    """Concrete SSH box presented in the UI after merging sources."""

    name: str
    connect_host: str
    display_host: str
    user: str
    port: int = 22
    keyfile: str | None = None
    agent: bool = False
    favorites: list[str] = field(default_factory=list)
    default_dir: str | None = None
    known_hosts: str | None = None
    source: str = "custom"


@dataclass
class AppConfig:
    """Complete configuration containing merged boxes and stored overrides."""

    boxes: list[Box] = field(default_factory=list)
    stored: dict[str, StoredBox] = field(default_factory=dict)
    ssh_config_path: str | None = None

    def get_box(self, name: str) -> Box | None:
        for box in self.boxes:
            if box.name == name:
                return box
        return None

    def get_or_create_stored(self, name: str) -> StoredBox:
        stored = self.stored.get(name)
        if stored is None:
            stored = StoredBox(name=name)
            self.stored[name] = stored
        return stored


DEFAULT_CONFIGURATION_TEMPLATE: dict[str, Any] = {"boxes": []}


def get_config_dir() -> Path:
    """Return the configuration directory, creating it when missing."""

    override_directory = os.getenv(ENV_CONFIG_DIR)
    if override_directory:
        configuration_dir = Path(override_directory).expanduser()
    else:
        configuration_dir = Path(user_config_dir(appname="sshler", appauthor=False))
    configuration_dir.mkdir(parents=True, exist_ok=True)
    return configuration_dir


def get_config_path() -> Path:
    """Return the path to the boxes configuration file."""

    return get_config_dir() / "boxes.yaml"


def ensure_config() -> Path:
    """Create a default configuration file when none exists."""

    config_path = get_config_path()
    if not config_path.exists():
        with config_path.open("w", encoding="utf-8") as file_pointer:
            yaml.safe_dump(DEFAULT_CONFIGURATION_TEMPLATE, file_pointer, sort_keys=False)
    return config_path


def load_config(ssh_config_path: str | None = None) -> AppConfig:
    """Load the application configuration from disk and merge SSH config hosts."""

    config_path = ensure_config()
    with config_path.open("r", encoding="utf-8") as file_pointer:
        raw_data = yaml.safe_load(file_pointer) or {}

    stored = {}
    for entry in raw_data.get("boxes", []):
        stored_box = _stored_box_from_dict(entry)
        stored[stored_box.name] = stored_box

    _remove_legacy_seed(stored)

    resolved_path = _resolve_ssh_config_path(ssh_config_path)
    boxes = _build_boxes(stored, load_ssh_config(resolved_path))
    return AppConfig(
        boxes=boxes,
        stored=stored,
        ssh_config_path=str(resolved_path) if resolved_path else None,
    )


def save_config(application_config: AppConfig) -> None:
    """Persist stored overrides to disk."""

    config_path = get_config_path()
    payload = {
        "boxes": [
            _stored_box_to_dict(stored)
            for stored in sorted(
                application_config.stored.values(), key=lambda item: item.name.lower()
            )
        ]
    }
    with config_path.open("w", encoding="utf-8") as file_pointer:
        yaml.safe_dump(payload, file_pointer, sort_keys=False)


def find_box(application_config: AppConfig, name: str) -> Box | None:
    """Return the box matching ``name`` when present."""

    return application_config.get_box(name)


def rebuild_boxes(application_config: AppConfig, ssh_config_path: str | None = None) -> None:
    """Refresh the merged box list after stored overrides change."""

    resolved_path = _resolve_ssh_config_path(ssh_config_path or application_config.ssh_config_path)
    application_config.ssh_config_path = str(resolved_path) if resolved_path else None
    application_config.boxes = _build_boxes(
        application_config.stored, load_ssh_config(resolved_path)
    )


def _stored_box_from_dict(data: dict[str, Any]) -> StoredBox:
    favorites = data.get("favorites") or []
    return StoredBox(
        name=data["name"],
        host=data.get("host"),
        user=data.get("user"),
        port=int(data["port"]) if "port" in data and data["port"] is not None else None,
        keyfile=data.get("keyfile"),
        agent=bool(data.get("agent", False)),
        favorites=list(favorites),
        default_dir=data.get("default_dir"),
        known_hosts=data.get("known_hosts"),
    )


def _stored_box_to_dict(stored: StoredBox) -> dict[str, Any]:
    result: dict[str, Any] = {"name": stored.name}
    if stored.host:
        result["host"] = stored.host
    if stored.user:
        result["user"] = stored.user
    if stored.port is not None:
        result["port"] = int(stored.port)
    if stored.keyfile:
        result["keyfile"] = stored.keyfile
    if stored.agent:
        result["agent"] = stored.agent
    if stored.favorites:
        result["favorites"] = stored.favorites
    if stored.default_dir:
        result["default_dir"] = stored.default_dir
    if stored.known_hosts:
        result["known_hosts"] = stored.known_hosts
    return result


def _build_boxes(stored: dict[str, StoredBox], ssh_hosts: dict[str, HostConfig]) -> list[Box]:
    boxes: list[Box] = []
    seen: set[str] = set()

    for name, host_config in ssh_hosts.items():
        stored_override = stored.get(name)
        boxes.append(_merge_host(name, host_config, stored_override))
        seen.add(name)

    for name, stored_override in stored.items():
        if name not in seen:
            boxes.append(_merge_host(name, None, stored_override))

    boxes.sort(key=lambda item: item.name.lower())
    return boxes


def _merge_host(
    name: str, host_config: HostConfig | None, stored_override: StoredBox | None
) -> Box:
    if stored_override and stored_override.host:
        connect_host = stored_override.host
    else:
        connect_host = name

    if stored_override and stored_override.host:
        display_host = stored_override.host
    elif host_config and host_config.hostname:
        display_host = host_config.hostname
    else:
        display_host = name

    base_user = stored_override.user if stored_override and stored_override.user else None
    if base_user is None and host_config and host_config.user:
        base_user = host_config.user
    if base_user is None:
        base_user = _default_user()

    base_port = stored_override.port if stored_override and stored_override.port else None
    if base_port is None and host_config and host_config.port:
        base_port = host_config.port
    if base_port is None:
        base_port = 22

    base_keyfile = stored_override.keyfile if stored_override and stored_override.keyfile else None
    if base_keyfile is None and host_config and host_config.identity_files:
        base_keyfile = host_config.identity_files[0]

    favorites = list(stored_override.favorites) if stored_override else []
    default_dir = stored_override.default_dir if stored_override else None
    known_hosts = stored_override.known_hosts if stored_override else None
    agent = stored_override.agent if stored_override else False
    source = "ssh_config" if host_config else "custom"

    return Box(
        name=name,
        connect_host=connect_host,
        display_host=display_host,
        user=base_user,
        port=base_port,
        keyfile=base_keyfile,
        agent=agent,
        favorites=favorites,
        default_dir=default_dir,
        known_hosts=known_hosts,
        source=source,
    )


def _default_user() -> str:
    try:
        return getpass.getuser()
    except Exception:
        return ""


def _remove_legacy_seed(stored: dict[str, StoredBox]) -> None:
    legacy = stored.get("gabu-server")
    if not legacy:
        return
    if legacy.host == "example.tailnet.ts.net" and legacy.user == "gabu":
        if legacy.favorites:
            legacy.favorites.clear()


def _resolve_ssh_config_path(explicit: str | None = None) -> Path | None:
    if explicit:
        return Path(explicit).expanduser()
    env_override = os.getenv("SSHLER_SSH_CONFIG")
    if env_override:
        return Path(env_override).expanduser()
    default_path = Path.home() / ".ssh" / "config"
    return default_path if default_path.exists() else default_path
