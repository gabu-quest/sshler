"""Layouts and the tmux sessions of a box.

A session route runs tmux on a box through the box's runner (``BoxRunner.tmux``): the
same words on both kinds, the local box's tmux on this machine and a remote box's over
its pooled connection. Two things the kinds really do differently each live in one
function here: ``live_sessions`` (which sessions are running) and
``force_kill_supported`` (a force kill is built for the local box only).
"""

from __future__ import annotations

import contextlib
import logging
import re

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from .. import state
from .. import tmux as tmux_module
from ..config import AppConfig, Box
from ..tmux import discover_local_sessions, force_kill_local_session
from ..validation import PathValidator, ValidationError
from . import dependencies
from .box_files import is_local
from .box_runner import TmuxTimeoutError, box_runner
from .dependencies import APIDependencies
from .models import (
    APILayout,
    APILayoutCreate,
    APISession,
    APISessionCreate,
    APISessionInfo,
    APISessionUpdate,
    APISimpleMessage,
)

logger = logging.getLogger(__name__)


# Lists sessions on the remote default tmux server and on every per-session
# ``ts-*`` server, so sessions opened by sshler or by ``ts`` both count as live.
# A fixed text, never built from a request; sent as one word to ``sh -c``.
REMOTE_LIST_SESSIONS = """tmux list-sessions -F '#{session_name}' 2>/dev/null || true
socket_dir=/tmp/tmux-$(id -u)
for socket in "$socket_dir"/ts-*; do
    [ -S "$socket" ] || continue
    server=${socket##*/}
    timeout 2 tmux -L "$server" list-sessions -F '#{session_name}' 2>/dev/null || true
done
"""


async def live_sessions(deps: APIDependencies, box: Box, application_config: AppConfig) -> set[str]:
    """The names of the tmux sessions running on ``box``: those of its default server
    and of every ``ts-*`` server. Empty when they cannot be read.

    The local box scans its own socket folder in Python (``discover_local_sessions``).
    A remote box runs ``REMOTE_LIST_SESSIONS`` in one command on its pooled connection,
    the connect and the command bounded by ``PROBE_TIMEOUT_SECONDS`` (the overview polls
    this every 30 s per box); a failure of any kind is an empty set.
    """
    if is_local(box):
        return await discover_local_sessions()
    limit = dependencies.PROBE_TIMEOUT_SECONDS
    try:
        async with box_runner(deps, box, application_config, deadline=limit) as runner:
            ran = await runner.run(["sh", "-c", REMOTE_LIST_SESSIONS], folder=None, timeout=limit)
    except Exception as exc:
        logger.warning(f"Failed to list the tmux sessions of {box.name}: {exc}")
        return set()
    if not ran.ok:
        return set()
    return {line.strip() for line in ran.output.splitlines() if line.strip()}


def force_kill_supported(box: Box) -> bool:
    """Whether a force kill (stop the session's own tmux server, then SIGKILL every
    pane process that outlived it) is built for ``box``: for the local box only. A
    remote session runs on its own ``ts-<name>`` server too, so a remote force kill
    could be scoped the same way, but it is not built."""
    return is_local(box)


