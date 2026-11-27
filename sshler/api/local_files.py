"""The files of the local box: ``LocalFiles``, and the placing, listing, reading and
writing it is built from."""

from __future__ import annotations

import asyncio
import contextlib
import errno
import mimetypes
import os
import platform
import secrets
import shutil
import stat as stat_mod
import sys
import tarfile
import tempfile
import zipfile
import zlib
from collections.abc import AsyncIterator, Callable, Sequence
from functools import partial
from pathlib import Path
from typing import BinaryIO

import anyio
from fastapi.responses import FileResponse, Response
from starlette.datastructures import Headers
from starlette.responses import MalformedRangeHeader, RangeNotSatisfiable
from starlette.types import Receive, Scope, Send

from ..proc import run_bounded
from ..validation import PathValidator, ValidationError
from .failure import (
    REASON_FAILED,
    REASON_INVALID,
    Failure,
    answer,
    failure_entry,
    failure_of,
)
from .file_contract import (
    MISSING_FILE,
    MISSING_SOURCE,
    NOT_A_FOLDER,
    TAR_SUFFIXES,
    BatchOutcome,
    FolderZip,
    NameRefusedError,
    PathKind,
    ReadChunk,
    SourceFile,
    SpelledPaths,
    archive_folder_name,
    is_archive_name,
)
from .helpers import (
    LOCAL_IS_WINDOWS,
    _normalize_local_path,
    local_path_form,
)
from .models import APIDirectoryEntry
from .placement import (
    _NO_FOLDER_COPY,
    _ONLY_FILES,
    COPY_CHUNK_BYTES,
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


def _set_bits(fd: int, path: str, bits: int) -> None:
    """Set an open file's permission bits: through its descriptor, or by its path on
    Windows before Python 3.13, which has no ``fchmod``."""
    if hasattr(os, "fchmod"):
        os.fchmod(fd, bits)
    else:
        os.chmod(path, bits)


def _temp_with_default_bits(target: str) -> tuple[int, str]:
    """A new temp file beside ``target`` with the bits ``open`` gives a new file (0o666
    less the umask), as a descriptor and a path. ``mkstemp`` makes its file 0600, and the
    umask can only be read by setting it, which every thread of the process would see."""
    folder, name = os.path.split(target)
    while True:
        temp = os.path.join(folder, f".{temp_stem(name)}.{os.urandom(6).hex()}")
        try:
            return os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o666), temp
        except FileExistsError:
            continue


class _LocalLanding:
    """A local file being written under the rule. A free name is created exclusively
    (``O_CREAT|O_EXCL``: ``FileExistsError`` when taken, a dangling link included); a
    replace writes a temp file beside the target and renames it over on ``commit``.

    ``mode`` gives the new file its permission bits: the replaced file's on a replace,
    the source's on a free name. They are on the file before its first byte and the
    umask cannot narrow them. Without ``mode`` a free name, and the temp file of a
    replace, get the default bits (the umask's)."""

    def __init__(self, target: str, *, replace: bool, mode: int | None = None) -> None:
        self.target = target
        self.path = target  # where the file lands
        self.temp: str | None = None
        if replace:
            if mode is None:
                fd, self.temp = _temp_with_default_bits(target)
            else:
                fd, self.temp = tempfile.mkstemp(
                    dir=os.path.dirname(target),
                    prefix=f".{temp_stem(os.path.basename(target))}.",
                )
                _set_bits(fd, self.temp, kept_permission_bits(mode))
            self.handle = os.fdopen(fd, "wb")
        elif mode is None:
            self.handle = open(target, "xb")  # noqa: SIM115 - closed by commit/abort
        else:
            bits = kept_permission_bits(mode)
            # Created no wider than ``bits`` (the umask only narrows), then set exactly,
            # both before the first write.
            fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, bits)
            try:
                _set_bits(fd, target, bits)
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
    target: str,
    *,
    overwrite: bool,
    source_regular: bool = True,
    source_mode: int | None = None,
    exists_status: int = 409,
    keep_both: bool = False,
) -> _LocalLanding:
    """Decide ``target`` by the rule and open it for the bytes of a file. A free name
    gets ``source_mode``'s permission bits (the default bits when None); a replace keeps
    the replaced file's. With ``keep_both`` a taken name gives the first free name of
    its series; the landing's ``path`` says which."""
    dest_mode = _local_lstat_mode(target)
    replace = _refuse_or_replace(
        dest_mode,
        overwrite=overwrite,
        source_regular=source_regular,
        exists_status=exists_status,
        keep_both=keep_both,
    )
    if not source_regular:
        raise Failure(REASON_INVALID, _ONLY_FILES)
    if replace:
        return _LocalLanding(target, replace=True, mode=dest_mode)
    return _local_take_free_name(
        target,
        lambda candidate: _LocalLanding(candidate, replace=False, mode=source_mode),
        keep_both=keep_both,
        folder=False,
        source_regular=source_regular,
        exists_status=exists_status,
    )


