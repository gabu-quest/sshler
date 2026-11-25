"""``sshler stop``: it ends the server its pid file names, on POSIX and on Windows.

The real-process tests start a stand-in server (a Python child that sleeps) and stop it
through a pid file in a temp folder; they run on both systems. The Windows tests emulate
what this box cannot run: ``os.kill`` raising a plain ``OSError`` (as Windows does for a
pid that is gone, and for signal 0, which is ``CTRL_C_EVENT`` there), no
``signal.SIGKILL``, and psutil's Windows answers, where ending a process is
``TerminateProcess`` and nothing can ignore it. The search by ``pgrep`` is stubbed in
every test so a stop here never reaches a process the test did not start.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
from collections.abc import Callable, Iterator
from pathlib import Path

import psutil
import pytest

from sshler.cli import server

# A stand-in server: says it is ready once its SIGTERM handling is set, then sleeps.
_STAND_IN = """
import signal, sys, time
if sys.argv[1] == "ignore":
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
print("ready", flush=True)
time.sleep(120)
"""


@pytest.fixture
def pid_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "sshler.pid"
    monkeypatch.setattr(server, "_PID_FILE", path)
    monkeypatch.setattr(server, "_get_sshler_pids", lambda: [])
    monkeypatch.setattr(server, "_GRACE_SECONDS", 0.5)
    return path


@pytest.fixture
def stand_in(pid_file: Path) -> Iterator[Callable[[str], subprocess.Popen[str]]]:
    """Starts a stand-in server and writes its pid to the pid file; any it leaves alive
    is killed by its own process object at the end."""
    started: list[subprocess.Popen[str]] = []

    def start(on_sigterm: str) -> subprocess.Popen[str]:
        child = subprocess.Popen(
            [sys.executable, "-c", _STAND_IN, on_sigterm], stdout=subprocess.PIPE, text=True
        )
        started.append(child)
        assert child.stdout is not None
        assert child.stdout.readline() == "ready\n"
        pid_file.write_text(str(child.pid))
        return child

    yield start
    for child in started:
        if child.poll() is None:
            child.kill()
        child.wait(timeout=10)
        if child.stdout is not None:
            child.stdout.close()


def _ended(child: subprocess.Popen[str]) -> bool:
    try:
        child.wait(timeout=5)
    except subprocess.TimeoutExpired:
        return False
    return True


def test_stop_ends_the_server_its_pid_file_names(stand_in, pid_file, capsys):
    child = stand_in("default")
    assert server.stop() == 0
    assert _ended(child) is True
    assert capsys.readouterr().out == f"Stopping sshler (PID: {child.pid})...\nsshler stopped\n"
    assert pid_file.exists() is False


def test_stop_ends_a_server_that_ignores_the_first_request(stand_in, pid_file, capsys):
    child = stand_in("ignore")
    assert server.stop() == 0
    assert _ended(child) is True
    assert capsys.readouterr().out == (
        f"Stopping sshler (PID: {child.pid})...\nForce killing PID {child.pid}\nsshler stopped\n"
    )
    assert pid_file.exists() is False


def test_stop_leaves_a_process_that_started_after_the_pid_file_was_written(
    stand_in, pid_file, capsys
):
    """A pid file a crash or a reboot left behind can name a pid another program has
    since been given: that program started after the file was written, so it is not the
    server the file names."""
    child = stand_in("default")
    written = psutil.Process(child.pid).create_time() - 60
    os.utime(pid_file, (written, written))

    assert server.stop() == 1

    assert child.poll() is None
    assert capsys.readouterr().out == "sshler is not running\n"


def test_stop_when_the_pid_file_names_a_gone_pid_says_so(pid_file, capsys):
    gone = subprocess.Popen([sys.executable, "-c", "pass"])
    gone.wait(timeout=30)
    pid_file.write_text(str(gone.pid))
    assert server.stop() == 1
    assert capsys.readouterr().out == "sshler is not running\n"


# --- Windows, emulated -----------------------------------------------------------------


class _WindowsProcesses:
    """psutil on Windows over a table of live pids: a gone pid is ``NoSuchProcess``,
    and ``terminate`` and ``kill`` are both ``TerminateProcess``."""

    def __init__(self, live: set[int]) -> None:
        self.live = live
        self.ended: list[tuple[int, str]] = []

    def pid_exists(self, pid: int) -> bool:
        return pid in self.live

    def process(self, pid: int) -> _WindowsProcess:
        if pid not in self.live:
            raise psutil.NoSuchProcess(pid)
        return _WindowsProcess(self, pid)


class _WindowsProcess:
    def __init__(self, table: _WindowsProcesses, pid: int) -> None:
        self.table = table
        self.pid = pid

    def create_time(self) -> float:
        return 0.0  # started before any pid file was written

    def _end(self, how: str) -> None:
        self.table.ended.append((self.pid, how))
        self.table.live.discard(self.pid)

    def terminate(self) -> None:
        self._end("terminate")

    def kill(self) -> None:
        self._end("kill")

    def wait(self, timeout: float | None = None) -> None:
        if self.pid in self.table.live:
            raise psutil.TimeoutExpired(timeout or 0, self.pid)


@pytest.fixture
def windows(pid_file: Path, monkeypatch: pytest.MonkeyPatch) -> _WindowsProcesses:
    """A Windows stop: no SIGKILL, every ``os.kill`` a plain ``OSError`` (WinError 87),
    and psutil's Windows answers over a table holding pid 4242."""

    def windows_kill(pid: int, sig: int) -> None:
        raise OSError(22, "The parameter is incorrect")

    table = _WindowsProcesses({4242})
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.delattr(signal, "SIGKILL")
    monkeypatch.setattr(os, "kill", windows_kill)
    monkeypatch.setattr(psutil, "pid_exists", table.pid_exists)
    monkeypatch.setattr(psutil, "Process", table.process)
    return table


def test_stop_on_windows_ends_the_server_and_says_so(windows, pid_file, capsys):
    pid_file.write_text("4242")
    assert server.stop() == 0
    assert windows.ended == [(4242, "terminate")]
    assert capsys.readouterr().out == "Stopping sshler (PID: 4242)...\nsshler stopped\n"
    assert pid_file.exists() is False


def test_stop_on_windows_when_the_pid_file_names_a_gone_pid_says_so(windows, pid_file, capsys):
    pid_file.write_text("5151")
    assert server.stop() == 1
    assert windows.ended == []
    assert capsys.readouterr().out == "sshler is not running\n"


def test_stop_on_windows_ends_a_server_found_without_a_pid_file(
    windows, pid_file, monkeypatch, capsys
):
    monkeypatch.setattr(server, "_get_sshler_pids", lambda: sorted(windows.live))
    assert server.stop() == 0
    assert windows.ended == [(4242, "terminate")]
    assert capsys.readouterr().out == "Stopping sshler (PIDs: 4242)...\nsshler stopped\n"
