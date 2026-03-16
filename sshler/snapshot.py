"""Periodic tmux session state snapshots for crash recovery."""

from __future__ import annotations

import asyncio
import logging
import platform
import shlex
import time

from . import state

logger = logging.getLogger(__name__)

_IS_WINDOWS = platform.system().lower().startswith("windows")


def _tmux_base_command() -> list[str]:
    if _IS_WINDOWS:
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


async def reconcile_on_startup() -> list[dict]:
    """Detect lost vs recovered sessions at startup. Returns list of lost sessions."""
    from .api.sessions import _get_live_tmux_sessions_local

    live_sessions = await _get_live_tmux_sessions_local()
    all_sessions = await state.list_all_active_sessions_async()

    # Also check inactive sessions that have snapshots (they may have been marked
    # inactive by a previous clean shutdown but the tmux died in a crash)
    inactive = await state.list_all_snapshotted_sessions_async()
    seen_ids = {s.id for s in all_sessions}
    for s in inactive:
        if s.id not in seen_ids:
            all_sessions.append(s)

    lost: list[dict] = []
    for session in all_sessions:
        meta = session.metadata
        transport = meta.get("transport", "local")
        if transport != "local":
            continue

        if session.session_name in live_sessions:
            # Tmux survived — mark as active
            await state.update_session_activity_async(session.id, active=True)
            logger.info("Recovered session: %s", session.session_name)
        elif meta.get("last_snapshot_at"):
            # Has snapshot but no live tmux — lost
            lost.append({
                "id": session.id,
                "box": session.box,
                "session_name": session.session_name,
                "working_directory": session.working_directory,
                "last_snapshot_at": meta["last_snapshot_at"],
                "windows": meta.get("windows", []),
            })
            logger.warning("Lost session: %s (last snapshot %.0fs ago)",
                           session.session_name,
                           time.time() - meta["last_snapshot_at"])

    return lost


async def recreate_session(session_name: str, windows: list[dict]) -> bool:
    """Recreate a tmux session with the last-known window layout."""
    if not windows:
        return False

    base = _tmux_base_command()

    # Create session with first window
    first = windows[0]
    cmd = base + [
        "new-session", "-d", "-s", session_name,
        "-c", first.get("path", "~"),
    ]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()
        if proc.returncode != 0:
            logger.error("Failed to create session %s: %s", session_name, stderr.decode())
            return False
    except Exception:
        logger.exception("Failed to create session %s", session_name)
        return False

    # Echo last command in first window
    if first.get("command"):
        echo_msg = f"Last running: {first['command']}"
        await _tmux_send_keys(base, session_name, 0, echo_msg)

    # Create additional windows
    for win in windows[1:]:
        new_win_cmd = base + [
            "new-window", "-t", session_name,
            "-c", win.get("path", "~"),
        ]
        try:
            proc = await asyncio.create_subprocess_exec(
                *new_win_cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            )
            await proc.communicate()
        except Exception:
            continue

        if win.get("command"):
            echo_msg = f"Last running: {win['command']}"
            await _tmux_send_keys(base, session_name, win["index"], echo_msg)

    return True


async def _tmux_send_keys(base: list[str], session: str, window_index: int, message: str) -> None:
    """Send an echo command to a tmux window."""
    escaped = shlex.quote(message)
    cmd = base + ["send-keys", "-t", f"{session}:{window_index}", f"echo {escaped}", "Enter"]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        await proc.communicate()
    except Exception:
        pass
