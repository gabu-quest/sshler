from __future__ import annotations

from fastapi import APIRouter, Request

from .dependencies import APIDependencies
from .models import APITerminalHandshake


def get_router(deps: APIDependencies) -> APIRouter:
    router = APIRouter()

    @router.get("/terminal/handshake", response_model=APITerminalHandshake)
    async def api_terminal_handshake(
        request: Request,
    ) -> APITerminalHandshake:
        base = request.url.hostname or "localhost"
        scheme = "wss" if request.url.scheme == "https" else "ws"
        port_segment = f":{request.url.port}" if request.url.port else ""
        ws_url = f"{scheme}://{base}{port_segment}/ws/term"
        return APITerminalHandshake(
            ws_url=ws_url,
            token_header="X-SSHLER-TOKEN",
            token=deps.settings.csrf_token,
        )

    return router
