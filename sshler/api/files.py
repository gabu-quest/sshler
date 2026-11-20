from __future__ import annotations

import asyncio
import base64
import contextlib
import errno
import logging
import mimetypes
import os
import posixpath
import secrets
import shlex
import shutil
import stat as stat_mod
import tempfile
import zipfile
from collections.abc import Awaitable, Callable, Coroutine
from pathlib import Path, PurePosixPath
from typing import Any, NoReturn
from urllib.parse import quote

import anyio
import asyncssh
from fastapi import APIRouter, Body, Depends, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.routing import APIRoute

from .. import state
from ..config import AppConfig
from ..proc import run_bounded
from ..ssh import SSHError, sftp_chmod, sftp_list_directory
from ..ssh_pool import get_pool
from ..validation import PathValidator, ValidationError
from .dependencies import APIDependencies
from .helpers import (
    IMAGE_CONTENT_TYPES,
    LOCAL_IS_WINDOWS,
    MAX_IMAGE_PREVIEW_BYTES,
    _compose_local_child_path,
    _compose_remote_child_path,
    _is_markdown_file,
    _local_delete_file,
    _local_read_bytes,
    _local_read_text,
    _local_write_text,
    _normalize_directory_path,
    _normalize_local_path,
    _read_file_bytes,
    _read_remote_text,
    _syntax_from_filename,
)
from .models import (
    APIChmodRequest,
    APICopyRequest,
    APIDeleteRequest,
    APIDirectoryEntry,
    APIDirectoryListing,
    APIFilePreview,
    APIMoveRequest,
    APIRenameRequest,
    APISimpleMessage,
    APITouchRequest,
)
from .rate_limiting import rate_limit_delete, rate_limit_upload, rate_limit_write

logger = logging.getLogger(__name__)


class _TempFileResponse(FileResponse):
    """A ``FileResponse`` that deletes its file once the response is over.

    A ``background`` task is not enough: Starlette answers a malformed or
    unsatisfiable ``Range`` with an early ``return`` that skips it, and a client
    disconnect raises out of ``send`` before it runs. ``finally`` covers every outcome.
    """

    async def __call__(self, scope, receive, send) -> None:
        try:
            await super().__call__(scope, receive, send)
        finally:
            Path(self.path).unlink(missing_ok=True)


# The gitignore marks of a listing are a nicety: a git that does not answer in
# this time is given up on and the listing goes out without them.
GITIGNORE_TIMEOUT = 5.0


async def _get_gitignored_names(directory: str, names: list[str]) -> set[str]:
    """Check which filenames in a directory are gitignored. Returns set of ignored names."""
    try:
        returncode, stdout, _ = await run_bounded(
            ["git", "check-ignore", "--stdin"],
            GITIGNORE_TIMEOUT,
            cwd=directory,
            stdin_data="\n".join(names).encode(),
        )
        if returncode not in (0, 1):  # 1 means none matched, 128 means not a git repo
            return set()
        return set(line.strip() for line in stdout.decode().splitlines() if line.strip())
    except Exception:
        return set()


async def _resolve_remote_tilde(connection, path: str) -> str:
    """Expand a leading ``~`` / ``~/`` to the remote home directory.

    The remote path is quoted for the shell later, which stops the remote shell
    from expanding ``~``; resolving it here keeps validation, ``du`` and ``zip``
    all working on the real folder.
    """
    if path != "~" and not path.startswith("~/"):
        return path
    result = await asyncio.wait_for(connection.run('printf %s "$HOME"'), timeout=10)
    home = (result.stdout or "").strip()
    if result.exit_status != 0 or not home.startswith("/"):
        raise HTTPException(status_code=502, detail="Could not resolve the remote home directory")
    # Join with a plain "/" (posixpath.join would drop `home` when the rest starts
    # with "/", as in `~//etc`), then normalise; the caller validates the result.
    return home if path == "~" else posixpath.normpath(f"{home}/{path[2:]}")


async def _remote_path(connection, path: str) -> str:
    """``path`` with ``~`` resolved to the remote home, validated (raises
    ``ValidationError``). SFTP does not expand ``~``: it would be a literal folder."""
    return PathValidator.validate_remote_path(await _resolve_remote_tilde(connection, path))


async def _remote_directory(connection, directory: str | None) -> str:
    """A remote folder with ``~`` resolved the way the listing does, then normalised.
    Without the lookup ``_normalize_directory_path`` maps ``~`` to ``/``."""
    resolved = await _resolve_remote_tilde(connection, (directory or "/").strip())
    return _normalize_directory_path(resolved)


