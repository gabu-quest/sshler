"""Claude Code session dashboard endpoints.

Lists resumable Claude sessions read from the local filesystem and resumes a
chosen one (``claude --resume <uuid>``) into a new window inside the
directory's ``ts`` tmux session, which the browser then attaches to via the
normal ``/ws/term`` flow.

Local-box only — the transcripts live on the same machine sshler runs on.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query

from .. import state
from ..claude_sessions import (
    get_claude_session,
    is_valid_session_id,
    list_claude_sessions,
)
from ..tmux import (
    _configure_tmux_bindings,
    _run_local_tmux_command,
    discover_local_sessions,
    list_local_window_names,
    record_ts_history,
    ts_session_name,
)
from .dependencies import APIDependencies
from .models import APIClaudeOpenRequest, APIClaudeOpenResult, APIClaudeSession

logger = logging.getLogger(__name__)

# Default command typed into a freshly-resumed session. `{id}` is replaced with
# the validated session UUID. Clients may override this (globally or per-session)
# to inject their own launcher/flags, e.g. `mywrapper --resume {id}`.
DEFAULT_RESUME_TEMPLATE = "claude --resume {id}"


def _resolve_resume_command(template: str | None, session_id: str) -> str:
    """Validate a resume-command template and substitute the (validated) id.

    The template's non-``{id}`` text is typed verbatim into the user's own
    interactive shell, so it's only as privileged as the terminal already is.
    We still reject control characters (a newline would fire an extra Enter
    mid-command) and require the ``{id}`` placeholder so the resume targets the
    chosen session.
    """
    chosen = (template or "").strip() or DEFAULT_RESUME_TEMPLATE
    if "{id}" not in chosen:
        raise HTTPException(
            status_code=400, detail="Resume command template must contain {id}"
        )
    if any(ch in chosen for ch in ("\n", "\r", "\x00")):
        raise HTTPException(
            status_code=400, detail="Resume command template contains illegal characters"
        )
    return chosen.replace("{id}", session_id)


def _window_name(session_id: str) -> str:
    """Deterministic tmux window (tab) name for a Claude session within its dir
    session — folds in the UUID so several conversations in one directory each
    get their own window."""
    return f"cl-{session_id.replace('-', '')[:6]}"


def get_router(deps: APIDependencies) -> APIRouter:
    router = APIRouter()

    @router.get("/claude/sessions", response_model=list[APIClaudeSession])
    async def list_sessions(
        limit: int = Query(100, ge=1, le=500),
        since_days: float | None = Query(None, gt=0),
    ) -> list[APIClaudeSession]:
        infos = await asyncio.to_thread(list_claude_sessions, limit, since_days)
        return [
            APIClaudeSession(
                id=info.id,
                cwd=info.cwd,
                title=info.title,
                last_prompt=info.last_prompt,
                last_active=info.last_active,
                git_branch=info.git_branch,
                version=info.version,
                size_bytes=info.size_bytes,
                project_dir=info.project_dir,
                repo_root=info.repo_root,
            )
            for info in infos
        ]

    @router.post("/claude/sessions/{session_id}/open", response_model=APIClaudeOpenResult)
    async def open_session(
        session_id: str, body: APIClaudeOpenRequest | None = None
    ) -> APIClaudeOpenResult:
        if not is_valid_session_id(session_id):
            raise HTTPException(status_code=400, detail="Invalid Claude session id")

        command = _resolve_resume_command(
            body.command_template if body else None, session_id
        )

        info = await asyncio.to_thread(get_claude_session, session_id)
        if info is None or not info.cwd:
            raise HTTPException(status_code=404, detail="Claude session not found")

        # The tmux SESSION is the repo root's, so a conversation that ran in a
        # subdirectory (e.g. repo/replay-server) lands as a tab in the repo's
        # top-level session, matching how the user organizes these. But the
        # window's shell must start in the session's EXACT cwd: `claude --resume
        # <uuid>` is cwd-scoped (it only finds sessions whose project folder maps
        # to the current directory), so running it from the repo root fails with
        # "No conversation found". `-c info.cwd` cd's the window there natively —
        # no shell escaping of the path required.
        session_dir = info.repo_root or info.cwd  # repo root → the tmux session
        window_dir = info.cwd  # exact cwd → where `claude --resume` actually runs
        if not Path(window_dir).is_dir():
            raise HTTPException(
                status_code=409, detail="Working directory no longer exists"
            )

        session = ts_session_name(session_dir)
        window = _window_name(session_id)
        target = f"{session}:{window}"

        # Ensure the repo-root session exists.
        if session not in await discover_local_sessions():
            await _run_local_tmux_command(
                session, ["new-session", "-d", "-s", session, "-c", session_dir]
            )
            await _configure_tmux_bindings(session)

        # Idempotent + non-destructive: if this conversation already has a
        # window, just select it (never re-type into a running claude); the UI
        # toasts "already open". Otherwise open a new window and resume there.
        already_open = window in await list_local_window_names(session)
        if already_open:
            await _run_local_tmux_command(session, ["select-window", "-t", target])
        else:
            await _run_local_tmux_command(
                session, ["new-window", "-t", session, "-n", window, "-c", window_dir]
            )
            # Type the command literally (-l), then Enter as a separate key.
            await _run_local_tmux_command(session, ["send-keys", "-t", target, "-l", command])
            await _run_local_tmux_command(session, ["send-keys", "-t", target, "Enter"])
            await _run_local_tmux_command(session, ["select-window", "-t", target])
            await record_ts_history(session, session_dir)
            await state.create_or_update_session_async(
                box_name="local",
                session_name=session,
                working_directory=session_dir,
                metadata={
                    "kind": "claude",
                    "claude_session_id": session_id,
                    "window": window,
                    "ai_title": info.title,
                },
            )

        return APIClaudeOpenResult(
            box="local",
            session_name=session,
            working_directory=session_dir,
            window=window,
            already_open=already_open,
        )

    return router
