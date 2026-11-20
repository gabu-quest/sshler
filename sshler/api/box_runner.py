"""The one way a route runs a program on a box.

A route hands the runner of its box the words of a command, the folder to run it in
and a time limit, and gets back the exit status and the output::

    async with box_runner(deps, box, application_config) as runner:
        folder = await runner.folder(directory)
        ran = await runner.run(["git", "log", "-n", "5"], folder=folder, timeout=30)

What both kinds promise:

* Every word reaches the program as that one word: a word with a space, a quote, a
  ``$``, a backtick, a ``;`` or a newline is not read by any shell, and nothing else
  runs. A word that starts with ``-`` is still read by the program as an option, so a
  route puts ``--`` (or ``./``) in front of a word that must not be one.
* ``folder`` None runs the program where the box starts it (this process's folder on
  the local box, the login folder on a remote one). A folder that cannot be entered
  runs nothing and gives a non-zero status and no output.
* The output is what the program wrote on its standard output, read as UTF-8; its
  error output is dropped. With ``output_limit`` only the first that many bytes come
  back (on a remote box no more is sent), and the status is then not read: it is None.
* Past ``timeout`` seconds the call raises ``TimeoutError``. On the local box the
  program is killed and reaped first; on a remote box the connection is not handed to
  another request afterwards, as a command may still run on it.

Where the kinds differ, each as the routes had it before:

* A program that is not there: the local runner raises ``ProgramNotFoundError``
  before it starts anything; on a remote box the shell answers status 127.
* Output that is not UTF-8: the local runner puts U+FFFD for each bad byte; on a
  remote box the read fails (``UnicodeDecodeError``), as asyncssh decodes strictly.
* ``folder(path)``: on the local box ``~`` is this user's home and the path is made
  absolute with links resolved; on a remote box the path is checked (a bad one raises
  ``ValidationError``), ``~`` is the box's home, asked once per connection.
* ``file(path, folder)``: a path given absolute or relative to ``folder``, made
  absolute. The local box resolves links (and ``~user``); a remote box can only
  normalise the text (``..`` is applied to the words, a link is not followed), with
  ``~`` and ``~/x`` under the box's home, and a path with a null byte raises
  ``ValidationError``. Nothing is run on the box for it.
* ``reach(timeout)``: the local box is this machine and answers at once (0 s, nothing
  is run: Windows has no ``true``); a remote box runs ``true`` once.

``tmux(session, args)`` runs tmux on the session's own server (``tmux -L ts-<session>``,
built by ``sshler.tmux``) with ``args``, each one word, and gives what tmux wrote on its
error output too. Its limit is ``tmux.TMUX_COMMAND_TIMEOUT`` unless one is given; past
it the call raises ``TmuxTimeoutError`` (a ``TimeoutError``), and only then, so a
connect that timed out is not taken for tmux that did. Where the kinds differ: the
local box runs ``tmux.run_local_tmux`` (no tmux there raises ``FileNotFoundError``;
on Windows tmux runs in WSL); a remote box sends ``tmux.remote_tmux_command`` (no tmux
there is status 127 with the shell's error text).

A remote runner takes its box's pooled connection at the first call that needs it and
gives it back when the block ends.
"""

from __future__ import annotations

import asyncio
import os
import posixpath
import shutil
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path, PurePath, PurePosixPath
from typing import Any, Protocol

from .. import tmux as tmux_module
from ..config import AppConfig, Box
from ..proc import run_bounded
from ..ssh_pool import get_pool
from ..validation import PathValidator
from .box_files import is_local
from .dependencies import APIDependencies, ProbeTimeoutError
from .helpers import _normalize_local_path
from .remote import command, in_directory, pipe, quiet, remote_path, resolve_tilde

# The status of a run whose folder could not be entered (the status `cd` gives).
FOLDER_NOT_ENTERED = 1


@dataclass(frozen=True)
class Ran:
    """What a program gave: its exit status (None when ``output_limit`` cut the output),
    its standard output, and its error output (given by ``tmux`` only; empty from
    ``run``)."""

    status: int | None
    output: str
    errors: str = ""

    @property
    def ok(self) -> bool:
        return self.status == 0


class ProgramNotFoundError(Exception):
    """The local box has no program by the first word's name."""


class TmuxTimeoutError(TimeoutError):
    """tmux itself, not the connect before it, ran past its limit."""


