"""Every local tmux (and ``ts-add``) subprocess is bounded, killed and reaped.

A stand-in ``tmux`` / ``ts-add`` is put first on ``PATH``, so these tests also hold
for call sites that build their own argv instead of using ``local_tmux_command``.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import signal
import time
from collections.abc import Awaitable, Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
import yaml
from fastapi.testclient import TestClient

from sshler import snapshot, state, tmux
from sshler.api import dependencies
from sshler.api import sessions as sessions_api
from sshler.webapp import ServerSettings, make_app

TOKEN = "tmux-subprocess-token"
SESSION = "proj-1"
BOUND = 0.5
CLAUDE_UUID = "aaaaaaaa-0000-4000-8000-0000000000e1"

# Records its own pid, then blocks for 30 s whatever its arguments are.
STALLED = '#!/bin/sh\necho $$ > "$STALL_PID_FILE"\nexec sleep 30\n'
# Exits 0 at once, but `new-session` first backgrounds a child that inherits
# stdout, the way a freshly forked tmux server does.
FORKS_A_SERVER = (
    "#!/bin/sh\n"
    'for arg in "$@"; do\n'
    '  case "$arg" in\n'
    "    new-session|new|start-server|start)\n"
    '      sleep 30 & echo $! >> "$FORK_PID_FILE";;\n'
    "  esac\n"
    "done\n"
    "exit 0\n"
)
QUIET = "#!/bin/sh\nexit 0\n"


def _gone_within(pid: int, seconds: float) -> bool:
    """True once *pid* no longer exists (exited AND reaped by its parent)."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        time.sleep(0.05)
    return False


def _install(bin_dir: Path, name: str, script: str) -> None:
    path = bin_dir / name
    path.write_text(script, encoding="utf-8")
    path.chmod(0o755)


def _kill_pids_in(pid_file: Path) -> None:
    if not pid_file.exists():
        return
    for line in pid_file.read_text().split():
        with contextlib.suppress(ProcessLookupError, ValueError):
            os.kill(int(line), signal.SIGKILL)


