"""Layouts and the tmux sessions of a box.

A session route runs tmux on a box through the box's runner (``BoxRunner.tmux``): the
same words on both kinds, the local box's tmux on this machine and a remote box's over
its pooled connection, and a force kill through ``BoxRunner.force_kill``. One thing the
kinds really do differently lives in one function here: ``_local_live_sessions`` (which
sessions are running: the local box is asked in Python, a remote box by one script;
``live_sessions`` asks about every session of a box, ``live_sessions_on`` about one).

A failure of tmux or of the box is raised and answered by its reason: a box that
cannot be reached is ``unreachable``, tmux past its limit ``timeout``, no tmux on the
box ``program_missing``, and a refusal by tmux itself ``failed`` with tmux's words.
"""

from __future__ import annotations

import contextlib
import logging
import re
import shutil
from collections.abc import Sequence
from typing import Any

from fastapi import APIRouter, Depends, Query, Request

from .. import state
from .. import tmux as tmux_module
from ..config import AppConfig, Box
from ..session_activity import match_tmux_all
from ..tmux import (
    LiveSessions,
    ask_local_session,
    discover_local_sessions,
)
from ..validation import PathValidator, ValidationError
from . import dependencies
from .box_files import is_local
from .box_runner import BoxRunner, Ran, TmuxTimeoutError, box_runner, over
from .dependencies import APIDependencies
from .failure import (
    REASON_FAILED,
    REASON_INVALID,
    REASON_NOT_FOUND,
    REASON_TIMEOUT,
    Failure,
    failure_of,
)
from .models import (
    APILayout,
    APILayoutCreate,
    APISession,
    APISessionCreate,
    APISessionInfo,
    APISessionUpdate,
    APISimpleMessage,
)
from .run_failures import ProgramNotFoundError

logger = logging.getLogger(__name__)


# Lists the sessions of the remote default tmux server and of every per-session
# ``ts-*`` server, so sessions opened by sshler or by ``ts`` both count as live; given
# a session name as its first argument, of that session's own ``ts-<name>`` server and
# the default server only. The names are its output, one per line. Its exit status says
# what it learnt: 0 when every server it asked answered (a socket with no server behind
# it answers "none", in the words ``tmux._NO_SERVER`` reads), ``REMOTE_LISTING_INCOMPLETE``
# when one could not be asked (past ``timeout``'s limit, or another refusal), 127 when
# the box has no tmux (the shell's status for a command it cannot find). The socket
# folder is tmux's own: ``$TMUX_TMPDIR``, else ``/tmp``. A fixed text, never built from
# a request; sent as one word to ``sh -c``, the session name as its own word after it.
REMOTE_LISTING_INCOMPLETE = 75
REMOTE_LIST_SESSIONS = """command -v tmux >/dev/null 2>&1 || exit 127
limit=
command -v timeout >/dev/null 2>&1 && limit="timeout 2"
unasked=0
ask() {
    errors=$($limit tmux "$@" list-sessions -F '#{session_name}' 2>&1 >&3)
    case $? in
        0) ;;
        1) case $errors in
               "no server running on "*) ;;
               "error connecting to "*"(No such file or directory)"*) ;;
               *) unasked=1 ;;
           esac ;;
        *) unasked=1 ;;
    esac
}
{
    ask
    if [ $# -gt 0 ]; then
        ask -L "ts-$1"
    else
        for socket in "${TMUX_TMPDIR:-/tmp}/tmux-$(id -u)"/ts-*; do
            [ -S "$socket" ] || continue
            ask -L "${socket##*/}"
        done
    fi
} 3>&1
[ "$unasked" -eq 0 ] || exit 75
"""


def require_local_tmux(box: str, command: Sequence[str]) -> None:
    """The one check that the local box has tmux: raises ``ProgramNotFoundError`` when
    the program of the tmux command line ``command`` (its first word) is not on PATH."""
    if shutil.which(command[0]) is None:
        raise ProgramNotFoundError("tmux", box)


