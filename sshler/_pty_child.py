"""Run by path (never imported): become session leader on the PTY, then run the command.

``ptyspawn.spawn_on_pty`` starts this with the PTY slave on fds 0 to 2. The first
argument says what ``LC_CTYPE`` the command gets: ``-`` for none, or ``=`` and the
value. An interpreter started in the C locale sets ``LC_CTYPE`` in its own
environment; the command must get the environment the caller gave, so it is put back.
"""

import os
import sys


def main() -> None:
    argv = sys.argv[2:]
    try:
        import fcntl
        import termios

        lc_ctype = sys.argv[1]
        if lc_ctype.startswith("="):
            os.environ["LC_CTYPE"] = lc_ctype[1:]
        else:
            os.environ.pop("LC_CTYPE", None)
        os.setsid()
        fcntl.ioctl(0, termios.TIOCSCTTY, 0)
        os.execvp(argv[0], argv)
    except (OSError, IndexError) as exc:
        name = argv[0] if argv else ""
        reason = exc.strerror if isinstance(exc, OSError) and exc.strerror else str(exc)
        try:
            os.write(2, f"sshler: cannot run {name}: {reason}\n".encode(errors="replace"))
        finally:
            os._exit(127)


if __name__ == "__main__":
    main()
