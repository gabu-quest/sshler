"""A local terminal must always tear down, bound every tmux call, and reap its children.

Regression tests for the hang where a stalled tmux (or any silent PTY child) kept
the /ws/term handler, its background tmux tasks and the test client alive forever.
"""

from __future__ import annotations

import asyncio
import contextlib
import errno
import os
import pty
import select
import signal
import threading
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
import yaml
from fastapi.testclient import TestClient

from sshler import tmux
from sshler.webapp import LocalPTYProcess, ServerSettings, _list_local_tmux_windows, make_app

TOKEN = "teardown-token"
SESSION = "proj-1"


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


def _kill_quietly(pid: int) -> None:
    with contextlib.suppress(ProcessLookupError):
        os.kill(pid, signal.SIGKILL)
    with contextlib.suppress(ChildProcessError):
        os.waitpid(pid, 0)


def _sleeper(pid_file: Path) -> Callable[[str], list[str]]:
    """A tmux stand-in that records its pid, ignores its arguments and blocks for 30 s."""

    def command(_session: str) -> list[str]:
        return ["sh", "-c", 'echo $$ > "$0"; exec sleep 30', str(pid_file)]

    return command


@pytest.fixture
def sleeper_pid_file(tmp_path: Path) -> Iterator[Path]:
    pid_file = tmp_path / "sleeper.pid"
    yield pid_file
    if pid_file.exists():
        with contextlib.suppress(ProcessLookupError, ValueError):
            os.kill(int(pid_file.read_text()), signal.SIGKILL)


def test_tmux_command_times_out_and_kills_its_child(
    monkeypatch: pytest.MonkeyPatch, sleeper_pid_file: Path
) -> None:
    monkeypatch.setattr(tmux, "local_tmux_command", _sleeper(sleeper_pid_file))
    monkeypatch.setattr(tmux, "TMUX_COMMAND_TIMEOUT", 0.5, raising=False)

    started = time.monotonic()
    asyncio.run(asyncio.wait_for(tmux._run_local_tmux_command(SESSION, ["bind-key"]), timeout=5))

    assert time.monotonic() - started < 3.0
    assert _gone_within(int(sleeper_pid_file.read_text()), 2.0)


def test_window_listing_times_out_and_kills_its_child(
    monkeypatch: pytest.MonkeyPatch, sleeper_pid_file: Path
) -> None:
    monkeypatch.setattr(tmux, "local_tmux_command", _sleeper(sleeper_pid_file))
    monkeypatch.setattr("sshler.webapp.local_tmux_command", _sleeper(sleeper_pid_file))
    monkeypatch.setattr(tmux, "TMUX_COMMAND_TIMEOUT", 0.5, raising=False)

    started = time.monotonic()
    windows = asyncio.run(asyncio.wait_for(_list_local_tmux_windows(SESSION), timeout=5))

    assert windows is None
    assert time.monotonic() - started < 3.0
    assert _gone_within(int(sleeper_pid_file.read_text()), 2.0)


def _spawn_on_pty(argv: list[str]) -> LocalPTYProcess:
    """Start *argv* as a session leader on a fresh PTY, like _open_local_pty_tmux does."""
    master_fd, slave_fd = pty.openpty()
    pid = os.posix_spawnp(
        argv[0],
        argv,
        dict(os.environ),
        file_actions=[
            (os.POSIX_SPAWN_DUP2, slave_fd, 0),
            (os.POSIX_SPAWN_DUP2, slave_fd, 1),
            (os.POSIX_SPAWN_DUP2, slave_fd, 2),
        ],
        setsid=True,
    )
    os.close(slave_fd)
    stdin = os.fdopen(master_fd, "wb", buffering=0, closefd=False)
    stdout = os.fdopen(master_fd, "rb", buffering=0, closefd=False)
    return LocalPTYProcess(master_fd, pid, stdin, stdout)


def test_close_hangs_up_and_reaps_a_running_child() -> None:
    process = _spawn_on_pty(["sleep", "30"])
    try:
        process.close()
        # Under the reaper's 2 s SIGKILL fallback, so only the SIGHUP can pass this.
        assert _gone_within(process.pid, 1.0)
    finally:
        _kill_quietly(process.pid)


