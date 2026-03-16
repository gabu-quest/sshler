"""Modular API routers for sshler."""

from fastapi import APIRouter, Depends

from .archive import get_router as archive_router
from .batch import get_router as batch_router
from .boxes import get_router as boxes_router
from .config import get_router as config_router
from .dependencies import APIDependencies
from .files import get_router as files_router
from .grep import get_router as grep_router
from .recovery import get_router as recovery_router
from .search import get_router as search_router
from .sessions import get_router as sessions_router
from .snippets import get_router as snippets_router
from .terminal import get_router as terminal_router
from .tunnels import get_router as tunnels_router


def create_api_router(deps: APIDependencies) -> APIRouter:
    api_router = APIRouter(prefix="/api/v1", tags=["api"], dependencies=[Depends(deps.require_token)])
    api_router.include_router(archive_router(deps))
    api_router.include_router(batch_router(deps))
    api_router.include_router(config_router(deps))
    api_router.include_router(boxes_router(deps))
    api_router.include_router(files_router(deps))
    api_router.include_router(grep_router(deps))
    api_router.include_router(recovery_router(deps))
    api_router.include_router(search_router(deps))
    api_router.include_router(sessions_router(deps))
    api_router.include_router(snippets_router(deps))
    api_router.include_router(terminal_router(deps))
    api_router.include_router(tunnels_router(deps))
    return api_router


__all__ = ["create_api_router", "APIDependencies"]
