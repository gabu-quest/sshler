from __future__ import annotations

import os
import sys
from pathlib import Path

from ..config_dir import get_config_dir

_RUNTIME_TOKEN_FILENAME = "runtime-token"


def _runtime_token_path() -> Path:
    """Path to the locally-cached CSRF token written by ``sshler serve``.

    Co-located with ``boxes.yaml`` so it follows ``SSHLER_CONFIG_DIR``.
    """
    return get_config_dir() / _RUNTIME_TOKEN_FILENAME


def _write_runtime_token(token: str) -> None:
    """Persist the active CSRF token for local CLI consumers (mode 0600).

    Best-effort: if the write fails the server keeps running, but local
    `sshler progress` commands lose their token auto-discovery.
    """
    path = _runtime_token_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(token, encoding="utf-8")
        try:
            os.chmod(path, 0o600)
        except OSError:
            # Windows / unusual FS — chmod is advisory there.
            pass
    except OSError as exc:
        print(f"[sshler] warning: could not write runtime token cache: {exc}", file=sys.stderr)


def _read_runtime_token() -> str | None:
    path = _runtime_token_path()
    try:
        if not path.exists():
            return None
        token = path.read_text(encoding="utf-8").strip()
        return token or None
    except OSError:
        return None
