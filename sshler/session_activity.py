"""The one rule for a session row's ``active`` flag.

A tmux session's row is active exactly while tmux has that session. Every writer that
learns what tmux has calls ``match_tmux`` with it: the socket's end
(``webapp._run_terminal``), the sync route (``api/sessions.py``), the startup reconcile
and the snapshot loop (``snapshot.py``), and a dismiss of a lost session
(``api/recovery.py``). Where tmux could not be asked about a session, its row is left
as it is: "could not ask" is never read as "has no such session". It sits beside the
state store and below the API, so ``snapshot.py`` can call it too (``api`` imports
``snapshot`` through its recovery routes).

A row that is not a tmux session's is left alone: on a Windows server a local row is a
persisted native shell, whose registry marks it when the shell ends. A plain shell has
no row.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable

from . import state
from . import tmux as tmux_module
from .tmux import LiveSessions

logger = logging.getLogger(__name__)


def is_tmux_row(row: state.Session) -> bool:
    """Whether *row* is a tmux session's: a remote box's row always is, a local row
    unless this server runs on Windows (where the local box opens native shells)."""
    return row.metadata.get("transport", "local") != "local" or not tmux_module._IS_WINDOWS


async def match_tmux(row: state.Session, has_session: bool | None) -> bool:
    """Make *row* active exactly when tmux has its session (*has_session*, as
    ``LiveSessions.has`` answers it). None (tmux could not be asked about it) leaves the
    row as it is, as does a row that already says so or is not a tmux session's. True
    when the flag changed."""
    if has_session is None or not is_tmux_row(row) or row.active == has_session:
        return False
    await state.update_session_activity_async(row.id, active=has_session)
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
