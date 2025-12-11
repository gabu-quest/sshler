from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from .. import state
from ..config import AppConfig
from .dependencies import APIDependencies
from .models import APISession, APISessionCreate, APISessionInfo, APISessionUpdate, APISimpleMessage


def get_router(deps: APIDependencies) -> APIRouter:
    router = APIRouter()

    @router.get("/sessions", response_model=APISessionInfo)
    async def api_sessions(
        box: str | None = Query(None),
        active_only: bool = Query(False),
        limit: int = Query(50, ge=1, le=200),
    ) -> APISessionInfo:
        sessions: list[str] = []
        if box:
            records = await state.list_sessions_async(box, active_only=active_only, limit=limit)
            sessions = [s.session_name for s in records]
        return APISessionInfo(sessions=sessions)

    @router.get("/boxes/{name}/sessions", response_model=list[APISession])
    async def api_list_box_sessions(
        name: str,
        active_only: bool = Query(False),
        limit: int = Query(50, ge=1, le=200),
        application_config: AppConfig = Depends(deps.get_application_config),
    ) -> list[APISession]:
        deps.get_box_or_404(application_config, name)
        records = await state.list_sessions_async(name, active_only=active_only, limit=limit)
        return [
            APISession(
                id=item.id,
                box=item.box,
                session_name=item.session_name,
                working_directory=item.working_directory,
                created_at=item.created_at,
                last_accessed_at=item.last_accessed_at,
                active=item.active,
                window_count=item.window_count,
                metadata=item.metadata,
            )
            for item in records
        ]

    @router.post("/boxes/{name}/sessions", response_model=APISession)
    async def api_create_session(
        name: str,
        payload: APISessionCreate,
        application_config: AppConfig = Depends(deps.get_application_config),
    ) -> APISession:
        deps.get_box_or_404(application_config, name)
        record = await state.create_session_async(
            box_name=name,
            session_name=payload.session_name,
            working_directory=payload.working_directory,
            metadata=payload.metadata or {},
        )
        return APISession(
            id=record.id,
            box=record.box,
            session_name=record.session_name,
            working_directory=record.working_directory,
            created_at=record.created_at,
            last_accessed_at=record.last_accessed_at,
            active=record.active,
            window_count=record.window_count,
            metadata=record.metadata,
        )

    @router.patch("/boxes/{name}/sessions/{session_id}", response_model=APISession)
    async def api_update_session(
        name: str,
        session_id: str,
        payload: APISessionUpdate,
        application_config: AppConfig = Depends(deps.get_application_config),
    ) -> APISession:
        deps.get_box_or_404(application_config, name)
        record = await state.get_session_by_id_async(session_id)
        if record is None or record.box != name:
            raise HTTPException(status_code=404, detail="Session not found")
        if payload.metadata is not None or payload.window_count is not None:
            await state.update_session_metadata_async(
                session_id=record.id,
                metadata=payload.metadata,
                window_count=payload.window_count,
            )
        if payload.active is not None:
            await state.update_session_activity_async(record.id, active=payload.active)
        record = await state.get_session_by_id_async(session_id)
        if record is None:
            raise HTTPException(status_code=404, detail="Session not found")
        return APISession(
            id=record.id,
            box=record.box,
            session_name=record.session_name,
            working_directory=record.working_directory,
            created_at=record.created_at,
            last_accessed_at=record.last_accessed_at,
            active=record.active,
            window_count=record.window_count,
            metadata=record.metadata,
        )

    @router.delete("/boxes/{name}/sessions/{session_id}", response_model=APISimpleMessage)
    async def api_delete_session(
        name: str,
        session_id: str,
        application_config: AppConfig = Depends(deps.get_application_config),
    ) -> APISimpleMessage:
        deps.get_box_or_404(application_config, name)
        record = await state.get_session_by_id_async(session_id)
        if record is None or record.box != name:
            raise HTTPException(status_code=404, detail="Session not found")
        deleted = await state.delete_session_async(session_id)
        if not deleted:
            raise HTTPException(status_code=500, detail="Failed to delete session")
        return APISimpleMessage(status="ok", message="deleted", path=session_id)

    return router
