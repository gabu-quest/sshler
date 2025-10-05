from __future__ import annotations

import os
from dataclasses import asdict, dataclass, field
from pathlib import Path

import yaml
from platformdirs import user_config_dir

ENV_CONFIG_DIR = "SSHLER_CONFIG_DIR"


@dataclass
class Box:
    """Connection details for a single SSH host.

    Attributes:
        name: Human-friendly identifier shown in the UI.
        host: Fully qualified domain name or IP of the host.
        user: Remote user account used for SSH.
        port: SSH port exposed by the host.
        keyfile: Optional path to the private key used for authentication.
        agent: Whether to rely on the local SSH agent.
        favorites: Directories surfaced as quick links in the UI.
        default_dir: Directory to open when no path is provided.
        known_hosts: Either ``"ignore"`` or a path to a known-hosts file.
    """

    name: str
    host: str
    user: str
    port: int = 22
    keyfile: str | None = None
    agent: bool = False
    favorites: list[str] = field(default_factory=list)
    default_dir: str | None = None
    known_hosts: str | None = None


@dataclass
class AppConfig:
    """Collection of SSH boxes persisted to disk.

    Attributes:
        boxes: Ordered list of user-defined SSH boxes.
    """

    boxes: list[Box] = field(default_factory=list)


DEFAULT_CONFIGURATION_TEMPLATE: dict[str, object] = {
    "boxes": [
        {
            "name": "gabu-server",
            "host": "example.tailnet.ts.net",
            "user": "gabu",
            "port": 22,
            "keyfile": "C:/Users/gabu/.ssh/id_ed25519",
            "favorites": [
                "/home/gabu",
                "/home/gabu/projects",
                "/srv/codex",
            ],
            "default_dir": "/home/gabu",
        }
    ]
}


def get_config_dir() -> Path:
    """Return the configuration directory, creating it when missing.

    Returns:
        Path: Absolute path to the directory storing user configuration.
    """

    override_directory = os.getenv(ENV_CONFIG_DIR)
    if override_directory:
        configuration_dir = Path(override_directory).expanduser()
    else:
        configuration_dir = Path(user_config_dir(appname="sshler", appauthor=False))
    configuration_dir.mkdir(parents=True, exist_ok=True)
    return configuration_dir


def get_config_path() -> Path:
    """Return the path to the boxes configuration file.

    Returns:
        Path: Location of ``boxes.yaml`` inside the configuration directory.
    """

    return get_config_dir() / "boxes.yaml"


def ensure_config() -> Path:
    """Create a default configuration file when none exists.

    Returns:
        Path: Path to the ensured configuration file.
    """

    config_path = get_config_path()
    if not config_path.exists():
        with config_path.open("w", encoding="utf-8") as file_pointer:
            yaml.safe_dump(DEFAULT_CONFIGURATION_TEMPLATE, file_pointer, sort_keys=False)
    return config_path


def load_config() -> AppConfig:
    """Load the application configuration from disk.

    Returns:
        AppConfig: Deserialized configuration structure.
    """

    config_path = ensure_config()
    with config_path.open("r", encoding="utf-8") as file_pointer:
        raw_data = yaml.safe_load(file_pointer) or {}
    box_items = [Box(**box_data) for box_data in raw_data.get("boxes", [])]
    return AppConfig(boxes=box_items)


def save_config(application_config: AppConfig) -> None:
    """Persist the provided configuration to disk.

    Args:
        application_config: Configuration instance to serialise to YAML.
    """

    config_path = get_config_path()
    serialized = {"boxes": [asdict(box) for box in application_config.boxes]}
    with config_path.open("w", encoding="utf-8") as file_pointer:
        yaml.safe_dump(serialized, file_pointer, sort_keys=False)


def find_box(application_config: AppConfig, name: str) -> Box | None:
    """Return the box matching ``name`` when present.

    Args:
        application_config: Configuration to search within.
        name: Box name to locate.

    Returns:
        Box | None: Matching ``Box`` instance when found, otherwise ``None``.
    """

    for box in application_config.boxes:
        if box.name == name:
            return box
    return None