def _compose_remote_child(directory: str, filename: str) -> str:
    """``directory/filename``, validated; a bad result is a 400."""
    try:
        return PathValidator.validate_remote_path(_compose_remote_child_path(directory, filename))
    except (ValidationError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


# --- No file lands on an existing name unless the caller said replace -------------
#
# Upload, new file, rename, move, copy, batch move and copy and the cross-box transfer
# all decide through `_refuse_or_replace`. A free name is then taken with a call that
# fails when the name exists (an exclusive create, a hard link, a new folder), never by
# check-then-rename, so a name that appears after the check is refused, not replaced.
# A replace is an atomic rename of a finished file over the old one, so a failed copy
# never leaves the old file half written; the new file gets the old one's permission
# bits. A move removes its source only after the copy is complete and only if the
# source is still the file that was copied.

_ALREADY_EXISTS = "File already exists"
_NOT_REGULAR = "Only a regular file can be replaced"
_SAME_FILE = "Source and destination are the same file"
_SAME_NAME_IN_BATCH = "Another item of this request has the same name"
_INTO_ITSELF = "A folder cannot be placed inside itself"
_NO_FOLDER_COPY = "Copying directories not supported"
_ONLY_FILES = "Only files can be transferred"

# `reason` of a refused item in a batch's failed list and a transfer's `done` event.
REASON_EXISTS = "exists"  # a regular file has the name; `overwrite` would replace it
REASON_CANNOT_REPLACE = "cannot_replace"  # the name is taken; `overwrite` cannot replace it
REASON_FAILED = "failed"  # any other failure

COPY_CHUNK_BYTES = 256 * 1024


class DestinationRefused(HTTPException):
    """A destination the rule refuses; ``reason`` is one of the ``REASON_*`` values."""

    def __init__(self, status_code: int, detail: str, reason: str) -> None:
        super().__init__(status_code=status_code, detail=detail)
        self.reason = reason


class _RefusalAnswers(APIRoute):
    """A route that places a file under a new name: a ``DestinationRefused`` that reaches
    the route's edge is answered as ``{"detail": text, "reason": reason}`` with its
    status, so a caller can tell a name it may ask to replace from one it never can."""

    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        handler = super().get_route_handler()

        async def answer(request: Request) -> Response:
            try:
                return await handler(request)
            except DestinationRefused as exc:
                return JSONResponse(
                    {"detail": exc.detail, "reason": exc.reason},
                    status_code=exc.status_code,
                    headers=exc.headers,
                )

        return answer


def failure_entry(path: str, exc: BaseException) -> dict[str, str]:
    """One entry of a batch's or a transfer's failed list."""
    if isinstance(exc, DestinationRefused):
        return {"path": path, "error": str(exc.detail), "reason": exc.reason}
    detail = exc.detail if isinstance(exc, HTTPException) else exc
    return {"path": path, "error": str(detail), "reason": REASON_FAILED}


def _reason(exc: BaseException) -> str:
    """An error as the user reads it (an SFTP error's own reason)."""
    reason = getattr(exc, "reason", None)
    return reason if isinstance(reason, str) and reason else str(exc)


def _refuse_or_replace(
    dest_mode: int | None,
    *,
    overwrite: bool,
    source_regular: bool = True,
    same_file: bool = False,
    exists_status: int = 409,
) -> bool:
    """The rule. ``dest_mode`` is the destination's ``lstat`` mode (a link is not
    followed, so a dangling link is a taken name), None when the name is free.

    Returns False for a free name and True for a regular file that ``overwrite`` lets a
    regular file replace; raises ``DestinationRefused`` for every other taken name."""
    if dest_mode is None:
        return False
    is_regular = stat_mod.S_ISREG(dest_mode)
    replaceable = is_regular and source_regular and not same_file
    if not overwrite:
        reason = REASON_EXISTS if replaceable else REASON_CANNOT_REPLACE
        raise DestinationRefused(exists_status, _ALREADY_EXISTS, reason)
    if not replaceable:
        detail = _SAME_FILE if same_file and is_regular and source_regular else _NOT_REGULAR
        raise DestinationRefused(400, detail, REASON_CANNOT_REPLACE)
    return True


def _taken_after_check(
    dest_mode: int | None, *, source_regular: bool = True, exists_status: int = 409
) -> NoReturn:
    """The name was free at the check and taken at the write: refuse it as the rule
    would have at the check (a name gone again by now counts as a regular file)."""
    _refuse_or_replace(
        stat_mod.S_IFREG if dest_mode is None else dest_mode,
        overwrite=False,
        source_regular=source_regular,
        exists_status=exists_status,
    )
    raise AssertionError("unreachable: an existing name is always refused")


def refuse_a_name_this_request_placed(target: str, landed: set[str]) -> None:
    """Two items of one batch or transfer with the same name: the second is refused,
    even with ``overwrite``, since replacing would lose the first item."""
    if target in landed:
        raise DestinationRefused(409, _SAME_NAME_IN_BATCH, REASON_CANNOT_REPLACE)


def _refuse_a_folder_into_itself(
    source: str, target_parent: str, commonpath: Callable[[list[str]], str] = os.path.commonpath
) -> None:
    """A folder copied or moved into itself or into a folder under it is refused before
    anything is made (a copy would descend into its own copy). Both paths are resolved
    (links followed); ``commonpath`` is the local or the POSIX one."""
    if commonpath([source, target_parent]) == source:
        raise DestinationRefused(400, _INTO_ITSELF, REASON_CANNOT_REPLACE)


def kept_permission_bits(mode: int) -> int:
    """The bits a replace keeps from the replaced file: the nine permission bits.
    Setuid, setgid and sticky are never carried to the new file."""
    return mode & 0o777


def ensure_source_unchanged(before: object, after: object, target: str) -> None:
    """A move removes its source only if the source's fingerprint is what it was when
    the copy began (``local_fingerprint``, ``remote_fingerprint``; for a folder moved
    across file systems also ``_local_tree_record``). Otherwise the source is not what
    was copied (another box reaches the same file and the copy replaced it, or it was
    written to meanwhile): it is kept and the move fails."""
    if before != after:
        raise HTTPException(
            status_code=500,
            detail=f"Copied to {target}, but the source changed during the copy and was kept",
        )


async def run_to_end[T](call: Awaitable[T]) -> T:
    """Await a cleanup to its end even when the request is being cancelled (a client
    that went away cancels a streaming response, and every further await with it)."""
    with anyio.CancelScope(shield=True):
        result = await call
    return result


# --- local transport (every function here runs in a worker thread) ---------------


def _local_lstat(path: str) -> os.stat_result | None:
    """``lstat`` of a destination (a link is not followed), None when the name is free."""
    try:
        return os.lstat(path)
    except FileNotFoundError:
        return None


def _local_lstat_mode(path: str) -> int | None:
    found = _local_lstat(path)
    return None if found is None else found.st_mode


def local_fingerprint(found: os.stat_result) -> tuple[int, int, int]:
    """What ``ensure_source_unchanged`` compares for a local file: size, modification
    time and inode (a replace by a temp file and a rename gives the path a new inode
    even when size and time are equal). A remote fingerprint has no inode: see
    ``remote_fingerprint``."""
    return found.st_size, found.st_mtime_ns, found.st_ino


class _LocalLanding:
    """A local file being written under the rule. A free name is created exclusively
    (``O_CREAT|O_EXCL``: ``FileExistsError`` when taken, a dangling link included); a
    replace writes a temp file beside the target and renames it over on ``commit``.

    A replace's temp file gets ``mode``'s permission bits (the replaced file's); a free
    name gets the default bits (the umask's), which a copy within the box then sets to
    the source's."""

    def __init__(self, target: str, *, replace: bool, mode: int | None = None) -> None:
        self.target = target
        self.temp: str | None = None
        if replace:
            fd, self.temp = tempfile.mkstemp(
                dir=os.path.dirname(target), prefix=f".{os.path.basename(target)}."
            )
            if mode is not None:
                os.fchmod(fd, kept_permission_bits(mode))
            self.handle = os.fdopen(fd, "wb")
        else:
            self.handle = open(target, "xb")  # noqa: SIM115 - closed by commit/abort

    def write(self, data: bytes) -> None:
        self.handle.write(data)

    def commit(self) -> None:
        self.handle.close()
        if self.temp is not None:
            os.replace(self.temp, self.target)

    def abort(self) -> None:
        """Remove what this landing wrote: its temp file, or the file it created. The
        removal happens whatever closing the handle does (a full disk fails the close
        too)."""
        try:
            with contextlib.suppress(OSError):
                self.handle.close()
        finally:
            with contextlib.suppress(OSError):
                os.unlink(self.temp if self.temp is not None else self.target)


def _local_landing(target: str, *, overwrite: bool, source_regular: bool = True) -> _LocalLanding:
    """Decide ``target`` by the rule and open it for the bytes of a file."""
    dest_mode = _local_lstat_mode(target)
    replace = _refuse_or_replace(dest_mode, overwrite=overwrite, source_regular=source_regular)
    if not source_regular:
        raise HTTPException(status_code=400, detail=_ONLY_FILES)
    try:
        return _LocalLanding(target, replace=replace, mode=dest_mode)
    except FileExistsError:
        _taken_after_check(_local_lstat_mode(target), source_regular=source_regular)


def _local_copy_file(source: str, target: str, *, replace_mode: int | None = None) -> None:
    """Copy a file in chunks (never whole into memory) with its times. A free name gets
    the source's mode; a replace (``replace_mode``, the replaced file's mode) keeps the
    replaced file's permission bits."""
    with open(source, "rb") as src:
        landing = _LocalLanding(target, replace=replace_mode is not None, mode=replace_mode)
        try:
            shutil.copyfileobj(src, landing.handle, COPY_CHUNK_BYTES)
            landing.commit()
        except BaseException:
            landing.abort()
            raise
    if replace_mode is None:
        shutil.copystat(source, target)
    else:
        found = os.stat(source)
        os.utime(target, ns=(found.st_atime_ns, found.st_mtime_ns))


def _first_copy_error(exc: BaseException) -> str:
    """``copytree`` goes on after an error and raises them all at the end: the first."""
    if isinstance(exc, shutil.Error) and exc.args and isinstance(exc.args[0], list):
        if exc.args[0]:
            return str(exc.args[0][0][2])
    return str(exc)


def _tree_entry(path: str) -> tuple[int, int]:
    """What is compared for one entry of a folder moved across file systems: size and
    modification time (a link is not followed). No inode: nothing replaces an entry of
    the source here, since the target is never inside it, and a file system without
    inodes of its own (FAT) may number an entry anew while a long copy runs."""
    found = os.lstat(path)
    return found.st_size, found.st_mtime_ns


def _local_tree_record(source: str) -> dict[str, tuple[int, int]]:
    """Relative path to ``_tree_entry`` of ``source`` and of every entry under it."""
    record = {".": _tree_entry(source)}
    for directory, names, files in os.walk(source):
        for name in (*names, *files):
            path = os.path.join(directory, name)
            record[os.path.relpath(path, source)] = _tree_entry(path)
    return record


def _local_copy_folder_new(
    source: str,
    target: str,
    *,
    symlinks: bool,
    record: dict[str, tuple[int, int]] | None = None,
) -> None:
    """Copy folder ``source`` to the free name ``target``. The name is taken with a new
    folder (``FileExistsError`` when taken), so everything under it is this copy's own:
    a copy that fails removes it and reports the first error, or says where the partial
    copy is when it cannot be removed. With ``record`` the copy fills it with the
    ``_tree_entry`` of the folder and of each entry it is about to read."""

    def note(directory: str, names: list[str]) -> list[str]:
        assert record is not None
        for name in ("", *names):
            path = os.path.join(directory, name) if name else directory
            with contextlib.suppress(FileNotFoundError):
                record[os.path.relpath(path, source)] = _tree_entry(path)
        return []

    os.mkdir(target)
    try:
        shutil.copytree(
            source,
            target,
            symlinks=symlinks,
            dirs_exist_ok=True,
            ignore=note if record is not None else None,
        )
    except Exception as exc:
        detail = f"Could not copy {source} to {target}: {_first_copy_error(exc)}"
        try:
            shutil.rmtree(target)
        except OSError as cleanup:
            detail += f"; the partial copy at {target} could not be removed: {cleanup}"
        raise HTTPException(status_code=500, detail=detail) from exc


def _local_remove_moved_source(
    source: str,
    target: str,
    before: tuple[int, int, int],
    copied: dict[str, tuple[int, int]] | None = None,
) -> None:
    """Remove a moved source whose copy is complete at ``target``, if it is unchanged.
    ``copied`` (a folder copy's record) is compared with one walk of the source: an
    entry changed, added or gone since it was read is a change."""
    try:
        ensure_source_unchanged(before, local_fingerprint(os.lstat(source)), target)
        if copied is not None:
            ensure_source_unchanged(copied, _local_tree_record(source), target)
        if os.path.isdir(source) and not os.path.islink(source):
            shutil.rmtree(source)
        else:
            os.unlink(source)
    except OSError as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Copied to {target}, but the source could not be removed: {exc}",
        ) from exc


def _local_move_new(source: str, target: str, source_stat: os.stat_result) -> None:
    """Move to a free name; ``FileExistsError`` when it is taken by now."""
    if LOCAL_IS_WINDOWS:
        os.rename(source, target)  # Windows refuses an existing name itself
        return
    source_mode = source_stat.st_mode
    before = local_fingerprint(source_stat)
    if stat_mod.S_ISDIR(source_mode):
        os.mkdir(target)  # takes the name, or FileExistsError
        try:
            os.rename(source, target)  # over the empty folder made above
        except OSError as exc:
            with contextlib.suppress(OSError):
                os.rmdir(target)
            if exc.errno != errno.EXDEV:
                raise
            copied: dict[str, tuple[int, int]] = {}
            _local_copy_folder_new(source, target, symlinks=True, record=copied)
            _local_remove_moved_source(source, target, before, copied)
        return
    try:
        os.link(source, target, follow_symlinks=False)  # EEXIST when taken
    except FileExistsError:
        raise
    except OSError:
        # No hard link here (another filesystem, or one without links): copy into a
        # name created exclusively, then remove the source.
        if stat_mod.S_ISLNK(source_mode):
            os.symlink(os.readlink(source), target)
        else:
            _local_copy_file(source, target)
    _local_remove_moved_source(source, target, before)


