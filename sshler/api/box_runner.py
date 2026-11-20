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
* The output is what the program wrote on its standard output, read as UTF-8 with
  U+FFFD for each byte that is not; its error output is dropped, unless
  ``keep_errors`` asks for it in ``Ran.errors`` (read the same way). With
  ``output_limit`` only the first that many bytes come back (on a remote box no more
  is sent), and the status is then not read: it is None.
* Past ``timeout`` seconds the call raises ``TimeoutError``. On the local box the
  program is killed and reaped first; on a remote box the connection is not handed to
  another request afterwards, as a command may still run on it.
* A program that is not there raises ``ProgramNotFoundError`` naming the program (the
  first word, or ``program`` where a script runs it) and the box: the local runner
  before it starts anything, the remote one when the shell answers status 127 (the
  shell's status for a command it cannot find; nothing the runner sends exits 127
  otherwise). A cut output has no status, so there it is not seen.

Where the kinds differ, each as the routes had it before:

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
connect that timed out is not taken for tmux that did. No tmux on the box raises
``ProgramNotFoundError`` on both kinds. Where the kinds differ: the local box runs
``tmux.run_local_tmux`` (on Windows tmux runs in WSL); a remote box sends
``tmux.remote_tmux_command``.

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
from ..validation import PathValidator, ValidationError
from .box_files import is_local
from .dependencies import APIDependencies, ProbeTimeoutError
from .failure import failure_of
from .helpers import _normalize_local_path
from .remote import command, in_directory, pipe, quiet, remote_path, resolve_tilde
from .run_failures import ProgramNotFoundError

# The status of a run whose folder could not be entered (the status `cd` gives).
FOLDER_NOT_ENTERED = 1

# The status a POSIX shell gives a command it cannot find.
COMMAND_NOT_FOUND = 127


@dataclass(frozen=True)
class Ran:
    """What a program gave: its exit status (None when ``output_limit`` cut the output),
    its standard output, and its error output (given by ``tmux``, and by ``run`` with
    ``keep_errors``; empty otherwise)."""

    status: int | None
    output: str
    errors: str = ""

    @property
    def ok(self) -> bool:
        return self.status == 0


class TmuxTimeoutError(TimeoutError):
    """tmux itself, not the connect before it, ran past its limit."""


class CommandTimeoutError(TimeoutError):
    """A bounded request (``box_runner(..., deadline=...)``) ran past its limit after
    its box was reached: a command was running. A ``TimeoutError``, so it answers
    ``timeout``; past the limit while the connection was still being taken is
    ``ProbeTimeoutError`` (``unreachable``) instead."""


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
        keep_errors: bool = False,
        program: str | None = None,
    ) -> Ran:
        """``words`` on the box; ``program`` is the program a missing-program failure
        names when the first word is a shell running a script."""
        ...

    async def tmux(self, session: str, args: Sequence[str], *, timeout: float | None = None) -> Ran:
        """tmux on ``session``'s own server with ``args``: status, output, error output."""
        ...


class LocalRunner:
    """Runs a program on this machine with ``proc.run_bounded``; no shell. ``box`` is
    the name a missing-program failure gives the box."""

    def __init__(self, box: str = "local") -> None:
        self._box = box

    async def folder(self, path: str) -> str:
        # The one check of the path a remote box makes that a local path needs too: no
        # file system takes a name with a NUL in it.
        if "\0" in path:
            raise ValidationError("Path cannot contain null bytes")
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
        keep_errors: bool = False,
        program: str | None = None,
    ) -> Ran:
        found = shutil.which(words[0])
        if found is None:
            raise ProgramNotFoundError(words[0], self._box)
        if program is not None and shutil.which(program) is None:
            raise ProgramNotFoundError(program, self._box)
        if folder is not None and not os.path.isdir(folder):
            return Ran(FOLDER_NOT_ENTERED, "")
        try:
            status, stdout, stderr = await run_bounded([found, *words[1:]], timeout, cwd=folder)
        except TimeoutError:
            raise  # an OSError too, but not one about the folder
        except OSError:
            # Only a folder that is no longer one is the folder's failure; any other
            # failure to start the program is raised as it is.
            if folder is not None and not os.path.isdir(folder):
                return Ran(FOLDER_NOT_ENTERED, "")
            raise
        errors = _text(stderr) if keep_errors else ""
        if output_limit is not None:
            return Ran(None, _text(stdout[:output_limit]), errors)
        return Ran(status, _text(stdout), errors)

    async def tmux(self, session: str, args: Sequence[str], *, timeout: float | None = None) -> Ran:
        # A limit of None is run_local_tmux's own TMUX_COMMAND_TIMEOUT.
        try:
            status, stdout, stderr = await tmux_module.run_local_tmux(session, list(args), timeout)
        except TimeoutError as exc:
            raise TmuxTimeoutError(f"tmux on {session} timed out") from exc
        except FileNotFoundError as exc:
            # run_local_tmux runs no program but tmux (or WSL) and gives it no folder.
            raise ProgramNotFoundError("tmux", self._box) from exc
        return Ran(status, _text(stdout), _text(stderr))


