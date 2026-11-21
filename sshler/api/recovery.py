"""Recovery API endpoints for lost tmux sessions."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

from fastapi import APIRouter

from .. import state
from ..session_activity import match_tmux
from ..snapshot import (
    get_last_snapshot_at,
    get_recovery_sessions,
    recreate_session,
    remove_recovery_session,
    set_recovery_sessions,
)
from ..tmux import ask_local_session
from .dependencies import APIDependencies
from .failure import REASON_FAILED, REASON_NOT_FOUND, Failure, failure_entry, failure_of
from .models import (
    APILostSession,
    APIRecoveryPane,
    APIRecoveryWindow,
    APIRecreateBatchRequest,
    APIRecreateBatchResult,
    APISimpleMessage,
    APISnapshotConfig,
    APISnapshotConfigUpdate,
    APISnapshotStatus,
)

logger = logging.getLogger(__name__)


async def _recreate(session_id: str, lost: Mapping[str, dict[str, Any]]) -> str:
    """Recreate the lost session ``session_id`` and answer its tmux name; a session that
    cannot be recreated raises the ``Failure`` of why, the one answer of the single
    route and of each failed item of the batch."""
    target = lost.get(session_id)
    if not target:
        raise Failure(REASON_NOT_FOUND, "Session not in recovery list")

    windows = target.get("windows", [])
    logger.info("Recreating %s with %d window(s)", target["session_name"], len(windows))
    try:
        success = await recreate_session(target["session_name"], windows)
    except Exception as exc:
        logger.exception("Exception recreating %s", target["session_name"])
        raise failure_of(exc) from exc
    if not success:
        logger.error(
            "recreate_session returned False for %s (windows=%r)",
            target["session_name"],
            windows,
        )
        raise Failure(REASON_FAILED, "Failed to recreate tmux session")

    await state.update_session_activity_async(session_id, active=True)
    await state.clear_session_snapshot_async(session_id)
    remove_recovery_session(session_id)
    return str(target["session_name"])


async def _dismiss(session_id: str) -> None:
    """Forget the snapshot of the lost session ``session_id``, so the next start does
    not offer it again, and let its row say what tmux has now: the session may have
    been started again by hand since it was found lost (a lost session is local)."""
    await state.clear_session_snapshot_async(session_id)
    row = await state.get_session_by_id_async(session_id)
    if row is not None:
        live = await ask_local_session(row.session_name)
        await match_tmux(row, live.has(row.session_name))


def _lost_by_id() -> dict[str, dict[str, Any]]:
    return {s["id"]: s for s in get_recovery_sessions()}


def get_router(deps: APIDependencies) -> APIRouter:
    router = APIRouter(tags=["recovery"])

    @router.get("/recovery", response_model=list[APILostSession])
    async def api_get_recovery() -> list[APILostSession]:
        """Return the list of lost sessions (found when the server started)."""
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
        name = await _recreate(session_id, _lost_by_id())
        return APISimpleMessage(status="ok", message=f"Recreated {name}", path=session_id)

    @router.post("/recovery/recreate-batch", response_model=APIRecreateBatchResult)
    async def api_recreate_batch(body: APIRecreateBatchRequest) -> APIRecreateBatchResult:
        """Recreate several sessions. A 200 that names the sessions recreated and, for
        each other one, the reason and text the single route would answer for it."""
        lost = _lost_by_id()
        succeeded: list[str] = []
        failed: list[dict[str, str]] = []
        for sid in body.session_ids:
            try:
                await _recreate(sid, lost)
            except Exception as exc:
                failed.append(failure_entry(sid, exc))
                continue
            succeeded.append(sid)
        return APIRecreateBatchResult(
            status="ok" if not failed else "partial", succeeded=succeeded, failed=failed
        )

    @router.get("/snapshot/status", response_model=APISnapshotStatus)
    async def api_snapshot_status() -> APISnapshotStatus:
        """Return the timestamp of the last successful snapshot."""
        return APISnapshotStatus(last_snapshot_at=get_last_snapshot_at())

    @router.post("/recovery/{session_id}/dismiss", response_model=APISimpleMessage)
    async def api_dismiss_single(session_id: str) -> APISimpleMessage:
        """Dismiss a single recovery session permanently."""
        lost = get_recovery_sessions()
        set_recovery_sessions([s for s in lost if s["id"] != session_id])
        await _dismiss(session_id)
        return APISimpleMessage(status="ok", message="Dismissed")

    @router.post("/recovery/dismiss", response_model=APISimpleMessage)
    async def api_dismiss_recovery() -> APISimpleMessage:
        """Dismiss all recovery notifications permanently."""
        lost = get_recovery_sessions()
        for s in lost:
            await _dismiss(s["id"])
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
