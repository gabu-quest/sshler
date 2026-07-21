from __future__ import annotations

import asyncio
import contextlib
import logging
import re
import shlex

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from .. import state
from ..config import AppConfig
from ..tmux import discover_local_sessions, local_tmux_command
from ..validation import PathValidator, ValidationError
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


async def _get_live_tmux_sessions_local() -> set[str]:
    """Get live tmux session names from local system.

    Scans all ts-* server sockets (per-session isolation) and also
    checks the default server for backward compatibility.
    """
    return await discover_local_sessions()


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
            if not re.match(r'^[a-zA-Z0-9_.-]+$', new_name):
                raise HTTPException(status_code=400, detail="Session name may only contain alphanumeric, underscore, dot, and dash")
            try:
                if box.transport == "local":
                    # Use old session name for -L (socket is named after original session)
                    cmd = local_tmux_command(record.session_name) + ["rename-session", "-t", record.session_name, new_name]
                    process = await asyncio.create_subprocess_exec(
                        *cmd,
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE,
                    )
                    _, stderr = await process.communicate()
                    if process.returncode != 0:
                        logger.warning(f"tmux rename failed: {stderr.decode().strip()}")
                        raise HTTPException(status_code=400, detail="tmux rename failed")
                else:
                    connection = await deps.connect_for_box(box, application_config)
                    try:
                        result = await connection.run(
                            f"tmux rename-session -t {shlex.quote(record.session_name)} {shlex.quote(new_name)}",
                            check=False,
                        )
                        if result.returncode != 0:
                            raise HTTPException(status_code=400, detail="tmux rename failed")
                    finally:
                        with contextlib.suppress(Exception):
                            connection.close()
            except HTTPException:
                raise
            except Exception as exc:
                logger.warning(f"Failed to rename tmux session: {exc}")
                raise HTTPException(status_code=500, detail="Failed to rename session")
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
                if box.transport == "local":
                    cmd = local_tmux_command(record.session_name) + ["kill-session", "-t", record.session_name]
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
                            f"tmux kill-session -t {shlex.quote(record.session_name)}",
                            check=False,
                        )
                    finally:
                        with contextlib.suppress(Exception):
                            connection.close()
            except Exception as exc:
                logger.warning(f"Failed to kill tmux session {record.session_name}: {exc}")

        deleted = await state.delete_session_async(session_id)
        if not deleted:
            raise HTTPException(status_code=500, detail="Failed to delete session")
        return APISimpleMessage(status="ok", message="deleted", path=session_id)

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
            raise HTTPException(status_code=400, detail=str(exc))

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
        if not re.match(r'^[a-zA-Z0-9_.-]+$', session_name):
            raise HTTPException(status_code=400, detail="Invalid session name")
        if window is not None and not re.match(r'^[a-zA-Z0-9_.@:-]+$', window):
            raise HTTPException(status_code=400, detail="Invalid window")
        if pane is not None and not re.match(r'^[a-zA-Z0-9_.@:%-]+$', pane):
            raise HTTPException(status_code=400, detail="Invalid pane")

        target = session_name
        if window:
            target = f"{session_name}:{window}"
            if pane:
                target = f"{target}.{pane}"

        start_arg = "-" if lines == 0 else f"-{lines}"
        capture_args = ["capture-pane", "-p", "-J", "-S", start_arg, "-t", target]

        try:
            if box.transport == "local":
                cmd = local_tmux_command(session_name) + capture_args
                process = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                stdout_b, stderr_b = await process.communicate()
                if process.returncode != 0:
                    raise HTTPException(
                        status_code=400,
                        detail=f"tmux capture-pane failed: {stderr_b.decode(errors='replace').strip()}",
                    )
                text = stdout_b.decode(errors="replace")
            else:
                connection = await deps.connect_for_box(box, application_config)
                try:
                    quoted = " ".join(shlex.quote(a) for a in capture_args)
                    result = await connection.run(f"tmux {quoted}", check=False)
                    if result.returncode != 0:
                        stderr_txt = (result.stderr or "").strip() if isinstance(result.stderr, str) else ""
                        raise HTTPException(
                            status_code=400,
                            detail=f"tmux capture-pane failed: {stderr_txt}",
                        )
                    text = result.stdout if isinstance(result.stdout, str) else (result.stdout or b"").decode(errors="replace")
                finally:
                    with contextlib.suppress(Exception):
                        connection.close()
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
