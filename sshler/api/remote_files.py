"""The files of a remote box: ``RemoteFiles``, placing files over SFTP, and the size and
zip of a remote folder."""

from __future__ import annotations

import asyncio
import contextlib
import errno
import logging
import mimetypes
import os
import posixpath
import secrets
import stat as stat_mod
import tempfile
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from pathlib import Path, PurePosixPath
from types import TracebackType

import asyncssh
from fastapi.responses import Response

from .. import state
from ..config import Box
from ..ssh import sftp_chmod, sftp_list_directory, sftp_read_file, sftp_session
from ..ssh_pool import get_pool
from ..validation import PathValidator, ValidationError
from .failure import (
    REASON_FAILED,
    REASON_INVALID,
    REASON_NOT_FOUND,
    REASON_TOO_LARGE,
    REASON_UNREACHABLE,
    Failure,
    failure_entry,
    failure_of,
)
from .file_contract import (
    MISSING_FILE,
    MISSING_FOLDER,
    MISSING_SOURCE,
    NOT_A_FOLDER,
    TAR_SUFFIXES,
    BatchOutcome,
    FolderZip,
    PathKind,
    ReadChunk,
    SourceFile,
    SpelledPaths,
    archive_folder_name,
    is_archive_name,
)
from .helpers import _attachment_header
from .models import APIDirectoryEntry
from .placement import (
    _NO_FOLDER_COPY,
    _ONLY_FILES,
    COPY_CHUNK_BYTES,
    NameTakenError,
    _reason,
    _refuse_a_folder_into_itself,
    _refuse_or_replace,
    _taken_after_check,
    candidate_paths,
    ensure_source_unchanged,
    kept_permission_bits,
    refuse_a_name_this_request_placed,
    refuse_taken_entries,
    run_to_end,
    source_not_removed,
    temp_stem,
    unpacked_not_removed,
)
from .remote import (
    and_then,
    command,
    compose_remote_child,
    in_directory,
    path_word,
    remote_directory,
    remote_home,
    remote_path,
)

logger = logging.getLogger(__name__)

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
    """Only "no such file" counts as free; any other failure (permission denied) goes
    on as raised, since the name cannot be shown to be free."""
    try:
        attrs = await sftp_client.lstat(path)
    except asyncssh.SFTPNoSuchFile:
        return None
    return attrs.permissions or 0


def remote_fingerprint(attrs) -> tuple:
    """What ``ensure_source_unchanged`` compares for a remote file: size and
    modification time. SFTP has no inode, and on a server that sends no finer time the
    modification time is in whole seconds."""
    return attrs.size, attrs.mtime, attrs.mtime_ns


async def _remote_exclusive[T](
    sftp_client,
    target: str,
    call: Awaitable[T],
    *,
    source_regular: bool = True,
    keep_both: bool = False,
) -> T:
    """Await ``call``, which fails when ``target`` exists; failing with the name taken
    is the rule's refusal (SFTPv3 has no "exists" status: OpenSSH says FX_FAILURE), or
    under ``keep_both`` ``NameTakenError``, for the caller to try the next name."""
    try:
        return await call
    except asyncssh.SFTPError:
        mode = await _remote_lstat_mode(sftp_client, target)
        if mode is not None:
            _taken_after_check(mode, source_regular=source_regular, keep_both=keep_both)
        raise


async def _remote_take_free_name[T](
    target: str,
    take: Callable[[str], Awaitable[T]],
    *,
    keep_both: bool,
    folder: bool,
) -> T:
    """``take(candidate)``, which takes its name with the exclusive calls above, on
    ``target``; with ``keep_both`` on each next name of ``candidate_paths`` while it
    answers ``NameTakenError``."""
    for candidate in candidate_paths(target, keep_both=keep_both, folder=folder, paths=posixpath):
        try:
            return await take(candidate)
        except NameTakenError:
            continue
    raise AssertionError("unreachable: candidate_paths ends with a refusal")


def _remote_temp_beside(target: str, suffix: str = ".sshler") -> str:
    parent, name = posixpath.split(target)
    return posixpath.join(parent, f".{temp_stem(name)}.{secrets.token_hex(6)}{suffix}")


