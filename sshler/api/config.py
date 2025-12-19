from __future__ import annotations

from fastapi import APIRouter, HTTPException

from .. import __version__
from ..ssh_pool import get_pool
from .dependencies import APIDependencies
from .models import APIBootstrap, APIPoolConfig, APIPoolConfigUpdate


def get_router(deps: APIDependencies) -> APIRouter:
    router = APIRouter()

    @router.get("/bootstrap", response_model=APIBootstrap)
    async def api_bootstrap() -> APIBootstrap:
        """Expose runtime settings to bootstrap the SPA."""

        return APIBootstrap(
            version=__version__,
            token_header="X-SSHLER-TOKEN",
            token=deps.settings.csrf_token,
            basic_auth_required=bool(deps.settings.basic_auth),
            allow_origins=deps.settings.allow_origins,
            spa_base="/app/" if deps.settings.serve_spa else "",
            spa_enabled=deps.settings.serve_spa,
        )

    @router.get("/pool/config", response_model=APIPoolConfig)
    async def get_pool_config() -> APIPoolConfig:
        """Get current SSH connection pool configuration."""
        pool = get_pool()
        config = pool.get_config()
        return APIPoolConfig(
            idle_timeout=config["idle_timeout"],
            max_lifetime=config["max_lifetime"],
            max_connections_per_box=config["max_connections_per_box"] or 5,
        )

    @router.put("/pool/config", response_model=APIPoolConfig)
    async def update_pool_config(update: APIPoolConfigUpdate) -> APIPoolConfig:
        """Update SSH connection pool configuration dynamically."""
        pool = get_pool()

        # Validate inputs
        if update.idle_timeout is not None and update.idle_timeout < 0:
            raise HTTPException(status_code=400, detail="idle_timeout must be >= 0 or null")
        if update.max_lifetime is not None and update.max_lifetime < 0:
            raise HTTPException(status_code=400, detail="max_lifetime must be >= 0 or null")
        if update.max_connections_per_box is not None and update.max_connections_per_box < 1:
            raise HTTPException(
                status_code=400, detail="max_connections_per_box must be >= 1"
            )

        # Apply updates
        pool.update_config(
            idle_timeout=update.idle_timeout,
            max_lifetime=update.max_lifetime,
            max_connections_per_box=update.max_connections_per_box,
        )

        # Return updated config
        config = pool.get_config()
        return APIPoolConfig(
            idle_timeout=config["idle_timeout"],
            max_lifetime=config["max_lifetime"],
            max_connections_per_box=config["max_connections_per_box"] or 5,
        )

    return router
