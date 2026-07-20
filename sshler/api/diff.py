"""Diff Notebook server-side persistence — short URLs (/app/diff/n/<id>).

Notebooks are immutable: every save creates a new id. There is no PUT/upsert. To
"edit" a shared notebook the client forks: edits drop ``serverId`` and switch
back to the ``?n=<base64>`` URL form. Hitting "Save & share" again issues a new
id. This keeps shared links stable forever.

ACL: token-gated like every other API. No per-author binding — any client with
the sshler token may read/save/delete any notebook. Acceptable for a single-user
localhost tool.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from fastapi import APIRouter, HTTPException, Path
from pydantic import BaseModel, Field, field_validator

from .. import state
from .dependencies import APIDependencies

logger = logging.getLogger(__name__)

# Accept the shape ``secrets.token_urlsafe(8)`` produces, plus longer tokens in
# case operators want to seed something longer manually. Refuses traversal-shaped
# inputs (``..``, ``/``) and any whitespace.
_ID_RE = re.compile(r"^[A-Za-z0-9_-]{8,32}$")
_MAX_ENVELOPE_BYTES = 1_000_000  # 1 MB hard cap on the saved envelope
_MAX_LABEL_LEN = 200


class APIDiffNotebookEnvelope(BaseModel):
    """The frontend's notebook envelope. The server treats `cells` as opaque."""

    v: int = Field(..., description="Envelope schema version")
    cells: list[dict[str, Any]] = Field(...)
    # `def` is a Python keyword; expose it as `def_` and map via alias.
    def_: dict[str, Any] | None = Field(default=None, alias="def")

    model_config = {"populate_by_name": True}

    @field_validator("v")
    @classmethod
    def _version(cls, value: int) -> int:
        if value != 1:
            raise ValueError("Envelope version must be 1")
        return value


class APIDiffNotebookSave(BaseModel):
    envelope: APIDiffNotebookEnvelope
    label: str = Field(default="", max_length=_MAX_LABEL_LEN)


class APIDiffNotebookMeta(BaseModel):
    id: str
    label: str
    cell_count: int
    created_at: float
    updated_at: float


class APIDiffNotebookFull(APIDiffNotebookMeta):
    envelope: APIDiffNotebookEnvelope


class APIDiffNotebookList(BaseModel):
    notebooks: list[APIDiffNotebookMeta]


def _validate_id(notebook_id: str) -> None:
    if not _ID_RE.match(notebook_id):
        raise HTTPException(status_code=404, detail="Diff notebook not found")


def _meta_from(row: state.DiffNotebook) -> APIDiffNotebookMeta:
    return APIDiffNotebookMeta(
        id=row.id,
        label=row.label,
        cell_count=row.cell_count,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _full_from(row: state.DiffNotebook) -> APIDiffNotebookFull:
    try:
        envelope_obj = json.loads(row.envelope_json) if row.envelope_json else {"v": 1, "cells": []}
    except (json.JSONDecodeError, TypeError) as exc:
        # Corrupt persisted envelope. Better to 500 than silently return garbage.
        logger.error("Corrupt envelope_json for notebook %s: %s", row.id, exc)
        raise HTTPException(status_code=500, detail="Stored notebook is corrupt") from exc
    envelope = APIDiffNotebookEnvelope.model_validate(envelope_obj)
    return APIDiffNotebookFull(
        id=row.id,
        label=row.label,
        cell_count=row.cell_count,
        created_at=row.created_at,
        updated_at=row.updated_at,
        envelope=envelope,
    )


def get_router(deps: APIDependencies) -> APIRouter:  # noqa: ARG001 - deps reserved for symmetry
    router = APIRouter()

    @router.get("/diff/notebooks", response_model=APIDiffNotebookList)
    async def list_notebooks() -> APIDiffNotebookList:
        rows = await state.list_diff_notebooks_async()
        return APIDiffNotebookList(notebooks=[_meta_from(r) for r in rows])

    @router.get(
        "/diff/notebooks/{notebook_id}",
        response_model=APIDiffNotebookFull,
        response_model_exclude_none=True,
        response_model_by_alias=True,
    )
    async def get_notebook(notebook_id: str = Path(..., min_length=8, max_length=32)) -> APIDiffNotebookFull:
        _validate_id(notebook_id)
        row = await state.get_diff_notebook_async(notebook_id)
        if row is None:
            raise HTTPException(status_code=404, detail="Diff notebook not found")
        return _full_from(row)

    @router.post(
        "/diff/notebooks",
        response_model=APIDiffNotebookFull,
        response_model_exclude_none=True,
        response_model_by_alias=True,
    )
    async def create_notebook(body: APIDiffNotebookSave) -> APIDiffNotebookFull:
        envelope_obj = body.envelope.model_dump(by_alias=True, exclude_none=True)
        envelope_json = json.dumps(envelope_obj)
        if len(envelope_json) > _MAX_ENVELOPE_BYTES:
            raise HTTPException(
                status_code=413,
                detail=f"Notebook envelope too large (>{_MAX_ENVELOPE_BYTES} bytes)",
            )
        row = await state.save_diff_notebook_async(
            label=body.label,
            envelope_json=envelope_json,
            cell_count=len(body.envelope.cells),
        )
        return _full_from(row)

    @router.delete("/diff/notebooks/{notebook_id}")
    async def delete_notebook(notebook_id: str = Path(..., min_length=8, max_length=32)) -> dict[str, bool]:
        _validate_id(notebook_id)
        removed = await state.delete_diff_notebook_async(notebook_id)
        return {"ok": True, "removed": removed}

    return router