def remote_command_line(
    words: Sequence[str],
    folder: str | None = None,
    output_limit: int | None = None,
    keep_errors: bool = False,
) -> str:
    """The line the remote runner sends for ``words``: each word quoted, the error
    output dropped (kept with ``keep_errors``), cut to ``output_limit`` bytes, run in
    ``folder``."""
    line = command(*words) if keep_errors else quiet(command(*words))
    if output_limit is not None:
        line = pipe(line, command("head", "-c", str(output_limit)))
    return line if folder is None else in_directory(folder, line)


class RemoteRunner:
    """Runs a program on a remote box: one command line per call, over the connection
    ``connection()`` gives."""

    def __init__(self, connection: Callable[[], Awaitable[Any]], box: str) -> None:
        self._connection = connection
        self._box = box
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
        keep_errors: bool = False,
        program: str | None = None,
    ) -> Ran:
        connection = await self._connection()
        line = remote_command_line(words, folder, output_limit, keep_errors)
        try:
            # Bytes, decoded here: a byte that is not UTF-8 is marked, as on the local
            # box, where a strict decode on the channel would close the connection.
            result = await asyncio.wait_for(
                connection.run(line, check=False, encoding=None), timeout=timeout
            )
        except (TimeoutError, asyncio.CancelledError):
            self.timed_out = True
            raise
        output = _text(result.stdout)
        errors = _text(result.stderr) if keep_errors else ""
        if output_limit is not None:
            return Ran(None, output, errors)
        status = result.exit_status
        if status == COMMAND_NOT_FOUND:
            raise ProgramNotFoundError(program or words[0], self._box)
        return Ran(-1 if status is None else status, output, errors)

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
        if status == COMMAND_NOT_FOUND:
            raise ProgramNotFoundError("tmux", self._box)
        return Ran(-1 if status is None else status, _text(result.stdout), _text(result.stderr))


def over(connection: Any, box: str = "remote") -> RemoteRunner:
    """A remote runner on a connection the caller holds, for the box named ``box``."""

    async def given() -> Any:
        return connection

    return RemoteRunner(given, box)


@asynccontextmanager
async def box_runner(
    deps: APIDependencies,
    box: Box,
    application_config: AppConfig,
    *,
    deadline: float | None = None,
) -> AsyncIterator[BoxRunner]:
    """The runner of ``box`` for the block (``_runner``). Every failure in the block
    leaves it as a ``Failure`` named by ``failure_of``, so a route answers it by its
    reason."""
    try:
        async with _runner(deps, box, application_config, deadline) as runner:
            yield runner
    except Exception as exc:
        answered = failure_of(exc)
        if answered is exc:
            raise
        raise answered from exc


@asynccontextmanager
async def _runner(
    deps: APIDependencies,
    box: Box,
    application_config: AppConfig,
    deadline: float | None,
) -> AsyncIterator[BoxRunner]:
    """The runner of ``box`` for the block.

    With ``deadline`` the whole block (taking the connection and every command) is
    bounded, as a polled request is. Past it, while the connection was still being
    taken, ``ProbeTimeoutError`` is raised ("connect to NAME timed out after N s": the
    box was not reached); after, ``CommandTimeoutError`` ("command on NAME timed out
    (N s limit for connect plus command)"). The local box is always reached.
    Giving the connection back to the pool is not counted, so a block whose work
    finished is never reported as a timeout.

    On a remote box the pooled connection is taken by the first call that needs it (a
    connect that times out on its own limit raises ``ProbeTimeoutError`` too) and
    given back when the block ends; it is closed instead when the block ended by a
    timeout or a cancel, or a command in it ran past its limit.
    """
    if is_local(box):
        runner = LocalRunner(box.name)
        if deadline is None:
            yield runner
            return
        try:
            async with asyncio.timeout(deadline) as bound:
                yield runner
        except TimeoutError:
            if bound.expired():
                raise CommandTimeoutError(_command_timed_out(box, deadline)) from None
            raise
        return

    pool = get_pool()
    taken: list[Any] = []

    async def connection() -> Any:
        if not taken:
            try:
                taken.append(
                    await pool.acquire(box, lambda: deps.connect_for_box(box, application_config))
                )
            except TimeoutError as exc:
                # The connect's own limit: the box was not reached.
                raise ProbeTimeoutError(f"connect to {box.name} timed out") from exc
        return taken[0]

    remote = RemoteRunner(connection, box.name)
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
                    raise ProbeTimeoutError(_connect_timed_out(box, deadline)) from None
                raise CommandTimeoutError(_command_timed_out(box, deadline)) from None
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
