from __future__ import annotations

import hmac
import logging
import os
import time
from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import HTTPException, Request

from ..config import AppConfig, Box, find_box, get_config_path, load_config
from ..config_cache import get_cache
from ..ssh import SSHError, connect

logger = logging.getLogger(__name__)

_CONNECT_FAIL_CACHE: dict[str, float] = {}  # box_name -> timestamp of last failure
_CONNECT_FAIL_COOLDOWN = 60  # seconds


class APIDependencies:
    def __init__(self, settings):
        self.settings = settings
        # Optional broadcaster for the progress-bar fan-out WebSocket.
        # Wired by ``make_app`` so api/progress.py can fan out events without
        # importing webapp.py (avoids circular imports).
        self.broadcast_progress: Callable[[dict[str, Any]], Awaitable[None]] | None = None
        # Optional broadcaster for the ping fan-out WebSocket.
        self.broadcast_ping: Callable[[dict[str, Any]], Awaitable[None]] | None = None

    def require_token(self, request: Request) -> None:
        if not self.settings.csrf_token:
            return
        if request.url.path.endswith("/api/v1/bootstrap"):
            return
        # Accept token from header or query param (query param needed for SSE/EventSource)
        supplied = request.headers.get("x-sshler-token") or request.query_params.get("token")
        expected = self.settings.csrf_token

        if not hmac.compare_digest(supplied or "", expected):
            logger.warning(
                f"Token mismatch on {request.url.path}: "
                f"supplied={supplied[:8] + '...' if supplied else 'None'}, "
                f"expected={expected[:8] + '...' if expected else 'None'}"
            )
            raise HTTPException(status_code=403, detail="Missing or invalid X-SSHLER-TOKEN header")

    async def get_application_config(self) -> AppConfig:
        """Dependency that loads the persisted configuration with caching."""
        config_cache = get_cache(ttl=60)
        config_path = get_config_path()
        ssh_config_env = os.getenv("SSHLER_SSH_CONFIG")
        signature = (
            str(config_path),
            config_path.stat().st_mtime if config_path.exists() else None,
            ssh_config_env,
            os.getenv("SSHLER_CONFIG_DIR"),
        )

        async def _loader() -> AppConfig:
            return load_config(ssh_config_env)

        return await config_cache.get(_loader, signature=signature)

    def get_box_or_404(self, application_config: AppConfig, name: str) -> Box:
        box = find_box(application_config, name)
        if box is None:
            raise HTTPException(status_code=404, detail="Box not found")
        return box

    async def connect_for_box(self, box: Box, application_config: AppConfig):
        # Fast-fail for recently unreachable boxes
        last_fail = _CONNECT_FAIL_CACHE.get(box.name)
        if last_fail and (time.time() - last_fail) < _CONNECT_FAIL_COOLDOWN:
            raise SSHError(f"{box.name}: unreachable (retry in {int(_CONNECT_FAIL_COOLDOWN - (time.time() - last_fail))}s)")

        try:
            conn = await connect(
                box.connect_host,
                box.user,
                port=box.port,
                keyfile=box.keyfile,
                known_hosts=box.known_hosts,
                ssh_config_path=application_config.ssh_config_path,
                ssh_alias=box.ssh_alias,
                allow_alias=self.settings.allow_ssh_alias,
            )
        except (SSHError, OSError):
            _CONNECT_FAIL_CACHE[box.name] = time.time()
            raise

        # Success — clear failure cache
        _CONNECT_FAIL_CACHE.pop(box.name, None)
        return conn

    @staticmethod
    def clear_connect_failure(box_name: str):
        """Clear the connection failure cache for a box (e.g., on manual refresh)."""
        _CONNECT_FAIL_CACHE.pop(box_name, None)
