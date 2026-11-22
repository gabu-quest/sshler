"""spawn_on_pty: one way to start a child on a PTY, with no fork in Python."""

from __future__ import annotations

import json
import os
import select
import struct
import sys
import threading
import time
import warnings
from pathlib import Path

import pytest

from sshler import ptyspawn
from sshler.ptyspawn import spawn_on_pty

# POSIX-only modules: the file must still import on Windows, where every test is skipped.
if sys.platform != "win32":
    import fcntl
    import termios

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="PTYs are POSIX-only")

_SCRIPT = (
    "import json, os, sys\n"
    "def fds():\n"
    "    return sorted(int(x) for x in os.listdir('/proc/self/fd'))\n"
    "def tty_ok():\n"
    "    try:\n"
    "        os.close(os.open('/dev/tty', os.O_RDWR))\n"
    "        return True\n"
    "    except OSError:\n"
    "        return False\n"
    "print('INFO' + json.dumps({\n"
    "    'argv': sys.argv[1:],\n"
    "    'leader': os.getsid(0) == os.getpid(),\n"
    "    'tty': tty_ok(),\n"
    "    'size': list(os.get_terminal_size(0)),\n"
    "    'fds': fds() if os.path.isdir('/proc/self/fd') else None,\n"
    "}), flush=True)\n"
)


def _read_all(fd: int, limit: float = 10.0) -> str:
    """Read until the child closes its side (EIO/EOF) or *limit* seconds pass."""
    buf = b""
    end = time.monotonic() + limit
    while time.monotonic() < end:
        if not select.select([fd], [], [], 0.2)[0]:
            continue
        try:
            chunk = os.read(fd, 4096)
        except OSError:
            break
        if not chunk:
            break
        buf += chunk
    return buf.decode(errors="replace")


def _run(
    argv: list[str], rows: int = 24, cols: int = 80, env: dict[str, str] | None = None
) -> tuple[str, int]:
    master, pid = spawn_on_pty(
        argv, env=dict(os.environ) if env is None else env, rows=rows, cols=cols
    )
    try:
        out = _read_all(master)
    finally:
        os.close(master)
    _, status = os.waitpid(pid, 0)
    return out, os.waitstatus_to_exitcode(status)


def _info(out: str) -> dict:
    line = next(ln for ln in out.splitlines() if ln.startswith("INFO"))
    return json.loads(line[4:])


@pytest.fixture
def script(tmp_path: Path) -> Path:
    path = tmp_path / "info.py"
    path.write_text(_SCRIPT)
    return path


def _open_fds() -> set[int]:
    return {int(x) for x in os.listdir("/proc/self/fd")}


def test_missing_command_exits_127_with_one_line() -> None:
    out, code = _run(["definitely-not-a-command-sshler"])

    assert code == 127
    assert out.strip() == (
        "sshler: cannot run definitely-not-a-command-sshler: No such file or directory"
    )


@pytest.mark.parametrize(
    "locale_vars",
    [
        {},
        {"LANG": "C"},
        {"LC_CTYPE": "POSIX"},
        {"LC_CTYPE": ""},
        {"LANG": "C.UTF-8"},
        {"LC_ALL": "C", "LC_CTYPE": "C"},
    ],
)
def test_the_command_gets_exactly_the_environment_it_was_given(
    locale_vars: dict[str, str],
) -> None:
    """The interpreter that starts the command must not add its own locale to it."""
    env = {"PATH": os.environ["PATH"], "MARK": "a b", **locale_vars}

    out, code = _run(["env"], env=env)

    assert code == 0
    assert sorted(out.splitlines()) == sorted(f"{k}={v}" for k, v in env.items())


def test_arguments_with_space_quote_and_empty_arrive_exactly(script: Path) -> None:
    args = ["a b", 'it\'s "q"', "", "$HOME"]

    out, code = _run([sys.executable, str(script), *args])

    assert code == 0
    assert _info(out)["argv"] == args


def test_child_is_session_leader_and_can_open_dev_tty(script: Path) -> None:
    out, _ = _run([sys.executable, str(script)])

    info = _info(out)
    assert info["leader"] is True
    assert info["tty"] is True


def test_initial_size_is_what_the_child_reads(script: Path) -> None:
    out, _ = _run([sys.executable, str(script)], rows=50, cols=132)

    assert _info(out)["size"] == [132, 50]


@pytest.mark.skipif(not os.path.isdir("/proc/self/fd"), reason="needs /proc")
def test_child_has_only_fds_0_to_2(script: Path, tmp_path: Path) -> None:
    held = [open(tmp_path / f"held{i}", "w") for i in range(3)]
    try:
        out, _ = _run([sys.executable, str(script)])
    finally:
        for fh in held:
            fh.close()

    # the child's own listing of /proc/self/fd holds one fd for the listing itself
    fds = [fd for fd in _info(out)["fds"] if fd <= 2]
    assert fds == [0, 1, 2]
    assert max(_info(out)["fds"]) <= 3


def test_call_from_worker_thread_with_other_threads_and_warnings_as_errors(
    script: Path,
) -> None:
    stop = threading.Event()
    others = [threading.Thread(target=stop.wait) for _ in range(4)]
    for t in others:
        t.start()
    results: list[tuple[str, int]] = []
    errors: list[BaseException] = []

    def work() -> None:
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("error")
                results.append(_run([sys.executable, str(script)]))
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    worker = threading.Thread(target=work)
    worker.start()
    worker.join(30)
    stop.set()
    for t in others:
        t.join()

    assert errors == []
    assert results[0][1] == 0
    assert _info(results[0][0])["leader"] is True


@pytest.mark.skipif(not os.path.isdir("/proc/self/fd"), reason="needs /proc")
def test_failed_spawn_leaves_no_fd_behind(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*args: object, **kwargs: object) -> int:
        raise OSError("no spawn")

    monkeypatch.setattr(ptyspawn.os, "posix_spawn", boom)
    before = _open_fds()

    with pytest.raises(OSError, match="no spawn"):
        spawn_on_pty(["true"], env={}, rows=24, cols=80)

    assert _open_fds() == before


def test_resize_of_the_master_reaches_the_child() -> None:
    code = (
        "import os, signal, sys\n"
        "signal.signal(signal.SIGWINCH, lambda *a: (print('WINCH', os.get_terminal_size(0), "
        "flush=True), os._exit(0)))\n"
        "print('ready', flush=True)\n"
        "signal.pause()\n"
    )
    master, pid = spawn_on_pty([sys.executable, "-c", code], env=dict(os.environ), rows=24, cols=80)
    try:
        buf = ""
        end = time.monotonic() + 10
        while "ready" not in buf and time.monotonic() < end:
            if select.select([master], [], [], 0.2)[0]:
                buf += os.read(master, 4096).decode()
        fcntl.ioctl(master, termios.TIOCSWINSZ, struct.pack("HHHH", 50, 132, 0, 0))
        out = buf + _read_all(master)
    finally:
        os.close(master)
        os.waitpid(pid, 0)

    assert "WINCH os.terminal_size(columns=132, lines=50)" in out


def test_no_fork_left_under_the_package() -> None:
    root = Path(ptyspawn.__file__).parent
    hits = [
        str(p.name)
        for p in root.rglob("*.py")
        if any(w in p.read_text() for w in ("os.fork(", "pty.fork(", "os.forkpty("))
    ]

    assert hits == []