def _local_copy_new(source: str, target: str, source_mode: int) -> None:
    if stat_mod.S_ISDIR(source_mode):
        _local_copy_folder_new(source, target, symlinks=False)
        return
    _local_copy_file(source, target)


def _local_replace_by_move(
    source: str, target: str, dest_mode: int, before: tuple[int, int, int]
) -> None:
    """Move a regular file over a regular file, keeping the replaced file's bits."""
    try:
        os.replace(source, target)
    except OSError as exc:
        if exc.errno != errno.EXDEV:
            raise
        _local_copy_file(source, target, replace_mode=dest_mode)
        _local_remove_moved_source(source, target, before)
        return
    os.chmod(target, kept_permission_bits(dest_mode))


def _same_spelling(source: str, target: str) -> bool:
    return os.path.normcase(source).casefold() == os.path.normcase(target).casefold()


def _local_place(
    source: str, target: str, *, move: bool, overwrite: bool, allow_dirs: bool = True
) -> None:
    """Move or copy ``source`` to ``target`` under the rule (call from a worker thread).

    A move onto the source itself (the same path, or one that differs only in letter
    case on a filesystem that ignores case) is a plain rename, not a taken name. A
    missing source raises ``FileNotFoundError``."""
    source_stat = os.lstat(source) if move else os.stat(source)
    dest_stat = _local_lstat(target)
    same_file = dest_stat is not None and os.path.samestat(source_stat, dest_stat)
    if move and same_file and _same_spelling(source, target):
        os.rename(source, target)
        return
    if stat_mod.S_ISDIR(source_stat.st_mode):
        _refuse_a_folder_into_itself(
            os.path.realpath(source), os.path.realpath(os.path.dirname(target))
        )
    source_regular = stat_mod.S_ISREG(source_stat.st_mode)
    replace = _refuse_or_replace(
        None if dest_stat is None else dest_stat.st_mode,
        overwrite=overwrite,
        source_regular=source_regular,
        same_file=same_file,
    )
    if stat_mod.S_ISDIR(source_stat.st_mode) and not allow_dirs:
        raise HTTPException(status_code=400, detail=_NO_FOLDER_COPY)
    if replace:
        assert dest_stat is not None
        if move:
            _local_replace_by_move(
                source, target, dest_stat.st_mode, local_fingerprint(source_stat)
            )
        else:
            _local_copy_file(source, target, replace_mode=dest_stat.st_mode)
        return
    try:
        if move:
            _local_move_new(source, target, source_stat)
        else:
            _local_copy_new(source, target, source_stat.st_mode)
    except FileExistsError:
        _taken_after_check(_local_lstat_mode(target), source_regular=source_regular)


def _local_destination(destination: str) -> Path:
    """The normalised destination folder; nothing is made."""
    return Path(_normalize_local_path(destination))


def _local_make_folder(dest_dir: Path) -> Path:
    """Make ``dest_dir`` if missing (call from a worker thread)."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    return dest_dir


def _local_refuse_a_folder_into_itself(source: str, dest_dir: Path, *, move: bool) -> None:
    """Refuse a source folder whose destination is itself or lies under it, before the
    destination is made (it may not exist yet; call from a worker thread)."""
    found = os.lstat(source) if move else os.stat(source)
    if stat_mod.S_ISDIR(found.st_mode):
        _refuse_a_folder_into_itself(os.path.realpath(source), os.path.realpath(dest_dir))


def _local_place_into(
    source_path: str,
    destination: str,
    new_name: str | None,
    *,
    move: bool,
    overwrite: bool,
    allow_dirs: bool = True,
) -> str:
    """Place ``source_path`` in folder ``destination`` as ``new_name`` (default: its own
    name) under the rule; returns the target (call from a worker thread)."""
    source = _normalize_local_path(source_path)
    if not os.path.lexists(source):
        raise FileNotFoundError(source)
    dest_dir = _local_destination(destination)
    _local_refuse_a_folder_into_itself(source, dest_dir, move=move)
    target = str(_local_make_folder(dest_dir) / (new_name or Path(source).name))
    _local_place(source, target, move=move, overwrite=overwrite, allow_dirs=allow_dirs)
    return target


async def _local_write_new(
    target_path: str, data: bytes, *, overwrite: bool, exists_status: int
) -> None:
    """Write ``data`` to a new local file, or replace a regular file with ``overwrite``
    (in place, as the remote twin does). An existing name answers ``exists_status``."""

    def _worker() -> None:
        replace = _refuse_or_replace(
            _local_lstat_mode(target_path), overwrite=overwrite, exists_status=exists_status
        )
        Path(target_path).parent.mkdir(parents=True, exist_ok=True)
        try:
            with open(target_path, "wb" if replace else "xb") as handle:
                handle.write(data)
        except FileExistsError:
            _taken_after_check(_local_lstat_mode(target_path), exists_status=exists_status)

    await asyncio.to_thread(_worker)


# --- remote transport (SFTP) --------------------------------------------------------
#
# Measured on the loopback asyncssh server (tests/test_ssh_loopback.py): a plain SFTP
# rename onto an existing file is refused, but the server checks with a `stat` that
# follows links and then renames, so it replaces a dangling link and has a gap; other
# servers replace in silence. So a free remote name is taken with a hard link
# (hardlink@openssh.com), an exclusive open ("xb") or a new folder, and a replace is a
# posix-rename@openssh.com of a finished file; a server without it gets a replace
# through a backup name (`_RemoteLanding._replace_through_backup`).


async def _remote_lstat_mode(sftp_client, path: str) -> int | None:
    """Only "no such file" counts as free; any other failure (permission denied) is a
    400, since the name cannot be shown to be free."""
    try:
        attrs = await sftp_client.lstat(path)
    except asyncssh.SFTPNoSuchFile:
        return None
    except asyncssh.SFTPError as exc:
        raise HTTPException(
            status_code=400, detail=f"Cannot check whether {path} exists: {exc.reason}"
        ) from exc
    return attrs.permissions or 0


def remote_fingerprint(attrs) -> tuple:
    """What ``ensure_source_unchanged`` compares for a remote file: size and
    modification time. SFTP has no inode, and on a server that sends no finer time the
    modification time is in whole seconds."""
    return attrs.size, attrs.mtime, attrs.mtime_ns


async def _remote_exclusive[T](
    sftp_client, target: str, call: Awaitable[T], *, source_regular: bool = True
) -> T:
    """Await ``call``, which fails when ``target`` exists; failing with the name taken
    is the rule's refusal (SFTPv3 has no "exists" status: OpenSSH says FX_FAILURE)."""
    try:
        return await call
    except asyncssh.SFTPError:
        mode = await _remote_lstat_mode(sftp_client, target)
        if mode is not None:
            _taken_after_check(mode, source_regular=source_regular)
        raise


def _remote_temp_beside(target: str, suffix: str = ".sshler") -> str:
    parent, name = posixpath.split(target)
    return posixpath.join(parent, f".{name}.{secrets.token_hex(6)}{suffix}")


class _RemoteLanding:
    """Remote twin of ``_LocalLanding``: a free name is opened "xb"; a replace writes a
    temp file beside the target and posix-renames it over on ``commit``.

    The file is opened with ``mode``'s permission bits: the source's on a copy within
    the box, the replaced file's on a replace. A transfer to a free name passes None,
    so the file gets the server's default bits."""

    def __init__(self, sftp_client, target: str, temp: str | None, handle) -> None:
        self.sftp = sftp_client
        self.target = target
        self.temp = temp
        self.handle = handle
        self.keep: int | None = None  # a replace's bits, set exactly (the umask narrows an open)

    @classmethod
    async def start(
        cls, sftp_client, target: str, *, replace: bool, source_regular: bool = True, mode=None
    ) -> _RemoteLanding:
        attrs = (
            asyncssh.SFTPAttrs(permissions=kept_permission_bits(mode))
            if mode
            else asyncssh.SFTPAttrs()
        )
        if replace:
            temp = _remote_temp_beside(target)
            landing = cls(sftp_client, target, temp, await sftp_client.open(temp, "xb", attrs))
            landing.keep = attrs.permissions
            return landing
        handle = await _remote_exclusive(
            sftp_client,
            target,
            sftp_client.open(target, "xb", attrs),
            source_regular=source_regular,
        )
        return cls(sftp_client, target, None, handle)

    async def write(self, data: bytes) -> None:
        await self.handle.write(data)

    async def commit(self) -> None:
        await self.handle.close()
        if self.temp is None:
            return
        if self.keep is not None:
            await self.sftp.chmod(self.temp, self.keep)
        try:
            await self.sftp.posix_rename(self.temp, self.target)
        except asyncssh.SFTPOpUnsupported:
            await self._replace_through_backup(self.temp)

    async def _replace_through_backup(self, temp: str) -> None:
        """No atomic replace on this server: the old file steps aside to a backup name,
        the new one takes the name, then the backup is removed. If the new one cannot
        take the name the backup is put back; a backup that cannot be put back is named
        in the error, so the old file is never lost without saying where it is."""
        backup = _remote_temp_beside(self.target, ".sshler-old")
        await self.sftp.rename(self.target, backup)
        try:
            await self.sftp.rename(temp, self.target)
        except BaseException as exc:
            try:
                await run_to_end(self.sftp.rename(backup, self.target))
            except Exception as restore_exc:
                raise HTTPException(
                    status_code=500,
                    detail=f"Could not replace {self.target}: {_reason(exc)}; "
                    f"the old file is kept at {backup}",
                ) from restore_exc
            raise
        try:
            await self.sftp.remove(backup)
        except asyncssh.SFTPError as exc:
            logger.warning("Replaced %s, but could not remove %s: %s", self.target, backup, exc)

    async def abort(self) -> None:
        """Remove what this landing wrote, to the end even if the request is cancelled."""
        await run_to_end(self._abort())

    async def _abort(self) -> None:
        with contextlib.suppress(Exception):
            await self.handle.close()
        with contextlib.suppress(asyncssh.SFTPError):
            await self.sftp.remove(self.temp if self.temp is not None else self.target)


