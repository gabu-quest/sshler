"""The one rule for a session row's ``active`` flag.

A tmux session's row is active exactly while tmux has that session. Every writer that
learns what tmux has calls ``match_tmux`` with it: the socket's end
(``webapp._run_terminal``), the sync route (``api/sessions.py``), the startup reconcile
and the snapshot loop (``snapshot.py``), and a dismiss of a lost session or a "Restore
layout" (``api/recovery.py``). Where tmux could not be asked about a session, its row is left
as it is: "could not ask" is never read as "has no such session". Every one of them but
the startup reconcile is the running server seeing what tmux has, so an end it finds
there is recorded on the row, and the start leaves that session out of its lost list.
It sits beside the state store and below the API, so ``snapshot.py`` can call it too
(``api`` imports ``snapshot`` through its recovery routes).

A terminal's row is made by ``track_session`` only: when a terminal opens
(``webapp._track_terminal``) and, for a session tmux has on its own server that has no row
yet (one started with ``ts`` outside sshler), by the sync route (``track_live_sessions``).
The one other maker is the Claude dashboard's open route (``api/claude_sessions.py``), for
the local box only; its row records no ``transport``, which ``is_tmux_row`` reads as local.

A row that is not a tmux session's is left alone: on a Windows server a local row is a
persisted native shell, whose registry marks it when the shell ends. A plain shell has
no row.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable

from . import state
from . import tmux as tmux_module
from .config import Box
from .tmux import LiveSessions

logger = logging.getLogger(__name__)


def is_tmux_row(row: state.Session) -> bool:
    """Whether *row* is a tmux session's: a remote box's row always is, a local row
    unless this server runs on Windows (where the local box opens native shells)."""
    return is_tmux_transport(row.metadata.get("transport", "local"))


def is_tmux_transport(transport: str) -> bool:
    """Whether a row recording *transport* is a tmux session's (``is_tmux_row``)."""
    return transport != "local" or not tmux_module._IS_WINDOWS


async def match_tmux(
    row: state.Session, has_session: bool | None, *, at_start: bool = False
) -> bool:
    """Make *row* active exactly when tmux has its session (*has_session*, as
    ``LiveSessions.has`` answers it). None (tmux could not be asked about it) leaves the
    row as it is, as does a row that already says so or is not a tmux session's. True
    when the flag changed.

    An active row going inactive here is an end the running server saw, and the row
    records it (``state.END_SEEN_AT``) unless *at_start*: a session the start finds gone
    ended while sshler was not running, so it was lost. A row active again drops the
    record."""
    if has_session is None or not is_tmux_row(row) or row.active == has_session:
        return False
    seen = not has_session and not at_start
    await state.update_session_activity_async(row.id, active=has_session, end_seen=seen)
    row.active = has_session
    logger.info(
        "Session %s on %s is %s",
        row.session_name,
        row.box,
        "active" if has_session else "inactive",
    )
    return True


async def match_tmux_all(rows: Iterable[state.Session], live: LiveSessions) -> int:
    """``match_tmux`` for each of *rows*, *live* being what tmux answered for their box.
    The number of rows whose flag changed."""
    changed = 0
    for row in rows:
        if await match_tmux(row, live.has(row.session_name)):
            changed += 1
    return changed


def refused_session_name(name: str) -> bool:
    """Whether the session routes refuse *name*: blank, or holding CR, LF or NUL."""
    return not name.strip() or any(c in name for c in "\r\n\x00")


def transport_of(box: Box) -> str:
    """The ``transport`` a row of *box* records (``is_tmux_row`` reads it)."""
    return getattr(box, "transport", "ssh")


async def track_session(
    box: Box,
    session_name: str,
    folder: str,
    metadata: dict | None = None,
    *,
    keep_existing: bool = False,
) -> state.Session:
    """The maker of a terminal's session row: the row of *session_name* on *box*, active, in
    *folder*, recording the box's ``transport`` beside *metadata*. A row that exists is
    updated, or with *keep_existing* answered as it is."""
    return await state.create_or_update_session_async(
        box_name=box.name,
        session_name=session_name,
        working_directory=folder,
        metadata={**(metadata or {}), "transport": transport_of(box)},
        keep_existing=keep_existing,
    )


async def track_live_sessions(box: Box, rows: Iterable[state.Session], live: LiveSessions) -> int:
    """A row for each session *live* found on its own server that has none among
    *rows* (the box's rows), in the folder tmux gave. Nothing on a box whose rows are
    not tmux sessions' (the local box of a Windows server). The number of rows made."""
    if not is_tmux_transport(transport_of(box)):
        return 0
    known = {row.session_name for row in rows}
    made = 0
    for name, folder in live.folders.items():
        if name in known or refused_session_name(name):
            continue
        await track_session(box, name, folder, keep_existing=True)
        made += 1
    return made