@pytest.fixture
def fake_bin(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    return bin_dir


@pytest.fixture
def stalled_pid_file(
    fake_bin: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[Path]:
    """``tmux`` and ``ts-add`` stall for 30 s; every tmux bound is cut to 0.5 s."""
    pid_file = tmp_path / "stalled.pid"
    monkeypatch.setenv("STALL_PID_FILE", str(pid_file))
    _install(fake_bin, "tmux", STALLED)
    _install(fake_bin, "ts-add", STALLED)
    monkeypatch.setattr(tmux, "SOCKET_TIMEOUT", BOUND)
    monkeypatch.setattr(tmux, "TMUX_COMMAND_TIMEOUT", BOUND)
    monkeypatch.setattr(tmux, "TMUX_CAPTURE_TIMEOUT", BOUND)
    yield pid_file
    _kill_pids_in(pid_file)


@pytest.fixture
def fork_pid_file(
    fake_bin: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[Path]:
    """``tmux new-session`` leaves a child holding stdout open; ``ts-add`` is a no-op."""
    pid_file = tmp_path / "forked.pids"
    monkeypatch.setenv("FORK_PID_FILE", str(pid_file))
    _install(fake_bin, "tmux", FORKS_A_SERVER)
    _install(fake_bin, "ts-add", QUIET)
    yield pid_file
    _kill_pids_in(pid_file)


def _client() -> TestClient:
    config_dir = Path(os.environ["SSHLER_CONFIG_DIR"])
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "boxes.yaml").write_text(yaml.safe_dump({"boxes": []}), encoding="utf-8")
    # No lifespan: entering it would run startup tmux discovery through the fake.
    return TestClient(make_app(ServerSettings(csrf_token=TOKEN)))


# --------------------------------------------------------------------------- #
# 1. Every call site is bounded and its child is killed and reaped.
# --------------------------------------------------------------------------- #


async def _expect_timeout(call: Awaitable[Any]) -> str:
    with pytest.raises(asyncio.TimeoutError):
        await call
    return "timed out"


SITES: list[tuple[str, Callable[[], Awaitable[Any]], Any]] = [
    ("query_server", lambda: tmux._query_server(f"ts-{SESSION}"), set()),
    ("query_default_server", lambda: tmux._query_default_server(), set()),
    ("list_local_window_names", lambda: tmux.list_local_window_names(SESSION), set()),
    ("list_local_pane_pids", lambda: tmux._list_local_pane_pids(SESSION), []),
    ("ts_add", lambda: tmux.record_ts_history(SESSION, "/tmp"), None),
    ("run_local_tmux_command", lambda: tmux._run_local_tmux_command(SESSION, ["bind-key"]), None),
    (
        "run_local_tmux",
        lambda: _expect_timeout(tmux.run_local_tmux(SESSION, ["list-panes"])),
        "timed out",
    ),
    (
        "snapshot_run_tmux_captured",
        lambda: _expect_timeout(snapshot._run_tmux(["tmux", "has-session"], timeout=BOUND)),
        "timed out",
    ),
    (
        "snapshot_run_tmux_uncaptured",
        lambda: _expect_timeout(
            snapshot._run_tmux(["tmux", "kill-server"], timeout=BOUND, capture_output=False)
        ),
        "timed out",
    ),
]


@pytest.mark.parametrize(("call", "expected"), [s[1:] for s in SITES], ids=[s[0] for s in SITES])
def test_a_stalled_child_is_gone_within_2s_of_its_timeout(
    stalled_pid_file: Path, call: Callable[[], Awaitable[Any]], expected: Any
) -> None:
    started = time.monotonic()
    result = asyncio.run(asyncio.wait_for(call(), timeout=10))
    elapsed = time.monotonic() - started

    assert result == expected
    assert elapsed < BOUND + 2.0
    assert _gone_within(int(stalled_pid_file.read_text()), 2.0)


def test_a_cancelled_command_kills_and_reaps_its_child(stalled_pid_file: Path) -> None:
    async def cancel_mid_command() -> None:
        task = asyncio.ensure_future(tmux.run_local_tmux(SESSION, ["list-panes"], timeout=30))
        # Cancel only once the child is running and has written its whole pid.
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            with contextlib.suppress(FileNotFoundError):
                if stalled_pid_file.read_text().endswith("\n"):
                    break
            await asyncio.sleep(0.02)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(asyncio.wait_for(cancel_mid_command(), timeout=10))

    assert _gone_within(int(stalled_pid_file.read_text()), 2.0)


def _open_fd_count() -> int:
    return len(os.listdir("/proc/self/fd"))


# Stalls like STALLED, but first leaves a grandchild holding the inherited pipes.
HOLDS_THE_PIPE = (
    '#!/bin/sh\necho $$ > "$STALL_PID_FILE"\n'
    'sleep 30 & echo $! >> "$FORK_PID_FILE"\nexec sleep 30\n'
)


def test_a_timed_out_command_releases_its_pipes_while_a_grandchild_holds_them(
    fake_bin: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stall_file = tmp_path / "stalled.pid"
    fork_file = tmp_path / "forked.pids"
    monkeypatch.setenv("STALL_PID_FILE", str(stall_file))
    monkeypatch.setenv("FORK_PID_FILE", str(fork_file))
    _install(fake_bin, "tmux", HOLDS_THE_PIPE)

    async def wait_for_exit_only(self: asyncio.subprocess.Process) -> int:
        # Newer Pythons resolve wait() on the child's exit even while a grandchild
        # holds the pipes; 3.12.3 waits for the pipes too. Model the former.
        deadline = time.monotonic() + 5.0
        while self._transport.get_returncode() is None and time.monotonic() < deadline:
            await asyncio.sleep(0.01)
        return self._transport.get_returncode()

    monkeypatch.setattr(asyncio.subprocess.Process, "wait", wait_for_exit_only)

    async def timed_out_run() -> int:
        before = _open_fd_count()
        with pytest.raises(asyncio.TimeoutError):
            await tmux.run_bounded(["tmux", "list-panes"], timeout=BOUND)
        # Transports release their fds on the next loop iterations; wait for that.
        deadline = time.monotonic() + 2.0
        while _open_fd_count() > before and time.monotonic() < deadline:
            await asyncio.sleep(0.02)
        return _open_fd_count() - before

    try:
        leaked = asyncio.run(asyncio.wait_for(timed_out_run(), timeout=10))
        # The grandchild really did outlive the command.
        os.kill(int(fork_file.read_text().split()[0]), 0)
    finally:
        _kill_pids_in(stall_file)
        _kill_pids_in(fork_file)

    assert leaked == 0


# --------------------------------------------------------------------------- #
# 2. A command that forks a tmux server does not wait on the server's pipes.
# --------------------------------------------------------------------------- #


SERVER_STARTING = ["new-session", "new", "start-server", "start"]


@pytest.mark.parametrize("subcommand", SERVER_STARTING)
def test_starting_a_server_returns_while_its_child_holds_stdout(
    fork_pid_file: Path, subcommand: str
) -> None:
    started = time.monotonic()
    result = asyncio.run(
        asyncio.wait_for(
            tmux.run_local_tmux(SESSION, [subcommand, "-d", "-s", SESSION]), timeout=10
        )
    )
    elapsed = time.monotonic() - started

    assert result == (0, b"", b"")
    assert elapsed < 5.0
    # The backgrounded child is still alive, i.e. it really did hold the pipe open.
    forked = [int(pid) for pid in fork_pid_file.read_text().split()]
    assert len(forked) == 1
    os.kill(forked[0], 0)


@pytest.mark.parametrize("subcommand", SERVER_STARTING)
def test_side_effect_commands_return_while_a_forked_child_holds_stdout(
    fork_pid_file: Path, subcommand: str
) -> None:
    started = time.monotonic()
    asyncio.run(
        asyncio.wait_for(
            tmux._run_local_tmux_command(SESSION, [subcommand, "-d", "-s", SESSION]),
            timeout=10,
        )
    )

    assert time.monotonic() - started < 5.0
    assert len(fork_pid_file.read_text().split()) == 1


def test_claude_open_returns_while_the_new_server_holds_stdout(
    fork_pid_file: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    persisted: list[tuple[str, str, str]] = []

    async def record(box_name: str, session_name: str, working_directory: str, **_: Any) -> None:
        persisted.append((box_name, session_name, working_directory))

    # No lifespan, so no state store; only persistence is stubbed, tmux is the PATH fake.
    monkeypatch.setattr(state, "create_or_update_session_async", record)
    work = tmp_path / "work"
    (work / ".git").mkdir(parents=True)
    transcript = (
        Path(os.environ["CLAUDE_CONFIG_DIR"]) / "projects" / "-work" / f"{CLAUDE_UUID}.jsonl"
    )
    transcript.parent.mkdir(parents=True)
    transcript.write_text(
        json.dumps(
            {
                "type": "user",
                "promptSource": "typed",
                "cwd": str(work),
                "message": {"role": "user", "content": "hi"},
                "sessionId": CLAUDE_UUID,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    client = _client()

    started = time.monotonic()
    response = client.post(
        f"/api/v1/claude/sessions/{CLAUDE_UUID}/open", headers={"X-SSHLER-TOKEN": TOKEN}
    )
    elapsed = time.monotonic() - started

    assert response.status_code == 200
    assert response.json()["session_name"] == "work"
    assert response.json()["already_open"] is False
    assert len(fork_pid_file.read_text().split()) == 1
    assert persisted == [("local", "work", str(work))]
    assert elapsed < 5.0


# --------------------------------------------------------------------------- #
# 3. A capture has a 30 s bound, and a timeout is a 504 that says so.
# --------------------------------------------------------------------------- #


def test_capture_timeout_is_a_504_that_says_it_timed_out(stalled_pid_file: Path) -> None:
    client = _client()

    started = time.monotonic()
    response = client.get(
        f"/api/v1/boxes/local/sessions/{SESSION}/capture", headers={"X-SSHLER-TOKEN": TOKEN}
    )

    assert response.status_code == 504
    assert response.json()["detail"] == "tmux capture-pane timed out after 0.5 s"
    assert time.monotonic() - started < BOUND + 2.0
    assert _gone_within(int(stalled_pid_file.read_text()), 2.0)


def test_capture_is_bounded_at_30s(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, list[str], float | None]] = []

    async def spy(
        session: str, args: list[str], timeout: float | None = None
    ) -> tuple[int, bytes, bytes]:
        calls.append((session, list(args), timeout))
        return 0, b"line one\n", b""

    monkeypatch.setattr(sessions_api, "run_local_tmux", spy)
    client = _client()

    response = client.get(
        f"/api/v1/boxes/local/sessions/{SESSION}/capture", headers={"X-SSHLER-TOKEN": TOKEN}
    )

    assert response.status_code == 200
    assert calls == [
        (SESSION, ["capture-pane", "-p", "-J", "-S", "-", "-t", SESSION], 30.0),
    ]


# --------------------------------------------------------------------------- #
# 4. Only a tmux timeout is a 504; a connect timeout keeps its old 500.
# --------------------------------------------------------------------------- #


def _ssh_client() -> TestClient:
    config_dir = Path(os.environ["SSHLER_CONFIG_DIR"])
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "boxes.yaml").write_text(
        yaml.safe_dump({"boxes": [{"name": "remote", "host": "203.0.113.1", "user": "u"}]}),
        encoding="utf-8",
    )
    return TestClient(make_app(ServerSettings(csrf_token=TOKEN)))


def test_a_connect_timeout_is_not_reported_as_a_capture_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def connect_times_out(*_: Any, **__: Any) -> None:
        raise TimeoutError

    monkeypatch.setattr(dependencies, "connect", connect_times_out)
    dependencies._CONNECT_FAIL_CACHE.clear()
    client = _ssh_client()

    response = client.get(
        f"/api/v1/boxes/remote/sessions/{SESSION}/capture", headers={"X-SSHLER-TOKEN": TOKEN}
    )
    dependencies._CONNECT_FAIL_CACHE.clear()

    assert response.status_code == 500
    assert response.json()["detail"] == "capture failed: "


def test_a_remote_capture_timeout_is_a_504(monkeypatch: pytest.MonkeyPatch) -> None:
    class StalledConnection:
        closed = False

        async def run(self, *_: Any, **__: Any) -> None:
            await asyncio.sleep(30)

        def close(self) -> None:
            self.closed = True

    connection = StalledConnection()

    async def connect_ok(*_: Any, **__: Any) -> StalledConnection:
        return connection

    monkeypatch.setattr(dependencies, "connect", connect_ok)
    monkeypatch.setattr(tmux, "TMUX_CAPTURE_TIMEOUT", BOUND)
    dependencies._CONNECT_FAIL_CACHE.clear()
    client = _ssh_client()

    response = client.get(
        f"/api/v1/boxes/remote/sessions/{SESSION}/capture", headers={"X-SSHLER-TOKEN": TOKEN}
    )

    assert response.status_code == 504
    assert response.json()["detail"] == "tmux capture-pane timed out after 0.5 s"
    assert connection.closed is True
