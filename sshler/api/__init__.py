"""Modular API routers for sshler."""

from fastapi import APIRouter, Depends

from .boxes import get_router as boxes_router
from .config import get_router as config_router
from .dependencies import APIDependencies
from .files import get_router as files_router
from .search import get_router as search_router
from .sessions import get_router as sessions_router
from .terminal import get_router as terminal_router


def create_api_router(deps: APIDependencies) -> APIRouter:
    api_router = APIRouter(prefix="/api/v1", tags=["api"], dependencies=[Depends(deps.require_token)])
    api_router.include_router(config_router(deps))
    api_router.include_router(boxes_router(deps))
    api_router.include_router(files_router(deps))
    api_router.include_router(search_router(deps))
    api_router.include_router(sessions_router(deps))
    api_router.include_router(terminal_router(deps))
    return api_router


__all__ = ["create_api_router", "APIDependencies"]