def _text(data: str | bytes | None) -> str:
    """Output as text: bytes read as UTF-8 with a bad byte marked."""
    if data is None:
        return ""
    return data if isinstance(data, str) else data.decode("utf-8", errors="replace")


class BoxRunner(Protocol):
    async def folder(self, path: str) -> str:
        """``path`` as a folder of this box, for ``run``."""
        ...

    async def file(self, path: str, folder: str) -> PurePath:
        """``path`` (absolute, or relative to ``folder``) as an absolute path of this
        box, in the box's own form."""
        ...

    async def reach(self, *, timeout: float) -> float | None:
        """Seconds the box took to answer, or None when it did not run the probe."""
        ...

    async def run(
        self,
        words: Sequence[str],
        *,
        folder: str | None,
        timeout: float,
        output_limit: int | None = None,
    ) -> Ran: ...

    async def tmux(self, session: str, args: Sequence[str], *, timeout: float | None = None) -> Ran:
        """tmux on ``session``'s own server with ``args``: status, output, error output."""
        ...


class LocalRunner:
    """Runs a program on this machine with ``proc.run_bounded``; no shell."""

    async def folder(self, path: str) -> str:
        return _normalize_local_path(path)

    async def file(self, path: str, folder: str) -> PurePath:
        requested = Path(path).expanduser()
        candidate = requested if requested.is_absolute() else Path(folder) / requested
        return candidate.resolve(strict=False)

    async def reach(self, *, timeout: float) -> float | None:
        return 0.0

    async def run(
        self,
        words: Sequence[str],
        *,
        folder: str | None,
        timeout: float,
        output_limit: int | None = None,
    ) -> Ran:
        program = shutil.which(words[0])
        if program is None:
            raise ProgramNotFoundError(words[0])
        if folder is not None and not os.path.isdir(folder):
            return Ran(FOLDER_NOT_ENTERED, "")
        try:
            status, stdout, _ = await run_bounded([program, *words[1:]], timeout, cwd=folder)
        except TimeoutError:
            raise  # an OSError too, but not one about the folder
        except OSError:
            # The folder went away or cannot be entered.
            return Ran(FOLDER_NOT_ENTERED, "")
        if output_limit is not None:
            return Ran(None, stdout[:output_limit].decode("utf-8", errors="replace"))
        return Ran(status, stdout.decode("utf-8", errors="replace"))

    async def tmux(self, session: str, args: Sequence[str], *, timeout: float | None = None) -> Ran:
        # A limit of None is run_local_tmux's own TMUX_COMMAND_TIMEOUT.
        try:
            status, stdout, stderr = await tmux_module.run_local_tmux(session, list(args), timeout)
        except TimeoutError as exc:
            raise TmuxTimeoutError(f"tmux on {session} timed out") from exc
        return Ran(status, _text(stdout), _text(stderr))


def remote_command_line(
    words: Sequence[str], folder: str | None = None, output_limit: int | None = None
) -> str:
    """The line the remote runner sends for ``words``: each word quoted, the error
    output dropped, cut to ``output_limit`` bytes, run in ``folder``."""
    line = quiet(command(*words))
    if output_limit is not None:
        line = pipe(line, command("head", "-c", str(output_limit)))
    return line if folder is None else in_directory(folder, line)


