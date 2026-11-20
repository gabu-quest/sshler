"""Placing and listing files on the local file system; every function here runs in a
worker thread or is called from one.
"""

from __future__ import annotations

import asyncio
import contextlib
import errno
import os
import shutil
import stat as stat_mod
import tempfile
from pathlib import Path

from fastapi import HTTPException

from .helpers import (
    LOCAL_IS_WINDOWS,
    _normalize_local_path,
)
from .placement import (
    _NO_FOLDER_COPY,
    _ONLY_FILES,
    COPY_CHUNK_BYTES,
    _refuse_a_folder_into_itself,
    _refuse_or_replace,
    _taken_after_check,
    ensure_source_unchanged,
    kept_permission_bits,
)

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

    ``mode`` gives the new file its permission bits: the replaced file's on a replace,
    the source's on a free name. They are on the file before its first byte and the
    umask cannot narrow them. Without ``mode`` a free name gets the default bits (the
    umask's)."""

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
        elif mode is None:
            self.handle = open(target, "xb")  # noqa: SIM115 - closed by commit/abort
        else:
            bits = kept_permission_bits(mode)
            # Created no wider than ``bits`` (the umask only narrows), then set exactly,
            # both before the first write.
            fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, bits)
            try:
                os.fchmod(fd, bits)
            except BaseException:
                os.close(fd)
                with contextlib.suppress(OSError):
                    os.unlink(target)
                raise
            self.handle = os.fdopen(fd, "wb")

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


def _local_landing(
    target: str, *, overwrite: bool, source_regular: bool = True, source_mode: int | None = None
) -> _LocalLanding:
    """Decide ``target`` by the rule and open it for the bytes of a file. A free name
    gets ``source_mode``'s permission bits (the default bits when None); a replace keeps
    the replaced file's."""
    dest_mode = _local_lstat_mode(target)
    replace = _refuse_or_replace(dest_mode, overwrite=overwrite, source_regular=source_regular)
    if not source_regular:
        raise HTTPException(status_code=400, detail=_ONLY_FILES)
    try:
        return _LocalLanding(target, replace=replace, mode=dest_mode if replace else source_mode)
    except FileExistsError:
        _taken_after_check(_local_lstat_mode(target), source_regular=source_regular)


def _local_copy_file(source: str, target: str, *, replace_mode: int | None = None) -> None:
    """Copy a file in chunks (never whole into memory) with its times. A free name gets
    the source's permission bits, a replace (``replace_mode``, the replaced file's mode)
    keeps the replaced file's; either way they are on the file before its first byte."""
    with open(source, "rb") as src:
        found = os.fstat(src.fileno())
        mode = found.st_mode if replace_mode is None else replace_mode
        landing = _LocalLanding(target, replace=replace_mode is not None, mode=mode)
        try:
            shutil.copyfileobj(src, landing.handle, COPY_CHUNK_BYTES)
            landing.commit()
        except BaseException:
            landing.abort()
            raise
    os.utime(target, ns=(found.st_atime_ns, found.st_mtime_ns))
    if replace_mode is None:
        _local_copy_extended_attributes(source, target)


def _local_copy_extended_attributes(source: str, target: str) -> None:
    """Carry a file's extended attributes to its copy, where the platform has them; one
    that cannot be read or set is left out, as ``shutil.copystat`` leaves it."""
    if not hasattr(os, "listxattr"):
        return
    with contextlib.suppress(OSError):
        for name in os.listxattr(source):
            with contextlib.suppress(OSError):
                os.setxattr(target, name, os.getxattr(source, name))


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
    """Move a regular file over a regular file, keeping the replaced file's bits: set on
    the source first, so the name never holds the source's own, and put back if the
    move fails."""
    own = stat_mod.S_IMODE(os.lstat(source).st_mode)
    keep = kept_permission_bits(dest_mode)
    if own != keep:
        os.chmod(source, keep)
    try:
        os.replace(source, target)
    except BaseException as exc:
        if own != keep:
            with contextlib.suppress(OSError):
                os.chmod(source, own)
        if not (isinstance(exc, OSError) and exc.errno == errno.EXDEV):
            raise
        _local_copy_file(source, target, replace_mode=dest_mode)
        _local_remove_moved_source(source, target, before)


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
