"""Periodic tmux session state snapshots for crash recovery."""

from __future__ import annotations

import asyncio
import logging
import time

from . import state
from .api.helpers import LOCAL_IS_WINDOWS

logger = logging.getLogger(__name__)


def _tmux_base_command() -> list[str]:
    if LOCAL_IS_WINDOWS:
        return ["wsl", "--", "tmux"]
    return ["tmux"]


async def capture_local_windows(session_name: str) -> list[dict] | None:
    """Capture per-window state for a local tmux session."""
    command = _tmux_base_command() + [
        "list-windows",
        "-F",
        "#{window_index}|#{window_name}|#{pane_current_command}|#{pane_current_path}",
        "-t",
        session_name,
    ]
    try:
        process = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await process.communicate()
    except Exception:
        return None

    if process.returncode != 0:
        return None

    windows = []
    for line in stdout.decode("utf-8", errors="ignore").splitlines():
        parts = line.split("|", 3)
        if len(parts) < 4:
            continue
        windows.append({
            "index": int(parts[0]),
            "name": parts[1],
            "command": parts[2],
            "path": parts[3],
        })
    return windows


async def snapshot_all_sessions() -> int:
    """Capture window state for all active local sessions. Returns count."""
    sessions = await state.list_all_active_sessions_async()
    count = 0
    for session in sessions:
        meta = session.metadata
        transport = meta.get("transport", "local")
        if transport != "local":
            continue

        windows = await capture_local_windows(session.session_name)
        if windows is not None:
            await state.update_session_snapshot_async(session.id, windows)
            count += 1
    return count


async def snapshot_loop(interval: float = 30.0) -> None:
    """Background task that periodically snapshots tmux state."""
    logger.info("Snapshot loop started (interval=%.0fs)", interval)
    while True:
        await asyncio.sleep(interval)
        try:
            count = await snapshot_all_sessions()
            if count > 0:
                logger.debug("Snapshotted %d session(s)", count)
        except Exception:
            logger.exception("Snapshot loop error")
