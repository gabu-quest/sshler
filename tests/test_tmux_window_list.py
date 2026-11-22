"""Window list and snapshot capture keep every name and path exactly as tmux prints it."""

from __future__ import annotations

import asyncio
import shutil
import subprocess
import uuid
from collections.abc import Iterator
from types import SimpleNamespace

import pytest

from sshler import snapshot, tmux
from sshler.terminal.local import list_local_tmux_windows as _list_local_tmux_windows
from sshler.terminal.remote import list_remote_tmux_windows as _list_tmux_windows

pytestmark = pytest.mark.skipif(shutil.which("tmux") is None, reason="tmux not installed")

NAMES = ["my window", "a|b", " lead", "trail ", "1 1", "ta\tb", "x"]
PANE_DIR_NAME = "a|b c"


@pytest.fixture
def private_tmux(monkeypatch: pytest.MonkeyPatch, tmp_path) -> Iterator[list[str]]:
    """A tmux server of this test's own; the session helpers are pointed at it."""
    socket = f"sshler-test-{uuid.uuid4().hex[:8]}"
    base = ["tmux", "-L", socket, "-f", "/dev/null"]
    monkeypatch.setattr(tmux, "local_tmux_command", lambda session: ["tmux", "-L", socket])
    monkeypatch.setattr("sshler.webapp.local_tmux_command", lambda session: ["tmux", "-L", socket])
    pane_dir = tmp_path / PANE_DIR_NAME
    pane_dir.mkdir()

    def run(*args: str) -> None:
        subprocess.run([*base, *args], check=True, capture_output=True, timeout=10)

    def open_in_pane_dir(*args: str) -> None:
        # tmux answers before the pane's child has changed folder; until then it reports
        # the server's folder. The pane's own command runs after, so it says when.
        ready = f"ready-{uuid.uuid4().hex[:8]}"
        pane = f"tmux -L {socket} wait-for -S {ready}; exec cat"
        run(*args, "-c", str(pane_dir), pane)
        run("wait-for", ready)

    run("new-session", "-d", "-s", "m", "-x", "80", "-y", "24", "-n", NAMES[0])
    try:
        for name in NAMES[1:]:
            open_in_pane_dir("new-window", "-t", "m:", "-n", name)
        open_in_pane_dir("split-window", "-t", "m:6")
        open_in_pane_dir("split-window", "-t", "m:6")
        run("select-window", "-t", "m:3")
        yield base
    finally:
        subprocess.run([*base, "kill-server"], capture_output=True)


def _printed_names(base: list[str]) -> list[str]:
    out = subprocess.run(
        [*base, "list-windows", "-t", "m", "-F", "#{window_name}"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    return out.split("\n")[:-1]


def test_the_local_window_list_returns_each_name_exactly_and_the_active_window(private_tmux):
    windows = asyncio.run(_list_local_tmux_windows("m"))
    assert windows == [
        {"index": str(i), "name": name, "active": i == 3} for i, name in enumerate(NAMES)
    ]
    assert [w["name"] for w in windows or []] == _printed_names(private_tmux)


def test_the_remote_window_list_parses_the_same_output(private_tmux):
    out = subprocess.run(
        [*private_tmux, "list-windows", "-t", "m", "-F", tmux.WINDOW_LIST_FORMAT],
        check=True,
        capture_output=True,
        text=True,
    ).stdout

    class Conn:
        async def run(self, command: str, check: bool = False):
            return SimpleNamespace(returncode=0, stdout=out)

    windows = asyncio.run(_list_tmux_windows(Conn(), "m"))  # type: ignore[arg-type]
    assert windows == [
        {"index": str(i), "name": name, "active": i == 3} for i, name in enumerate(NAMES)
    ]


@pytest.mark.parametrize("line", ["", "7", "7 1"])
def test_a_short_window_line_is_skipped(line):
    assert tmux.parse_window_list(line) == []


def test_the_snapshot_holds_every_window_with_its_panes(private_tmux, tmp_path):
    windows = asyncio.run(snapshot.capture_local_windows("m"))
    assert windows is not None
    assert [w["name"] for w in windows] == NAMES
    assert [w["index"] for w in windows] == list(range(len(NAMES)))
    pane_path = str(tmp_path / PANE_DIR_NAME)
    sixth = windows[6]
    assert [p["index"] for p in sixth["panes"]] == [0, 1, 2]
    assert [p["path"] for p in sixth["panes"]] == [pane_path] * 3
    assert sixth["path"] == pane_path
    assert windows[1]["panes"][0]["path"] == pane_path
    assert [len(w["panes"]) for w in windows] == [1, 1, 1, 1, 1, 1, 3]


def test_a_window_seen_by_only_one_of_the_two_snapshot_calls_is_left_out(monkeypatch):
    """A window that closes or opens between the window list and the pane list."""
    answers = {
        "list-windows": b"0 1 main\n3 0 opened late\n",
        "list-panes": b"0\t0\tzsh\t/a\n0\t1\tvim\t/a b\n2\t0\ttop\t/closed\n",
    }

    async def fake_tmux(session: str, args: list[str]) -> tuple[int, bytes, bytes]:
        return 0, answers[args[0]], b""

    monkeypatch.setattr(snapshot, "run_local_tmux", fake_tmux)
    assert asyncio.run(snapshot.capture_local_windows("m")) == [
        {
            "index": 0,
            "name": "main",
            "command": "zsh",
            "path": "/a",
            "panes": [
                {"index": 0, "command": "zsh", "path": "/a"},
                {"index": 1, "command": "vim", "path": "/a b"},
            ],
        }
    ]