def get_router(deps: APIDependencies) -> APIRouter:
    router = APIRouter()

    @router.get("/layouts", response_model=list[APILayout])
    async def api_list_layouts() -> list[APILayout]:
        layouts = await state.list_layouts_async()
        return [
            APILayout(
                id=item.id,
                name=item.name,
                terminals=item.terminals,
                created_at=item.created_at,
            )
            for item in layouts
        ]

    @router.post("/layouts", response_model=APILayout, status_code=201)
    async def api_create_layout(body: APILayoutCreate) -> APILayout:
        layout = await state.save_layout_async(body.name, body.terminals)
        return APILayout(
            id=layout.id,
            name=layout.name,
            terminals=layout.terminals,
            created_at=layout.created_at,
        )

    @router.delete("/layouts/{layout_id}")
    async def api_delete_layout(layout_id: str) -> dict:
        deleted = await state.delete_layout_async(layout_id)
        if not deleted:
            raise HTTPException(status_code=404, detail="Layout not found")
        return {"status": "ok"}

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
        box = deps.get_box_or_404(application_config, name)
        record = await state.get_session_by_id_async(session_id)
        if record is None or record.box != name:
            raise HTTPException(status_code=404, detail="Session not found")

        # Handle session rename via tmux
        if payload.session_name is not None and payload.session_name != record.session_name:
            new_name = payload.session_name.strip()
            if not new_name or len(new_name) > 64:
                raise HTTPException(status_code=400, detail="Invalid session name")
            # Only allow safe characters
            if not re.match(r"^[a-zA-Z0-9_.-]+$", new_name):
                raise HTTPException(
                    status_code=400,
                    detail="Session name may only contain alphanumeric, underscore, dot, and dash",
                )
            limit = dependencies.PROBE_TIMEOUT_SECONDS
            try:
                async with box_runner(deps, box, application_config, deadline=limit) as runner:
                    # The server keeps the old name: its socket is named after it.
                    ran = await runner.tmux(
                        record.session_name, ["rename-session", "-t", record.session_name, new_name]
                    )
                if not ran.ok:
                    logger.warning(f"tmux rename failed: {ran.errors.strip()}")
                    raise HTTPException(status_code=400, detail="tmux rename failed")
            except HTTPException:
                raise
            except Exception as exc:
                logger.warning(f"Failed to rename tmux session: {exc}")
                raise HTTPException(status_code=500, detail="Failed to rename session") from exc
            await state.rename_session_async(record.id, new_name)

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
        request: Request,
        name: str,
        session_id: str,
        kill_tmux: bool = Query(False),
        force: bool = Query(False),
        application_config: AppConfig = Depends(deps.get_application_config),
    ) -> APISimpleMessage:
        box = deps.get_box_or_404(application_config, name)
        record = await state.get_session_by_id_async(session_id)
        if record is None or record.box != name:
            raise HTTPException(status_code=404, detail="Session not found")

        # Native Windows shells persist in the registry (no tmux). Deleting a
        # session must terminate the live ConPTY too, not just drop the DB row.
        registry = getattr(request.app.state, "win_terminal_registry", None)
        if registry is not None and record.session_name:
            with contextlib.suppress(Exception):
                await registry.kill((record.box, record.session_name))

        if kill_tmux and record.session_name:
            try:
                if force and force_kill_supported(box):
                    # Stops the session's own ts-<name> server and SIGKILLs any pane
                    # process that ignored SIGHUP; other sessions are untouched.
                    await force_kill_local_session(record.session_name)
                else:
                    if force:
                        logger.info(
                            "force kill requested for remote session %s; a remote force "
                            "kill is not built, so the session is ended with kill-session",
                            record.session_name,
                        )
                    limit = dependencies.PROBE_TIMEOUT_SECONDS
                    async with box_runner(deps, box, application_config, deadline=limit) as runner:
                        await runner.tmux(
                            record.session_name, ["kill-session", "-t", record.session_name]
                        )
            except Exception as exc:
                logger.warning(f"Failed to kill tmux session {record.session_name}: {exc}")

        deleted = await state.delete_session_async(session_id)
        if not deleted:
            raise HTTPException(status_code=500, detail="Failed to delete session")
        return APISimpleMessage(status="ok", message="deleted", path=session_id)

    @router.post(
        "/boxes/{name}/sessions/by-name/{session_name}/force-kill",
        response_model=APISimpleMessage,
    )
    async def api_force_kill_session_by_name(
        name: str,
        session_name: str,
        application_config: AppConfig = Depends(deps.get_application_config),
    ) -> APISimpleMessage:
        """Force-kill a LOCAL tmux session by its name (no DB id needed).

        Powers the terminal toolbar's "super kill" — kills the session's own
        per-session tmux server and SIGKILLs any pane process that ignored the
        SIGHUP. Local box only: a remote force kill is not built
        (``force_kill_supported``), so a remote box is answered 400.
        """
        box = deps.get_box_or_404(application_config, name)
        if not force_kill_supported(box):
            raise HTTPException(
                status_code=400,
                detail="force-kill by name is supported for the local box only",
            )
        if not session_name.strip() or any(c in session_name for c in "\r\n\x00"):
            raise HTTPException(status_code=400, detail="Invalid session name")

        await force_kill_local_session(session_name)
        # Drop any DB rows tracking this local session so the switcher updates.
        with contextlib.suppress(Exception):
            for record in await state.list_sessions_async("local"):
                if record.session_name == session_name:
                    await state.delete_session_async(record.id)
        return APISimpleMessage(status="ok", message="force-killed", path=session_name)

    @router.delete(
        "/boxes/{name}/terminal-sessions/{session_name}",
        response_model=APISimpleMessage,
    )
    async def api_kill_terminal_session(
        request: Request,
        name: str,
        session_name: str,
        application_config: AppConfig = Depends(deps.get_application_config),
    ) -> APISimpleMessage:
        """Forcibly kill a native shell by session NAME (the "kill terminal" action).

        Closing a browser tab only *detaches* — the native ConPTY persists in the
        registry so the tab can re-attach (that's the session-persistence feature).
        This endpoint terminates that live shell, keyed by ``(box, session_name)``
        exactly as the registry stores it, so a tab reopened with the same name
        spawns a FRESH shell instead of re-attaching to the old (e.g. laggy) one.
        It also drops any tracking row so the session doesn't linger as stale.

        Keying by name — not the DB id like ``api_delete_session`` — lets a tab kill
        its OWN shell without first resolving an id, and still works if the DB row
        is missing or out of sync with the registry.
        """
        box = deps.get_box_or_404(application_config, name)
        try:
            safe_name = PathValidator.sanitize_session_name(session_name)
        except ValidationError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        # Terminate the live ConPTY (no-op returning False if it's already gone).
        killed = False
        registry = getattr(request.app.state, "win_terminal_registry", None)
        if registry is not None:
            with contextlib.suppress(Exception):
                killed = await registry.kill((box.name, safe_name))

        # Best-effort: drop the tracking row so the session list stays clean.
        record = await state.get_session_by_name_async(box.name, safe_name)
        if record is not None:
            with contextlib.suppress(Exception):
                await state.delete_session_async(record.id)

        return APISimpleMessage(
            status="ok",
            message="killed" if killed else "not_found",
            path=safe_name,
        )

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
        running = await live_sessions(deps, box, application_config)

        # Get DB sessions and mark stale ones inactive
        db_sessions = await state.list_sessions_async(name, active_only=False)
        updated_count = 0
        for session in db_sessions:
            if session.active and session.session_name not in running:
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

    def _capture_timeout(box_name: str, session_name: str, limit: float) -> HTTPException:
        logger.warning(f"capture-pane timed out for {box_name}:{session_name} after {limit:g} s")
        return HTTPException(
            status_code=504, detail=f"tmux capture-pane timed out after {limit:g} s"
        )

    @router.get("/boxes/{name}/sessions/{session_name}/capture")
    async def api_capture_tmux_pane(
        name: str,
        session_name: str,
        lines: int = Query(0, ge=0, le=200000),
        window: str | None = Query(None),
        pane: str | None = Query(None),
        application_config: AppConfig = Depends(deps.get_application_config),
    ) -> dict:
        """Capture the full scrollback of a tmux pane as plain text.

        `lines=0` means "everything" (`-S -`). Otherwise the last N lines.
        `window`/`pane` default to the active window's active pane.
        """
        box = deps.get_box_or_404(application_config, name)

        # Validate session name with the same charset other endpoints use
        if not re.match(r"^[a-zA-Z0-9_.-]+$", session_name):
            raise HTTPException(status_code=400, detail="Invalid session name")
        if window is not None and not re.match(r"^[a-zA-Z0-9_.@:-]+$", window):
            raise HTTPException(status_code=400, detail="Invalid window")
        if pane is not None and not re.match(r"^[a-zA-Z0-9_.@:%-]+$", pane):
            raise HTTPException(status_code=400, detail="Invalid pane")

        target = session_name
        if window:
            target = f"{session_name}:{window}"
            if pane:
                target = f"{target}.{pane}"

        start_arg = "-" if lines == 0 else f"-{lines}"
        capture_args = ["capture-pane", "-p", "-J", "-S", start_arg, "-t", target]

        # A full-history capture can take far longer than a normal tmux command.
        limit = tmux_module.TMUX_CAPTURE_TIMEOUT
        # The capture's own limit is tmux's alone; the bound of the request adds the
        # time a connect may take, so a slow connect does not eat into the capture.
        deadline = limit + dependencies.PROBE_TIMEOUT_SECONDS
        try:
            async with box_runner(deps, box, application_config, deadline=deadline) as runner:
                try:
                    ran = await runner.tmux(session_name, capture_args, timeout=limit)
                except TmuxTimeoutError as exc:
                    raise _capture_timeout(name, session_name, limit) from exc
            if not ran.ok:
                raise HTTPException(
                    status_code=400,
                    detail="tmux capture-pane failed: " + ran.errors.strip(),
                )
            text = ran.output
        except HTTPException:
            raise
        except Exception as exc:
            logger.warning(f"capture-pane error for {name}:{session_name}: {exc}")
            raise HTTPException(status_code=500, detail=f"capture failed: {exc}") from exc

        # Trim trailing blank lines so the dump doesn't end with a wall of empties
        stripped = text.rstrip("\n")
        return {
            "session": session_name,
            "target": target,
            "text": stripped,
            "lines": stripped.count("\n") + 1 if stripped else 0,
            "chars": len(stripped),
        }

    return router