def _local_take_free_name[T](
    target: str,
    take: Callable[[str], T],
    *,
    keep_both: bool,
    folder: bool,
    source_regular: bool = True,
    exists_status: int = 409,
) -> T:
    """``take(candidate)``, the exclusive call that fails with ``FileExistsError`` when
    its name is taken, on ``target``; with ``keep_both`` on each next name of
    ``candidate_paths`` while the name is taken, otherwise the rule's refusal."""
    for candidate in candidate_paths(target, keep_both=keep_both, folder=folder):
        try:
            return take(candidate)
        except FileExistsError:
            if not keep_both:
                _taken_after_check(
                    _local_lstat_mode(target),
                    source_regular=source_regular,
                    exists_status=exists_status,
                )
    raise AssertionError("unreachable: candidate_paths ends with a refusal")


class _ThreadedLanding:
    """The async face of a ``_LocalLanding``: each call runs in a worker thread."""

    def __init__(self, landing: _LocalLanding) -> None:
        self.landing = landing
        self.path = local_path_form(landing.path)

    async def write(self, data: bytes) -> None:
        await asyncio.to_thread(self.landing.write, data)

    async def commit(self) -> None:
        await asyncio.to_thread(self.landing.commit)

    async def abort(self) -> None:
        """To the end even if the request is cancelled: a worker-thread job cancelled
        before it starts never runs."""
        await run_to_end(asyncio.to_thread(self.landing.abort))


_NOT_A_FILE = "Not a regular file"


def _local_copy_file(source: str, target: str, *, replace_mode: int | None = None) -> None:
    """Copy a file in chunks (never whole into memory) with its times. A free name gets
    the source's permission bits, a replace (``replace_mode``, the replaced file's mode)
    keeps the replaced file's; either way they are on the file before its first byte.

    Only a regular file is opened: opening a named pipe waits for a writer, and a device
    has no end."""
    if not stat_mod.S_ISREG(os.stat(source).st_mode):
        raise OSError(errno.EINVAL, _NOT_A_FILE, source)
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


class _FirstCopyError:
    """``copytree`` goes on after an error and raises them all at the end, as texts; the
    copy of each file goes through ``copy``, which keeps the first error itself, so its
    reason is named by its type."""

    def __init__(self) -> None:
        self.first: OSError | None = None

    def copy(self, src: str, dst: str, *, follow_symlinks: bool = True) -> object:
        try:
            # A link is followed (``copytree`` hands over a link only when it is
            # to be copied as the file it points to).
            _local_copy_file(src, dst)
            return dst
        except OSError as exc:
            if self.first is None:
                self.first = exc
            raise

    def of(self, exc: BaseException) -> Failure:
        if self.first is not None:
            return failure_of(self.first)
        if isinstance(exc, shutil.Error) and exc.args and isinstance(exc.args[0], list):
            # A failure outside a file's copy (a folder's times and modes): `copytree`
            # raises the list of (source, target, text); the first text is the answer.
            if exc.args[0]:
                return Failure(REASON_FAILED, str(exc.args[0][0][2]))
        return failure_of(exc)


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
    errors = _FirstCopyError()
    try:
        shutil.copytree(
            source,
            target,
            symlinks=symlinks,
            dirs_exist_ok=True,
            ignore=note if record is not None else None,
            copy_function=errors.copy,
        )
    except Exception as exc:
        cause = errors.of(exc)
        detail = f"Could not copy {source} to {target}: {cause.detail}"
        try:
            shutil.rmtree(target)
        except OSError as cleanup:
            detail += (
                f"; the partial copy at {target} could not be removed: {failure_of(cleanup).detail}"
            )
        raise Failure(cause.reason, detail) from exc


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
        raise source_not_removed(target, str(exc.strerror or exc)) from exc


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
    source: str,
    target: str,
    *,
    move: bool,
    overwrite: bool,
    allow_dirs: bool = True,
    keep_both: bool = False,
) -> str:
    """Move or copy ``source`` to ``target`` under the rule (call from a worker thread);
    returns where it landed: ``target``, or with ``keep_both`` the first free name of
    its series when ``target`` is taken.

    A move onto the source itself (the same path, or one that differs only in letter
    case on a filesystem that ignores case) is a plain rename, not a taken name. A
    missing source raises ``FileNotFoundError``."""
    source_stat = os.lstat(source) if move else os.stat(source)
    dest_stat = _local_lstat(target)
    same_file = dest_stat is not None and os.path.samestat(source_stat, dest_stat)
    if move and same_file and _same_spelling(source, target):
        os.rename(source, target)
        return target
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
        keep_both=keep_both,
    )
    if stat_mod.S_ISDIR(source_stat.st_mode) and not allow_dirs:
        raise Failure(REASON_INVALID, _NO_FOLDER_COPY)
    if replace:
        assert dest_stat is not None
        if move:
            _local_replace_by_move(
                source, target, dest_stat.st_mode, local_fingerprint(source_stat)
            )
        else:
            _local_copy_file(source, target, replace_mode=dest_stat.st_mode)
        return target

    def take(candidate: str) -> str:
        if move:
            _local_move_new(source, candidate, source_stat)
        else:
            _local_copy_new(source, candidate, source_stat.st_mode)
        return candidate

    return _local_take_free_name(
        target,
        take,
        keep_both=keep_both,
        folder=stat_mod.S_ISDIR(source_stat.st_mode),
        source_regular=source_regular,
    )


