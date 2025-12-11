from __future__ import annotations

import contextlib
import time
from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends

from ..config import AppConfig, Box, StoredBox, rebuild_boxes, save_config
from .. import state
from .dependencies import APIDependencies
from .models import APIBox, APIBoxStatus, APIFavoriteToggle, APIPinToggle

if TYPE_CHECKING:  # pragma: no cover - type checking only
    from ..webapp import ServerSettings  # noqa: F401


def _box_to_api(box: StoredBox | Box) -> APIBox:
    """Convert box dataclass into API schema."""

    if isinstance(box, Box):
        host = box.display_host or box.connect_host
        return APIBox(
            name=box.name,
            host=host,
            user=box.user,
            port=box.port,
            transport=box.transport,
            pinned=box.pinned,
            default_dir=box.default_dir,
            favorites=box.favorites,
        )

    host = box.host or box.ssh_alias or box.name
    return APIBox(
        name=box.name,
        host=host,
        user=box.user or "",
        port=box.port or 22,
        transport="ssh",
        pinned=box.pinned,
        default_dir=box.default_dir,
        favorites=box.favorites,
    )


def get_router(deps: APIDependencies) -> APIRouter:
    router = APIRouter()

    @router.get("/boxes", response_model=list[APIBox])
    async def api_list_boxes(
        application_config: AppConfig = Depends(deps.get_application_config),
    ) -> list[APIBox]:
        return [_box_to_api(box) for box in application_config.boxes]

    @router.get("/boxes/{name}", response_model=APIBox)
    async def api_get_box(
        name: str,
        application_config: AppConfig = Depends(deps.get_application_config),
    ) -> APIBox:
        return _box_to_api(deps.get_box_or_404(application_config, name))

    @router.get("/boxes/{name}/status", response_model=APIBoxStatus)
    async def api_box_status(
        name: str,
        application_config: AppConfig = Depends(deps.get_application_config),
    ) -> APIBoxStatus:
        box = deps.get_box_or_404(application_config, name)
        if box.transport == "local":
            return APIBoxStatus(name=box.name, status="online", latency_ms=0)

        start = time.time()
        try:
            conn = await deps.connect_for_box(box, application_config)
            try:
                await conn.run("true", check=True)
            finally:
                with contextlib.suppress(Exception):
                    conn.close()
            latency = (time.time() - start) * 1000
            return APIBoxStatus(name=box.name, status="online", latency_ms=latency)
        except Exception:
            return APIBoxStatus(name=box.name, status="offline", latency_ms=None)

    @router.post("/boxes/{name}/pin", response_model=APIPinToggle)
    async def api_toggle_pin(
        name: str,
        application_config: AppConfig = Depends(deps.get_application_config),
    ) -> APIPinToggle:
        box = deps.get_box_or_404(application_config, name)
        stored = application_config.get_or_create_stored(name)
        stored.pinned = not stored.pinned
        save_config(application_config)
        rebuild_boxes(application_config)
        return APIPinToggle(name=box.name, pinned=stored.pinned)

    @router.post("/boxes/{name}/fav", response_model=APIFavoriteToggle)
    async def api_toggle_favorite(
        name: str,
        payload: APIFavoriteToggle,
        application_config: AppConfig = Depends(deps.get_application_config),
    ) -> APIFavoriteToggle:
        box = deps.get_box_or_404(application_config, name)
        stored = application_config.get_or_create_stored(name)
        favorites = set(stored.favorites or [])
        if payload.favorite:
            favorites.add(payload.path)
        else:
            favorites.discard(payload.path)
        stored.favorites = sorted(favorites)
        await state.replace_favorites_async(name, stored.favorites)
        save_config(application_config)
        rebuild_boxes(application_config)
        return APIFavoriteToggle(path=payload.path, favorite=payload.favorite)

    return router