class _RemoteLanding:
    """Remote twin of ``_LocalLanding``: a free name is opened "xb"; a replace writes a
    temp file beside the target and posix-renames it over on ``commit``.

    The file gets ``mode``'s permission bits: the source's on a free name, the replaced
    file's on a replace. A free name is set exactly before the first byte (the server's
    umask narrows what the open asks for). Without ``mode`` a free name gets the
    server's default bits."""

    def __init__(self, sftp_client, target: str, temp: str | None, handle) -> None:
        self.sftp = sftp_client
        self.target = target
        self.path = target  # where the file lands
        self.temp = temp
        self.handle = handle
        self.keep: int | None = None  # a replace's bits, set exactly (the umask narrows an open)

    @classmethod
    async def start(
        cls,
        sftp_client,
        target: str,
        *,
        replace: bool,
        source_regular: bool = True,
        mode=None,
        keep_both: bool = False,
    ) -> _RemoteLanding:
        # A mode of 0 is a server that sent none; a file with no bits set still has its
        # type bits, and keeps none.
        exact = kept_permission_bits(mode) if mode else None
        attrs = asyncssh.SFTPAttrs() if exact is None else asyncssh.SFTPAttrs(permissions=exact)
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
            keep_both=keep_both,
        )
        if exact is not None:
            try:
                await _set_bits_exactly(handle.chmod(exact))
            except BaseException:
                with contextlib.suppress(Exception):
                    await handle.close()
                await run_to_end(_remove_quietly(sftp_client, target))
                raise
        return cls(sftp_client, target, None, handle)

    async def write(self, data: bytes) -> None:
        await self.handle.write(data)

    async def commit(self) -> None:
        await self.handle.close()
        if self.temp is None:
            return
        if self.keep is not None:
            await _set_bits_exactly(self.sftp.chmod(self.temp, self.keep))
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
                raise Failure(
                    REASON_FAILED,
                    f"Could not replace {self.target}: {_reason(exc)}; "
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


async def _set_bits_exactly(chmod) -> None:
    """Await the chmod of a file this landing has just opened with the same bits. A
    server without the operation leaves the bits its open gave: the ones asked for,
    narrowed by the server's umask, so never wider. Any other failure is the caller's."""
    with contextlib.suppress(asyncssh.SFTPOpUnsupported):
        await chmod


async def _remove_quietly(sftp_client, path: str) -> None:
    with contextlib.suppress(asyncssh.SFTPError):
        await sftp_client.remove(path)


async def _remote_landing(
    sftp_client,
    target: str,
    *,
    overwrite: bool,
    source_regular: bool = True,
    source_mode: int | None = None,
    keep_both: bool = False,
) -> _RemoteLanding:
    """Decide ``target`` by the rule and open it for the bytes of a file; with
    ``keep_both`` a taken name gives the first free name of its series."""
    dest_mode = await _remote_lstat_mode(sftp_client, target)
    replace = _refuse_or_replace(
        dest_mode, overwrite=overwrite, source_regular=source_regular, keep_both=keep_both
    )
    if not source_regular:
        raise Failure(REASON_INVALID, _ONLY_FILES)
    if replace:
        return await _RemoteLanding.start(sftp_client, target, replace=True, mode=dest_mode)
    return await _remote_take_free_name(
        target,
        lambda candidate: _RemoteLanding.start(
            sftp_client,
            candidate,
            replace=False,
            source_regular=source_regular,
            mode=source_mode,
            keep_both=keep_both,
        ),
        keep_both=keep_both,
        folder=False,
    )


async def _remote_copy_file(
    sftp_client, source: str, target: str, *, replace: bool, mode: int, keep_both: bool = False
) -> None:
    """Copy a remote file to the same server in chunks, never whole into memory.
    ``mode``: the source's on a free name, the replaced file's on a replace."""
    async with await sftp_client.open(source, "rb") as src:
        landing = await _RemoteLanding.start(
            sftp_client, target, replace=replace, mode=mode, keep_both=keep_both
        )
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


async def _remote_copy_folder_new(
    sftp_client, source: str, target: str, *, keep_both: bool = False
) -> None:
    """Remote twin of ``_local_copy_folder_new``."""
    await _remote_exclusive(
        sftp_client, target, sftp_client.mkdir(target), source_regular=False, keep_both=keep_both
    )
    try:
        await _remote_copy_tree(sftp_client, source, target)
    except Exception as exc:
        cause = failure_of(exc)
        detail = f"Could not copy {source} to {target}: {cause.detail}"
        try:
            await run_to_end(_remote_remove_tree(sftp_client, target))
        except Exception as cleanup:
            detail += (
                f"; the partial copy at {target} could not be removed: {failure_of(cleanup).detail}"
            )
        raise Failure(cause.reason, detail) from exc


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
        raise source_not_removed(target, exc.reason) from exc


async def _remote_move_new(
    sftp_client, source: str, target: str, source_attrs, *, keep_both: bool = False
) -> None:
    """Move to a free name; a name taken by now is the rule's refusal, or under
    ``keep_both`` ``NameTakenError``."""
    source_mode = source_attrs.permissions or 0
    before = remote_fingerprint(source_attrs)
    if stat_mod.S_ISDIR(source_mode):
        await _remote_exclusive(
            sftp_client,
            target,
            sftp_client.mkdir(target),
            source_regular=False,
            keep_both=keep_both,
        )
        try:
            await sftp_client.posix_rename(source, target)  # over the empty folder above
        except asyncssh.SFTPOpUnsupported:
            # No posix-rename: the server's own rename decides about the name.
            await sftp_client.rmdir(target)
            await _remote_exclusive(
                sftp_client,
                target,
                sftp_client.rename(source, target),
                source_regular=False,
                keep_both=keep_both,
            )
        except asyncssh.SFTPError:
            with contextlib.suppress(asyncssh.SFTPError):
                await sftp_client.rmdir(target)
            raise
        return
    if stat_mod.S_ISLNK(source_mode):
        link_text = await sftp_client.readlink(source)
        await _remote_exclusive(
            sftp_client,
            target,
            sftp_client.symlink(link_text, target),
            source_regular=False,
            keep_both=keep_both,
        )
        await _remote_remove_moved_source(sftp_client, source, target, None)
        return
    try:
        await sftp_client.link(source, target)
    except asyncssh.SFTPError:
        mode = await _remote_lstat_mode(sftp_client, target)
        if mode is not None:
            _taken_after_check(mode, keep_both=keep_both)
        # No hard link on this server (or across its filesystems): copy into a name
        # opened exclusively, then remove the source if it is still what was copied.
        await _remote_copy_file(
            sftp_client, source, target, replace=False, mode=source_mode, keep_both=keep_both
        )
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


async def _remote_refuse_home_or_above(connection, sftp_client, paths: Sequence[str]) -> None:
    """Refuse the whole batch (400, nothing touched) when a resolved path is the home
    folder or above it, or is a link to either."""
    home = await remote_home(connection)
    for path in paths:
        names = [path]
        with contextlib.suppress(asyncssh.SFTPError):  # a missing path fails on its own later
            if stat_mod.S_ISLNK((await sftp_client.lstat(path)).permissions or 0):
                names.append(str(await sftp_client.realpath(path)))
        for name in names:
            try:
                PathValidator.refuse_home_or_ancestor(name, home)
            except ValidationError as exc:
                raise Failure(REASON_INVALID, str(exc)) from exc


async def _remote_realpath_of_missing(sftp_client, folder: str) -> str:
    """The real path of ``folder``, which may not exist yet: that of its nearest folder
    that does, with the missing names after it."""
    try:
        return str(await sftp_client.realpath(folder))
    except asyncssh.SFTPError:
        pass
    rest: list[str] = []
    while (
        await _remote_lstat_mode(sftp_client, folder) is None
        and posixpath.dirname(folder) != folder
    ):
        folder, name = posixpath.split(folder)
        rest.append(name)
    return posixpath.join(str(await sftp_client.realpath(folder)), *reversed(rest))


async def _remote_place(
    sftp_client,
    source: str,
    target: str,
    *,
    move: bool,
    overwrite: bool,
    allow_dirs: bool = True,
    make_parents: bool = False,
    keep_both: bool = False,
) -> str:
    """Remote twin of ``_local_place``; returns where it landed. A missing source raises
    ``asyncssh.SFTPNoSuchFile``. With ``make_parents``, a missing folder of ``target``
    is made once the source is known and not refused into itself, and before any other
    refusal, as the local box does."""
    folder = posixpath.dirname(target)
    if make_parents and not await _remote_is_folder(sftp_client, folder):
        found = await (sftp_client.lstat(source) if move else sftp_client.stat(source))
        if stat_mod.S_ISDIR(found.permissions or 0) and posixpath.dirname(
            source
        ) != posixpath.dirname(target):
            _refuse_a_folder_into_itself(
                await sftp_client.realpath(source),
                await _remote_realpath_of_missing(sftp_client, folder),
                posixpath.commonpath,
            )
        await _remote_make_missing(sftp_client, folder, keep=True)
    return await _remote_place_in_folder(
        sftp_client,
        source,
        target,
        move=move,
        overwrite=overwrite,
        allow_dirs=allow_dirs,
        keep_both=keep_both,
    )


async def _remote_place_in_folder(
    sftp_client,
    source: str,
    target: str,
    *,
    move: bool,
    overwrite: bool,
    allow_dirs: bool,
    keep_both: bool = False,
) -> str:
    """``_remote_place`` into a folder that is there. A move onto the same path is
    nothing to do (SFTP gives no inode, so another spelling of the same file is a taken
    name, except a name that differs only in letter case on a server that ignores
    case)."""
    source_attrs = await (sftp_client.lstat(source) if move else sftp_client.stat(source))
    source_mode = source_attrs.permissions or 0
    if move and source == target:
        return target
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
        return target
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
        keep_both=keep_both,
    )
    if stat_mod.S_ISDIR(source_mode) and not allow_dirs:
        raise Failure(REASON_INVALID, _NO_FOLDER_COPY)
    if replace and move:
        assert dest_mode is not None
        if not await _remote_replace_by_rename(sftp_client, source, target, source_mode, dest_mode):
            await _remote_copy_file(sftp_client, source, target, replace=True, mode=dest_mode)
            await _remote_remove_moved_source(
                sftp_client, source, target, remote_fingerprint(source_attrs)
            )
        return target
    if replace:
        assert dest_mode is not None
        await _remote_copy_file(sftp_client, source, target, replace=True, mode=dest_mode)
        return target

    async def take(candidate: str) -> str:
        if move:
            await _remote_move_new(
                sftp_client, source, candidate, source_attrs, keep_both=keep_both
            )
        elif stat_mod.S_ISDIR(source_mode):
            await _remote_copy_folder_new(sftp_client, source, candidate, keep_both=keep_both)
        else:
            await _remote_copy_file(
                sftp_client, source, candidate, replace=False, mode=source_mode, keep_both=keep_both
            )
        return candidate

    return await _remote_take_free_name(
        target, take, keep_both=keep_both, folder=stat_mod.S_ISDIR(source_mode)
    )


