from __future__ import annotations

import getpass
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from platformdirs import user_config_dir

from . import state
from .ssh_config import HostConfig, load_ssh_config

ENV_CONFIG_DIR = "SSHLER_CONFIG_DIR"


@dataclass
class StoredBox:
    """User-defined overrides and custom boxes persisted in YAML.

    English:
        Represents a single box entry saved by the user. Values here override
        hosts discovered from OpenSSH configuration files.

    日本語:
        ユーザーが保存したボックス定義を表します。OpenSSH の設定から検出した
        ホスト情報に対する上書き値として利用されます。
    """

    name: str
    host: str | None = None
    user: str | None = None
    port: int | None = None
    keyfile: str | None = None
    agent: bool = False
    favorites: list[str] = field(default_factory=list)
    default_dir: str | None = None
    known_hosts: str | None = None
    ssh_alias: str | None = None


@dataclass
class Box:
    """Concrete SSH box presented in the UI after merging sources.

    English:
        Materialised box configuration shown in the UI after combining SSH
        config values with stored overrides and synthetic entries such as the
        local workspace.

    日本語:
        SSH 設定、保存済みの上書き、ローカルワークスペースなどを統合した結果を
        UI に表示するための構造体です。
    """

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
    ssh_alias: str | None = None
    resolved_host: str | None = None
    transport: str = "ssh"


@dataclass
class AppConfig:
    """Complete configuration containing merged boxes and stored overrides.

    English:
        In-memory representation of all known boxes plus metadata such as the
        resolved SSH config path.

    日本語:
        既知のすべてのボックス情報と、解決済みの SSH 設定パスなどのメタデータを
        保持するメモリ上の設定です。
    """

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
    """Return the configuration directory, creating it when missing.

    English:
        Determines the directory that will contain ``boxes.yaml`` and creates
        it if necessary.

    日本語:
        ``boxes.yaml`` を格納するディレクトリを決定し、存在しない場合は作成します。
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

    English:
        Combines :func:`get_config_dir` with ``boxes.yaml`` to produce the full
        configuration filename.

    日本語:
        :func:`get_config_dir` の結果に ``boxes.yaml`` を連結した設定ファイルのパスを
        返します。
    """

    return get_config_dir() / "boxes.yaml"


def ensure_config() -> Path:
    """Create a default configuration file when none exists.

    English:
        Writes an empty configuration file so later reads never fail because the
        file is missing.

    日本語:
        設定ファイルが存在しない場合に空のファイルを作成し、読み込みに失敗しない
        ようにします。
    """

    config_path = get_config_path()
    if not config_path.exists():
        with config_path.open("w", encoding="utf-8") as file_pointer:
            yaml.safe_dump(DEFAULT_CONFIGURATION_TEMPLATE, file_pointer, sort_keys=False)
    return config_path


def load_config(ssh_config_path: str | None = None) -> AppConfig:
    """Load the application configuration from disk and merge SSH config hosts.

    English:
        Reads ``boxes.yaml``, normalises data, merges it with OpenSSH hosts, and
        returns a populated :class:`AppConfig` including the synthetic local box.

    日本語:
        ``boxes.yaml`` を読み込んで正規化し、OpenSSH のホスト情報と統合したうえで
        ローカルボックスを含む :class:`AppConfig` を返します。
    """

    config_dir = get_config_dir()
    state.initialize(config_dir)
    config_path = ensure_config()
    with config_path.open("r", encoding="utf-8") as file_pointer:
        raw_data = yaml.safe_load(file_pointer) or {}

    stored = {}
    for entry in raw_data.get("boxes", []):
        stored_box = _stored_box_from_dict(entry)
        stored[stored_box.name] = stored_box

    migrated = state.migrate_legacy_favorites(stored)
    _remove_legacy_seed(stored)

    resolved_path = _resolve_ssh_config_path(ssh_config_path)
    boxes = _build_boxes(stored, load_ssh_config(resolved_path))
    boxes.insert(0, _build_local_box())
    _apply_favorites(boxes)
    app_config = AppConfig(
        boxes=boxes,
        stored=stored,
        ssh_config_path=str(resolved_path) if resolved_path else None,
    )
    if migrated:
        save_config(app_config)
    return app_config


def save_config(application_config: AppConfig) -> None:
    """Persist stored overrides to disk.

    English:
        Serialises the ``stored`` mapping to ``boxes.yaml`` so user-created
        overrides survive restarts.

    日本語:
        ``stored`` マッピングを ``boxes.yaml`` に書き出し、ユーザーが作成した上書き
        設定を永続化します。
    """

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
    """Return the box matching ``name`` when present.

    English:
        Helper used by request handlers to locate a box by name.

    日本語:
        リクエストハンドラがボックス名で検索するためのヘルパー関数です。
    """

    return application_config.get_box(name)


def rebuild_boxes(application_config: AppConfig, ssh_config_path: str | None = None) -> None:
    """Refresh the merged box list after stored overrides change.

    English:
        Recomputes the list of concrete boxes based on the latest stored data
        and (optionally) a new SSH config file path.

    日本語:
        最新の保存情報と SSH 設定パス (必要に応じて) を基に、利用可能なボックスの
        リストを再構築します。
    """

    state.initialize(get_config_dir())
    resolved_path = _resolve_ssh_config_path(ssh_config_path or application_config.ssh_config_path)
    application_config.ssh_config_path = str(resolved_path) if resolved_path else None
    application_config.boxes = _build_boxes(
        application_config.stored, load_ssh_config(resolved_path)
    )
    _apply_favorites(application_config.boxes)


def _apply_favorites(boxes: list[Box]) -> None:
    if not boxes:
        return
    mapping = state.favorites_map([box.name for box in boxes])
    for box in boxes:
        box.favorites = mapping.get(box.name, [])


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
        ssh_alias=data.get("ssh_alias"),
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
    if stored.default_dir:
        result["default_dir"] = stored.default_dir
    if stored.known_hosts:
        result["known_hosts"] = stored.known_hosts
    if stored.ssh_alias:
        result["ssh_alias"] = stored.ssh_alias
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


def _build_local_box() -> Box:
    home_directory = str(Path.home())
    return Box(
        name="local",
        connect_host="local",
        display_host="localhost",
        user=_default_user(),
        port=0,
        agent=False,
        favorites=[],
        default_dir=home_directory,
        known_hosts=None,
        source="local",
        ssh_alias=None,
        resolved_host=None,
        transport="local",
    )


def _merge_host(
    name: str, host_config: HostConfig | None, stored_override: StoredBox | None
) -> Box:
    if stored_override and stored_override.host:
        connect_host = stored_override.host
    elif host_config and host_config.hostname:
        connect_host = host_config.hostname
    else:
        connect_host = name

    if stored_override and stored_override.host:
        display_host = stored_override.host
    else:
        display_host = name

    if stored_override and stored_override.ssh_alias:
        ssh_alias = stored_override.ssh_alias
    else:
        ssh_alias = name

    resolved_host = host_config.hostname if host_config and host_config.hostname else None

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
        favorites=[],
        default_dir=default_dir,
        known_hosts=known_hosts,
        source=source,
        ssh_alias=ssh_alias,
        resolved_host=resolved_host,
        transport="ssh",
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