def _wait_for_zombie(pid: int, seconds: float = 5.0) -> None:
    """Return once *pid* has exited, without reaping it (it stays a zombie)."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if os.waitid(os.P_PID, pid, os.WEXITED | os.WNOWAIT | os.WNOHANG) is not None:
            return
        time.sleep(0.02)
    raise AssertionError(f"pid {pid} did not exit within {seconds} s")


def _wait_for_returncode(process: LocalPTYProcess, seconds: float = 5.0) -> int | None:
    """The process's recorded exit code once set, or None at the deadline."""
    deadline = time.monotonic() + seconds
    while process.returncode is None and time.monotonic() < deadline:
        time.sleep(0.02)
    return process.returncode


def test_close_reaps_a_child_that_already_exited() -> None:
    process = _spawn_on_pty(["true"])
    try:
        _wait_for_zombie(process.pid)
        process.close()
        assert _gone_within(process.pid, 2.0)
    finally:
        _kill_quietly(process.pid)


# A PTY child that ignores SIGHUP and then stays silent: only SIGKILL ends it.
_DEAF_CHILD = ["sh", "-c", 'trap "" HUP; echo deaf-ready; exec sleep 30']


def _spawn_deaf_child() -> LocalPTYProcess:
    """Spawn _DEAF_CHILD and wait until its SIGHUP trap is installed.

    Reads through the line's CRLF, so no part of it is left for a later read.
    """
    process = _spawn_on_pty(_DEAF_CHILD)
    seen = b""
    deadline = time.monotonic() + 5.0
    while not seen.endswith(b"deaf-ready\r\n"):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            _kill_quietly(process.pid)
            raise AssertionError(f"deaf child never became ready; read {seen!r}")
        readable, _, _ = select.select([process.master_fd], [], [], remaining)
        if readable:
            seen += os.read(process.master_fd, 1024)
    assert seen == b"deaf-ready\r\n"
    return process


class _ReadEnteredStream:
    """Wraps the PTY master's reader; sets *entered* just before blocking in read()."""

    def __init__(self, inner: Any, entered: threading.Event) -> None:
        self._inner = inner
        self._entered = entered

    def read(self, size: int) -> bytes:
        self._entered.set()
        return self._inner.read(size)


