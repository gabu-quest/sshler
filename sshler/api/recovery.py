"""Recovery API endpoints for lost tmux sessions."""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException

from .. import state
from ..snapshot import get_last_snapshot_at, get_recovery_sessions, recreate_session, set_recovery_sessions
from .dependencies import APIDependencies
from .models import APILostSession, APIRecoveryWindow, APISimpleMessage, APISnapshotStatus

logger = logging.getLogger(__name__)


def get_router(deps: APIDependencies) -> APIRouter:
    router = APIRouter(tags=["recovery"])

    @router.get("/recovery", response_model=list[APILostSession])
    async def api_get_recovery() -> list[APILostSession]:
        """Return list of lost sessions (startup + live-detected)."""
        return [
            APILostSession(
                id=s["id"],
                box=s["box"],
                session_name=s["session_name"],
                working_directory=s["working_directory"],
                last_snapshot_at=s["last_snapshot_at"],
                windows=[APIRecoveryWindow(**w) for w in s.get("windows", [])],
            )
            for s in get_recovery_sessions()
        ]

    @router.post("/recovery/{session_id}/recreate", response_model=APISimpleMessage)
    async def api_recreate_session(session_id: str) -> APISimpleMessage:
        """Recreate a lost tmux session from its last snapshot."""
        lost = get_recovery_sessions()
        target = next((s for s in lost if s["id"] == session_id), None)
        if not target:
            raise HTTPException(status_code=404, detail="Session not in recovery list")

        windows = target.get("windows", [])
        success = await recreate_session(target["session_name"], windows)
        if not success:
            raise HTTPException(status_code=500, detail="Failed to recreate tmux session")

        # Mark session as active again in DB
        await state.update_session_activity_async(session_id, active=True)

        # Remove from recovery list
        set_recovery_sessions([s for s in lost if s["id"] != session_id])

        logger.info("Recreated session: %s (%d windows)", target["session_name"], len(windows))
        return APISimpleMessage(status="ok", message=f"Recreated {target['session_name']}", path=session_id)

    @router.get("/snapshot/status", response_model=APISnapshotStatus)
    async def api_snapshot_status() -> APISnapshotStatus:
        """Return the timestamp of the last successful snapshot."""
        return APISnapshotStatus(last_snapshot_at=get_last_snapshot_at())

    @router.post("/recovery/{session_id}/dismiss", response_model=APISimpleMessage)
    async def api_dismiss_single(session_id: str) -> APISimpleMessage:
        """Dismiss a single recovery session permanently."""
        lost = get_recovery_sessions()
        set_recovery_sessions([s for s in lost if s["id"] != session_id])
        # Mark inactive and clear snapshot so startup reconcile won't re-detect it
        await state.update_session_activity_async(session_id, active=False)
        await state.clear_session_snapshot_async(session_id)
        return APISimpleMessage(status="ok", message="Dismissed")

    @router.post("/recovery/dismiss", response_model=APISimpleMessage)
    async def api_dismiss_recovery() -> APISimpleMessage:
        """Dismiss all recovery notifications permanently."""
        lost = get_recovery_sessions()
        # Mark all as inactive and clear snapshots so they don't get re-detected
        for s in lost:
            await state.update_session_activity_async(s["id"], active=False)
            await state.clear_session_snapshot_async(s["id"])
        set_recovery_sessions([])
        logger.info("Dismissed %d recovery session(s)", len(lost))
        return APISimpleMessage(status="ok", message=f"Dismissed {len(lost)} session(s)")

    return router
