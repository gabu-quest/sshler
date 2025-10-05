from __future__ import annotations

import logging
import os

from fastapi import HTTPException, Request

from ..config import AppConfig, Box, find_box, get_config_path, load_config
from ..config_cache import get_cache
from ..ssh import connect

logger = logging.getLogger(__name__)


class APIDependencies:
    def __init__(self, settings):
        self.settings = settings

    def require_token(self, request: Request) -> None:
        if not self.settings.csrf_token:
            return
        if request.url.path.endswith("/api/v1/bootstrap"):
            return
        supplied = request.headers.get("x-sshler-token")
        expected = self.settings.csrf_token

        if supplied != expected:
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
        return await connect(
            box.connect_host,
            box.user,
            port=box.port,
            keyfile=box.keyfile,
            known_hosts=box.known_hosts,
            ssh_config_path=application_config.ssh_config_path,
            ssh_alias=box.ssh_alias,
            allow_alias=self.settings.allow_ssh_alias,
        )
