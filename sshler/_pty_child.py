"""Run by path (never imported): become session leader on the PTY, then run the command.

``ptyspawn.spawn_on_pty`` starts this with the PTY slave on fds 0 to 2. The first
three arguments are optional values, each ``-`` for none or ``=`` and the value: the
``LC_CTYPE`` the command gets, the folder it starts in, and the name it reads as its
own (``argv[0]``). The rest is the command. An interpreter started in the C locale sets
``LC_CTYPE`` in its own environment; the command must get the environment the caller
gave, so it is put back.
``posix_spawn`` hands down every inheritable descriptor of the server (what its own
launcher left open, for one), so all above 2 are closed: the command gets the PTY only.
"""

import os
import sys


def _value(word: str) -> str | None:
    return word[1:] if word.startswith("=") else None


def main() -> None:
    argv = sys.argv[4:]
    doing = f"run {argv[0] if argv else ''}"
    try:
        import fcntl
        import termios

        lc_ctype, cwd, arg0 = (_value(word) for word in sys.argv[1:4])
        if lc_ctype is not None:
            os.environ["LC_CTYPE"] = lc_ctype
        else:
            os.environ.pop("LC_CTYPE", None)
        os.setsid()
        fcntl.ioctl(0, termios.TIOCSCTTY, 0)
        if cwd is not None:
            doing, running = f"enter {cwd}", doing
            os.chdir(cwd)
            doing = running
        for fd in map(int, os.listdir("/dev/fd")):
            if fd > 2:
                os.closerange(fd, fd + 1)  # ignores the listing's own, already closed
        os.execvp(argv[0], [arg0 if arg0 is not None else argv[0], *argv[1:]])
    except (OSError, IndexError) as exc:
        reason = exc.strerror if isinstance(exc, OSError) and exc.strerror else str(exc)
        try:
            os.write(2, f"sshler: cannot {doing}: {reason}\n".encode(errors="replace"))
        finally:
            os._exit(127)


if __name__ == "__main__":
    main()
