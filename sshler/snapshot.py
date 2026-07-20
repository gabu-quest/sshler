"""Periodic tmux session state snapshots for crash recovery."""

from __future__ import annotations

import asyncio
import logging
import os
import shlex
import time

from . import state
from .settings import SshlerSettings
from .tmux import discover_local_sessions, local_tmux_command

logger = logging.getLogger(__name__)

_last_snapshot_at: float | None = None
_recovery_sessions: list[dict] = []


def get_last_snapshot_at() -> float | None:
    """Return timestamp of the most recent successful snapshot."""
    return _last_snapshot_at


def get_recovery_sessions() -> list[dict]:
    """Return the current list of lost sessions (startup + live-detected)."""
    return _recovery_sessions


def set_recovery_sessions(sessions: list[dict]) -> None:
    """Replace the recovery session list."""
    global _recovery_sessions
    _recovery_sessions = sessions


async def capture_local_windows(session_name: str) -> list[dict] | None:
    """Capture per-window and per-pane state for a local tmux session.

    Uses ``list-panes -s`` to capture ALL panes across all windows,
    then groups them by window index.  Each window dict carries a
    ``panes`` list so recovery can recreate splits.
    """
    command = local_tmux_command(session_name) + [
        "list-panes",
        "-s",
        "-F",
        "#{window_index}|#{window_name}|#{pane_index}|#{pane_current_command}|#{pane_current_path}",
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

    win_map: dict[int, dict] = {}
    for line in stdout.decode("utf-8", errors="ignore").splitlines():
        parts = line.split("|", 4)
        if len(parts) < 5:
            continue
        win_idx = int(parts[0])
        pane = {"index": int(parts[2]), "command": parts[3], "path": parts[4]}

        if win_idx not in win_map:
            # command/path at window level = first pane (backward compat)
            win_map[win_idx] = {
                "index": win_idx,
                "name": parts[1],
                "command": pane["command"],
                "path": pane["path"],
                "panes": [],
            }
        win_map[win_idx]["panes"].append(pane)

    return sorted(win_map.values(), key=lambda w: w["index"])


async def snapshot_all_sessions() -> int:
    """Capture window state for all active local sessions. Returns count.

    Also detects dead sessions: if an active session's tmux is gone but we
    have a previous snapshot, it's added to the recovery list.
    """
    global _last_snapshot_at
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
        elif meta.get("last_snapshot_at") and meta.get("windows"):
            # tmux is gone but we have a snapshot — session died mid-run
            existing_ids = {s["id"] for s in _recovery_sessions}
            if session.id not in existing_ids:
                _recovery_sessions.append({
                    "id": session.id,
                    "box": session.box,
                    "session_name": session.session_name,
                    "working_directory": session.working_directory,
                    "last_snapshot_at": meta["last_snapshot_at"],
                    "windows": meta["windows"],
                })
                logger.warning("Detected dead session: %s", session.session_name)
                await state.update_session_activity_async(session.id, active=False)
    if count > 0:
        _last_snapshot_at = time.time()
    return count


async def snapshot_loop(settings: SshlerSettings) -> None:
    """Background task that periodically snapshots tmux state."""
    logger.info(
        "Snapshot loop started (enabled=%s interval=%ss)",
        settings.snapshot_enabled,
        settings.snapshot_interval,
    )
    ticks = 0
    while True:
        interval = max(5, settings.snapshot_interval)
        await asyncio.sleep(interval)
        ticks += 1
        try:
            if settings.snapshot_enabled:
                count = await snapshot_all_sessions()
                if count > 0:
                    logger.debug("Snapshotted %d session(s)", count)
            if ticks % max(1, 3600 // interval) == 0:
                purged = await state.purge_stale_snapshots_async()
                if purged > 0:
                    logger.info("Purged %d stale snapshot(s)", purged)
        except Exception:
            logger.exception("Snapshot loop error")


async def reconcile_on_startup() -> list[dict]:
    """Detect lost vs recovered sessions at startup. Returns list of lost sessions."""
    live_sessions = await discover_local_sessions()
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
            meta = session.metadata
            meta["recovered_at"] = time.time()
            await state.update_session_metadata_async(session.id, meta)
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


def remove_recovery_session(session_id: str) -> None:
    """Atomically remove a single session from the recovery list by ID."""
    global _recovery_sessions
    _recovery_sessions = [s for s in _recovery_sessions if s["id"] != session_id]


def _resolve_path(path: str | None, fallback: str = "~") -> str:
    """Return *path* if it exists on disk, otherwise *fallback*.

    tmux ``-c`` silently falls back to ``~`` when given a non-existent
    directory.  Validating here lets us log a warning and use a better
    fallback (e.g. the session's first window path) instead of silently
    losing the user's context.
    """
    if path and os.path.isdir(path):
        return path
    if path:
        logger.warning("Snapshot path %s no longer exists, falling back to %s", path, fallback)
    return fallback


TMUX_CMD_TIMEOUT = 5  # seconds


async def _run_tmux(
    cmd: list[str], timeout: float = TMUX_CMD_TIMEOUT, *, capture_output: bool = True,
) -> tuple[int, bytes, bytes]:
    """Run a tmux command with a timeout. Returns (returncode, stdout, stderr).

    When *capture_output* is False, stdout/stderr are sent to DEVNULL and
    ``proc.wait()`` is used instead of ``communicate()``.  This avoids a
    hang caused by tmux server processes inheriting pipe FDs — the server
    keeps the pipes open so ``communicate()`` never sees EOF.
    """
    if capture_output:
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            try:
                proc.kill()
            except ProcessLookupError:
                pass
            try:
                await proc.communicate()
            except Exception:
                pass
            raise
        return proc.returncode, stdout, stderr
    else:
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
        )
        try:
            await asyncio.wait_for(proc.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            try:
                proc.kill()
            except ProcessLookupError:
                pass
            raise
        return proc.returncode, b"", b""


async def _cleanup_stale_socket(session_name: str) -> None:
    """Kill a stale tmux server socket so new-session can start fresh.

    After a crash/SIGKILL the socket file exists but the server is dead.
    Sending kill-server with a short timeout clears it.
    """
    base = local_tmux_command(session_name)
    try:
        await _run_tmux(base + ["kill-server"], timeout=2, capture_output=False)
    except (asyncio.TimeoutError, Exception):
        pass


async def recreate_session(session_name: str, windows: list[dict]) -> bool:
    """Recreate a tmux session with the last-known window and pane layout."""
    if not windows:
        logger.warning("recreate_session(%s): no windows to recreate", session_name)
        return False

    base = local_tmux_command(session_name)

    # If the session already exists, kill it so we can recreate with the full
    # window layout from the snapshot.  This handles the race where Terminal
    # auto-reconnect creates a bare 1-window session before the user triggers
    # recovery from the UI.
    try:
        rc, _, _ = await _run_tmux(base + ["has-session", "-t", session_name], timeout=2, capture_output=False)
        if rc == 0:
            logger.info("Session %s already exists — killing for full recreation", session_name)
            await _cleanup_stale_socket(session_name)
    except asyncio.TimeoutError:
        # Stale socket — clean it up so new-session works
        logger.info("Stale socket for %s, cleaning up", session_name)
        await _cleanup_stale_socket(session_name)
    except Exception:
        pass

    # Create session with first window.
    # capture_output=False is critical: new-session may fork a tmux server
    # whose inherited pipe FDs block communicate() indefinitely.
    first = windows[0]
    first_path = _resolve_path(first.get("path"))
    cmd = base + [
        "new-session", "-d", "-s", session_name,
        "-c", first_path,
    ]
    if first.get("name"):
        cmd += ["-n", first["name"]]
    try:
        rc, _, _ = await _run_tmux(cmd, capture_output=False)
        if rc != 0:
            logger.error("Failed to create session %s (rc=%d)", session_name, rc)
            return False
    except asyncio.TimeoutError:
        logger.error("Timed out creating session %s", session_name)
        return False
    except Exception:
        logger.exception("Failed to create session %s", session_name)
        return False

    # Make Ctrl+B c inherit the current pane's directory instead of ~
    await _configure_session_bindings(base)

    # Echo last command in first pane of first window
    if first.get("command"):
        await _tmux_send_keys(base, session_name, 0, f"Last running: {first['command']}", pane_index=0)

    # Create additional panes in first window
    await _recreate_panes(base, session_name, first)

    # Create additional windows
    for win in windows[1:]:
        win_path = _resolve_path(win.get("path"), fallback=first_path)
        new_win_cmd = base + [
            "new-window", "-t", session_name,
            "-c", win_path,
        ]
        if win.get("name"):
            new_win_cmd += ["-n", win["name"]]
        try:
            await _run_tmux(new_win_cmd, capture_output=False)
        except (asyncio.TimeoutError, Exception):
            continue

        if win.get("command"):
            await _tmux_send_keys(base, session_name, win["index"], f"Last running: {win['command']}", pane_index=0)

        # Create additional panes in this window
        await _recreate_panes(base, session_name, win)

    total_panes = sum(len(w.get("panes", [])) or 1 for w in windows)
    logger.info("Recreated session %s with %d window(s), %d pane(s)", session_name, len(windows), total_panes)
    return True


async def _configure_session_bindings(base: list[str]) -> None:
    """Set tmux key bindings so new windows inherit the current pane's directory.

    Without this, Ctrl+B c creates windows in the tmux server's CWD (usually ~).
    """
    try:
        await _run_tmux(
            base + ["bind-key", "c", "new-window", "-c", "#{pane_current_path}"],
            capture_output=False,
        )
    except (asyncio.TimeoutError, Exception):
        logger.debug("Failed to set bind-key for pane_current_path")


async def _recreate_panes(base: list[str], session_name: str, win: dict) -> None:
    """Create additional panes (splits) within a window from snapshot data.

    Skips the first pane (already created with the window itself).
    Old snapshots without a ``panes`` key are handled gracefully (no-op).
    """
    panes = win.get("panes", [])
    if len(panes) <= 1:
        return

    win_path = win.get("path", "~")
    for pane in panes[1:]:
        pane_path = _resolve_path(pane.get("path"), fallback=win_path)
        split_cmd = base + [
            "split-window",
            "-t", f"{session_name}:{win['index']}",
            "-c", pane_path,
        ]
        try:
            rc, _, _ = await _run_tmux(split_cmd, capture_output=False)
            if rc != 0:
                logger.debug("split-window failed for %s:%d (rc=%d)", session_name, win["index"], rc)
                continue
        except (asyncio.TimeoutError, Exception):
            continue

        if pane.get("command"):
            # send-keys without explicit pane targets the active (newly split) pane
            await _tmux_send_keys(base, session_name, win["index"], f"Last running: {pane['command']}")


async def _tmux_send_keys(
    base: list[str], session: str, window_index: int, message: str, *, pane_index: int | None = None,
) -> None:
    """Send an echo command to a tmux window/pane."""
    escaped = shlex.quote(message)
    target = f"{session}:{window_index}"
    if pane_index is not None:
        target += f".{pane_index}"
    cmd = base + ["send-keys", "-t", target, f"echo {escaped}", "Enter"]
    try:
        await _run_tmux(cmd, capture_output=False)
    except (asyncio.TimeoutError, Exception):
        pass