def _local_make_folder(dest_dir: Path) -> Path:
    """Make ``dest_dir`` and its missing folders (call from a worker thread). A file
    standing where one of them is needed, ``dest_dir`` itself included, is a path that
    goes on through a file: ``NotADirectoryError``, as the system says when it opens
    such a path, whichever level the file stands at."""
    try:
        dest_dir.mkdir(parents=True, exist_ok=True)
    except FileExistsError as exc:
        raise NotADirectoryError(
            errno.ENOTDIR, os.strerror(errno.ENOTDIR), exc.filename or str(dest_dir)
        ) from exc
    return dest_dir


def _local_refuse_a_folder_into_itself(source: str, dest_dir: Path, *, move: bool) -> None:
    """Refuse a source folder whose destination is itself or lies under it, before the
    destination is made (it may not exist yet; call from a worker thread)."""
    found = os.lstat(source) if move else os.stat(source)
    if stat_mod.S_ISDIR(found.st_mode):
        _refuse_a_folder_into_itself(os.path.realpath(source), os.path.realpath(dest_dir))


def _local_place_into(
    source: str,
    dest_dir: Path,
    new_name: str | None,
    *,
    move: bool,
    overwrite: bool,
    allow_dirs: bool = True,
    keep_both: bool = False,
) -> str:
    """Place ``source`` in folder ``dest_dir`` (both resolved) as ``new_name`` (default:
    its own name) under the rule; returns where it landed (call from a worker thread)."""
    if not os.path.lexists(source):
        raise FileNotFoundError(errno.ENOENT, os.strerror(errno.ENOENT), source)
    _local_refuse_a_folder_into_itself(source, dest_dir, move=move)
    target = str(_local_make_folder(dest_dir) / (new_name or Path(source).name))
    return _local_place(
        source,
        target,
        move=move,
        overwrite=overwrite,
        allow_dirs=allow_dirs,
        keep_both=keep_both,
    )


async def _local_write_new(
    target_path: str, data: bytes, *, overwrite: bool, exists_status: int, keep_both: bool = False
) -> str:
    """Write ``data`` to a new local file, or replace a regular file with ``overwrite``
    through a landing: the old file stays whole until the new one is complete. An
    existing name answers ``exists_status``, or with ``keep_both`` gives the first free
    name of its series. Returns where the file landed."""

    def _worker() -> str:
        _local_make_folder(Path(target_path).parent)
        landing = _local_landing(
            target_path, overwrite=overwrite, exists_status=exists_status, keep_both=keep_both
        )
        try:
            landing.write(data)
            landing.commit()
        except BaseException:
            landing.abort()
            raise
        return landing.path

    return await asyncio.to_thread(_worker)


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


def _compose_local_child_path(directory: str, filename: str) -> str:
    cleaned = (filename or "").strip()
    if not cleaned:
        raise ValueError("Filename is required")
    if cleaned in {".", ".."} or any(sep in cleaned for sep in ("/", "\\")):
        raise ValueError("Filename cannot contain path separators")
    if "\x00" in cleaned:
        raise ValueError("Filename contains unsupported characters")
    parent = Path(directory or Path.home())
    return local_path_form((parent / cleaned).expanduser())


async def _local_read_text(path: str, max_bytes: int) -> str:
    def _worker() -> str:
        with open(Path(path), "rb") as handle:
            data = handle.read(max_bytes)
        return data.decode("utf-8", errors="replace")

    return await asyncio.to_thread(_worker)


async def _local_write_text(path: str, content: str) -> None:
    """Write *content* as UTF-8, its line ends as they were sent: a text-mode file would
    write each ``\\n`` as ``\\r\\n`` on Windows."""

    def _worker() -> None:
        _local_make_folder(Path(path).parent)
        with open(Path(path), "wb") as handle:
            handle.write(content.encode("utf-8"))

    await asyncio.to_thread(_worker)


async def _local_read_bytes(path: str, limit: int) -> tuple[bytes, bool]:
    def _worker() -> tuple[bytes, bool]:
        with open(Path(path), "rb") as handle:
            data = handle.read(limit + 1)
        return (data[:limit], len(data) > limit)

    return await asyncio.to_thread(_worker)


async def _local_batch_delete(path: str) -> None:
    def _worker() -> None:
        target = Path(path)
        if target.is_symlink() or target.is_junction():
            target.unlink()  # the link itself, never what it points to
        elif target.is_dir():
            shutil.rmtree(target)
        else:
            target.unlink()

    await asyncio.to_thread(_worker)


async def _local_delete_file(path: str) -> None:
    def _worker() -> None:
        Path(path).unlink()

    await asyncio.to_thread(_worker)


# The gitignore marks of a listing are a nicety: a git that does not answer in
# this time is given up on and the listing goes out without them.
GITIGNORE_TIMEOUT = 5.0
# Finding the folders that ignore all they hold walks the folder's tree as `git status`
# does; a tree too big for this time keeps only the marks of the first check.
WHOLLY_IGNORED_TIMEOUT = 2.0


