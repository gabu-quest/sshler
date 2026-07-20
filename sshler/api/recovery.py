"""Recovery API endpoints for lost tmux sessions."""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException

from .. import state
from ..snapshot import get_last_snapshot_at, get_recovery_sessions, recreate_session, remove_recovery_session, set_recovery_sessions
from .dependencies import APIDependencies
from .models import (
    APILostSession,
    APIRecoveryPane,
    APIRecoveryWindow,
    APISimpleMessage,
    APISnapshotConfig,
    APISnapshotConfigUpdate,
    APISnapshotStatus,
)

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
                windows=[
                    APIRecoveryWindow(
                        index=w["index"],
                        name=w["name"],
                        command=w["command"],
                        path=w["path"],
                        panes=[APIRecoveryPane(**p) for p in w.get("panes", [])],
                    )
                    for w in s.get("windows", [])
                ],
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
        logger.info("Recreating %s with %d window(s)", target["session_name"], len(windows))
        try:
            success = await recreate_session(target["session_name"], windows)
        except Exception:
            logger.exception("Exception recreating %s", target["session_name"])
            raise HTTPException(status_code=500, detail="Exception during recreate")
        if not success:
            logger.error("recreate_session returned False for %s (windows=%r)", target["session_name"], windows)
            raise HTTPException(status_code=500, detail="Failed to recreate tmux session")

        await state.update_session_activity_async(session_id, active=True)
        await state.clear_session_snapshot_async(session_id)
        remove_recovery_session(session_id)

        return APISimpleMessage(status="ok", message=f"Recreated {target['session_name']}", path=session_id)

    @router.post("/recovery/recreate-batch")
    async def api_recreate_batch(body: dict) -> dict:
        """Recreate multiple sessions. Returns per-session results."""
        session_ids: list[str] = body.get("session_ids", [])
        lost = get_recovery_sessions()
        lost_map = {s["id"]: s for s in lost}

        results: dict[str, str] = {}  # id -> "ok" | error message
        for sid in session_ids:
            target = lost_map.get(sid)
            if not target:
                results[sid] = "not found"
                continue
            try:
                success = await recreate_session(target["session_name"], target.get("windows", []))
            except Exception as exc:
                results[sid] = str(exc)
                continue
            if not success:
                results[sid] = "tmux creation failed"
                continue
            await state.update_session_activity_async(sid, active=True)
            await state.clear_session_snapshot_async(sid)
            remove_recovery_session(sid)
            results[sid] = "ok"
            logger.info("Recreated session: %s", target["session_name"])

        return {"results": results}

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

    @router.get("/snapshot/config", response_model=APISnapshotConfig)
    async def get_snapshot_config() -> APISnapshotConfig:
        return APISnapshotConfig(
            enabled=deps.settings.snapshot_enabled,
            interval=deps.settings.snapshot_interval,
        )

    @router.put("/snapshot/config", response_model=APISnapshotConfig)
    async def update_snapshot_config(update: APISnapshotConfigUpdate) -> APISnapshotConfig:
        if update.enabled is not None:
            deps.settings.snapshot_enabled = update.enabled
        if update.interval is not None:
            deps.settings.snapshot_interval = max(5, min(300, update.interval))
        return APISnapshotConfig(
            enabled=deps.settings.snapshot_enabled,
            interval=deps.settings.snapshot_interval,
        )

    return router
