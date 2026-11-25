"""Run a local child process with a time limit; kill and reap it on timeout."""

from __future__ import annotations

import asyncio
import contextlib

# After a timeout the child is SIGKILLed; this bounds the wait for asyncio to
# see it exit. ``wait()`` also waits for the stdout/stderr pipes, which a
# grandchild (e.g. a freshly forked tmux server) may hold open indefinitely.
REAP_GRACE = 1.0


async def _kill_and_reap(process: asyncio.subprocess.Process) -> None:
    """SIGKILL *process* and wait (bounded) until it has been reaped."""
    try:
        with contextlib.suppress(ProcessLookupError):
            process.kill()
        with contextlib.suppress(asyncio.TimeoutError, Exception):
            await asyncio.wait_for(process.wait(), timeout=REAP_GRACE)
    finally:
        # A grandchild may still hold the pipe write ends after the child is
        # gone; closing the transport releases our read ends either way.
        transport = getattr(process, "_transport", None)
        if transport is not None:
            with contextlib.suppress(Exception):
                transport.close()


async def run_bounded(
    argv: list[str],
    timeout: float,
    *,
    capture_output: bool = True,
    cwd: str | None = None,
    stdin_data: bytes | None = None,
) -> tuple[int, bytes, bytes]:
    """Run *argv* bounded by *timeout*; return ``(returncode, stdout, stderr)``.

    Every local subprocess with a time limit (tmux, ``ts-add``, grep, git, find,
    zoxide) goes through here. On timeout or cancellation the child is killed
    and reaped before the exception propagates, so a wedged command never
    outlives its bound. Raises ``asyncio.TimeoutError`` on timeout.

    With *capture_output* False, stdout/stderr go to DEVNULL and only the exit
    is awaited: a tmux server forked by the command inherits those fds, and
    with pipes ``communicate()`` would never see EOF.

    *stdin_data* is written to the child's stdin, which is then closed; without
    it the child reads from DEVNULL. It needs *capture_output*.
    """
    stream = asyncio.subprocess.PIPE if capture_output else asyncio.subprocess.DEVNULL
    process = await asyncio.create_subprocess_exec(
        *argv,
        stdin=asyncio.subprocess.DEVNULL if stdin_data is None else asyncio.subprocess.PIPE,
        stdout=stream,
        stderr=stream,
        cwd=cwd,
    )
    try:
        if capture_output:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(stdin_data), timeout=timeout
            )
        else:
            await asyncio.wait_for(process.wait(), timeout=timeout)
            stdout, stderr = b"", b""
    except BaseException:
        await _kill_and_reap(process)
        raise
    return process.returncode if process.returncode is not None else -1, stdout, stderr