async def _get_gitignored_names(directory: str, names: list[str]) -> set[str]:
    """The names in *directory* that `git status --ignored` lists as ignored.

    `check-ignore` marks the names a rule matches. A folder that ignores its own contents
    with a `.gitignore` of `*` (.mypy_cache, .pytest_cache, a uv .venv) is matched by no
    rule, so `ls-files` adds the folders git reports as wholly ignored (`name/`).
    """
    try:
        returncode, stdout, _ = await run_bounded(
            # -z: names in and out as they are; without it git prints a name with
            # non-ASCII characters (日本.log) quoted and escaped, and it never matches.
            ["git", "check-ignore", "--stdin", "-z"],
            GITIGNORE_TIMEOUT,
            cwd=directory,
            stdin_data="\0".join(names).encode(),
        )
        if returncode not in (0, 1):  # 1 means none matched, 128 means not a git repo
            return set()
        ignored = {name for name in stdout.decode(errors="replace").split("\0") if name}
    except Exception:
        return set()
    if ignored.issuperset(names):
        return ignored
    try:
        returncode, stdout, _ = await run_bounded(
            ["git", "ls-files", "-z", "--others", "--ignored", "--exclude-standard", "--directory"],
            WHOLLY_IGNORED_TIMEOUT,
            cwd=directory,
        )
    except Exception:
        return ignored
    if returncode != 0:
        return ignored
    wanted = set(names)
    for path in stdout.decode(errors="replace").split("\0"):
        name, slash, rest = path.partition("/")
        if slash and not rest and name in wanted:
            ignored.add(name)
    return ignored


def _safe_extract_zip(zf: zipfile.ZipFile, dest_dir: Path) -> None:
    """Extract zip safely, preventing path traversal (zip-slip).

    Validates every entry's resolved path stays within dest_dir before extracting.
    Handles backslash paths, encoded sequences, and parent references.
    """
    dest_resolved = dest_dir.resolve()
    for info in zf.infolist():
        # Normalize: replace backslashes, strip leading slashes
        safe_name = info.filename.replace("\\", "/").lstrip("/")
        # Check for parent traversal after normalization
        target = (dest_resolved / safe_name).resolve()
        # Target must be inside dest_dir
        if not (target == dest_resolved or str(target).startswith(str(dest_resolved) + os.sep)):
            raise Failure(REASON_INVALID, f"Archive contains unsafe path: {info.filename}")
        # Rewrite the filename to the safe version before extracting
        info.filename = safe_name
        zf.extract(info, dest_dir)


def _local_pack(paths: Sequence[str], out: BinaryIO, unfinished: Path, fmt: str) -> None:
    """Pack each path (resolved) under its own name into ``out``; a folder in a zip is
    packed file by file. ``unfinished`` is the file ``out`` writes: a folder that holds
    it never packs it into itself."""
    if fmt in ("tar.gz", "tgz"):

        def not_unfinished(info: tarfile.TarInfo, source: Path) -> tarfile.TarInfo | None:
            return None if source.parent / info.name == unfinished else info

        with tarfile.open(fileobj=out, mode="w:gz") as tf:
            for p in paths:
                source = Path(p)
                tf.add(
                    str(source), arcname=source.name, filter=partial(not_unfinished, source=source)
                )
        return
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        for p in paths:
            source = Path(p)
            if source.is_dir():
                for child in source.rglob("*"):
                    if child.is_file() and child != unfinished:
                        arcname = str(source.name / child.relative_to(source))
                        zf.write(str(child), arcname)
            else:
                zf.write(str(source), source.name)


def _local_create_archive(
    paths: Sequence[str], archive_path: Path, fmt: str, *, overwrite: bool, keep_both: bool = False
) -> str:
    """Pack ``paths`` into a temp file beside ``archive_path`` and place it under that
    name by the rule when the packer has finished (call from a worker thread). A name
    that is taken is refused before anything is packed (and again at the placing); a
    failure at any point leaves the old archive whole and no temp file. Returns where
    it landed (with ``keep_both`` a taken name gives the first free name of its series)."""
    target = str(archive_path)
    _refuse_or_replace(_local_lstat_mode(target), overwrite=overwrite, keep_both=keep_both)
    landing = _LocalLanding(target, replace=True)
    try:
        assert landing.temp is not None
        _local_pack(paths, landing.handle, Path(landing.temp), fmt)
        landing.handle.close()
        return _local_place(
            landing.temp, target, move=True, overwrite=overwrite, keep_both=keep_both
        )
    except BaseException:
        landing.abort()
        raise


def tar_link_guard_is_fixed(version: tuple[int, ...] = tuple(sys.version_info[:3])) -> bool:
    """Whether tarfile's ``data`` filter of this Python keeps a link from leading an
    entry outside the destination: the ways round it were fixed in 3.12.11 and 3.13.4
    (and every 3.14)."""
    if version[:2] == (3, 12):
        return version >= (3, 12, 11)
    if version[:2] == (3, 13):
        return version >= (3, 13, 4)
    return version >= (3, 14)


_OLD_TAR_GUARD = (
    "This Python ({}) is too old to unpack links from a tar safely; 3.12.11 or 3.13.4 is needed"
)


def _tar_filter(member: tarfile.TarInfo, path: str) -> tarfile.TarInfo | None:
    """tarfile's ``data`` filter (no entry outside the folder, no link that leaves it, no
    device file), and on a Python whose filter has the known holes no link at all."""
    if (member.issym() or member.islnk()) and not tar_link_guard_is_fixed():
        raise Failure(REASON_INVALID, _OLD_TAR_GUARD.format(platform.python_version()))
    return tarfile.data_filter(member, path)