class RemoteRunner:
    """Runs a program on a remote box: one command line per call, over the connection
    ``connection()`` gives."""

    def __init__(self, connection: Callable[[], Awaitable[Any]]) -> None:
        self._connection = connection
        self.timed_out = False

    async def folder(self, path: str) -> str:
        # Refused before a connection is opened; `~` is the home before `..` is
        # applied (`resolve_tilde`), so `~/..` is the folder above the home.
        PathValidator.validate_remote_path(path)
        return await remote_path(await self._connection(), path)

    async def file(self, path: str, folder: str) -> PurePath:
        resolved = await resolve_tilde(await self._connection(), path)
        joined = posixpath.join(folder, resolved)  # an absolute path stays as it is
        return PurePosixPath(PathValidator.validate_remote_path(joined))

    async def reach(self, *, timeout: float) -> float | None:
        # Timed from the first call of the block, so a new connection is counted.
        started = time.monotonic()
        ran = await self.run(["true"], folder=None, timeout=timeout)
        return time.monotonic() - started if ran.ok else None

    async def run(
        self,
        words: Sequence[str],
        *,
        folder: str | None,
        timeout: float,
        output_limit: int | None = None,
    ) -> Ran:
        connection = await self._connection()
        line = remote_command_line(words, folder, output_limit)
        try:
            # Bytes, decoded here: a byte that is not UTF-8 is marked, as on the local
            # box, where a strict decode on the channel would close the connection.
            result = await asyncio.wait_for(
                connection.run(line, check=False, encoding=None), timeout=timeout
            )
        except (TimeoutError, asyncio.CancelledError):
            self.timed_out = True
            raise
        stdout = result.stdout or ""
        output = stdout if isinstance(stdout, str) else stdout.decode("utf-8", errors="replace")
        if output_limit is not None:
            return Ran(None, output)
        status = result.exit_status
        return Ran(-1 if status is None else status, output)

    async def tmux(self, session: str, args: Sequence[str], *, timeout: float | None = None) -> Ran:
        connection = await self._connection()  # outside the limit: a connect is not tmux
        limit = tmux_module.TMUX_COMMAND_TIMEOUT if timeout is None else timeout
        line = tmux_module.remote_tmux_command(session, list(args))
        try:
            result = await asyncio.wait_for(
                connection.run(line, check=False, encoding=None), timeout=limit
            )
        except asyncio.CancelledError:
            self.timed_out = True
            raise
        except TimeoutError as exc:
            self.timed_out = True
            raise TmuxTimeoutError(f"tmux on {session} timed out") from exc
        status = result.exit_status
        return Ran(-1 if status is None else status, _text(result.stdout), _text(result.stderr))


def over(connection: Any) -> RemoteRunner:
    """A remote runner on a connection the caller holds."""

    async def given() -> Any:
        return connection

    return RemoteRunner(given)


@asynccontextmanager
async def box_runner(
    deps: APIDependencies,
    box: Box,
    application_config: AppConfig,
    *,
    deadline: float | None = None,
) -> AsyncIterator[BoxRunner]:
    """The runner of ``box`` for the block.

    With ``deadline`` the whole block (taking the connection and every command) is
    bounded, as a polled request is: past it ``ProbeTimeoutError`` is raised, naming the
    step ("connect to NAME timed out after N s" while the connection was still being
    taken, "command on NAME timed out (N s limit for connect plus command)" after).
    Giving the connection back to the pool is not counted, so a block whose work
    finished is never reported as a timeout.

    On a remote box the pooled connection is taken by the first call that needs it and
    given back when the block ends; it is closed instead when the block ended by a
    timeout or a cancel, or a command in it ran past its limit.
    """
    if is_local(box):
        runner = LocalRunner()
        if deadline is None:
            yield runner
            return
        try:
            async with asyncio.timeout(deadline) as bound:
                yield runner
        except TimeoutError:
            if bound.expired():
                raise ProbeTimeoutError(_command_timed_out(box, deadline)) from None
            raise
        return

    pool = get_pool()
    taken: list[Any] = []

    async def connection() -> Any:
        if not taken:
            taken.append(
                await pool.acquire(box, lambda: deps.connect_for_box(box, application_config))
            )
        return taken[0]

    remote = RemoteRunner(connection)
    spoiled = False
    try:
        if deadline is None:
            yield remote
        else:
            try:
                async with asyncio.timeout(deadline) as bound:
                    yield remote
            except TimeoutError:
                if not bound.expired():
                    raise
                spoiled = True
                if not taken:
                    message = _connect_timed_out(box, deadline)
                else:
                    message = _command_timed_out(box, deadline)
                raise ProbeTimeoutError(message) from None
    except BaseException as exc:
        if taken:
            ordinary = isinstance(exc, Exception) and not isinstance(exc, TimeoutError)
            if ordinary and not (spoiled or remote.timed_out):
                await pool.release(box.name, taken[0])
            else:
                pool.discard(box.name, taken[0])
        raise
    if taken:
        if remote.timed_out:
            pool.discard(box.name, taken[0])
        else:
            await pool.release(box.name, taken[0])


def _connect_timed_out(box: Box, deadline: float) -> str:
    return f"connect to {box.name} timed out after {deadline:g} s"


def _command_timed_out(box: Box, deadline: float) -> str:
    return f"command on {box.name} timed out ({deadline:g} s limit for connect plus command)"