async def live_sessions(
    deps: APIDependencies, box: Box, application_config: AppConfig
) -> LiveSessions:
    """The tmux sessions running on ``box``: those of its default server and of every
    ``ts-*`` server, as ``LiveSessions`` (``complete`` False when a server could not be
    asked, so a session it did not see may still run). When the box cannot be asked at
    all the failure is raised as a ``Failure`` by its reason, never taken for "no
    sessions": no tmux on the box is ``program_missing``; on a remote box, a box that
    cannot be reached is ``unreachable``, a listing past its limit ``timeout``, and a
    listing that exits with another status ``failed``.

    The local box scans its own socket folder in Python (``discover_local_sessions``).
    A remote box runs ``REMOTE_LIST_SESSIONS`` in one command on its pooled connection,
    the connect and the command bounded by ``PROBE_TIMEOUT_SECONDS`` (the overview polls
    this every 30 s per box).
    """
    local = await _local_live_sessions(box)
    if local is not None:
        return local
    limit = dependencies.PROBE_TIMEOUT_SECONDS
    async with box_runner(deps, box, application_config, deadline=limit) as runner:
        return await _remote_live_sessions(runner, box, limit)


async def live_sessions_on(box: Box, connection: Any, session: str) -> LiveSessions:
    """What tmux on ``box`` says about ``session`` alone, asked on a connection the
    caller holds (a terminal's own), so nothing connects: its own ``ts-<session>``
    server and the default server are asked, whatever the number of other servers (on
    the local box two tmux commands, ``ask_local_session``; on a remote box one command
    on *connection*, bounded by one tmux command's limit ``tmux.TMUX_COMMAND_TIMEOUT``).
    Read with ``LiveSessions.has(session)``. Fails as ``live_sessions`` does."""
    local = await _local_live_sessions(box, session)
    if local is not None:
        return local
    try:
        return await _remote_live_sessions(
            over(connection, box.name), box, tmux_module.TMUX_COMMAND_TIMEOUT, session
        )
    except Exception as exc:
        answered = failure_of(exc)
        if answered is exc:
            raise
        raise answered from exc


async def _local_live_sessions(box: Box, session: str | None = None) -> LiveSessions | None:
    """The live sessions of the local box, asked in Python: every server, or with
    *session* that session's own server and the default one. None for a remote box,
    which its caller asks with ``REMOTE_LIST_SESSIONS``. The one place the live sessions
    tell the kinds of box apart."""
    if not is_local(box):
        return None
    try:
        require_local_tmux(box.name, tmux_module.default_tmux_command())
    except ProgramNotFoundError as exc:
        raise failure_of(exc) from exc
    if session is None:
        return await discover_local_sessions()
    return await ask_local_session(session)


async def _remote_live_sessions(
    runner: BoxRunner, box: Box, limit: float, session: str | None = None
) -> LiveSessions:
    words = ["sh", "-c", REMOTE_LIST_SESSIONS]
    if session is not None:
        words += ["sh", session]
    ran = await runner.run(words, folder=None, timeout=limit, program="tmux")
    if ran.status not in (0, REMOTE_LISTING_INCOMPLETE):
        raise Failure(
            REASON_FAILED,
            f"listing the tmux sessions of {box.name} exited with status {ran.status}",
        )
    names = frozenset(line.strip() for line in ran.output.splitlines() if line.strip())
    return LiveSessions(names, complete=ran.ok)


