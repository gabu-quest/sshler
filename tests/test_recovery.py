"""Tests for tmux session recovery (snapshot.py)."""

from __future__ import annotations

import os
import tempfile
from unittest.mock import AsyncMock, patch

import pytest

os.environ["SSHLER_CONFIG_DIR"] = tempfile.mkdtemp(prefix="sshler_")

from sshler.snapshot import recreate_session


@pytest.fixture
def two_window_snapshot() -> list[dict]:
    """Snapshot with 2 windows, 3 panes total."""
    return [
        {
            "index": 0,
            "name": "editor",
            "command": "nvim",
            "path": "/tmp",
            "panes": [{"index": 0, "command": "nvim", "path": "/tmp"}],
        },
        {
            "index": 1,
            "name": "shell",
            "command": "zsh",
            "path": "/tmp",
            "panes": [
                {"index": 0, "command": "zsh", "path": "/tmp"},
                {"index": 1, "command": "htop", "path": "/tmp"},
            ],
        },
    ]


def _make_run_tmux(has_session_rc: int = 1):
    """Build a mock _run_tmux that tracks all commands issued.

    *has_session_rc* controls what ``has-session`` returns:
      0 = session exists (the auto-reconnect race scenario)
      1 = session does not exist (normal recovery)
    """
    calls: list[list[str]] = []

    async def fake_run_tmux(cmd, timeout=5, *, capture_output=True):
        calls.append(cmd)
        subcmd = next((c for c in cmd if c in {
            "has-session", "kill-server", "new-session", "new-window",
            "split-window", "send-keys", "bind-key",
        }), None)
        if subcmd == "has-session":
            return (has_session_rc, b"", b"")
        return (0, b"", b"")

    return fake_run_tmux, calls


@pytest.mark.asyncio
async def test_recreate_creates_all_windows(two_window_snapshot: list[dict]) -> None:
    """Normal recovery: session doesn't exist, all windows are created."""
    fake_run, calls = _make_run_tmux(has_session_rc=1)

    with patch("sshler.snapshot._run_tmux", side_effect=fake_run):
        result = await recreate_session("myproj", two_window_snapshot)

    assert result is True
    subcmds = [next((c for c in cmd if not c.startswith("-") and not c.startswith("ts-") and c != "tmux"), "") for cmd in calls]
    # Must have: has-session, new-session, bind-key, send-keys (first win), split-window (second pane of win 1), new-window, send-keys (win 1)
    assert any("new-session" in cmd for cmd in calls), f"Expected new-session in {calls}"
    assert any("new-window" in cmd for cmd in calls), f"Expected new-window for second window in {calls}"


@pytest.mark.asyncio
async def test_recreate_kills_existing_before_recreating(two_window_snapshot: list[dict]) -> None:
    """Race condition fix: session exists from auto-reconnect, must be killed and recreated."""
    fake_run, calls = _make_run_tmux(has_session_rc=0)  # session already exists

    with patch("sshler.snapshot._run_tmux", side_effect=fake_run):
        result = await recreate_session("myproj", two_window_snapshot)

    assert result is True

    # Must kill the existing session before recreating
    assert any("kill-server" in cmd for cmd in calls), \
        f"Expected kill-server to destroy bare auto-reconnect session, got: {calls}"

    # Must still create the session with full layout
    assert any("new-session" in cmd for cmd in calls), \
        f"Expected new-session after killing bare session, got: {calls}"
    assert any("new-window" in cmd for cmd in calls), \
        f"Expected new-window for second window, got: {calls}"

    # Verify ordering: kill-server comes before new-session
    kill_idx = next(i for i, cmd in enumerate(calls) if "kill-server" in cmd)
    new_idx = next(i for i, cmd in enumerate(calls) if "new-session" in cmd)
    assert kill_idx < new_idx, "kill-server must happen before new-session"


@pytest.mark.asyncio
async def test_recreate_empty_windows_returns_false() -> None:
    """No windows in snapshot → return False, don't create anything."""
    fake_run, calls = _make_run_tmux()

    with patch("sshler.snapshot._run_tmux", side_effect=fake_run):
        result = await recreate_session("myproj", [])

    assert result is False
    assert len(calls) == 0, "Should not issue any tmux commands with empty windows"