async def _remote_landing(
    sftp_client, target: str, *, overwrite: bool, source_regular: bool = True
) -> _RemoteLanding:
    """Decide ``target`` by the rule and open it for the bytes of a file."""
    dest_mode = await _remote_lstat_mode(sftp_client, target)
    replace = _refuse_or_replace(dest_mode, overwrite=overwrite, source_regular=source_regular)
    if not source_regular:
        raise HTTPException(status_code=400, detail=_ONLY_FILES)
    return await _RemoteLanding.start(
        sftp_client,
        target,
        replace=replace,
        source_regular=source_regular,
        mode=dest_mode if replace else None,
    )


async def _remote_copy_file(
    sftp_client, source: str, target: str, *, replace: bool, mode: int
) -> None:
    """Copy a remote file to the same server in chunks, never whole into memory.
    ``mode``: the source's on a free name, the replaced file's on a replace."""
    async with await sftp_client.open(source, "rb") as src:
        landing = await _RemoteLanding.start(sftp_client, target, replace=replace, mode=mode)
        try:
            while chunk := await src.read(COPY_CHUNK_BYTES):
                await landing.write(chunk)
            await landing.commit()
        except BaseException:
            await landing.abort()
            raise


async def _remote_copy_tree(sftp_client, source: str, target: str) -> None:
    """Copy the contents of folder ``source`` into the new, empty folder ``target``.
    Links are copied as links; anything but folders, files and links is left out."""
    for entry in await sftp_client.readdir(source):
        name = entry.filename
        if name in (".", ".."):
            continue
        src, dst = posixpath.join(source, name), posixpath.join(target, name)
        mode = entry.attrs.permissions or 0
        if stat_mod.S_ISDIR(mode):
            await sftp_client.mkdir(dst)
            await _remote_copy_tree(sftp_client, src, dst)
        elif stat_mod.S_ISLNK(mode):
            await sftp_client.symlink(await sftp_client.readlink(src), dst)
        elif stat_mod.S_ISREG(mode):
            await _remote_copy_file(sftp_client, src, dst, replace=False, mode=mode)


async def _remote_remove_tree(sftp_client, path: str) -> None:
    """Remove folder ``path`` and everything under it (links are removed, not followed)."""
    for entry in await sftp_client.readdir(path):
        if entry.filename in (".", ".."):
            continue
        child = posixpath.join(path, entry.filename)
        if stat_mod.S_ISDIR(entry.attrs.permissions or 0):
            await _remote_remove_tree(sftp_client, child)
        else:
            await sftp_client.remove(child)
    await sftp_client.rmdir(path)


async def _remote_copy_folder_new(sftp_client, source: str, target: str) -> None:
    """Remote twin of ``_local_copy_folder_new``."""
    await _remote_exclusive(sftp_client, target, sftp_client.mkdir(target), source_regular=False)
    try:
        await _remote_copy_tree(sftp_client, source, target)
    except Exception as exc:
        detail = f"Could not copy {source} to {target}: {_reason(exc)}"
        try:
            await run_to_end(_remote_remove_tree(sftp_client, target))
        except Exception as cleanup:
            detail += f"; the partial copy at {target} could not be removed: {_reason(cleanup)}"
        raise HTTPException(status_code=500, detail=detail) from exc


async def _remote_remove_moved_source(
    sftp_client, source: str, target: str, before: tuple | None
) -> None:
    """Remove a moved source whose copy is complete at ``target``. ``before`` is the
    source's fingerprint when bytes were copied: the source is removed only if it is
    unchanged. None where no bytes were copied (a hard link or a new link): there is
    nothing to compare, so the check is not paid for."""
    try:
        if before is not None:
            after = remote_fingerprint(await sftp_client.lstat(source))
            ensure_source_unchanged(before, after, target)
        await sftp_client.remove(source)
    except asyncssh.SFTPError as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Copied to {target}, but the source could not be removed: {exc.reason}",
        ) from exc


async def _remote_move_new(sftp_client, source: str, target: str, source_attrs) -> None:
    source_mode = source_attrs.permissions or 0
    before = remote_fingerprint(source_attrs)
    if stat_mod.S_ISDIR(source_mode):
        await _remote_exclusive(
            sftp_client, target, sftp_client.mkdir(target), source_regular=False
        )
        try:
            await sftp_client.posix_rename(source, target)  # over the empty folder above
        except asyncssh.SFTPOpUnsupported:
            # No posix-rename: the server's own rename decides about the name.
            await sftp_client.rmdir(target)
            await _remote_exclusive(
                sftp_client, target, sftp_client.rename(source, target), source_regular=False
            )
        except asyncssh.SFTPError:
            with contextlib.suppress(asyncssh.SFTPError):
                await sftp_client.rmdir(target)
            raise
        return
    if stat_mod.S_ISLNK(source_mode):
        link_text = await sftp_client.readlink(source)
        await _remote_exclusive(
            sftp_client, target, sftp_client.symlink(link_text, target), source_regular=False
        )
        await _remote_remove_moved_source(sftp_client, source, target, None)
        return
    try:
        await sftp_client.link(source, target)
    except asyncssh.SFTPError:
        mode = await _remote_lstat_mode(sftp_client, target)
        if mode is not None:
            _taken_after_check(mode)
        # No hard link on this server (or across its filesystems): copy into a name
        # opened exclusively, then remove the source if it is still what was copied.
        await _remote_copy_file(sftp_client, source, target, replace=False, mode=source_mode)
        await _remote_remove_moved_source(sftp_client, source, target, before)
    else:
        # Source and target are one file now: the check could not find anything.
        await _remote_remove_moved_source(sftp_client, source, target, None)


async def _remote_replace_by_rename(
    sftp_client, source: str, target: str, source_mode: int, dest_mode: int
) -> bool:
    """Posix-rename a regular file over a regular file, keeping the replaced file's
    bits (set on the source first, put back if the rename fails). False when the server
    has no posix-rename."""
    own, keep = stat_mod.S_IMODE(source_mode), kept_permission_bits(dest_mode)
    if own != keep:
        await sftp_client.chmod(source, keep)
    try:
        await sftp_client.posix_rename(source, target)  # atomic replace
    except BaseException as exc:
        if own != keep:
            with contextlib.suppress(asyncssh.SFTPError):
                await run_to_end(sftp_client.chmod(source, own))
        if isinstance(exc, asyncssh.SFTPOpUnsupported):
            return False
        raise
    return True


async def _remote_own_name_by_case(sftp_client, source: str, target: str) -> bool:
    """A rename within one folder that changes only letter case, on a server that looks
    names up without regard to case (``lstat`` of the target found the source): the
    folder's listing decides. No entry spelled exactly as the target means the name is
    the source's own."""
    source_dir, source_name = posixpath.split(source)
    target_dir, target_name = posixpath.split(target)
    if source_dir != target_dir or source_name == target_name:
        return False
    if source_name.casefold() != target_name.casefold():
        return False
    names = {entry.filename for entry in await sftp_client.readdir(target_dir)}
    return target_name not in names


async def _remote_place(
    sftp_client, source: str, target: str, *, move: bool, overwrite: bool, allow_dirs: bool = True
) -> None:
    """Remote twin of ``_local_place``. A move onto the same path is nothing to do (SFTP
    gives no inode, so another spelling of the same file is a taken name, except a name
    that differs only in letter case on a server that ignores case). A missing source
    raises ``asyncssh.SFTPNoSuchFile``."""
    source_attrs = await (sftp_client.lstat(source) if move else sftp_client.stat(source))
    source_mode = source_attrs.permissions or 0
    if move and source == target:
        return
    dest_mode = await _remote_lstat_mode(sftp_client, target)
    if (
        move
        and dest_mode is not None
        and await _remote_own_name_by_case(sftp_client, source, target)
    ):
        try:
            await sftp_client.posix_rename(source, target)
        except asyncssh.SFTPOpUnsupported:
            await sftp_client.rename(source, target)
        return
    if stat_mod.S_ISDIR(source_mode) and posixpath.dirname(source) != posixpath.dirname(target):
        _refuse_a_folder_into_itself(
            await sftp_client.realpath(source),
            await sftp_client.realpath(posixpath.dirname(target)),
            posixpath.commonpath,
        )
    source_regular = stat_mod.S_ISREG(source_mode)
    replace = _refuse_or_replace(
        dest_mode,
        overwrite=overwrite,
        source_regular=source_regular,
        same_file=source == target,
    )
    if stat_mod.S_ISDIR(source_mode) and not allow_dirs:
        raise HTTPException(status_code=400, detail=_NO_FOLDER_COPY)
    if replace and move:
        assert dest_mode is not None
        if not await _remote_replace_by_rename(sftp_client, source, target, source_mode, dest_mode):
            await _remote_copy_file(sftp_client, source, target, replace=True, mode=dest_mode)
            await _remote_remove_moved_source(
                sftp_client, source, target, remote_fingerprint(source_attrs)
            )
    elif replace:
        assert dest_mode is not None
        await _remote_copy_file(sftp_client, source, target, replace=True, mode=dest_mode)
    elif move:
        await _remote_move_new(sftp_client, source, target, source_attrs)
    elif stat_mod.S_ISDIR(source_mode):
        await _remote_copy_folder_new(sftp_client, source, target)
    else:
        await _remote_copy_file(sftp_client, source, target, replace=False, mode=source_mode)


