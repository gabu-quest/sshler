from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Query

from .. import state
from .dependencies import APIDependencies
from .models import APISimpleMessage, APISnippet, APISnippetCreate, APISnippetUpdate

logger = logging.getLogger(__name__)

GLOBAL_BOX = "__global__"


def _snippet_to_api(s: state.Snippet) -> APISnippet:
    return APISnippet(
        id=s.id,
        box=s.box,
        label=s.label,
        command=s.command,
        category=s.category,
        sort_order=s.sort_order,
        created_at=s.created_at,
    )


def get_router(deps: APIDependencies) -> APIRouter:
    router = APIRouter()

    @router.get("/snippets", response_model=list[APISnippet])
    async def api_list_snippets(
        box: str = Query(..., description="Box name to list snippets for"),
    ) -> list[APISnippet]:
        records = await state.list_snippets_async(box)
        return [_snippet_to_api(r) for r in records]

    @router.post("/snippets", response_model=APISnippet)
    async def api_create_snippet(
        payload: APISnippetCreate,
    ) -> APISnippet:
        if not payload.label.strip():
            raise HTTPException(status_code=422, detail="Label cannot be empty")
        if not payload.command.strip():
            raise HTTPException(status_code=422, detail="Command cannot be empty")
        record = await state.create_snippet_async(
            box=payload.box,
            label=payload.label.strip(),
            command=payload.command,
            category=payload.category.strip(),
        )
        return _snippet_to_api(record)

    @router.put("/snippets/{snippet_id}", response_model=APISnippet)
    async def api_update_snippet(
        snippet_id: str,
        payload: APISnippetUpdate,
    ) -> APISnippet:
        record = await state.update_snippet_async(
            snippet_id=snippet_id,
            label=payload.label,
            command=payload.command,
            category=payload.category,
            sort_order=payload.sort_order,
        )
        if record is None:
            raise HTTPException(status_code=404, detail="Snippet not found")
        return _snippet_to_api(record)

    @router.delete("/snippets/{snippet_id}", response_model=APISimpleMessage)
    async def api_delete_snippet(
        snippet_id: str,
    ) -> APISimpleMessage:
        deleted = await state.delete_snippet_async(snippet_id)
        if not deleted:
            raise HTTPException(status_code=404, detail="Snippet not found")
        return APISimpleMessage(status="ok", message="deleted", path=snippet_id)

    return router
