from __future__ import annotations

from fastapi import APIRouter

from .. import __version__
from .dependencies import APIDependencies
from .models import APIBootstrap


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

    return router