async def _remote_write_new(sftp_client, remote_path: str, data: bytes, *, overwrite: bool) -> None:
    """Write ``data`` to a new remote file, or replace a regular file with ``overwrite``
    (in place: the bytes come from the request, so they cannot be the file itself)."""
    replace = _refuse_or_replace(
        await _remote_lstat_mode(sftp_client, remote_path), overwrite=overwrite
    )
    if replace:
        remote_file = await sftp_client.open(remote_path, "wb")
    else:
        remote_file = await _remote_exclusive(
            sftp_client, remote_path, sftp_client.open(remote_path, "xb")
        )
    async with remote_file:
        await remote_file.write(data)


def _attachment_header(filename: str) -> str:
    """``Content-Disposition`` for a download, safe for any file name.

    ``filename*`` carries the exact name percent-encoded as UTF-8 (RFC 5987/6266);
    ``filename`` is an ASCII fallback with ``"``, ``\\``, ``%`` and non-ASCII or
    control characters replaced, so a Japanese or quoted name cannot break the header.
    """
    fallback = "".join(
        ch if 0x20 <= ord(ch) < 0x7F and ch not in '"\\%' else "_" for ch in filename
    )
    return f"attachment; filename=\"{fallback}\"; filename*=UTF-8''{quote(filename, safe='')}"


async def _du_bytes(connection, command: str, multiplier: int) -> int | None:
    """Run a ``du`` command and return its total times ``multiplier``, or None.

    ``du`` exits non-zero when a subfolder is unreadable but still prints the
    total, so the printed number decides, not the exit status.
    """
    result = await asyncio.wait_for(connection.run(command), timeout=30)
    first_field = (result.stdout or "").strip().split("\t")[0]
    return int(first_field) * multiplier if first_field.isdigit() else None


# Remote folder zips are streamed to a temp file and aborted past this size,
# whatever `du` reported. Read at call time so tests can lower them.
MAX_DIR_DOWNLOAD_BYTES = 500 * 1024 * 1024  # 500 MB hard limit
ZIP_STREAM_CHUNK_BYTES = 1024 * 1024
ZIP_STREAM_TIMEOUT_SECONDS = 300
ZIP_STDERR_KEEP_BYTES = 64 * 1024  # tail of zip's stderr kept for the error message


class _ZipTooLargeError(Exception):
    """The remote zip passed ``MAX_DIR_DOWNLOAD_BYTES``; its process was killed."""


async def _drain_tail(stream, chunk_size: int) -> bytes:
    """Read ``stream`` to EOF in chunks; return its last ``ZIP_STDERR_KEEP_BYTES``."""
    tail = b""
    while True:
        chunk = await stream.read(chunk_size)
        if not chunk:
            return tail
        tail = (tail + chunk)[-ZIP_STDERR_KEEP_BYTES:]


async def _spool_remote_zip(connection, command: str, dest: str) -> None:
    """Run ``command`` and write its stdout to ``dest`` one chunk at a time.

    At most one chunk is held in memory. Past ``MAX_DIR_DOWNLOAD_BYTES`` the remote
    process is killed and ``_ZipTooLargeError`` raised; the caller must not pool the
    connection afterwards. A non-zero exit raises ``HTTPException(500)`` with stderr.
    """
    limit = MAX_DIR_DOWNLOAD_BYTES
    chunk_size = ZIP_STREAM_CHUNK_BYTES
    process = await connection.create_process(command, encoding=None)
    # stdout and stderr share one SSH channel window: stderr is read alongside
    # stdout, or a zip with many warnings stalls until the timeout.
    stderr_task = asyncio.ensure_future(_drain_tail(process.stderr, chunk_size))
    # Mark a reader error as retrieved when an abort leaves it unawaited.
    stderr_task.add_done_callback(lambda t: t.cancelled() or t.exception())
    try:
        total = 0
        with open(dest, "wb") as out:
            while True:
                chunk = await process.stdout.read(chunk_size)
                if not chunk:
                    break
                total += len(chunk)
                if total > limit:
                    with contextlib.suppress(Exception):
                        process.kill()
                    raise _ZipTooLargeError
                out.write(chunk)
        stderr = await stderr_task
        completed = await process.wait()
    finally:
        # A no-op once drained; on an abort the reader is stopped, not awaited, so an
        # outer cancel is never swallowed.
        stderr_task.cancel()
        process.close()
    if completed.exit_status != 0:
        message = stderr.decode(errors="replace")
        raise HTTPException(status_code=500, detail=f"zip failed: {message}")


def _local_children(target: Path) -> list[tuple[Path, bool]]:
    """Children of a folder, folders first then by lowercase name (runs in a worker thread)."""
    children = []
    for child in target.iterdir():
        try:
            is_dir = child.is_dir()
            children.append((child, is_dir))
        except OSError:
            continue  # broken symlink or deleted between iterdir and stat
    children.sort(key=lambda item: (not item[1], item[0].name.lower()))
    return children


def _local_entry_stats(
    children: list[tuple[Path, bool]],
) -> list[tuple[Path, bool, int | None, float, int]]:
    """Stat each child; one that vanished or cannot be read is left out (worker thread)."""
    rows = []
    for child, is_dir in children:
        try:
            stats = child.stat()
        except OSError:
            continue
        rows.append(
            (
                child,
                is_dir,
                stats.st_size if child.is_file() else None,
                stats.st_mtime,
                stats.st_mode & 0o7777,
            )
        )
    return rows


