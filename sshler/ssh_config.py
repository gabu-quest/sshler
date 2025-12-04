from __future__ import annotations

import os
import shlex
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

SSH_CONFIG_ENV = "SSHLER_SSH_CONFIG"
DEFAULT_SSH_CONFIG = Path.home() / ".ssh" / "config"


@dataclass
class HostConfig:
    """Subset of SSH host configuration important to sshler."""

    name: str
    hostname: str | None = None
    user: str | None = None
    port: int | None = None
    identity_files: list[str] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)


def _expand_path(value: str, *, base_dir: Path) -> str:
    expanded = os.path.expandvars(os.path.expanduser(value))
    expanded_path = Path(expanded)
    if expanded_path.is_absolute():
        return str(expanded_path)
    return str((base_dir / expanded_path).resolve())


def _parse_file(path: Path, *, seen: set[Path]) -> dict[str, HostConfig]:
    hosts: dict[str, HostConfig] = {}
    if not path.exists() or not path.is_file():
        return hosts

    real_path = path.resolve()
    if real_path in seen:
        return hosts
    seen.add(real_path)

    current_patterns: list[str] = []
    current_options: dict[str, Any] = {}

    def flush() -> None:
        nonlocal current_patterns, current_options
        if not current_patterns:
            return
        for pattern in current_patterns:
            if any(ch in pattern for ch in "*? "):
                continue
            entry = HostConfig(
                name=pattern,
                hostname=current_options.get("hostname"),
                user=current_options.get("user"),
                port=int(current_options["port"]) if "port" in current_options else None,
                identity_files=[
                    _expand_path(value, base_dir=path.parent)
                    for value in current_options.get("identityfile", [])
                ],
                raw=current_options.copy(),
            )
            hosts[pattern] = entry
        current_patterns = []
        current_options = {}

    with path.open("r", encoding="utf-8") as file_pointer:
        for line in file_pointer:
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            if "#" in stripped:
                before, _, _ = stripped.partition("#")
                stripped = before.strip()
                if not stripped:
                    continue

            try:
                tokens = shlex.split(stripped, comments=False, posix=True)
            except ValueError:
                continue
            if not tokens:
                continue

            keyword = tokens[0].lower()
            values = tokens[1:]
            if keyword == "host":
                flush()
                current_patterns = values
                current_options = {}
                continue

            if keyword == "include" and values:
                include_pattern = values[0]
                parent = path.parent
                for include_path in parent.glob(include_pattern):
                    hosts.update(_parse_file(include_path, seen=seen))
                continue

            if keyword == "match":
                flush()
                current_patterns = []
                current_options = {}
                continue

            if not current_patterns:
                continue

            if keyword in {"hostname", "user", "port"} and values:
                current_options[keyword] = values[-1]
            elif keyword == "identityfile" and values:
                current_options.setdefault("identityfile", []).extend(values)
            else:
                current_options[keyword] = values[-1] if values else None

    flush()
    return hosts


def load_ssh_config(explicit_path: str | None = None) -> dict[str, HostConfig]:
    explicit = explicit_path or os.getenv(SSH_CONFIG_ENV)
    seen: set[Path] = set()
    candidates: list[Path] = []
    if explicit:
        candidates.append(Path(explicit).expanduser())
    else:
        candidates.append(DEFAULT_SSH_CONFIG)

    aggregated: dict[str, HostConfig] = {}
    for candidate in candidates:
        aggregated.update(_parse_file(candidate, seen=seen))
    return aggregated