def _refused(command: str, ran: Ran) -> str:
    """The text of tmux refusing ``command``: its error output, else its exit status."""
    words = ran.errors.strip()
    if words:
        return f"tmux {command} failed: {words}"
    return f"tmux {command} exited with status {ran.status}"


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
            raise Failure(REASON_NOT_FOUND, "Layout not found")
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
            raise Failure(REASON_NOT_FOUND, "Session not found")

        # Handle session rename via tmux
        if payload.session_name is not None and payload.session_name != record.session_name:
            new_name = payload.session_name.strip()
            if not new_name or len(new_name) > 64:
                raise Failure(REASON_INVALID, "Invalid session name")
            # Only allow safe characters
            if not re.match(r"^[a-zA-Z0-9_.-]+$", new_name):
                raise Failure(
                    REASON_INVALID,
                    "Session name may only contain alphanumeric, underscore, dot, and dash",
                )
            limit = dependencies.PROBE_TIMEOUT_SECONDS
            async with box_runner(deps, box, application_config, deadline=limit) as runner:
                # The server keeps the old name: its socket is named after it.
                ran = await runner.tmux(
                    record.session_name, ["rename-session", "-t", record.session_name, new_name]
                )
            if not ran.ok:
                raise Failure(REASON_FAILED, _refused("rename-session", ran))
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
            raise Failure(REASON_NOT_FOUND, "Session not found")
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
            raise Failure(REASON_NOT_FOUND, "Session not found")

        # Native Windows shells persist in the registry (no tmux). Deleting a
        # session must terminate the live ConPTY too, not just drop the DB row.
        registry = getattr(request.app.state, "win_terminal_registry", None)
        if registry is not None and record.session_name:
            with contextlib.suppress(Exception):
                await registry.kill((record.box, record.session_name))

        if kill_tmux and record.session_name:
            try:
                limit = dependencies.PROBE_TIMEOUT_SECONDS
                async with box_runner(deps, box, application_config, deadline=limit) as runner:
                    if force:
                        # Stops the session's own ts-<name> server and SIGKILLs any pane
                        # process that ignored SIGHUP; other sessions are untouched.
                        await runner.force_kill(record.session_name)
                    else:
                        await runner.tmux(
                            record.session_name, ["kill-session", "-t", record.session_name]
                        )
            except Exception as exc:
                logger.warning(f"Failed to kill tmux session {record.session_name}: {exc}")

        deleted = await state.delete_session_async(session_id)
        if not deleted:
            raise Failure(REASON_FAILED, "Failed to delete session")
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
        """Force-kill a tmux session of the box by its name (no DB id needed).

        Powers the terminal toolbar's "super kill": ``BoxRunner.force_kill`` stops the
        session's own tmux server and SIGKILLs any pane process that ignored the SIGHUP,
        on either kind of box. A box that cannot be reached answers ``unreachable``, no
        tmux on a remote box ``program_missing``, past the limit ``timeout``; a session
        that is already gone is ``ok``.
        """
        box = deps.get_box_or_404(application_config, name)
        if not session_name.strip() or any(c in session_name for c in "\r\n\x00"):
            raise Failure(REASON_INVALID, "Invalid session name")

        limit = dependencies.PROBE_TIMEOUT_SECONDS
        async with box_runner(deps, box, application_config, deadline=limit) as runner:
            await runner.force_kill(session_name)
        # Drop any DB rows tracking this session so the switcher updates.
        with contextlib.suppress(Exception):
            for record in await state.list_sessions_async(name):
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
            raise Failure(REASON_INVALID, str(exc)) from exc

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
        """Make every tmux row of the box say whether tmux has its session
        (``match_tmux_all``), both ways, and answer the box's rows. When the live
        sessions cannot be read the failure is answered and no row changes.
        """
        box = deps.get_box_or_404(application_config, name)
        running = await live_sessions(deps, box, application_config)
        db_sessions = await state.list_sessions_async(name, active_only=False)
        changed = await match_tmux_all(db_sessions, running)
        if changed > 0:
            logger.info(f"Synced {name}: {changed} session(s) changed")

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

    def _capture_timeout(box_name: str, session_name: str, limit: float) -> Failure:
        logger.warning(f"capture-pane timed out for {box_name}:{session_name} after {limit:g} s")
        return Failure(REASON_TIMEOUT, f"tmux capture-pane timed out after {limit:g} s")

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
            raise Failure(REASON_INVALID, "Invalid session name")
        if window is not None and not re.match(r"^[a-zA-Z0-9_.@:-]+$", window):
            raise Failure(REASON_INVALID, "Invalid window")
        if pane is not None and not re.match(r"^[a-zA-Z0-9_.@:%-]+$", pane):
            raise Failure(REASON_INVALID, "Invalid pane")

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
        async with box_runner(deps, box, application_config, deadline=deadline) as runner:
            try:
                ran = await runner.tmux(session_name, capture_args, timeout=limit)
            except TmuxTimeoutError as exc:
                raise _capture_timeout(name, session_name, limit) from exc
        if not ran.ok:
            raise Failure(REASON_FAILED, _refused("capture-pane", ran))
        text = ran.output

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