def get_router(deps: APIDependencies) -> APIRouter:
    router = APIRouter()
    # The routes that place a file under a new name answer a refusal with its reason.
    placing = APIRouter(route_class=_RefusalAnswers)

    @router.get("/boxes/{name}/ls", response_model=APIDirectoryListing)
    async def api_list_directory(
        name: str,
        directory: str = Query("/"),
        application_config: AppConfig = Depends(deps.get_application_config),
    ) -> APIDirectoryListing:
        box = deps.get_box_or_404(application_config, name)

        ssh_pool = get_pool()
        entries: list[APIDirectoryEntry] = []

        if box.transport == "local":
            normalized = _normalize_local_path(directory)
            target = Path(normalized)
            if not target.exists():
                raise HTTPException(status_code=404, detail="Directory not found")
            if not target.is_dir():
                raise HTTPException(status_code=400, detail="Path is not a directory")
            children = await asyncio.to_thread(_local_children, target)

            # Check gitignored status for all entries in one batch
            ignored_names: set[str] = set()
            if children:
                ignored_names = await _get_gitignored_names(
                    normalized, [c.name for c, _ in children]
                )

            for child, is_dir, size, modified, mode in await asyncio.to_thread(
                _local_entry_stats, children
            ):
                entries.append(
                    APIDirectoryEntry(
                        name=child.name,
                        path=str(child),
                        is_directory=is_dir,
                        size=size,
                        modified=modified,
                        mode=mode,
                        gitignored=child.name in ignored_names,
                    )
                )
            return APIDirectoryListing(box=box.name, directory=normalized, entries=entries)

        try:
            async with ssh_pool.connection(
                box, lambda: deps.connect_for_box(box, application_config)
            ) as connection:
                # `~` is the remote home, as for the folder zip (not `/`).
                normalized_remote = await _remote_directory(connection, directory)
                raw_entries = await sftp_list_directory(connection, normalized_remote)
        except HTTPException:
            raise
        except (SSHError, ConnectionError) as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        except Exception as exc:  # pragma: no cover - remote errors vary
            raise HTTPException(status_code=500, detail=str(exc)) from exc

        for entry in raw_entries:
            entry_path = posixpath.join(normalized_remote, str(entry["name"]))
            entries.append(
                APIDirectoryEntry(
                    name=str(entry["name"]),
                    path=entry_path,
                    is_directory=bool(entry.get("is_directory")),
                    size=int(str(entry["size"])) if entry.get("size") is not None else None,
                    modified=float(str(entry["modified"]))
                    if entry.get("modified") is not None
                    else None,
                    mode=int(str(entry["mode"])) if entry.get("mode") is not None else None,
                )
            )

        # Track directory visit for frecency-based search (remote boxes only)
        await state.record_directory_visit_async(box.name, normalized_remote)

        return APIDirectoryListing(box=box.name, directory=normalized_remote, entries=entries)

    @placing.post("/boxes/{name}/rename", response_model=APISimpleMessage)
    async def api_rename(
        name: str,
        payload: APIRenameRequest,
        application_config: AppConfig = Depends(deps.get_application_config),
    ) -> APISimpleMessage:
        box = deps.get_box_or_404(application_config, name)

        if box.transport == "local":
            try:
                new_name = PathValidator.validate_filename(payload.new_name)
            except ValidationError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc

            def _rename() -> str:
                source = Path(_normalize_local_path(payload.path))
                target = str(source.parent / new_name)
                _local_place(str(source), target, move=True, overwrite=payload.overwrite)
                return target

            try:
                target = await asyncio.to_thread(_rename)
            except HTTPException:
                raise
            except FileNotFoundError as exc:
                raise HTTPException(status_code=404, detail="File not found") from exc
            except Exception as exc:
                raise HTTPException(status_code=500, detail=str(exc)) from exc
            return APISimpleMessage(status="ok", message="renamed", path=target)

        try:
            PathValidator.validate_remote_path(payload.path)
            new_name = PathValidator.validate_filename(payload.new_name)
        except ValidationError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        ssh_pool = get_pool()
        try:
            async with ssh_pool.connection(
                box, lambda: deps.connect_for_box(box, application_config)
            ) as connection:
                validated_path = await _remote_path(connection, payload.path)
                target_path = str(PurePosixPath(validated_path).parent / new_name)
                sftp_client = await connection.start_sftp_client()
                try:
                    await _remote_place(
                        sftp_client,
                        validated_path,
                        target_path,
                        move=True,
                        overwrite=payload.overwrite,
                    )
                finally:
                    with contextlib.suppress(Exception):
                        sftp_client.exit()
        except HTTPException:
            raise
        except ValidationError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except (FileNotFoundError, asyncssh.SFTPNoSuchFile) as exc:
            raise HTTPException(status_code=404, detail="File not found") from exc
        except SSHError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        return APISimpleMessage(status="ok", message="renamed", path=target_path)

    @router.post("/boxes/{name}/touch", response_model=APISimpleMessage)
    async def api_touch_file(
        name: str,
        payload: APITouchRequest,
        application_config: AppConfig = Depends(deps.get_application_config),
    ) -> APISimpleMessage:
        box = deps.get_box_or_404(application_config, name)

        if box.transport == "local":
            directory_path = _normalize_local_path(payload.directory)
            target_path = _compose_local_child_path(directory_path, payload.filename)
            try:
                await _local_write_new(target_path, b"", overwrite=False, exists_status=400)
            except HTTPException:
                raise
            except Exception as exc:
                raise HTTPException(status_code=500, detail=str(exc)) from exc
            return APISimpleMessage(status="ok", message="created", path=target_path)

        try:
            validated_filename = PathValidator.validate_filename(payload.filename)
        except ValidationError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        ssh_pool = get_pool()
        try:
            async with ssh_pool.connection(
                box, lambda: deps.connect_for_box(box, application_config)
            ) as connection:
                remote_path = _compose_remote_child(
                    await _remote_directory(connection, payload.directory), validated_filename
                )
                sftp_client = await connection.start_sftp_client()
                try:
                    await _remote_write_new(sftp_client, remote_path, b"", overwrite=False)
                finally:
                    with contextlib.suppress(Exception):
                        sftp_client.exit()
        except SSHError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

        return APISimpleMessage(status="ok", message="created", path=remote_path)

    @router.post("/boxes/{name}/mkdir", response_model=APISimpleMessage)
    async def api_mkdir(
        name: str,
        payload: APITouchRequest,
        application_config: AppConfig = Depends(deps.get_application_config),
    ) -> APISimpleMessage:
        box = deps.get_box_or_404(application_config, name)

        if box.transport == "local":
            directory_path = _normalize_local_path(payload.directory)
            target_path = _compose_local_child_path(directory_path, payload.filename)
            path_obj = Path(target_path)
            if path_obj.exists():
                raise HTTPException(status_code=400, detail="Directory already exists")
            try:
                path_obj.mkdir(parents=False, exist_ok=False)
            except Exception as exc:
                raise HTTPException(status_code=500, detail=str(exc)) from exc
            return APISimpleMessage(status="ok", message="created", path=target_path)

        try:
            validated_name = PathValidator.validate_filename(payload.filename)
        except ValidationError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        ssh_pool = get_pool()
        try:
            async with ssh_pool.connection(
                box, lambda: deps.connect_for_box(box, application_config)
            ) as connection:
                remote_path = _compose_remote_child(
                    await _remote_directory(connection, payload.directory), validated_name
                )
                sftp_client = await connection.start_sftp_client()
                try:
                    try:
                        await sftp_client.stat(remote_path)
                        raise HTTPException(status_code=400, detail="Directory already exists")
                    except HTTPException:
                        raise
                    except Exception:
                        pass
                    await sftp_client.mkdir(remote_path)
                finally:
                    with contextlib.suppress(Exception):
                        sftp_client.exit()
        except SSHError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

        return APISimpleMessage(status="ok", message="created", path=remote_path)

    @router.post("/boxes/{name}/delete", response_model=APISimpleMessage)
    async def api_delete_file(
        request: Request,
        name: str,
        payload: APIDeleteRequest,
        application_config: AppConfig = Depends(deps.get_application_config),
        _rate_limit: None = Depends(rate_limit_delete),
    ) -> APISimpleMessage:
        box = deps.get_box_or_404(application_config, name)

        if box.transport == "local":
            target_path = _normalize_local_path(payload.path)
            try:
                await _local_delete_file(target_path)
            except FileNotFoundError as exc:
                raise HTTPException(status_code=404, detail="File not found") from exc
            except Exception as exc:
                raise HTTPException(status_code=500, detail=str(exc)) from exc
            return APISimpleMessage(status="ok", message="deleted", path=target_path)

        try:
            PathValidator.validate_remote_path(payload.path)
        except ValidationError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        ssh_pool = get_pool()
        try:
            async with ssh_pool.connection(
                box, lambda: deps.connect_for_box(box, application_config)
            ) as connection:
                validated_path = await _remote_path(connection, payload.path)
                sftp_client = await connection.start_sftp_client()
                try:
                    await sftp_client.remove(validated_path)
                finally:
                    with contextlib.suppress(Exception):
                        sftp_client.exit()
        except HTTPException:
            raise
        except ValidationError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail="File not found") from exc
        except SSHError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

        return APISimpleMessage(status="ok", message="deleted", path=validated_path)

    @placing.post("/boxes/{name}/move", response_model=APISimpleMessage)
    async def api_move(
        name: str,
        payload: APIMoveRequest,
        application_config: AppConfig = Depends(deps.get_application_config),
    ) -> APISimpleMessage:
        box = deps.get_box_or_404(application_config, name)

        if box.transport == "local":
            try:
                target = await asyncio.to_thread(
                    _local_place_into,
                    payload.source,
                    payload.destination,
                    None,
                    move=True,
                    overwrite=payload.overwrite,
                )
            except HTTPException:
                raise
            except FileNotFoundError as exc:
                raise HTTPException(status_code=404, detail="Source not found") from exc
            except Exception as exc:
                raise HTTPException(status_code=500, detail=str(exc)) from exc
            return APISimpleMessage(status="ok", message="moved", path=target)

        try:
            PathValidator.validate_remote_path(payload.source)
            PathValidator.validate_remote_path(payload.destination)
        except ValidationError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        ssh_pool = get_pool()
        try:
            async with ssh_pool.connection(
                box, lambda: deps.connect_for_box(box, application_config)
            ) as connection:
                remote_src = await _remote_path(connection, payload.source)
                remote_dest_dir = await _remote_path(connection, payload.destination)
                remote_target = str(PurePosixPath(remote_dest_dir) / PurePosixPath(remote_src).name)
                sftp_client = await connection.start_sftp_client()
                try:
                    await _remote_place(
                        sftp_client,
                        remote_src,
                        remote_target,
                        move=True,
                        overwrite=payload.overwrite,
                    )
                finally:
                    with contextlib.suppress(Exception):
                        sftp_client.exit()
        except HTTPException:
            raise
        except ValidationError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except (FileNotFoundError, asyncssh.SFTPNoSuchFile) as exc:
            raise HTTPException(status_code=404, detail="Source not found") from exc
        except SSHError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        return APISimpleMessage(status="ok", message="moved", path=remote_target)

    @placing.post("/boxes/{name}/copy", response_model=APISimpleMessage)
    async def api_copy(
        name: str,
        payload: APICopyRequest,
        application_config: AppConfig = Depends(deps.get_application_config),
    ) -> APISimpleMessage:
        box = deps.get_box_or_404(application_config, name)

        if box.transport == "local":
            if payload.new_name:
                try:
                    PathValidator.validate_filename(payload.new_name)
                except ValidationError as exc:
                    raise HTTPException(status_code=400, detail=str(exc)) from exc
            try:
                target = await asyncio.to_thread(
                    _local_place_into,
                    payload.source,
                    payload.destination,
                    payload.new_name,
                    move=False,
                    overwrite=payload.overwrite,
                    allow_dirs=False,
                )
            except HTTPException:
                raise
            except FileNotFoundError as exc:
                raise HTTPException(status_code=404, detail="Source not found") from exc
            except Exception as exc:
                raise HTTPException(status_code=500, detail=str(exc)) from exc
            return APISimpleMessage(status="ok", message="copied", path=target)

        try:
            PathValidator.validate_remote_path(payload.source)
            PathValidator.validate_remote_path(payload.destination)
            if payload.new_name:
                PathValidator.validate_filename(payload.new_name)
        except ValidationError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        ssh_pool = get_pool()
        try:
            async with ssh_pool.connection(
                box, lambda: deps.connect_for_box(box, application_config)
            ) as connection:
                remote_src = await _remote_path(connection, payload.source)
                remote_dest_dir = await _remote_path(connection, payload.destination)
                # Named after the resolved source: `~` alone has no usable name.
                new_name = PathValidator.validate_filename(
                    payload.new_name or PurePosixPath(remote_src).name
                )
                remote_target = str(PurePosixPath(remote_dest_dir) / new_name)
                sftp_client = await connection.start_sftp_client()
                try:
                    await _remote_place(
                        sftp_client,
                        remote_src,
                        remote_target,
                        move=False,
                        overwrite=payload.overwrite,
                        allow_dirs=False,
                    )
                finally:
                    with contextlib.suppress(Exception):
                        sftp_client.exit()
        except (FileNotFoundError, asyncssh.SFTPNoSuchFile) as exc:
            raise HTTPException(status_code=404, detail="Source not found") from exc
        except HTTPException:
            raise
        except ValidationError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except SSHError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        return APISimpleMessage(status="ok", message="copied", path=remote_target)

    @router.get("/boxes/{name}/file", response_model=APIFilePreview)
    async def api_file_preview(
        name: str,
        path: str,
        application_config: AppConfig = Depends(deps.get_application_config),
    ) -> APIFilePreview:
        box = deps.get_box_or_404(application_config, name)

        if box.transport == "local":
            normalized_path = _normalize_local_path(path)
            suffix = Path(normalized_path).suffix.lower()
            image_mime = IMAGE_CONTENT_TYPES.get(suffix)
            image_data: str | None = None
            image_too_large = False
            if image_mime:
                image_bytes, too_large = await _local_read_bytes(
                    normalized_path, MAX_IMAGE_PREVIEW_BYTES
                )
                if too_large:
                    image_too_large = True
                else:
                    image_data = base64.b64encode(image_bytes).decode("ascii")

            text_content: str | None = None
            if not image_mime or image_too_large:
                text_content = await _local_read_text(
                    normalized_path, deps.settings.max_upload_bytes
                )

            parent_dir = str(Path(normalized_path).parent)
            is_markdown = _is_markdown_file(normalized_path)
            syntax_class = _syntax_from_filename(normalized_path)

            return APIFilePreview(
                box=box.name,
                path=normalized_path,
                parent=parent_dir,
                content=text_content or None,
                syntax_class=syntax_class,
                image_data=image_data,
                image_mime=image_mime,
                image_too_large=image_too_large,
                is_markdown=is_markdown,
            )

        try:
            PathValidator.validate_remote_path(path)
        except ValidationError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        ssh_pool = get_pool()
        try:
            async with ssh_pool.connection(
                box, lambda: deps.connect_for_box(box, application_config)
            ) as connection:
                validated_path = await _remote_path(connection, path)
                suffix = Path(validated_path).suffix.lower()
                image_mime = IMAGE_CONTENT_TYPES.get(suffix)
                remote_image_data: str | None = None
                image_too_large = False
                if image_mime:
                    image_bytes, too_large = await _read_file_bytes(
                        connection, validated_path, MAX_IMAGE_PREVIEW_BYTES
                    )
                    if too_large:
                        image_too_large = True
                    else:
                        remote_image_data = base64.b64encode(image_bytes).decode("ascii")

                remote_text_content: str | None = None
                if not image_mime or image_too_large:
                    remote_text_content = await _read_remote_text(
                        connection, validated_path, deps.settings.max_upload_bytes
                    )

                parent_dir = str(PurePosixPath(validated_path).parent)
                is_markdown = _is_markdown_file(validated_path)
                syntax_class = _syntax_from_filename(validated_path)

                return APIFilePreview(
                    box=box.name,
                    path=validated_path,
                    parent=parent_dir,
                    content=remote_text_content or None,
                    syntax_class=syntax_class,
                    image_data=remote_image_data,
                    image_mime=image_mime,
                    image_too_large=image_too_large,
                    is_markdown=is_markdown,
                )
        except HTTPException:
            raise
        except ValidationError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except SSHError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @router.post("/boxes/{name}/write", response_model=APISimpleMessage)
    async def api_write_file(
        request: Request,
        name: str,
        path: str = Body(...),
        content: str = Body(...),
        application_config: AppConfig = Depends(deps.get_application_config),
        _rate_limit: None = Depends(rate_limit_write),
    ) -> APISimpleMessage:
        box = deps.get_box_or_404(application_config, name)

        encoded_len = len(content.encode("utf-8"))
        if encoded_len > deps.settings.max_upload_bytes:
            raise HTTPException(
                status_code=400,
                detail=f"File exceeds {deps.settings.max_upload_bytes // 1024}KB editing limit",
            )

        if box.transport == "local":
            target_path = _normalize_local_path(path)
            try:
                await _local_write_text(target_path, content)
            except FileNotFoundError as exc:
                raise HTTPException(status_code=404, detail="File not found") from exc
            except Exception as exc:
                raise HTTPException(status_code=500, detail=str(exc)) from exc
            return APISimpleMessage(status="ok", message="saved", path=target_path)

        try:
            PathValidator.validate_remote_path(path)
        except ValidationError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        ssh_pool = get_pool()
        try:
            async with ssh_pool.connection(
                box, lambda: deps.connect_for_box(box, application_config)
            ) as connection:
                validated_path = await _remote_path(connection, path)
                sftp_client = await connection.start_sftp_client()
                try:
                    async with await sftp_client.open(
                        validated_path, "w", encoding="utf-8"
                    ) as remote_file:
                        await remote_file.write(content)
                finally:
                    with contextlib.suppress(Exception):
                        sftp_client.exit()
        except HTTPException:
            raise
        except ValidationError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail="File not found") from exc
        except SSHError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

        return APISimpleMessage(status="ok", message="saved", path=validated_path)

    @router.get("/boxes/{name}/download")
    async def api_download_file(
        name: str,
        path: str,
        application_config: AppConfig = Depends(deps.get_application_config),
    ):
        box = deps.get_box_or_404(application_config, name)

        if box.transport == "local":
            normalized_path = _normalize_local_path(path)
            file_path = Path(normalized_path)
            if not file_path.exists() or not file_path.is_file():
                raise HTTPException(status_code=404, detail="File not found")
            return FileResponse(file_path)

        try:
            PathValidator.validate_remote_path(path)
        except ValidationError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        ssh_pool = get_pool()
        try:
            async with ssh_pool.connection(
                box, lambda: deps.connect_for_box(box, application_config)
            ) as connection:
                validated_path = await _remote_path(connection, path)
                content, too_large = await _read_file_bytes(
                    connection, validated_path, deps.settings.max_upload_bytes
                )
                if too_large:
                    raise HTTPException(
                        status_code=413, detail="File too large to download via API"
                    )
        except HTTPException:
            raise
        except ValidationError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except (SSHError, ConnectionError) as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

        filename = PurePosixPath(validated_path).name
        return Response(
            content=content,
            media_type="application/octet-stream",
            headers={"Content-Disposition": _attachment_header(filename)},
        )

    @router.get("/boxes/{name}/view")
    async def api_view_file(
        name: str,
        path: str,
        application_config: AppConfig = Depends(deps.get_application_config),
    ):
        """Serve a file inline with its natural MIME type (for browser rendering)."""
        box = deps.get_box_or_404(application_config, name)

        if box.transport == "local":
            normalized_path = _normalize_local_path(path)
            file_path = Path(normalized_path)
            if not file_path.exists() or not file_path.is_file():
                raise HTTPException(status_code=404, detail="File not found")
            mime, _ = mimetypes.guess_type(normalized_path)
            return FileResponse(
                file_path,
                media_type=mime or "application/octet-stream",
                content_disposition_type="inline",
            )

        try:
            PathValidator.validate_remote_path(path)
        except ValidationError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        ssh_pool = get_pool()
        try:
            async with ssh_pool.connection(
                box, lambda: deps.connect_for_box(box, application_config)
            ) as connection:
                validated_path = await _remote_path(connection, path)
                content, too_large = await _read_file_bytes(
                    connection, validated_path, deps.settings.max_upload_bytes
                )
                if too_large:
                    raise HTTPException(status_code=413, detail="File too large to view")
        except HTTPException:
            raise
        except ValidationError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except (SSHError, ConnectionError) as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

        mime, _ = mimetypes.guess_type(validated_path)
        return Response(
            content=content,
            media_type=mime or "application/octet-stream",
            headers={"Content-Disposition": "inline"},
        )

    @router.get("/boxes/{name}/dir-size")
    async def api_directory_size(
        name: str,
        path: str,
        application_config: AppConfig = Depends(deps.get_application_config),
    ):
        """Get the total size of a directory in bytes."""
        box = deps.get_box_or_404(application_config, name)

        if box.transport == "local":
            normalized = _normalize_local_path(path)

            def _calc_size() -> int:
                total = 0
                base = Path(normalized)
                if not base.is_dir():
                    raise ValueError("Not a directory")
                for child in base.rglob("*"):
                    if child.is_file():
                        try:
                            total += child.stat().st_size
                        except OSError:
                            pass
                return total

            try:
                size = await asyncio.to_thread(_calc_size)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            except Exception as exc:
                raise HTTPException(status_code=500, detail=str(exc)) from exc
            return {"size_bytes": size}

        # Remote
        try:
            PathValidator.validate_remote_path(path)
        except ValidationError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        ssh_pool = get_pool()
        try:
            async with ssh_pool.connection(
                box, lambda: deps.connect_for_box(box, application_config)
            ) as connection:
                validated_path = await _remote_path(connection, path)
                quoted = shlex.quote(validated_path)
                remote_size = await _du_bytes(connection, f"du -sb {quoted}", 1)
                if remote_size is None:
                    # BSD/macOS `du` has no -b; -k works everywhere.
                    remote_size = await _du_bytes(connection, f"du -sk {quoted}", 1024)
                if remote_size is None:
                    exists = await asyncio.wait_for(connection.run(f"test -d {quoted}"), timeout=10)
                    if exists.exit_status != 0:
                        raise HTTPException(status_code=404, detail="Directory not found")
        except HTTPException:
            raise
        except ValidationError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except (SSHError, ConnectionError) as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        except TimeoutError as exc:
            raise HTTPException(status_code=504, detail="Directory size timed out") from exc
        except Exception as exc:
            logger.warning(f"Failed to get dir size: {exc}")
            raise HTTPException(status_code=500, detail=str(exc)) from exc

        # None: both `du` forms failed on a folder that exists; the size is unknown
        # (never 0) and the UI asks.
        return {"size_bytes": remote_size}

    @router.get("/boxes/{name}/download-dir")
    async def api_download_directory(
        name: str,
        path: str,
        application_config: AppConfig = Depends(deps.get_application_config),
    ):
        """Download a directory as a zip file."""
        box = deps.get_box_or_404(application_config, name)

        if box.transport == "local":
            normalized = _normalize_local_path(path)
            base = Path(normalized)
            if not base.is_dir():
                raise HTTPException(status_code=400, detail="Not a directory")

            tmp = tempfile.NamedTemporaryFile(suffix=".zip", delete=False)
            tmp_path = tmp.name
            tmp.close()

            def _create_zip():
                with zipfile.ZipFile(tmp_path, "w", zipfile.ZIP_DEFLATED) as zf:
                    for child in base.rglob("*"):
                        if child.is_file():
                            arcname = str(child.relative_to(base))
                            zf.write(str(child), arcname)

            try:
                await asyncio.to_thread(_create_zip)
            except Exception as exc:
                Path(tmp_path).unlink(missing_ok=True)
                raise HTTPException(status_code=500, detail=str(exc)) from exc

            dirname = base.name or "download"
            return _TempFileResponse(
                tmp_path,
                media_type="application/zip",
                headers={"Content-Disposition": _attachment_header(f"{dirname}.zip")},
            )

        # Remote: stream the zip through SSH into a temp file, capped in size.
        try:
            PathValidator.validate_remote_path(path)
        except ValidationError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        ssh_pool = get_pool()
        tmp = tempfile.NamedTemporaryFile(suffix=".zip", delete=False)
        tmp_path = tmp.name
        tmp.close()
        connection = None
        try:
            try:
                connection = await ssh_pool.acquire(
                    box, lambda: deps.connect_for_box(box, application_config)
                )
                validated_path = await _remote_path(connection, path)
                if validated_path == "/":
                    # No folder name to zip: archive the contents of `/` as "root".
                    dirname, parent, target = "root", "/", "."
                else:
                    dirname = PurePosixPath(validated_path).name
                    parent = PurePosixPath(validated_path).parent.as_posix()
                    target = dirname
                cmd = f"cd {shlex.quote(parent)} && zip -r -q - {shlex.quote(target)}"
                async with asyncio.timeout(ZIP_STREAM_TIMEOUT_SECONDS):
                    await _spool_remote_zip(connection, cmd, tmp_path)
                # Inside the cleanup `try`: a cancel during the release's health check
                # must still delete the zip. `release` closes the connection itself
                # then, so it is no longer ours to release or discard.
                done, connection = connection, None
                await ssh_pool.release(box.name, done)
            except BaseException as exc:
                Path(tmp_path).unlink(missing_ok=True)
                if connection is not None:
                    # A capped, timed-out or cancelled zip may still be running on the
                    # connection: close it rather than pool it.
                    reusable = isinstance(exc, Exception) and not isinstance(
                        exc, (TimeoutError, _ZipTooLargeError)
                    )
                    if reusable:
                        await ssh_pool.release(box.name, connection)
                    else:
                        ssh_pool.discard(box.name, connection)
                raise
        except _ZipTooLargeError as exc:
            raise HTTPException(status_code=413, detail="Directory too large to download") from exc
        except HTTPException:
            raise
        except ValidationError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except (SSHError, ConnectionError) as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        except TimeoutError as exc:
            raise HTTPException(status_code=504, detail="Download timed out") from exc
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

        return _TempFileResponse(
            tmp_path,
            media_type="application/zip",
            headers={"Content-Disposition": _attachment_header(f"{dirname}.zip")},
        )

    @router.get("/boxes/{name}/stat")
    async def api_stat_path(
        name: str,
        path: str,
        application_config: AppConfig = Depends(deps.get_application_config),
    ):
        """Check if a path exists and whether it's a file or directory."""
        box = deps.get_box_or_404(application_config, name)

        if box.transport == "local":
            normalized = _normalize_local_path(path)
            p = Path(normalized)
            if not p.exists():
                return {"exists": False, "is_directory": False, "is_file": False}
            return {
                "exists": True,
                "is_directory": p.is_dir(),
                "is_file": p.is_file(),
            }

        try:
            PathValidator.validate_remote_path(path)
        except ValidationError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        ssh_pool = get_pool()
        try:
            async with ssh_pool.connection(
                box, lambda: deps.connect_for_box(box, application_config)
            ) as connection:
                validated_path = await _remote_path(connection, path)
                sftp_client = await connection.start_sftp_client()
                try:
                    attrs = await sftp_client.stat(validated_path)
                    is_dir = stat_mod.S_ISDIR(attrs.permissions) if attrs.permissions else False
                    is_file = stat_mod.S_ISREG(attrs.permissions) if attrs.permissions else False
                    return {"exists": True, "is_directory": is_dir, "is_file": is_file}
                except (FileNotFoundError, OSError):
                    return {"exists": False, "is_directory": False, "is_file": False}
                finally:
                    with contextlib.suppress(Exception):
                        sftp_client.exit()
        except HTTPException:
            raise
        except ValidationError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except SSHError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @router.post("/boxes/{name}/upload", response_model=APISimpleMessage)
    async def api_upload(
        request: Request,
        name: str,
        directory: str = Form(...),
        file: UploadFile = File(...),
        overwrite: bool = Form(False),
        application_config: AppConfig = Depends(deps.get_application_config),
        _rate_limit: None = Depends(rate_limit_upload),
    ) -> APISimpleMessage:
        box = deps.get_box_or_404(application_config, name)
        candidate_name = (file.filename or "").strip()
        if not candidate_name:
            raise HTTPException(status_code=400, detail="Missing filename")

        try:
            contents = await file.read()
        finally:
            await file.close()

        if len(contents) > deps.settings.max_upload_bytes:
            limit_kb = deps.settings.max_upload_bytes // 1024
            raise HTTPException(status_code=400, detail=f"Upload exceeds {limit_kb} KB limit")

        if box.transport == "local":
            directory_path = _normalize_local_path(directory)
            try:
                target_path = _compose_local_child_path(directory_path, candidate_name)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            try:
                await _local_write_new(
                    target_path, contents, overwrite=overwrite, exists_status=409
                )
            except HTTPException:
                raise
            except Exception as exc:
                raise HTTPException(status_code=500, detail=str(exc)) from exc
            return APISimpleMessage(status="ok", message="uploaded", path=target_path)

        try:
            validated_filename = PathValidator.validate_filename(candidate_name)
        except ValidationError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        ssh_pool = get_pool()
        try:
            async with ssh_pool.connection(
                box, lambda: deps.connect_for_box(box, application_config)
            ) as connection:
                remote_path = _compose_remote_child(
                    await _remote_directory(connection, directory), validated_filename
                )
                sftp_client = await connection.start_sftp_client()
                try:
                    await _remote_write_new(sftp_client, remote_path, contents, overwrite=overwrite)
                finally:
                    with contextlib.suppress(Exception):
                        sftp_client.exit()
        except HTTPException:
            raise
        except SSHError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

        return APISimpleMessage(status="ok", message="uploaded", path=remote_path)

    @router.post("/boxes/{name}/chmod", response_model=APISimpleMessage)
    async def api_chmod(
        name: str,
        payload: APIChmodRequest,
        application_config: AppConfig = Depends(deps.get_application_config),
    ) -> APISimpleMessage:
        box = deps.get_box_or_404(application_config, name)

        try:
            mode = int(payload.mode, 8)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Invalid octal mode") from exc
        if mode < 0 or mode > 0o7777:
            raise HTTPException(status_code=400, detail="Mode out of range")

        if box.transport == "local":
            target = Path(_normalize_local_path(payload.path))
            if not target.exists():
                raise HTTPException(status_code=404, detail="File not found")
            try:
                target.chmod(mode)
            except Exception as exc:
                raise HTTPException(status_code=500, detail=str(exc)) from exc
            return APISimpleMessage(status="ok", message="chmod applied", path=str(target))

        try:
            PathValidator.validate_remote_path(payload.path)
        except ValidationError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        ssh_pool = get_pool()
        try:
            async with ssh_pool.connection(
                box, lambda: deps.connect_for_box(box, application_config)
            ) as connection:
                validated_path = await _remote_path(connection, payload.path)
                await sftp_chmod(connection, validated_path, mode)
        except HTTPException:
            raise
        except ValidationError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except SSHError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

        return APISimpleMessage(status="ok", message="chmod applied", path=validated_path)

    router.include_router(placing)
    return router