async def _remote_into_missing_folder[T](
    sftp_client, folder: str, attempt: Callable[[], Awaitable[T]]
) -> T:
    """``attempt()``, a write into ``folder``. When it fails because a folder is not
    there, the missing folders are made and it runs once more; a folder that is there
    costs no extra call."""
    try:
        return await attempt()
    except (asyncssh.SFTPNoSuchFile, asyncssh.SFTPNoSuchPath):
        if await _remote_lstat_mode(sftp_client, folder) is not None:
            raise
        await _remote_make_missing(sftp_client, folder, keep=True)
        return await attempt()


async def _remote_write_new(
    sftp_client, remote_path: str, data: bytes, *, overwrite: bool, keep_both: bool = False
) -> str:
    """Write ``data`` to a new remote file, or replace a regular file with ``overwrite``
    through a landing: the old file stays whole until the new one is complete. With
    ``keep_both`` a taken name gives the first free name of its series. Returns where
    the file landed."""
    landing = await _remote_into_missing_folder(
        sftp_client,
        posixpath.dirname(remote_path),
        lambda: _remote_landing(sftp_client, remote_path, overwrite=overwrite, keep_both=keep_both),
    )
    try:
        await landing.write(data)
        await landing.commit()
    except BaseException:
        await landing.abort()
        raise
    return landing.path


def du_command(flag: str, path: str) -> str:
    """``du <flag> <path>``: the total size of a remote folder."""
    return command("du", flag, path_word(path))


def directory_test_command(path: str) -> str:
    """Succeeds when ``path`` is a folder on the remote."""
    return command("test", "-d", path_word(path))


def folder_zip_command(path: str) -> tuple[str, str]:
    """``(zip name, command)`` that writes a zip of the remote folder ``path`` to
    stdout, with the folder's own name as the top of every entry. ``/`` has no name:
    its contents are zipped as "root"."""
    if path == "/":
        dirname, parent, target = "root", "/", "."
    else:
        dirname = PurePosixPath(path).name
        parent = PurePosixPath(path).parent.as_posix()
        target = dirname
    return dirname, in_directory(parent, command("zip", "-r", "-q", "-", path_word(target)))


# An archive made or unpacked on a remote box runs at most this long.
ARCHIVE_TIMEOUT = 120


