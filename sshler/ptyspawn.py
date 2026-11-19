"""Start a child as session leader on a fresh PTY, without forking Python."""

from __future__ import annotations

import os
import struct
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path

# PTY-related modules (Unix-only); this module must still import on Windows.
if sys.platform != "win32":
    import fcntl
    import pty
    import termios

_CHILD = str(Path(__file__).with_name("_pty_child.py"))


def spawn_on_pty(
    argv: Sequence[str],
    *,
    env: Mapping[str, str],
    rows: int,
    cols: int,
) -> tuple[int, int]:
    """Run *argv* on a new PTY of the given size; return ``(master_fd, pid)``.

    The child is the leader of its own session with the PTY as its controlling
    terminal on fds 0 to 2, and its environment is exactly *env*. If *argv[0]*
    cannot be run the child writes one line to the PTY and exits 127. The caller
    reaps the pid.
    """
    # The interpreter that runs _pty_child.py may set LC_CTYPE for itself; tell it
    # what the command is to get.
    lc_ctype = "=" + env["LC_CTYPE"] if "LC_CTYPE" in env else "-"
    master_fd, slave_fd = pty.openpty()
    try:
        fcntl.ioctl(slave_fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))
        pid = os.posix_spawn(
            sys.executable,
            [sys.executable, "-I", "-S", _CHILD, lc_ctype, *argv],
            env,
            file_actions=[
                (os.POSIX_SPAWN_DUP2, slave_fd, 0),
                (os.POSIX_SPAWN_DUP2, slave_fd, 1),
                (os.POSIX_SPAWN_DUP2, slave_fd, 2),
            ],
        )
    except BaseException:
        os.close(master_fd)
        os.close(slave_fd)
        raise
    os.close(slave_fd)
    return master_fd, pid