def _local_unpack(archive: Path, into: str) -> None:
    """Unpack ``archive`` into the empty folder ``into``. A tar goes through
    ``_tar_filter``; a zip entry that would leave the folder refuses the whole zip with a
    400. An archive the reader refuses (a tar entry the filter refuses, a file that is no
    archive or is cut short) is ``failed`` with its text, as a remote ``tar`` or
    ``unzip`` that exits with an error is; an entry the filter refuses is named (the
    filter's own text names a path in the temp folder, not in the destination)."""
    try:
        if archive.name.lower().endswith(TAR_SUFFIXES):
            with tarfile.open(str(archive), "r:gz") as tf:
                tf.extractall(path=into, filter=_tar_filter)
        else:
            with zipfile.ZipFile(str(archive), "r") as zf:
                _safe_extract_zip(zf, Path(into))
    except tarfile.FilterError as exc:
        raise Failure(REASON_FAILED, f"Archive contains unsafe entry: {exc.tarinfo.name}") from exc
    except (tarfile.TarError, zipfile.BadZipFile, EOFError, zlib.error) as exc:
        raise Failure(REASON_FAILED, str(exc)) from exc


def _local_make_missing(folder: str) -> list[str]:
    """Make ``folder`` and every missing folder above it; returns those made, the top
    one first (a failure part way removes them again)."""
    missing: list[str] = []
    while not os.path.lexists(folder) and os.path.dirname(folder) != folder:
        missing.append(folder)
        folder = os.path.dirname(folder)
    made: list[str] = []
    try:
        for path in reversed(missing):
            os.mkdir(path)
            made.append(path)
    except BaseException:
        _local_remove_made(made)
        raise
    return made


def _local_remove_made(made: list[str]) -> None:
    """Remove the folders ``_local_make_missing`` made, the deepest first; one that is
    not empty by now holds something else's and stays."""
    for path in reversed(made):
        with contextlib.suppress(OSError):
            os.rmdir(path)


def _local_plan(
    unpacked: str,
    dest: str,
    moves: list[tuple[str, str]],
    taken: list[tuple[str, int, bool]],
    shown: str = "",
) -> None:
    """Walk the unpacked folder against ``dest`` by the names the file system gives (a
    link is not followed). An entry whose name is free in ``dest`` moves as it is, a
    folder with everything in it; a folder where ``dest`` has a folder is walked into,
    since the archive's entries go into an existing folder; any other entry is a taken
    name for ``refuse_taken_entries``, and moves if the rule lets it replace."""
    with os.scandir(unpacked) as found:
        entries = sorted(found, key=lambda entry: entry.name)
    for entry in entries:
        target = os.path.join(dest, entry.name)
        source_mode = entry.stat(follow_symlinks=False).st_mode
        dest_mode = _local_lstat_mode(target)
        if dest_mode is not None and stat_mod.S_ISDIR(source_mode) and stat_mod.S_ISDIR(dest_mode):
            _local_plan(entry.path, target, moves, taken, f"{shown}{entry.name}/")
            continue
        if dest_mode is not None:
            taken.append((f"{shown}{entry.name}", dest_mode, stat_mod.S_ISREG(source_mode)))
        moves.append((entry.path, target))


def _local_extract_archive(
    archive: Path, dest_dir: Path, *, overwrite: bool, keep_both: bool = False
) -> str:
    """Unpack ``archive`` into a temp folder inside ``dest_dir`` (made if missing), check
    every entry against ``dest_dir`` by the rule, and only then move the entries into
    place (call from a worker thread). A refused or failed extract leaves ``dest_dir``
    as it was and no temp folder; a folder this made is removed again. With
    ``keep_both`` the temp folder itself is placed as a new folder named by
    ``archive_folder_name`` (or the next free name). Returns where the entries are."""
    made = _local_make_missing(str(dest_dir))
    unpacked: str | None = None
    try:
        if keep_both:
            # Made with the default bits, since this folder becomes the one that stays.
            temp = os.path.join(dest_dir, f".{temp_stem(archive.name)}.{secrets.token_hex(6)}")
            os.mkdir(temp)
            unpacked = temp
        else:
            unpacked = tempfile.mkdtemp(dir=dest_dir, prefix=f".{temp_stem(archive.name)}.")
        _local_unpack(archive, unpacked)
        if keep_both:
            landed = _local_place(
                unpacked,
                os.path.join(dest_dir, archive_folder_name(archive.name)),
                move=True,
                overwrite=False,
                keep_both=True,
            )
            unpacked = None
            return landed
        moves: list[tuple[str, str]] = []
        taken: list[tuple[str, int, bool]] = []
        _local_plan(unpacked, str(dest_dir), moves, taken)
        refuse_taken_entries(taken, overwrite=overwrite)
        for source, target in moves:
            _local_place(source, target, move=True, overwrite=overwrite)
    except Exception as exc:
        if unpacked is not None:
            try:
                shutil.rmtree(unpacked)
            except OSError as cleanup:
                raise unpacked_not_removed(exc, unpacked, cleanup) from exc
        _local_remove_made(made)
        raise
    try:
        shutil.rmtree(unpacked)
    except OSError as cleanup:
        raise unpacked_not_removed(None, unpacked, cleanup) from cleanup
    return str(dest_dir)


