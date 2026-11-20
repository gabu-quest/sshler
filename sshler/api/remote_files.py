"""Placing files over SFTP, and the size and zip of a remote folder."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import posixpath
import secrets
import stat as stat_mod
from collections.abc import Awaitable
from pathlib import PurePosixPath

import asyncssh
from fastapi import HTTPException

from .placement import (
    _NO_FOLDER_COPY,
    _ONLY_FILES,
    COPY_CHUNK_BYTES,
    _reason,
    _refuse_a_folder_into_itself,
    _refuse_or_replace,
    _taken_after_check,
    ensure_source_unchanged,
    kept_permission_bits,
    run_to_end,
)
from .remote import (
    command,
    in_directory,
    path_word,
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

    The file gets ``mode``'s permission bits: the source's on a free name, the replaced
    file's on a replace. A free name is set exactly before the first byte (the server's
    umask narrows what the open asks for). Without ``mode`` a free name gets the
    server's default bits."""

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
        mode=dest_mode if replace else source_mode,
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