def archive_create_command(fmt: str, archive_target: str, paths: list[str]) -> str:
    """The remote command that packs ``paths`` into ``archive_target`` (``fmt`` is
    ``tar.gz``, ``tgz`` or ``zip``); relative paths are taken from ``/``."""
    sources = [path_word(p) for p in paths]
    if fmt in ("tar.gz", "tgz"):
        return command("tar", "czf", path_word(archive_target), "-C", "/", *sources)
    return and_then(command("cd", "/"), command("zip", "-r", path_word(archive_target), *sources))


async def _run_archive_command(connection, cmd: str, what: str) -> None:
    """Run a ``tar`` or ``zip`` command within ``ARCHIVE_TIMEOUT``. Past it the command
    may still run on the connection, so a ``TimeoutError`` leaves the block (the pool
    closes the connection of a block that ends so) and answers 504 with ``what``."""
    try:
        await asyncio.wait_for(connection.run(cmd, check=True), timeout=ARCHIVE_TIMEOUT)
    except TimeoutError as exc:
        raise TimeoutError(f"{what} timed out") from exc


def archive_extract_command(archive: str, destination: str) -> str:
    """The remote command that unpacks ``archive`` (by its name, a tar.gz, .tgz or
    .zip) into ``destination``, an empty folder (a later zip entry of a name replaces
    an earlier one, as on the local box)."""
    if PurePosixPath(archive).name.lower().endswith(TAR_SUFFIXES):
        return command("tar", "xzf", path_word(archive), "-C", path_word(destination))
    return command("unzip", "-o", path_word(archive), "-d", path_word(destination))


async def _remote_make_missing(sftp_client, folder: str, *, keep: bool = False) -> list[str]:
    """Remote twin of ``_local_make_missing``: ``folder`` and every missing folder above
    it, the made ones returned. A failure part way removes them again unless ``keep``
    (a write leaves what it made, as ``mkdir -p`` does on the local box). A folder made
    by another request meanwhile is used as it stands."""
    missing: list[str] = []
    while (
        posixpath.dirname(folder) != folder
        and await _remote_lstat_mode(sftp_client, folder) is None
    ):
        missing.append(folder)
        folder = posixpath.dirname(folder)
    made: list[str] = []
    try:
        for path in reversed(missing):
            try:
                await sftp_client.mkdir(path)
            except asyncssh.SFTPError:
                if await _remote_is_folder(sftp_client, path):
                    continue  # made by another request meanwhile
                raise
            else:
                made.append(path)
    except BaseException:
        if not keep:
            await run_to_end(_remote_remove_made(sftp_client, made))
        raise
    return made


async def _remote_is_folder(sftp_client, path: str) -> bool:
    try:
        return stat_mod.S_ISDIR((await sftp_client.stat(path)).permissions or 0)
    except asyncssh.SFTPError:
        return False


async def _remote_remove_made(sftp_client, made: list[str]) -> None:
    """Remote twin of ``_local_remove_made``."""
    for path in reversed(made):
        with contextlib.suppress(asyncssh.SFTPError):
            await sftp_client.rmdir(path)


async def _remote_plan(
    sftp_client,
    unpacked: str,
    dest: str,
    moves: list[tuple[str, str]],
    taken: list[tuple[str, int, bool]],
    shown: str = "",
) -> None:
    """Remote twin of ``_local_plan``: the names and kinds are the server's listing
    of the unpacked folder (a link is not followed)."""
    found = [e for e in await sftp_client.readdir(unpacked) if e.filename not in (".", "..")]
    for entry in sorted(found, key=lambda e: e.filename):
        name = entry.filename
        source, target = posixpath.join(unpacked, name), posixpath.join(dest, name)
        source_mode = entry.attrs.permissions or 0
        dest_mode = await _remote_lstat_mode(sftp_client, target)
        if dest_mode is not None and stat_mod.S_ISDIR(source_mode) and stat_mod.S_ISDIR(dest_mode):
            await _remote_plan(sftp_client, source, target, moves, taken, f"{shown}{name}/")
            continue
        if dest_mode is not None:
            taken.append((f"{shown}{name}", dest_mode, stat_mod.S_ISREG(source_mode)))
        moves.append((source, target))


async def _du_bytes(connection, du_line: str, multiplier: int) -> int | None:
    """Run a ``du`` command and return its total times ``multiplier``, or None.

    ``du`` exits non-zero when a subfolder is unreadable but still prints the
    total, so the printed number decides, not the exit status.
    """
    result = await asyncio.wait_for(connection.run(du_line), timeout=30)
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
    connection afterwards. A non-zero exit is ``failed`` with stderr.
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
        raise Failure(REASON_FAILED, f"zip failed: {message}")


async def _read_file_bytes(
    connection: asyncssh.SSHClientConnection, path: str, limit: int
) -> tuple[bytes, bool]:
    async with sftp_session(connection) as sftp_client:
        async with await sftp_client.open(path, "rb") as remote_file:
            data_read = await remote_file.read(limit + 1)
            # Opened with 'rb' so data should be bytes
            data = data_read if isinstance(data_read, bytes) else data_read.encode()
    too_large = len(data) > limit
    if too_large:
        return b"", True
    return data, False


async def _read_remote_text(connection: asyncssh.SSHClientConnection, path: str, limit: int) -> str:
    """Retrieve UTF-8 text from an SFTP connection with graceful fallback."""

    try:
        return await sftp_read_file(connection, path, max_bytes=limit)
    except TypeError as exc:
        if "max_bytes" not in str(exc):
            raise
        return await sftp_read_file(connection, path)


def remove_tree_command(path: str) -> str:
    """The remote command that deletes the folder ``path`` and everything in it."""
    return command("rm", "-rf", path_word(path))