def _local_batch_item(
    source: str,
    dest_dir: Path,
    landed: set[str],
    *,
    move: bool,
    overwrite: bool,
    keep_both: bool = False,
) -> tuple[str, str]:
    """Move or copy one local item (resolved) into ``dest_dir`` (call from a worker
    thread); returns the target asked for and where the item landed, both in
    ``local_path_form`` (``landed`` holds that form too)."""
    if not os.path.lexists(source):
        raise FileNotFoundError(errno.ENOENT, os.strerror(errno.ENOENT), source)
    _local_refuse_a_folder_into_itself(source, dest_dir, move=move)
    target = local_path_form(_local_make_folder(dest_dir) / Path(source).name)
    refuse_a_name_this_request_placed(target, landed, keep_both=keep_both)
    at = _local_place(source, target, move=move, overwrite=overwrite, keep_both=keep_both)
    return target, local_path_form(at)


class FileAnswer(FileResponse):
    """A file streamed from disk whose ``Range`` header, when refused, answers as every
    failure does: ``invalid`` with the text of the refusal (400), or with 416 and its
    ``Content-Range`` for a range past the end. The range is read the way
    ``FileResponse`` reads it, before the first byte is sent."""

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        refusal = await self._range_refusal(scope)
        if refusal is not None:
            await refusal(scope, receive, send)
            return
        await super().__call__(scope, receive, send)

    async def _range_refusal(self, scope: Scope) -> Response | None:
        headers = Headers(scope=scope)
        http_range = headers.get("range")
        if (
            scope["type"] != "http"
            or http_range is None
            or self.status_code != 200
            or scope["method"].upper() not in ("GET", "HEAD")
        ):
            return None
        if self.stat_result is None:
            self.stat_result = await anyio.to_thread.run_sync(os.stat, self.path)
            self.set_stat_headers(self.stat_result)
        if_range = headers.get("if-range")
        if if_range is not None and not self._should_use_range(if_range):
            return None
        try:
            self._parse_range_header(http_range, self.stat_result.st_size)
        except MalformedRangeHeader as exc:
            return answer(REASON_INVALID, exc.content)
        except RangeNotSatisfiable as exc:
            return answer(
                REASON_INVALID,
                "Range not satisfiable",
                status=416,
                headers={"Content-Range": f"bytes */{exc.max_size}"},
            )
        return None


def _local_path(path: str) -> str:
    """A path the client sent, refused when empty or holding a null byte (the remote
    check's texts), then normalised (``~`` expanded, links resolved)."""
    if not path:
        raise ValidationError("Path cannot be empty")
    if "\0" in path:
        raise ValidationError("Path cannot contain null bytes")
    return _normalize_local_path(path)


def _local_link_itself(sent: str, resolved: str) -> str:
    """The link ``sent`` names when its last part is a link (worker thread), else
    ``resolved``. ``_local_path`` follows every link, so a delete or a move of a link
    would act on what it points to; the link's folder is resolved, its name is not."""
    try:
        base = os.path.abspath(os.path.expanduser(sent))
        name = os.path.basename(base)
        if name not in ("", ".", ".."):
            candidate = os.path.join(os.path.realpath(os.path.dirname(base)), name)
            # A Windows directory junction links a folder as a symlink does, but
            # `islink` says False for it.
            if os.path.islink(candidate) or os.path.isjunction(candidate):
                return local_path_form(candidate)
    except (OSError, ValueError):
        pass
    return resolved


def _local_refuse_home_or_above(resolved: Sequence[str]) -> None:
    """Refuse the whole batch when one path is the home folder or above it."""
    try:
        home = local_path_form(Path.home().resolve())
    except (OSError, RuntimeError):
        return
    for path in resolved:
        try:
            PathValidator.refuse_home_or_ancestor(path, home, windows=LOCAL_IS_WINDOWS)
        except ValidationError as exc:
            raise Failure(REASON_INVALID, str(exc)) from exc


def _local_folder_size(base: Path) -> int:
    """The bytes of the files under ``base`` (worker thread). A missing or unreadable
    ``base`` fails as the system says; an unreadable entry under it is left out."""
    if not stat_mod.S_ISDIR(base.stat().st_mode):
        raise Failure(REASON_INVALID, "Not a directory")
    with os.scandir(base):
        pass  # a folder that cannot be listed fails here, not as an empty one
    total = 0
    for child in base.rglob("*"):
        if child.is_file():
            try:
                total += child.stat().st_size
            except OSError:
                pass
    return total


def _local_check_readable(path: str) -> None:
    """Open and close ``path``: a file missing, unreadable or a folder fails here, before
    a streamed answer has begun. A pipe, a device or a socket is refused unopened:
    opening a pipe to read waits for a writer."""
    mode = os.stat(path).st_mode
    if not (stat_mod.S_ISREG(mode) or stat_mod.S_ISDIR(mode)):
        raise Failure(REASON_INVALID, _NOT_A_FILE)
    with open(path, "rb"):
        pass


