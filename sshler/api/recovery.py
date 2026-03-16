"""Recovery API endpoints for lost tmux sessions."""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request

from .. import state
from ..snapshot import recreate_session
from .dependencies import APIDependencies
from .models import APILostSession, APIRecoveryWindow, APISimpleMessage

logger = logging.getLogger(__name__)


def get_router(deps: APIDependencies) -> APIRouter:
    router = APIRouter(tags=["recovery"])

    @router.get("/recovery", response_model=list[APILostSession])
    async def api_get_recovery(request: Request) -> list[APILostSession]:
        """Return list of lost sessions detected at startup."""
        lost: list[dict] = getattr(request.app.state, "recovery_sessions", [])
        return [
            APILostSession(
                id=s["id"],
                box=s["box"],
                session_name=s["session_name"],
                working_directory=s["working_directory"],
                last_snapshot_at=s["last_snapshot_at"],
                windows=[APIRecoveryWindow(**w) for w in s.get("windows", [])],
            )
            for s in lost
        ]

    @router.post("/recovery/{session_id}/recreate", response_model=APISimpleMessage)
    async def api_recreate_session(session_id: str, request: Request) -> APISimpleMessage:
        """Recreate a lost tmux session from its last snapshot."""
        lost: list[dict] = getattr(request.app.state, "recovery_sessions", [])
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
        request.app.state.recovery_sessions = [s for s in lost if s["id"] != session_id]

        logger.info("Recreated session: %s (%d windows)", target["session_name"], len(windows))
        return APISimpleMessage(status="ok", message=f"Recreated {target['session_name']}", path=session_id)

    @router.post("/recovery/dismiss", response_model=APISimpleMessage)
    async def api_dismiss_recovery(request: Request) -> APISimpleMessage:
        """Dismiss all recovery notifications."""
        count = len(getattr(request.app.state, "recovery_sessions", []))
        request.app.state.recovery_sessions = []
        logger.info("Dismissed %d recovery session(s)", count)
        return APISimpleMessage(status="ok", message=f"Dismissed {count} session(s)")

    return router
