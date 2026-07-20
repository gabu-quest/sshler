"""ConPTY-backed local terminal process for native Windows shells.

On Windows there is no Unix ``pty``/``fcntl``/``termios``, so the Unix
``LocalPTYProcess`` path in :mod:`sshler.webapp` cannot run. This module wraps
:class:`winpty.PtyProcess` (pywinpty / ConPTY) in an object whose surface
mirrors ``LocalPTYProcess`` closely enough that the websocket reader/writer/
resize loops can treat both the same way:

* ``process.stdin.write(data: bytes)`` — keystrokes from the browser
* ``process.stdout.read(size: int) -> str`` — output for the browser
* ``process.resize(cols, rows)`` — ConPTY resize
* ``process.wait()`` / ``terminate()`` / ``close()`` / ``returncode``

pywinpty speaks text (UTF-8), so :class:`_WinPTYStdin` decodes incoming bytes
and :class:`_WinPTYStdout` returns ``str`` — the reader loop already encodes
``str`` to UTF-8 before sending it over the websocket. ``winpty.read`` raises
:class:`EOFError` when the child exits; we translate that to an empty string so
the reader loop's "empty read means done" contract holds.

``winpty`` is imported lazily (only when a native shell is actually spawned) so
that a Windows install missing the optional ``pywinpty`` wheel still starts the
server and runs SSH boxes — the failure surfaces as a clear per-terminal error
instead of crashing module import.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


class WinPTYUnavailableError(RuntimeError):
    """Raised when a native Windows shell is requested but pywinpty is missing."""


def _load_winpty() -> Any:
    """Import and return the ``winpty`` module, or raise a clear error."""
    try:
        import winpty  # type: ignore[import-untyped, import-not-found]
    except Exception as exc:  # pragma: no cover - only hit when dep is missing
        raise WinPTYUnavailableError(
            "pywinpty is not installed in this environment. Install it with "
            "'uv pip install pywinpty' (or reinstall sshler) to use native "
            "Windows shells."
        ) from exc
    return winpty


class _WinPTYStdin:
    """Sync write shim: bytes in (from xterm) -> str out (to ConPTY)."""

    def __init__(self, pty: Any) -> None:
        self._pty = pty

    def write(self, data: bytes) -> int:
        text = data.decode("utf-8", errors="replace") if isinstance(data, bytes) else data
        try:
            self._pty.write(text)
        except Exception as exc:
            logger.debug("[WinPTY] write after close: %s", exc)
            return 0
        return len(text)


class _WinPTYStdout:
    """Sync read shim: returns ``str`` from ConPTY, ``""`` once the child exits.

    The websocket reader treats an empty read as end-of-stream, so EOF/closed
    errors collapse to ``""`` instead of propagating.
    """

    def __init__(self, pty: Any) -> None:
        self._pty = pty

    def read(self, size: int = 1024) -> str:
        try:
            data = self._pty.read(size)
        except EOFError:
            return ""
        except Exception as exc:
            logger.debug("[WinPTY] read ended: %s", exc)
            return ""
        return data if isinstance(data, str) else str(data)


class WinPTYProcess:
    """Local Windows shell running in a ConPTY, with a LocalPTYProcess-like API."""

    def __init__(self, pty: Any) -> None:
        self._pty = pty
        self.stdin = _WinPTYStdin(pty)
        self.stdout = _WinPTYStdout(pty)
        self._returncode: int | None = None

    @classmethod
    def spawn(
        cls,
        argv: list[str],
        cwd: str | None = None,
        cols: int = 80,
        rows: int = 24,
    ) -> "WinPTYProcess":
        """Spawn *argv* in a new ConPTY sized *cols* x *rows*.

        Raises :class:`WinPTYUnavailableError` if pywinpty is not installed.
        """
        winpty = _load_winpty()
        pty = winpty.PtyProcess.spawn(argv, cwd=cwd or None, dimensions=(rows, cols))
        logger.info("[WinPTY] Spawned %s (cwd=%s) at %sx%s", argv, cwd, cols, rows)
        return cls(pty)

    @property
    def returncode(self) -> int | None:
        return self._returncode

    def resize(self, cols: int, rows: int) -> None:
        """Resize the ConPTY (rows, cols order for winpty)."""
        try:
            self._pty.setwinsize(rows, cols)
            logger.debug("[WinPTY] Resized to %sx%s", cols, rows)
        except Exception as exc:
            logger.warning("[WinPTY] Failed to resize: %s", exc)

    async def wait(self) -> int:
        """Block (in a thread) until the child exits, returning its exit code."""
        import asyncio

        def _worker() -> int:
            try:
                self._pty.wait()
            except Exception as exc:  # pragma: no cover - defensive
                logger.debug("[WinPTY] wait error: %s", exc)
            status = getattr(self._pty, "exitstatus", None)
            return int(status) if status is not None else 0

        self._returncode = await asyncio.to_thread(_worker)
        return self._returncode

    def terminate(self) -> None:
        try:
            self._pty.terminate(force=True)
        except Exception as exc:
            logger.debug("[WinPTY] terminate error: %s", exc)

    def close(self) -> None:
        try:
            self._pty.close()
        except Exception as exc:
            logger.debug("[WinPTY] close error: %s", exc)