class LocalFiles:
    """The files of the local box, on ``pathlib`` and the helpers above.

    A path the client sent is refused when empty or holding a null byte, then
    normalised. Every path it answers is in ``local_path_form``, the form a resolved
    path has (POSIX form on Windows, where the system spells a path it makes with
    backslashes). A failure an operation does not answer itself goes on as raised, for
    ``box_files`` to answer. A download streams from disk and is never read into memory.
    """

    def __init__(self) -> None:
        # A resolved local path is in POSIX form on Windows (`_normalize_local_path`).
        self._spelled = SpelledPaths("/" if LOCAL_IS_WINDOWS else os.sep)

    def _resolve(self, path: str) -> str:
        return self._spelled.note(_local_path(path), path)

    def _refuse_home_in(self, paths: Sequence[str]) -> None:
        """Worker thread: a path that cannot be resolved fails on its own later."""
        resolved: list[str] = []
        for path in paths:
            try:
                resolved.append(self._resolve(path))
            except Exception:
                continue
        _local_refuse_home_or_above(resolved)

    # --- paths -----------------------------------------------------------------------

    async def path(self, path: str) -> str:
        return self._resolve(path)

    async def folder(self, directory: str) -> str:
        return self._spelled.note(_local_path(directory), directory)

    async def child(self, directory: str, name: str) -> str:
        """Raises ``NameRefusedError`` for a name that is not one name."""
        folder = await self.folder(directory)
        try:
            return _compose_local_child_path(folder, name)
        except ValueError as exc:
            raise NameRefusedError(str(exc)) from exc

    def parent(self, path: str) -> str:
        return local_path_form(Path(path).parent)

    def check_path(self, path: str) -> str:
        return self._resolve(path)

    async def resolve(self, checked: str) -> str:
        return checked

    def name(self, path: str) -> str:
        return Path(path).name

    def join(self, folder: str, name: str) -> str:
        return local_path_form(Path(folder) / name)

    def as_sent(self, path: str) -> str | None:
        # The system names a path as a `Path` spells it (`C:\x` on Windows); it was
        # noted in the resolved form (`C:/x`).
        return self._spelled.as_sent(local_path_form(path))

    async def prepare(self) -> None:
        return None

    # --- reading ---------------------------------------------------------------------

    async def list_folder(self, directory: str) -> list[APIDirectoryEntry]:
        """Each entry says whether git ignores it."""
        target = Path(directory)
        if not stat_mod.S_ISDIR(target.stat().st_mode):
            raise Failure(REASON_INVALID, NOT_A_FOLDER)
        children = await asyncio.to_thread(_local_children, target)

        # Check gitignored status for all entries in one batch
        ignored_names: set[str] = set()
        if children:
            ignored_names = await _get_gitignored_names(directory, [c.name for c, _ in children])

        return [
            APIDirectoryEntry(
                name=child.name,
                path=local_path_form(child),
                is_directory=is_dir,
                size=size,
                modified=modified,
                mode=mode,
                gitignored=child.name in ignored_names,
            )
            for child, is_dir, size, modified, mode in await asyncio.to_thread(
                _local_entry_stats, children
            )
        ]

    async def stat(self, path: str) -> PathKind:
        p = Path(path)
        # Not `p.exists()`: from Python 3.14 it answers False for a path it may not
        # look at, so a folder sshler cannot read would read as "does not exist".
        # `stat()` raises PermissionError there on every version.
        try:
            p.stat()
        except (FileNotFoundError, NotADirectoryError):
            return PathKind(exists=False, is_directory=False, is_file=False)
        return PathKind(exists=True, is_directory=p.is_dir(), is_file=p.is_file())

    async def read_bytes(self, path: str, limit: int) -> tuple[bytes, bool]:
        return await _local_read_bytes(path, limit)

    async def read_text(self, path: str, limit: int) -> str:
        return await _local_read_text(path, limit)

    async def serve(self, path: str, *, inline: bool, limit: int) -> Response:
        """Streamed from disk, whatever its size: ``limit`` does not apply."""
        await asyncio.to_thread(_local_check_readable, path)
        file_path = Path(path)
        if not inline:
            return FileAnswer(file_path)
        mime, _ = mimetypes.guess_type(path)
        return FileAnswer(
            file_path,
            media_type=mime or "application/octet-stream",
            content_disposition_type="inline",
        )

    async def folder_size(self, path: str) -> int | None:
        return await asyncio.to_thread(_local_folder_size, Path(path))

    async def zip_folder(self, path: str) -> FolderZip:
        base = Path(await asyncio.to_thread(self._resolve, path))
        if not stat_mod.S_ISDIR((await asyncio.to_thread(base.stat)).st_mode):
            raise Failure(REASON_INVALID, "Not a directory")

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
        except BaseException:
            Path(tmp_path).unlink(missing_ok=True)
            raise
        return FolderZip(path=tmp_path, name=base.name or "download")

    async def source(self, path: str) -> SourceFile:
        st = await asyncio.to_thread(os.stat, path)
        return SourceFile(st.st_mode, st.st_size, local_fingerprint(st))

    @contextlib.asynccontextmanager
    async def open_read(self, path: str) -> AsyncIterator[ReadChunk]:
        handle = await asyncio.to_thread(open, path, "rb")
        try:
            yield lambda n: asyncio.to_thread(handle.read, n)
        finally:
            await asyncio.to_thread(handle.close)

    # --- writing ---------------------------------------------------------------------

    async def write_text(self, path: str, content: str) -> None:
        await _local_write_text(path, content)

    async def touch(self, path: str) -> None:
        """A name that exists is a 400."""
        await _local_write_new(path, b"", overwrite=False, exists_status=400)

    async def upload(
        self, path: str, data: bytes, *, overwrite: bool, keep_both: bool = False
    ) -> str:
        """A name that exists is a 409."""
        landed = await _local_write_new(
            path, data, overwrite=overwrite, exists_status=409, keep_both=keep_both
        )
        return local_path_form(landed)

    async def make_folder(self, path: str) -> None:
        path_obj = Path(path)
        if path_obj.exists():
            raise Failure(REASON_INVALID, "Directory already exists")
        path_obj.mkdir(parents=False, exist_ok=False)

    async def land(
        self, path: str, *, source_mode: int, overwrite: bool, keep_both: bool = False
    ) -> _ThreadedLanding:
        await asyncio.to_thread(_local_make_folder, Path(os.path.dirname(path)))
        landing = await asyncio.to_thread(
            _local_landing,
            path,
            overwrite=overwrite,
            source_regular=stat_mod.S_ISREG(source_mode),
            source_mode=source_mode,
            keep_both=keep_both,
        )
        return _ThreadedLanding(landing)

    async def remove_if_unchanged(self, path: str, fingerprint: tuple, target: str) -> None:
        def _check_and_remove() -> None:
            ensure_source_unchanged(fingerprint, local_fingerprint(os.stat(path)), target)
            os.unlink(path)

        await asyncio.to_thread(_check_and_remove)

    async def delete(self, path: str) -> str:
        def _target() -> str:
            resolved = self._resolve(path)
            _local_refuse_home_or_above([resolved])
            return _local_link_itself(path, resolved)

        target = await asyncio.to_thread(_target)
        await _local_delete_file(target)
        return target

    async def chmod(self, path: str, mode: int) -> None:
        await asyncio.to_thread(Path(path).chmod, mode)

    async def rename(
        self, path: str, new_name: str, *, overwrite: bool, keep_both: bool = False
    ) -> str:
        new_name = PathValidator.validate_filename(new_name)

        def _rename() -> str:
            resolved = self._resolve(path)
            _local_refuse_home_or_above([resolved])
            source = Path(_local_link_itself(path, resolved))
            target = str(source.parent / new_name)
            landed = _local_place(
                str(source), target, move=True, overwrite=overwrite, keep_both=keep_both
            )
            return local_path_form(landed)

        return await asyncio.to_thread(_rename)

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
        if new_name:
            PathValidator.validate_filename(new_name)
        resolved = await asyncio.to_thread(self._resolve, source)
        if move:
            _local_refuse_home_or_above([resolved])
            resolved = await asyncio.to_thread(_local_link_itself, source, resolved)
        dest_dir = Path(await asyncio.to_thread(self._resolve, destination))
        landed = await asyncio.to_thread(
            _local_place_into,
            resolved,
            dest_dir,
            new_name,
            move=move,
            overwrite=overwrite,
            allow_dirs=allow_dirs,
            keep_both=keep_both,
        )
        return local_path_form(landed)

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
        archive_path = Path(self._resolve(destination)) / name
        sources = [self._resolve(p) for p in paths]
        made = await asyncio.to_thread(
            partial(
                _local_create_archive,
                sources,
                archive_path,
                fmt,
                overwrite=overwrite,
                keep_both=keep_both,
            )
        )
        return local_path_form(made)

    async def extract_archive(
        self, archive: str, destination: str, *, overwrite: bool = False, keep_both: bool = False
    ) -> str:
        """A missing archive fails first, then a name that is no archive is a 400."""
        archive_path = Path(self._resolve(archive))
        dest_dir = Path(self._resolve(destination))
        await asyncio.to_thread(archive_path.stat)
        if not is_archive_name(archive_path.name):
            raise Failure(REASON_INVALID, "Unsupported archive format")
        unpacked = await asyncio.to_thread(
            partial(
                _local_extract_archive,
                archive_path,
                dest_dir,
                overwrite=overwrite,
                keep_both=keep_both,
            )
        )
        return local_path_form(unpacked)

    async def delete_many(self, paths: Sequence[str]) -> BatchOutcome:
        outcome = BatchOutcome()
        await asyncio.to_thread(self._refuse_home_in, paths)
        for path in paths:
            try:
                normalized = await asyncio.to_thread(self._resolve, path)
                normalized = await asyncio.to_thread(_local_link_itself, path, normalized)
                await _local_batch_delete(normalized)
                outcome.succeeded.append(path)
            except Exception as exc:
                outcome.failed.append(
                    failure_entry(path, exc, as_sent=self.as_sent, missing=MISSING_FILE)
                )
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
        outcome = BatchOutcome()
        landed: set[str] = set()
        dest_dir = Path(await asyncio.to_thread(self._resolve, destination))
        if move:
            await asyncio.to_thread(self._refuse_home_in, paths)
        for path in paths:
            try:
                source = await asyncio.to_thread(self._resolve, path)
                if move:
                    source = await asyncio.to_thread(_local_link_itself, path, source)
                target, at = await asyncio.to_thread(
                    partial(
                        _local_batch_item,
                        source,
                        dest_dir,
                        landed,
                        move=move,
                        overwrite=overwrite,
                        keep_both=keep_both,
                    )
                )
            except Exception as exc:
                outcome.failed.append(
                    failure_entry(path, exc, as_sent=self.as_sent, missing=MISSING_SOURCE)
                )
            else:
                outcome.landed(path, target, at)
                landed.add(at)
        return outcome