async def _resolve_tilde_paths(
    connection, validated: list[tuple[str, str]], failed: list[dict]
) -> list[tuple[str, str]]:
    """Resolve ``~`` in each validated path against the remote home. SFTP and the
    quoted shell commands do not expand it. A path that no longer validates is
    reported in ``failed``; a failed home lookup aborts the batch."""
    resolved: list[tuple[str, str]] = []
    for orig, vpath in validated:
        try:
            resolved.append((orig, await remote_path(connection, vpath)))
        except ValidationError as exc:
            failed.append(failure_entry(orig, exc))
    return resolved


async def _resolve_tilde_destination(connection, destination: str) -> str:
    try:
        return await remote_path(connection, destination)
    except ValidationError as exc:
        raise Failure(REASON_INVALID, f"Invalid destination: {exc}") from exc


class RemoteFiles:
    """The files of a remote box, over one pooled connection.

    Used as ``async with``: the connection is taken from the pool by the first
    operation that needs it, so the checks that come first cost no round trip, and it
    is given back when the block ends (closed instead after a timeout or a cancel, as
    ``ssh_pool.connection`` decides). The operations that work on an SFTP client share
    one session, opened when first needed and closed before the connection goes back.
    The listing, chmod and the reads open their own session through ``sshler.ssh``,
    one per call. A folder zip takes a connection of its own (see ``zip_folder``).
    """

    def __init__(
        self, box: Box, connect: Callable[[], Awaitable[asyncssh.SSHClientConnection]]
    ) -> None:
        self._box = box
        self._connect = connect
        self._stack = contextlib.AsyncExitStack()
        self._connection: asyncssh.SSHClientConnection | None = None
        self._sftp: asyncssh.SFTPClient | None = None
        self._after: list[Callable[[], Awaitable[object]]] = []
        self._spelled = SpelledPaths("/")

    async def __aenter__(self) -> RemoteFiles:
        await self._stack.__aenter__()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool | None:
        return await self._stack.__aexit__(exc_type, exc, traceback)

    async def after_release(self) -> None:
        """What waits for the connection to be back in the pool (the visit a listing
        records), after a block that ended without a failure."""
        for step in self._after:
            await step()

    async def _conn(self) -> asyncssh.SSHClientConnection:
        if self._connection is None:
            self._connection = await self._stack.enter_async_context(
                get_pool().connection(self._box, self._connect)
            )
        return self._connection

    async def _session(self) -> asyncssh.SFTPClient:
        if self._sftp is None:
            self._sftp = await self._stack.enter_async_context(sftp_session(await self._conn()))
        return self._sftp

    @contextlib.asynccontextmanager
    async def _needs(self, path: str, *, folder: bool) -> AsyncIterator[None]:
        """SFTP version 3 has no code for a folder where a file is needed or the reverse:
        the server says "no such file" or a bare failure. When the block fails so, the
        kind of ``path`` is asked for, and a folder where a file is needed fails as the
        local box's ``IsADirectoryError``, a file where a folder is needed as
        ``NOT_A_FOLDER``; any other failure goes on as raised."""
        try:
            yield
        except (asyncssh.SFTPNoSuchFile, asyncssh.SFTPFailure) as exc:
            try:
                attrs = await (await self._session()).stat(path)
            except asyncssh.SFTPError:
                raise exc from None
            is_folder = stat_mod.S_ISDIR(attrs.permissions or 0)
            if folder and not is_folder:
                raise Failure(REASON_INVALID, NOT_A_FOLDER) from exc
            if is_folder and not folder:
                raise IsADirectoryError(errno.EISDIR, os.strerror(errno.EISDIR), path) from exc
            raise

    # --- paths -----------------------------------------------------------------------

    async def path(self, path: str) -> str:
        PathValidator.validate_remote_path(path)
        return self._spelled.note(await remote_path(await self._conn(), path), path)

    async def folder(self, directory: str) -> str:
        # `~` is the remote home, as for the folder zip (not `/`).
        PathValidator.validate_remote_path(directory)
        resolved = await remote_directory(await self._conn(), directory)
        return self._spelled.note(resolved, directory)

    async def child(self, directory: str, name: str) -> str:
        validated = PathValidator.validate_filename(name)
        connection = await self._conn()
        return compose_remote_child(await remote_directory(connection, directory), validated)

    def parent(self, path: str) -> str:
        return str(PurePosixPath(path).parent)

    def check_path(self, path: str) -> str:
        PathValidator.validate_remote_path(path)
        return path

    async def resolve(self, checked: str) -> str:
        return self._spelled.note(await remote_path(await self._conn(), checked), checked)

    def name(self, path: str) -> str:
        return PurePosixPath(path).name

    def join(self, folder: str, name: str) -> str:
        return str(PurePosixPath(folder) / name)

    def as_sent(self, path: str) -> str | None:
        return self._spelled.as_sent(path)

    async def prepare(self) -> None:
        await self._session()

    # --- reading ---------------------------------------------------------------------

    async def list_folder(self, directory: str) -> list[APIDirectoryEntry]:
        """The visit to ``directory`` is recorded for the frecency search once the
        connection is back in the pool."""
        async with self._needs(directory, folder=True):
            raw_entries = await sftp_list_directory(await self._conn(), directory)
        entries = [
            APIDirectoryEntry(
                name=str(entry["name"]),
                path=posixpath.join(directory, str(entry["name"])),
                is_directory=bool(entry.get("is_directory")),
                size=int(str(entry["size"])) if entry.get("size") is not None else None,
                modified=float(str(entry["modified"]))
                if entry.get("modified") is not None
                else None,
                mode=int(str(entry["mode"])) if entry.get("mode") is not None else None,
            )
            for entry in raw_entries
        ]
        box_name = self._box.name
        self._after.append(lambda: state.record_directory_visit_async(box_name, directory))
        return entries

    async def stat(self, path: str) -> PathKind:
        sftp_client = await self._session()
        try:
            attrs = await sftp_client.stat(path)
        except (asyncssh.SFTPNoSuchFile, asyncssh.SFTPNoSuchPath):
            return PathKind(exists=False, is_directory=False, is_file=False)
        perms = attrs.permissions
        return PathKind(
            exists=True,
            is_directory=stat_mod.S_ISDIR(perms) if perms else False,
            is_file=stat_mod.S_ISREG(perms) if perms else False,
        )

    async def read_bytes(self, path: str, limit: int) -> tuple[bytes, bool]:
        """A file larger than ``limit`` gives ``(b"", True)``."""
        async with self._needs(path, folder=False):
            return await _read_file_bytes(await self._conn(), path, limit)

    async def read_text(self, path: str, limit: int) -> str:
        async with self._needs(path, folder=False):
            return await _read_remote_text(await self._conn(), path, limit)

    async def serve(self, path: str, *, inline: bool, limit: int) -> Response:
        """Read whole into memory, so a file larger than ``limit`` is a 413."""
        async with self._needs(path, folder=False):
            content, too_large = await _read_file_bytes(await self._conn(), path, limit)
        if too_large:
            detail = "File too large to view" if inline else "File too large to download via API"
            raise Failure(REASON_TOO_LARGE, detail)
        if inline:
            mime, _ = mimetypes.guess_type(path)
            return Response(
                content=content,
                media_type=mime or "application/octet-stream",
                headers={"Content-Disposition": "inline"},
            )
        return Response(
            content=content,
            media_type="application/octet-stream",
            headers={"Content-Disposition": _attachment_header(PurePosixPath(path).name)},
        )

    async def folder_size(self, path: str) -> int | None:
        """None: both ``du`` forms failed on a folder that exists; the size is unknown
        (never 0) and the UI asks."""
        connection = await self._conn()
        size = await _du_bytes(connection, du_command("-sb", path), 1)
        if size is None:
            # BSD/macOS `du` has no -b; -k works everywhere.
            size = await _du_bytes(connection, du_command("-sk", path), 1024)
        if size is None:
            exists = await asyncio.wait_for(
                connection.run(directory_test_command(path)), timeout=10
            )
            if exists.exit_status != 0:
                raise Failure(REASON_NOT_FOUND, MISSING_FOLDER)
        return size

    async def zip_folder(self, path: str) -> FolderZip:
        """The zip streams through SSH into a temp file, capped in size and time. It
        holds a connection of its own rather than the block's: a zip that was cut off
        (too large, timed out, cancelled) may still run on it, so it is closed rather
        than pooled; any other failure gives it back."""
        PathValidator.validate_remote_path(path)
        ssh_pool = get_pool()
        tmp = tempfile.NamedTemporaryFile(suffix=".zip", delete=False)
        tmp_path = tmp.name
        tmp.close()
        connection = None
        try:
            connection = await ssh_pool.acquire(self._box, self._connect)
            validated_path = await remote_path(connection, path)
            dirname, cmd = folder_zip_command(validated_path)
            async with asyncio.timeout(ZIP_STREAM_TIMEOUT_SECONDS):
                await _spool_remote_zip(connection, cmd, tmp_path)
            # Inside the cleanup `try`: a cancel during the release's health check
            # must still delete the zip. `release` closes the connection itself
            # then, so it is no longer ours to release or discard.
            done, connection = connection, None
            await ssh_pool.release(self._box.name, done)
        except BaseException as exc:
            Path(tmp_path).unlink(missing_ok=True)
            if connection is not None:
                reusable = isinstance(exc, Exception) and not isinstance(
                    exc, (TimeoutError, _ZipTooLargeError)
                )
                if reusable:
                    await ssh_pool.release(self._box.name, connection)
                else:
                    ssh_pool.discard(self._box.name, connection)
            if isinstance(exc, _ZipTooLargeError):
                raise Failure(REASON_TOO_LARGE, "Directory too large to download") from exc
            raise
        return FolderZip(path=tmp_path, name=dirname)

    async def source(self, path: str) -> SourceFile:
        attrs = await (await self._session()).stat(path)
        return SourceFile(attrs.permissions or 0, attrs.size or 0, remote_fingerprint(attrs))

    @contextlib.asynccontextmanager
    async def open_read(self, path: str) -> AsyncIterator[ReadChunk]:
        sftp_client = await self._session()
        async with await sftp_client.open(path, "rb") as handle:
            yield handle.read

    # --- writing ---------------------------------------------------------------------

    async def write_text(self, path: str, content: str) -> None:
        sftp_client = await self._session()
        async with self._needs(path, folder=False):
            opened = await _remote_into_missing_folder(
                sftp_client,
                posixpath.dirname(path),
                lambda: sftp_client.open(path, "w", encoding="utf-8"),
            )
            async with opened as remote_file:
                await remote_file.write(content)

    async def touch(self, path: str) -> None:
        """A name that exists is a 409."""
        await _remote_write_new(await self._session(), path, b"", overwrite=False)

    async def upload(
        self, path: str, data: bytes, *, overwrite: bool, keep_both: bool = False
    ) -> str:
        return await _remote_write_new(
            await self._session(), path, data, overwrite=overwrite, keep_both=keep_both
        )

    async def make_folder(self, path: str) -> None:
        sftp_client = await self._session()
        try:
            await sftp_client.stat(path)
        except Exception:
            pass  # free, or the mkdir below fails and says why
        else:
            raise Failure(REASON_INVALID, "Directory already exists")
        await sftp_client.mkdir(path)

    async def land(
        self, path: str, *, source_mode: int, overwrite: bool, keep_both: bool = False
    ) -> _RemoteLanding:
        """The missing folders of ``path`` are made first, all of them."""
        sftp_client = await self._session()
        return await _remote_into_missing_folder(
            sftp_client,
            posixpath.dirname(path),
            lambda: _remote_landing(
                sftp_client,
                path,
                overwrite=overwrite,
                source_regular=stat_mod.S_ISREG(source_mode),
                source_mode=source_mode,
                keep_both=keep_both,
            ),
        )

    async def remove_if_unchanged(self, path: str, fingerprint: tuple, target: str) -> None:
        sftp_client = await self._session()
        after = remote_fingerprint(await sftp_client.stat(path))
        ensure_source_unchanged(fingerprint, after, target)
        await sftp_client.remove(path)

    async def delete(self, path: str) -> str:
        PathValidator.validate_remote_path(path)
        connection = await self._conn()
        target = self._spelled.note(await remote_path(connection, path), path)
        sftp_client = await self._session()
        await _remote_refuse_home_or_above(connection, sftp_client, [target])
        async with self._needs(target, folder=False):
            await sftp_client.remove(target)  # a link is removed, not followed
        return target

    async def chmod(self, path: str, mode: int) -> None:
        await sftp_chmod(await self._conn(), path, mode)

    async def rename(
        self, path: str, new_name: str, *, overwrite: bool, keep_both: bool = False
    ) -> str:
        PathValidator.validate_remote_path(path)
        new_name = PathValidator.validate_filename(new_name)
        connection = await self._conn()
        source = self._spelled.note(await remote_path(connection, path), path)
        sftp_client = await self._session()
        await _remote_refuse_home_or_above(connection, sftp_client, [source])
        target = str(PurePosixPath(source).parent / new_name)
        return await _remote_place(
            sftp_client,
            source,
            target,
            move=True,
            overwrite=overwrite,
            keep_both=keep_both,
        )

    async def place_into(
        self,
        source: str,
        destination: str,
        new_name: str | None,
        *,
        move: bool,
        overwrite: bool,
        allow_dirs: bool,
        keep_both: bool = False,
    ) -> str:
        PathValidator.validate_remote_path(source)
        PathValidator.validate_remote_path(destination)
        if new_name:
            PathValidator.validate_filename(new_name)
        connection = await self._conn()
        remote_src = self._spelled.note(await remote_path(connection, source), source)
        remote_dest_dir = self._spelled.note(
            await remote_path(connection, destination), destination
        )
        sftp_client = await self._session()
        if move:
            await _remote_refuse_home_or_above(connection, sftp_client, [remote_src])
        name = new_name or PurePosixPath(remote_src).name
        if not move:
            # Named after the resolved source: `~` alone has no usable name.
            name = PathValidator.validate_filename(name)
        target = str(PurePosixPath(remote_dest_dir) / name)
        return await _remote_place(
            sftp_client,
            remote_src,
            target,
            move=move,
            overwrite=overwrite,
            allow_dirs=allow_dirs,
            make_parents=True,
            keep_both=keep_both,
        )

    async def create_archive(
        self,
        paths: Sequence[str],
        destination: str,
        name: str,
        fmt: str,
        *,
        overwrite: bool = False,
        keep_both: bool = False,
    ) -> str:
        """Every path is checked before the connection is taken (a bad source is a 400
        that names it); ``tar`` or ``zip`` runs on the box into a temp file beside the
        archive, which is placed under its name by the rule when the command is done.
        A name that is taken is refused before the command runs. A 504 past
        ``ARCHIVE_TIMEOUT``, with the connection closed (``TimeoutError`` leaves the
        block, so ``ssh_pool.connection`` does not pool it)."""
        dest_validated = PathValidator.validate_remote_path(destination)
        validated_paths: list[str] = []
        for p in paths:
            try:
                validated_paths.append(PathValidator.validate_remote_path(p))
            except ValidationError as exc:
                raise Failure(REASON_INVALID, f"Invalid path {p}: {exc}") from exc
        connection = await self._conn()
        # The quoted command does not expand `~`: it is resolved here.
        dest_resolved = await remote_path(connection, dest_validated)
        resolved_paths = [await remote_path(connection, p) for p in validated_paths]
        archive_target = str(PurePosixPath(dest_resolved) / name)
        sftp_client = await self._session()
        _refuse_or_replace(
            await _remote_lstat_mode(sftp_client, archive_target),
            overwrite=overwrite,
            keep_both=keep_both,
        )
        temp = _remote_temp_beside(archive_target)
        try:
            await _run_archive_command(
                connection, archive_create_command(fmt, temp, resolved_paths), "Archive creation"
            )
            return await _remote_place(
                sftp_client,
                temp,
                archive_target,
                move=True,
                overwrite=overwrite,
                allow_dirs=False,
                keep_both=keep_both,
            )
        except BaseException:
            await run_to_end(_remove_quietly(sftp_client, temp))
            raise

    async def extract_archive(
        self, archive: str, destination: str, *, overwrite: bool = False, keep_both: bool = False
    ) -> str:
        """Both paths are checked before the connection is taken; then, as on the local
        box, a missing archive fails first and a name that is no archive is a 400.
        ``tar`` or ``unzip`` runs on the box into a temp folder inside the destination,
        a 504 past ``ARCHIVE_TIMEOUT`` (the connection is closed, as for a compress);
        the entries are then checked and moved as on the local box, or with
        ``keep_both`` the temp folder is placed whole as a new folder, as there."""
        validated_archive = PathValidator.validate_remote_path(archive)
        validated_dest = PathValidator.validate_remote_path(destination)
        connection = await self._conn()
        # The quoted command does not expand `~`: it is resolved here.
        archive_resolved = self._spelled.note(
            await remote_path(connection, validated_archive), archive
        )
        dest_resolved = await remote_path(connection, validated_dest)
        sftp_client = await self._session()
        found = await sftp_client.stat(archive_resolved)
        if not is_archive_name(PurePosixPath(validated_archive).name):
            raise Failure(REASON_INVALID, "Unsupported archive format")
        if stat_mod.S_ISDIR(found.permissions or 0):
            raise IsADirectoryError(errno.EISDIR, os.strerror(errno.EISDIR), archive_resolved)
        made = await _remote_make_missing(sftp_client, dest_resolved)
        unpacked: str | None = None
        try:
            temp = _remote_temp_beside(
                posixpath.join(dest_resolved, PurePosixPath(archive_resolved).name)
            )
            # Under keep_both this folder is the one that stays: the server's default bits.
            await sftp_client.mkdir(
                temp, asyncssh.SFTPAttrs() if keep_both else asyncssh.SFTPAttrs(permissions=0o700)
            )
            unpacked = temp
            await _run_archive_command(
                connection, archive_extract_command(archive_resolved, unpacked), "Extraction"
            )
            if keep_both:
                landed = await _remote_place(
                    sftp_client,
                    unpacked,
                    posixpath.join(
                        dest_resolved, archive_folder_name(PurePosixPath(archive_resolved).name)
                    ),
                    move=True,
                    overwrite=False,
                    keep_both=True,
                )
                unpacked = None
                return landed
            moves: list[tuple[str, str]] = []
            taken: list[tuple[str, int, bool]] = []
            await _remote_plan(sftp_client, unpacked, dest_resolved, moves, taken)
            refuse_taken_entries(taken, overwrite=overwrite)
            for source, target in moves:
                await _remote_place(sftp_client, source, target, move=True, overwrite=overwrite)
        except BaseException as exc:
            # A request that is cancelled (the client went away) cleans up as well; only
            # a failure can carry the note of a folder that could not be removed.
            if unpacked is not None:
                try:
                    await run_to_end(_remote_remove_tree(sftp_client, unpacked))
                except Exception as cleanup:
                    if not isinstance(exc, Exception):
                        raise exc from cleanup
                    raise unpacked_not_removed(exc, unpacked, cleanup) from exc
            await run_to_end(_remote_remove_made(sftp_client, made))
            raise
        assert unpacked is not None
        try:
            await _remote_remove_tree(sftp_client, unpacked)
        except asyncssh.SFTPError as cleanup:
            raise unpacked_not_removed(None, unpacked, cleanup) from cleanup
        return dest_resolved

    def _unreachable_fails_the_rest(
        self, exc: Exception, validated: list[tuple[str, str]], outcome: BatchOutcome
    ) -> None:
        """A box that cannot be reached (SSH, the pool or the connection failed) fails
        each path not yet done, with that reason; any other failure of the whole batch,
        a home lookup that answered no home among them, goes on as raised."""
        if isinstance(exc, Failure) or failure_of(exc).reason != REASON_UNREACHABLE:
            raise exc
        already = {f["path"] for f in outcome.failed} | set(outcome.succeeded)
        for orig, _ in validated:
            if orig not in already:
                outcome.failed.append(failure_entry(orig, exc))

    async def delete_many(self, paths: Sequence[str]) -> BatchOutcome:
        """Every path is checked before the connection is taken; a folder is removed
        with ``rm -rf``. A box that cannot be reached fails each path not yet done."""
        outcome = BatchOutcome()
        succeeded, failed = outcome.succeeded, outcome.failed
        validated: list[tuple[str, str]] = []
        for path in paths:
            try:
                validated.append((path, PathValidator.validate_remote_path(path)))
            except ValidationError as exc:
                failed.append(failure_entry(path, exc))
        if not validated:
            return outcome
        try:
            connection = await self._conn()
            validated = await _resolve_tilde_paths(connection, validated, failed)
            sftp_client = await self._session()
            await _remote_refuse_home_or_above(connection, sftp_client, [v for _, v in validated])
            for orig, vpath in validated:
                self._spelled.note(vpath, orig)
                try:
                    attrs = await sftp_client.lstat(vpath)  # a link is removed, not followed
                    mode = attrs.permissions or 0
                    if stat_mod.S_ISDIR(mode) and not stat_mod.S_ISLNK(mode):
                        await connection.run(remove_tree_command(vpath), check=True)
                    else:
                        await sftp_client.remove(vpath)
                    succeeded.append(orig)
                except Exception as exc:
                    failed.append(
                        failure_entry(orig, exc, as_sent=self.as_sent, missing=MISSING_FILE)
                    )
        except Exception as exc:
            self._unreachable_fails_the_rest(exc, validated, outcome)
        return outcome

    async def place_many(
        self,
        paths: Sequence[str],
        destination: str,
        *,
        move: bool,
        overwrite: bool,
        keep_both: bool = False,
    ) -> BatchOutcome:
        """The destination, then every path, is checked before the connection is
        taken. A box that cannot be reached fails each path not yet done."""
        outcome = BatchOutcome()
        failed = outcome.failed
        landed: set[str] = set()
        try:
            dest_validated = PathValidator.validate_remote_path(destination)
        except ValidationError as exc:
            raise Failure(REASON_INVALID, f"Invalid destination: {exc}") from exc

        validated: list[tuple[str, str]] = []
        for path in paths:
            try:
                validated.append((path, PathValidator.validate_remote_path(path)))
            except ValidationError as exc:
                failed.append(failure_entry(path, exc))
        if not validated:
            return outcome
        try:
            connection = await self._conn()
            dest_resolved = await _resolve_tilde_destination(connection, dest_validated)
            validated = await _resolve_tilde_paths(connection, validated, failed)
            sftp_client = await self._session()
            if move:
                await _remote_refuse_home_or_above(
                    connection, sftp_client, [v for _, v in validated]
                )
            for orig, vpath in validated:
                self._spelled.note(vpath, orig)
                target = str(PurePosixPath(dest_resolved) / PurePosixPath(vpath).name)
                try:
                    refuse_a_name_this_request_placed(target, landed, keep_both=keep_both)
                    at = await _remote_place(
                        sftp_client,
                        vpath,
                        target,
                        move=move,
                        overwrite=overwrite,
                        make_parents=True,
                        keep_both=keep_both,
                    )
                except Exception as exc:
                    failed.append(
                        failure_entry(orig, exc, as_sent=self.as_sent, missing=MISSING_SOURCE)
                    )
                else:
                    outcome.landed(orig, target, at)
                    landed.add(at)
        except Exception as exc:
            self._unreachable_fails_the_rest(exc, validated, outcome)
        return outcome
