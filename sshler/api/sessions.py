from __future__ import annotations

import asyncio
import logging
import platform

from fastapi import APIRouter, Depends, HTTPException, Query

from .. import state
from ..config import AppConfig
from .dependencies import APIDependencies
from .models import APISession, APISessionCreate, APISessionInfo, APISessionUpdate, APISimpleMessage

logger = logging.getLogger(__name__)


def _local_tmux_command() -> list[str]:
    """Get the base tmux command for local execution."""
    if platform.system() == "Windows":
        return ["wsl", "--", "tmux"]
    return ["tmux"]


async def _get_live_tmux_sessions_local() -> set[str]:
    """Get live tmux session names from local system."""
    command = _local_tmux_command() + ["list-sessions", "-F", "#{session_name}"]
    try:
        process = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await process.communicate()
        if process.returncode == 0 and stdout:
            return set(line.strip() for line in stdout.decode().strip().split("\n") if line.strip())
    except Exception as exc:
        logger.debug(f"Failed to list local tmux sessions: {exc}")
    return set()


async def _get_live_tmux_sessions_remote(connection) -> set[str]:
    """Get live tmux session names from remote host via SSH."""
    try:
        result = await connection.run("tmux list-sessions -F '#{session_name}'", check=False)
        if result.returncode == 0 and result.stdout:
            return set(line.strip() for line in result.stdout.strip().split("\n") if line.strip())
    except Exception as exc:
        logger.debug(f"Failed to list remote tmux sessions: {exc}")
    return set()


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
        kill_tmux: bool = Query(False),
        application_config: AppConfig = Depends(deps.get_application_config),
    ) -> APISimpleMessage:
        box = deps.get_box_or_404(application_config, name)
        record = await state.get_session_by_id_async(session_id)
        if record is None or record.box != name:
            raise HTTPException(status_code=404, detail="Session not found")

        if kill_tmux and record.session_name:
            try:
                if box.transport == "local":
                    cmd = _local_tmux_command() + ["kill-session", "-t", record.session_name]
                    process = await asyncio.create_subprocess_exec(
                        *cmd,
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE,
                    )
                    await process.communicate()
                else:
                    connection = await deps.connect_for_box(box, application_config)
                    try:
                        await connection.run(
                            f"tmux kill-session -t {record.session_name}",
                            check=False,
                        )
                    finally:
                        import contextlib
                        with contextlib.suppress(Exception):
                            connection.close()
            except Exception as exc:
                logger.warning(f"Failed to kill tmux session {record.session_name}: {exc}")

        deleted = await state.delete_session_async(session_id)
        if not deleted:
            raise HTTPException(status_code=500, detail="Failed to delete session")
        return APISimpleMessage(status="ok", message="deleted", path=session_id)

    @router.post("/boxes/{name}/sessions/sync", response_model=list[APISession])
    async def api_sync_sessions(
        name: str,
        application_config: AppConfig = Depends(deps.get_application_config),
    ) -> list[APISession]:
        """Sync DB sessions with actual tmux sessions.

        Queries tmux for live sessions and marks DB sessions as inactive
        if they no longer exist. Returns the updated session list.
        """
        box = deps.get_box_or_404(application_config, name)

        # Get live tmux sessions
        if box.transport == "local":
            live_sessions = await _get_live_tmux_sessions_local()
        else:
            try:
                connection = await deps.connect_for_box(box, application_config)
                live_sessions = await _get_live_tmux_sessions_remote(connection)
            except Exception as exc:
                logger.warning(f"Failed to connect to {name} for session sync: {exc}")
                live_sessions = set()

        # Get DB sessions and mark stale ones inactive
        db_sessions = await state.list_sessions_async(name, active_only=False)
        updated_count = 0
        for session in db_sessions:
            if session.active and session.session_name not in live_sessions:
                await state.update_session_activity_async(session.id, active=False)
                updated_count += 1

        if updated_count > 0:
            logger.info(f"Synced {name}: marked {updated_count} stale sessions inactive")

        # Return updated list
        records = await state.list_sessions_async(name, active_only=False)
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

    return router
