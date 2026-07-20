"""Global progress-bar REST endpoints.

External scripts push updates here; the frontend listens via the ``/ws/progress``
WebSocket. There is no per-box gating — anyone holding the sshler token may
push or read any bar.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from fastapi import APIRouter, HTTPException, Path
from pydantic import BaseModel, Field

from .. import state
from .dependencies import APIDependencies

logger = logging.getLogger(__name__)

_NAME_RE = re.compile(r"^[A-Za-z0-9._:-]{1,64}$")
_ALLOWED_STATUSES = frozenset({"running", "done", "failed", "cancelled"})


_METADATA_MAX_BYTES = 4096
_METADATA_MAX_KEYS = 32


class APIProgressBar(BaseModel):
    name: str
    current: float
    total: float
    color: str | None = None
    label: str | None = None
    status: str
    created_at: float
    updated_at: float
    metadata: dict[str, Any] = Field(default_factory=dict)
    metadata_error: str | None = None


class APIProgressPush(BaseModel):
    current: float = Field(..., ge=0)
    total: float = Field(..., gt=0)
    color: str | None = Field(default=None, max_length=32)
    label: str | None = Field(default=None, max_length=200)
    status: str = Field(default="running")
    # Typed Any (not dict) on purpose: malformed metadata must never 400 — it is
    # validated leniently below and surfaced as metadata_error while the bar keeps
    # advancing. ``merge`` opts into shallow-merge instead of the default replace.
    metadata: Any = None
    merge: bool = False


class APIProgressList(BaseModel):
    bars: list[APIProgressBar]


def _to_api(bar: state.ProgressBar) -> APIProgressBar:
    return APIProgressBar(
        name=bar.name,
        current=bar.current,
        total=bar.total,
        color=bar.color,
        label=bar.label,
        status=bar.status,
        created_at=bar.created_at,
        updated_at=bar.updated_at,
        metadata=bar.metadata,
        metadata_error=bar.metadata_error,
    )


def _validate_name(name: str) -> None:
    if not _NAME_RE.match(name):
        raise HTTPException(
            status_code=400,
            detail="Invalid name: must match ^[A-Za-z0-9._:-]{1,64}$",
        )


def _validate_metadata(raw: Any) -> tuple[dict | None, str | None]:
    """Leniently validate pushed metadata.

    Returns ``(clean_dict, None)`` on success or ``(None, reason)`` when the
    payload is unusable. Never raises — a bad metadata payload must not block the
    progress update, only surface as ``metadata_error`` on the bar.
    """
    if raw is None:
        return {}, None  # explicit clear
    if not isinstance(raw, dict):
        return None, "metadata must be a JSON object"
    if len(raw) > _METADATA_MAX_KEYS:
        return None, f"metadata has too many keys (>{_METADATA_MAX_KEYS})"
    try:
        encoded = json.dumps(raw)
    except (TypeError, ValueError):
        return None, "metadata is not JSON-serializable"
    if len(encoded) > _METADATA_MAX_BYTES:
        return None, f"metadata too large (>{_METADATA_MAX_BYTES} bytes)"
    return raw, None


def get_router(deps: APIDependencies) -> APIRouter:
    router = APIRouter()

    async def _broadcast(event: dict[str, Any]) -> None:
        broadcaster = deps.broadcast_progress
        if broadcaster is None:
            return
        try:
            await broadcaster(event)
        except Exception as exc:  # pragma: no cover - best-effort fan-out
            logger.warning("progress broadcast failed: %s", exc)

    @router.get("/progress", response_model=APIProgressList)
    async def list_bars() -> APIProgressList:
        bars = await state.list_progress_async()
        return APIProgressList(bars=[_to_api(b) for b in bars])

    @router.get("/progress/{name}", response_model=APIProgressBar)
    async def get_bar(name: str = Path(...)) -> APIProgressBar:
        _validate_name(name)
        bar = await state.get_progress_async(name)
        if bar is None:
            raise HTTPException(status_code=404, detail="Progress bar not found")
        return _to_api(bar)

    @router.post("/progress/{name}", response_model=APIProgressBar)
    async def push_bar(
        body: APIProgressPush,
        name: str = Path(...),
    ) -> APIProgressBar:
        _validate_name(name)
        if body.status not in _ALLOWED_STATUSES:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid status: must be one of {sorted(_ALLOWED_STATUSES)}",
            )
        apply_metadata = "metadata" in body.model_fields_set
        clean_metadata: dict | None = None
        metadata_error: str | None = None
        if apply_metadata:
            clean_metadata, metadata_error = _validate_metadata(body.metadata)
        bar = await state.upsert_progress_async(
            name=name,
            current=body.current,
            total=body.total,
            color=body.color,
            label=body.label,
            status=body.status,
            apply_metadata=apply_metadata,
            metadata=clean_metadata,
            metadata_error=metadata_error,
            merge_metadata=body.merge,
        )
        api_bar = _to_api(bar)
        await _broadcast({"type": "upsert", "name": name, "bar": api_bar.model_dump()})
        return api_bar

    @router.delete("/progress/{name}")
    async def delete_bar(name: str = Path(...)) -> dict[str, bool]:
        _validate_name(name)
        removed = await state.delete_progress_async(name)
        if removed:
            await _broadcast({"type": "delete", "name": name, "bar": None})
        return {"ok": True, "removed": removed}

    return router
