"""Ping REST endpoint — fire-and-forget push notifications.

External scripts POST here; every connected ``/ws/ping`` client receives the
event as a Naive UI toast notification. No persistence — pings are ephemeral.
"""

from __future__ import annotations

import secrets
import time
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field

from .dependencies import APIDependencies


class APIPingPush(BaseModel):
    title: str = Field(..., max_length=200)
    body: str | None = Field(default=None, max_length=2000)
    color: str | None = Field(default=None, pattern=r"^(success|warning|error|info)$")
    icon: str | None = Field(default=None, max_length=8)  # emoji shown as notification avatar
    duration: int | None = Field(default=None, ge=1000, le=300_000)  # ms; null = manual dismiss
    source: str | None = Field(default=None, max_length=100)
    metadata: dict[str, Any] | None = None


class APIPingResponse(BaseModel):
    ok: bool
    id: str


def get_router(deps: APIDependencies) -> APIRouter:
    router = APIRouter()

    async def _broadcast(event: dict) -> None:
        if deps.broadcast_ping:
            await deps.broadcast_ping(event)

    @router.post("/ping", response_model=APIPingResponse)
    async def send_ping(body: APIPingPush) -> APIPingResponse:
        ping_id = secrets.token_urlsafe(6)
        event: dict[str, Any] = {
            "type": "ping",
            "id": ping_id,
            "title": body.title,
            "body": body.body,
            "color": body.color,
            "icon": body.icon,
            "duration": body.duration,
            "source": body.source,
            "metadata": body.metadata,
            "sent_at": time.time(),
        }
        await _broadcast(event)
        return APIPingResponse(ok=True, id=ping_id)

    return router
