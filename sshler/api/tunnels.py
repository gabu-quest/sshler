from __future__ import annotations

import logging
import secrets
import time
from dataclasses import dataclass, field

from fastapi import APIRouter, Depends, HTTPException

from ..config import AppConfig
from .dependencies import APIDependencies
from .models import APISimpleMessage, APITunnel, APITunnelCreate

logger = logging.getLogger(__name__)


@dataclass
class TunnelInfo:
    id: str
    box: str
    tunnel_type: str
    local_host: str
    local_port: int
    remote_host: str
    remote_port: int
    listener: object = field(default=None, repr=False)
    created_at: float = field(default_factory=time.time)


# In-memory registry — tunnels are ephemeral (die with server)
_active_tunnels: dict[str, TunnelInfo] = {}


def _tunnel_to_api(t: TunnelInfo) -> APITunnel:
    return APITunnel(
        id=t.id,
        box=t.box,
        tunnel_type=t.tunnel_type,
        local_host=t.local_host,
        local_port=t.local_port,
        remote_host=t.remote_host,
        remote_port=t.remote_port,
        created_at=t.created_at,
    )


def get_router(deps: APIDependencies) -> APIRouter:
    router = APIRouter()

    @router.get("/boxes/{name}/tunnels", response_model=list[APITunnel])
    async def api_list_tunnels(
        name: str,
        application_config: AppConfig = Depends(deps.get_application_config),
    ) -> list[APITunnel]:
        deps.get_box_or_404(application_config, name)
        tunnels = [t for t in _active_tunnels.values() if t.box == name]
        tunnels.sort(key=lambda t: t.created_at)
        return [_tunnel_to_api(t) for t in tunnels]

    @router.post("/boxes/{name}/tunnels", response_model=APITunnel)
    async def api_create_tunnel(
        name: str,
        payload: APITunnelCreate,
        application_config: AppConfig = Depends(deps.get_application_config),
    ) -> APITunnel:
        box = deps.get_box_or_404(application_config, name)

        if payload.tunnel_type not in ("local", "remote"):
            raise HTTPException(status_code=422, detail="tunnel_type must be 'local' or 'remote'")
        if not (1 <= payload.local_port <= 65535):
            raise HTTPException(status_code=422, detail="local_port must be 1-65535")
        if not (1 <= payload.remote_port <= 65535):
            raise HTTPException(status_code=422, detail="remote_port must be 1-65535")

        tunnel_id = secrets.token_urlsafe(12)

        try:
            connection = await deps.connect_for_box(box, application_config)

            if payload.tunnel_type == "local":
                listener = await connection.forward_local_port(
                    listen_host=payload.local_host,
                    listen_port=payload.local_port,
                    dest_host=payload.remote_host,
                    dest_port=payload.remote_port,
                )
            else:
                listener = await connection.forward_remote_port(
                    listen_host=payload.remote_host,
                    listen_port=payload.remote_port,
                    dest_host=payload.local_host,
                    dest_port=payload.local_port,
                )
        except Exception as exc:
            logger.warning(f"Failed to create tunnel for {name}: {exc}", exc_info=True)
            raise HTTPException(status_code=500, detail="Tunnel creation failed") from exc

        info = TunnelInfo(
            id=tunnel_id,
            box=name,
            tunnel_type=payload.tunnel_type,
            local_host=payload.local_host,
            local_port=payload.local_port,
            remote_host=payload.remote_host,
            remote_port=payload.remote_port,
            listener=listener,
        )
        _active_tunnels[tunnel_id] = info
        logger.info(
            f"Tunnel {tunnel_id} created: {payload.tunnel_type} "
            f"{payload.local_host}:{payload.local_port} <-> "
            f"{payload.remote_host}:{payload.remote_port} via {name}"
        )
        return _tunnel_to_api(info)

    @router.delete("/boxes/{name}/tunnels/{tunnel_id}", response_model=APISimpleMessage)
    async def api_delete_tunnel(
        name: str,
        tunnel_id: str,
        application_config: AppConfig = Depends(deps.get_application_config),
    ) -> APISimpleMessage:
        deps.get_box_or_404(application_config, name)

        info = _active_tunnels.get(tunnel_id)
        if not info or info.box != name:
            raise HTTPException(status_code=404, detail="Tunnel not found")

        try:
            if info.listener and hasattr(info.listener, "close"):
                info.listener.close()
        except Exception as exc:
            logger.warning(f"Error closing tunnel {tunnel_id}: {exc}")

        del _active_tunnels[tunnel_id]
        logger.info(f"Tunnel {tunnel_id} closed for {name}")
        return APISimpleMessage(status="ok", message="closed", path=tunnel_id)

    return router