def test_close_keeps_the_fd_open_until_an_in_flight_read_returns(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("sshler.webapp.PTY_HANGUP_GRACE", 30.0)
    process = _spawn_deaf_child()
    fd = process.master_fd
    pty_identity = (os.fstat(fd).st_dev, os.fstat(fd).st_ino)
    entered = threading.Event()
    process.stdout = _ReadEnteredStream(process.stdout, entered)  # type: ignore[assignment]
    result: list[bytes] = []
    reader = threading.Thread(target=lambda: result.append(process.read(1024)), daemon=True)
    try:
        reader.start()
        assert entered.wait(2.0)

        process.close()

        # The blocked read still owns the fd: it is open and still this PTY.
        assert (os.fstat(fd).st_dev, os.fstat(fd).st_ino) == pty_identity
        # Typing into the PTY echoes back on the master, which ends the read.
        os.write(fd, b"z")
        reader.join(2.0)
        assert result == [b"z"]
        with pytest.raises(OSError) as closed:
            os.fstat(fd)
        assert closed.value.errno == errno.EBADF
        # A read queued before close() and started after it never touches the fd.
        assert process.read(1024) == b""
    finally:
        _kill_quietly(process.pid)


def test_wait_after_close_returns_without_raising() -> None:
    process = _spawn_on_pty(["true"])
    try:
        _wait_for_zombie(process.pid)
        process.close()
        assert process.returncode == 0
        assert asyncio.run(process.wait()) == 0
    finally:
        if process.returncode is None:  # never signal a reaped (reusable) pid
            _kill_quietly(process.pid)


def test_no_signal_reaches_a_pid_after_the_reaper_reaped_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("sshler.webapp.PTY_HANGUP_GRACE", 0.2)
    process = _spawn_deaf_child()
    try:
        process.close()  # SIGHUP is ignored, so the background reaper must SIGKILL and reap it
        assert _wait_for_returncode(process) == -signal.SIGKILL

        sent: list[tuple[int, int]] = []
        monkeypatch.setattr(os, "kill", lambda pid, sig: sent.append((pid, sig)))
        monkeypatch.setattr(os, "killpg", lambda pgid, sig: sent.append((pgid, sig)))
        process.hangup()
        process.terminate()
        process.close()
        process.resize(100, 40)
        assert sent == []
    finally:
        monkeypatch.undo()
        if process.returncode is None:  # never signal a reaped (reusable) pid
            _kill_quietly(process.pid)


def test_resize_never_signals_a_reaped_but_open_pty() -> None:
    process = _spawn_on_pty(["true"])
    try:
        assert asyncio.run(process.wait()) == 0  # reaped; the PTY is still open
        sent: list[tuple[int, int]] = []
        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(os, "kill", lambda pid, sig: sent.append((pid, sig)))
            patch.setattr(os, "killpg", lambda pgid, sig: sent.append((pgid, sig)))
            patch.setattr(os, "getpgid", lambda pid: pid)
            process.resize(100, 40)
        assert sent == []
    finally:
        process.close()


def test_wait_reaps_without_os_waitid(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delattr(os, "waitid")  # absent on macOS before Python 3.13
    process = _spawn_on_pty(["sh", "-c", "sleep 0.3; exit 7"])
    try:
        assert asyncio.run(asyncio.wait_for(process.wait(), timeout=10)) == 7
        with pytest.raises(ChildProcessError):
            os.waitpid(process.pid, os.WNOHANG)  # already reaped: no zombie left
    finally:
        if process.returncode is None:  # never signal a reaped (reusable) pid
            _kill_quietly(process.pid)
        process.close()


def test_reaper_kills_and_reaps_a_deaf_child_without_os_waitid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("sshler.webapp.PTY_HANGUP_GRACE", 0.2)
    process = _spawn_deaf_child()
    monkeypatch.delattr(os, "waitid")  # absent on macOS before Python 3.13
    try:
        process.close()  # SIGHUP is ignored; the reaper must SIGKILL and then reap
        assert _wait_for_returncode(process) == -signal.SIGKILL
        with pytest.raises(ChildProcessError):
            os.waitpid(process.pid, os.WNOHANG)
    finally:
        monkeypatch.undo()
        if process.returncode is None:  # never signal a reaped (reusable) pid
            _kill_quietly(process.pid)


def test_socket_dir_follows_tmux_tmpdir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("TMUX_TMPDIR", str(tmp_path))
    assert tmux._socket_dir() == tmp_path / f"tmux-{os.getuid()}"


def test_socket_dir_defaults_to_tmp(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TMUX_TMPDIR", raising=False)
    assert tmux._socket_dir() == Path(f"/tmp/tmux-{os.getuid()}")


@pytest.fixture
def client() -> Iterator[TestClient]:
    config_dir = Path(os.environ["SSHLER_CONFIG_DIR"])
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "boxes.yaml").write_text(yaml.safe_dump({"boxes": []}), encoding="utf-8")
    with TestClient(make_app(ServerSettings(csrf_token=TOKEN))) as test_client:
        yield test_client


@pytest.fixture
def workdir(tmp_path: Path) -> Path:
    path = tmp_path / "work"
    path.mkdir()
    return path


def _term_url(workdir: Path) -> str:
    return f"/ws/term?host=local&dir={workdir}&session={SESSION}&cols=80&rows=24&token={TOKEN}"


def _receive_until(ws, marker: bytes, count: int = 1, max_messages: int = 50) -> bytes:  # type: ignore[no-untyped-def]
    received = b""
    for _ in range(max_messages):
        message = ws.receive()
        if message.get("bytes"):
            received += message["bytes"]
            if received.count(marker) >= count:
                return received
    return received


def _wait_until(condition: Callable[[], bool], seconds: float) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.02)
    return condition()


def _read_pid_file(pid_file: Path, seconds: float = 3.0) -> int:
    """The pid in *pid_file* once fully written (``echo $$ >`` truncates first)."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        with contextlib.suppress(FileNotFoundError):
            text = pid_file.read_text()
            if text.endswith("\n") and text.strip().isdigit():
                return int(text)
        time.sleep(0.02)
    raise AssertionError(f"{pid_file} held no complete pid after {seconds} s")


def _pty_child_pid(fake: Any) -> int:
    return _read_pid_file(Path(f"{fake.log}.pid"))


def test_pid_read_waits_for_the_pid_to_be_written(tmp_path: Path) -> None:
    pid_file = tmp_path / "child.pid"
    pid_file.write_text("")  # the shell has truncated the file but not written yet
    writer = threading.Timer(0.3, pid_file.write_text, args=("4242\n",))
    writer.start()
    try:
        assert _read_pid_file(pid_file) == 4242
    finally:
        writer.cancel()


def test_local_terminal_round_trips_bytes_through_a_real_pty(
    client: TestClient, fake_tmux: Any, workdir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    history: list[tuple[str, str]] = []

    async def fake_history(session: str, directory: str) -> None:
        history.append((session, directory))

    monkeypatch.setattr("sshler.webapp.record_ts_history", fake_history)

    with client.websocket_connect(_term_url(workdir)) as ws:
        ws.send_bytes(b"marker-7f3a\n")
        echoed = _receive_until(ws, b"marker-7f3a", count=2)
        history_recorded = _wait_until(lambda: bool(history), 3.0)

    # Once from the tty's own echo, once from `cat`: the fake tmux ran (and logged its
    # argv) only if the second copy arrives.
    assert echoed.count(b"marker-7f3a") == 2
    assert [call for call in fake_tmux.calls() if call.startswith("new ")] == [
        f"new -As {SESSION} -c {workdir}"
    ]
    assert history_recorded
    assert history == [(SESSION, str(workdir))]


def test_client_disconnect_hangs_up_a_silent_pty_child(
    client: TestClient, fake_tmux: Any, workdir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Push the reaper's SIGKILL fallback past the window, so only the hangup can pass.
    monkeypatch.setattr("sshler.webapp.PTY_HANGUP_GRACE", 30.0)
    with client.websocket_connect(_term_url(workdir)) as ws:
        ws.send_bytes(b"ready\n")
        _receive_until(ws, b"ready")
        child = _pty_child_pid(fake_tmux)
        # A browser going away: the handler sees a disconnect and nothing cancels it.
        ws.send({"type": "websocket.disconnect", "code": 1001})
        assert _gone_within(child, 3.0)


def test_closing_the_terminal_reaps_its_pty_child(
    client: TestClient, fake_tmux: Any, workdir: Path
) -> None:
    with client.websocket_connect(_term_url(workdir)) as ws:
        ws.send_bytes(b"ready\n")
        _receive_until(ws, b"ready")
        child = _pty_child_pid(fake_tmux)

    assert _gone_within(child, 3.0)


def test_closing_the_terminal_cancels_its_background_tmux_tasks(
    client: TestClient, fake_tmux: Any, workdir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    started = threading.Event()
    cancelled = threading.Event()

    async def stalled_bindings(_session: str) -> None:
        started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.set()
            raise

    monkeypatch.setattr("sshler.webapp._configure_tmux_bindings", stalled_bindings)

    with client.websocket_connect(_term_url(workdir)) as ws:
        ws.send_bytes(b"ready\n")
        _receive_until(ws, b"ready")
        assert started.wait(2.0)

    # The client's event loop is still running here, so only the handler can have cancelled it.
    assert cancelled.wait(2.0)


@pytest.fixture
def stalled_tmux(monkeypatch: pytest.MonkeyPatch, sleeper_pid_file: Path) -> Path:
    """Every local tmux command blocks for 30 s; every bound is cut to 0.5 s."""
    monkeypatch.setattr(tmux, "local_tmux_command", _sleeper(sleeper_pid_file))
    monkeypatch.setattr(tmux, "TMUX_COMMAND_TIMEOUT", 0.5)
    monkeypatch.setattr(tmux, "TMUX_CAPTURE_TIMEOUT", 0.5)
    return sleeper_pid_file


async def _tracked_session() -> str:
    from sshler import state

    record = await state.create_or_update_session_async("local", SESSION, "/tmp")
    return record.id


@pytest.mark.parametrize(
    ("method", "path", "body", "expected_status"),
    [
        ("PATCH", "/api/v1/boxes/local/sessions/{id}", {"session_name": "renamed"}, 500),
        ("DELETE", "/api/v1/boxes/local/sessions/{id}?kill_tmux=true", None, 200),
        ("GET", f"/api/v1/boxes/local/sessions/{SESSION}/capture", None, 504),
    ],
    ids=["rename", "delete-kill-session", "capture-pane"],
)
def test_session_endpoints_are_bounded_by_a_stalled_tmux(
    client: TestClient,
    stalled_tmux: Path,
    method: str,
    path: str,
    body: dict[str, str] | None,
    expected_status: int,
) -> None:
    session_id = client.portal.call(_tracked_session)  # type: ignore[union-attr]

    started = time.monotonic()
    response = client.request(
        method, path.format(id=session_id), json=body, headers={"X-SSHLER-TOKEN": TOKEN}
    )

    assert response.status_code == expected_status
    assert time.monotonic() - started < 3.0
    assert _gone_within(int(stalled_tmux.read_text()), 2.0)


def test_window_snapshot_is_bounded_by_a_stalled_tmux(stalled_tmux: Path) -> None:
    from sshler.snapshot import capture_local_windows

    started = time.monotonic()
    windows = asyncio.run(asyncio.wait_for(capture_local_windows(SESSION), timeout=5))

    assert windows is None
    assert time.monotonic() - started < 3.0
    assert _gone_within(int(stalled_tmux.read_text()), 2.0)
