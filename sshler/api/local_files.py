"""The files of the local box: ``LocalFiles``, and the placing, listing, reading and
writing it is built from."""

from __future__ import annotations

import asyncio
import contextlib
import errno
import mimetypes
import os
import shutil
import stat as stat_mod
import tarfile
import tempfile
import zipfile
from collections.abc import AsyncIterator, Sequence
from pathlib import Path

from fastapi import HTTPException
from fastapi.responses import FileResponse, Response

from ..proc import run_bounded
from ..validation import PathValidator
from .file_contract import (
    TAR_SUFFIXES,
    BatchOutcome,
    FolderZip,
    NameRefusedError,
    PathKind,
    ReadChunk,
    SourceFile,
    is_archive_name,
)
from .helpers import (
    LOCAL_IS_WINDOWS,
    _normalize_local_path,
)
from .models import APIDirectoryEntry
from .placement import (
    _NO_FOLDER_COPY,
    _ONLY_FILES,
    COPY_CHUNK_BYTES,
    _refuse_a_folder_into_itself,
    _refuse_or_replace,
    _taken_after_check,
    ensure_source_unchanged,
    failure_entry,
    kept_permission_bits,
    refuse_a_name_this_request_placed,
    run_to_end,
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


class _ThreadedLanding:
    """The async face of a ``_LocalLanding``: each call runs in a worker thread."""

    def __init__(self, landing: _LocalLanding) -> None:
        self.landing = landing

    async def write(self, data: bytes) -> None:
        await asyncio.to_thread(self.landing.write, data)

    async def commit(self) -> None:
        await asyncio.to_thread(self.landing.commit)

    async def abort(self) -> None:
        """To the end even if the request is cancelled: a worker-thread job cancelled
        before it starts never runs."""
        await run_to_end(asyncio.to_thread(self.landing.abort))


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


def _compose_local_child_path(directory: str, filename: str) -> str:
    cleaned = (filename or "").strip()
    if not cleaned:
        raise ValueError("Filename is required")
    if cleaned in {".", ".."} or any(sep in cleaned for sep in ("/", "\\")):
        raise ValueError("Filename cannot contain path separators")
    parent = Path(directory or Path.home())
    target = parent / cleaned
    if LOCAL_IS_WINDOWS:
        return target.expanduser().as_posix()
    return str(target.expanduser())


async def _local_read_text(path: str, max_bytes: int) -> str:
    def _worker() -> str:
        with open(Path(path), "rb") as handle:
            data = handle.read(max_bytes)
        return data.decode("utf-8", errors="replace")

    return await asyncio.to_thread(_worker)


async def _local_write_text(path: str, content: str) -> None:
    def _worker() -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with open(Path(path), "w", encoding="utf-8") as handle:
            handle.write(content)

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
        if target.is_dir():
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
            raise HTTPException(
                status_code=400,
                detail=f"Archive contains unsafe path: {info.filename}",
            )
        # Rewrite the filename to the safe version before extracting
        info.filename = safe_name
        zf.extract(info, dest_dir)


def _local_create_archive(paths: Sequence[str], archive_path: Path, fmt: str) -> None:
    """Pack each path under its own name; a folder in a zip is packed file by file."""
    if fmt in ("tar.gz", "tgz"):
        with tarfile.open(str(archive_path), "w:gz") as tf:
            for p in paths:
                source = Path(_normalize_local_path(p))
                tf.add(str(source), arcname=source.name)
        return
    with zipfile.ZipFile(str(archive_path), "w", zipfile.ZIP_DEFLATED) as zf:
        for p in paths:
            source = Path(_normalize_local_path(p))
            if source.is_dir():
                for child in source.rglob("*"):
                    if child.is_file():
                        arcname = str(source.name / child.relative_to(source))
                        zf.write(str(child), arcname)
            else:
                zf.write(str(source), source.name)


def _local_extract_archive(archive: Path, dest_dir: Path) -> None:
    """A tar is unpacked with tarfile's ``data`` filter (no entry outside ``dest_dir``,
    no link that leaves it, no device file); a zip entry that would leave ``dest_dir``
    refuses the whole zip with a 400."""
    if archive.name.lower().endswith(TAR_SUFFIXES):
        with tarfile.open(str(archive), "r:gz") as tf:
            tf.extractall(path=str(dest_dir), filter="data")
    else:
        with zipfile.ZipFile(str(archive), "r") as zf:
            _safe_extract_zip(zf, dest_dir)


def _local_batch_item(
    path: str, dest_dir: Path, landed: set[str], *, move: bool, overwrite: bool
) -> str:
    """Move or copy one local item into ``dest_dir`` (call from a worker thread)."""
    source = _normalize_local_path(path)
    if not os.path.lexists(source):
        raise FileNotFoundError(f"No such file or directory: {path}")
    _local_refuse_a_folder_into_itself(source, dest_dir, move=move)
    target = str(_local_make_folder(dest_dir) / Path(source).name)
    refuse_a_name_this_request_placed(target, landed)
    _local_place(source, target, move=move, overwrite=overwrite)
    return target


class LocalFiles:
    """The files of the local box, on ``pathlib`` and the helpers above.

    A path is normalised and nothing is checked before that. A failure an operation
    does not answer itself (a folder that cannot be read) goes on unanswered. A
    download streams from disk and is never read into memory.
    """

    # --- paths -----------------------------------------------------------------------

    async def path(self, path: str) -> str:
        return _normalize_local_path(path)

    async def folder(self, directory: str) -> str:
        return _normalize_local_path(directory)

    async def child(self, directory: str, name: str) -> str:
        """Raises ``NameRefusedError`` for a name that is not one name."""
        folder = _normalize_local_path(directory)
        try:
            return _compose_local_child_path(folder, name)
        except ValueError as exc:
            raise NameRefusedError(str(exc)) from exc

    def parent(self, path: str) -> str:
        return str(Path(path).parent)

    def check_path(self, path: str) -> str:
        return _normalize_local_path(path)

    async def resolve(self, checked: str) -> str:
        return checked

    def name(self, path: str) -> str:
        return Path(path).name

    def join(self, folder: str, name: str) -> str:
        return str(Path(folder) / name)

    async def prepare(self) -> None:
        return None

    # --- reading ---------------------------------------------------------------------

    async def list_folder(self, directory: str) -> list[APIDirectoryEntry]:
        """Each entry says whether git ignores it."""
        target = Path(directory)
        if not target.exists():
            raise HTTPException(status_code=404, detail="Directory not found")
        if not target.is_dir():
            raise HTTPException(status_code=400, detail="Path is not a directory")
        children = await asyncio.to_thread(_local_children, target)

        # Check gitignored status for all entries in one batch
        ignored_names: set[str] = set()
        if children:
            ignored_names = await _get_gitignored_names(directory, [c.name for c, _ in children])

        return [
            APIDirectoryEntry(
                name=child.name,
                path=str(child),
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
        if not p.exists():
            return PathKind(exists=False, is_directory=False, is_file=False)
        return PathKind(exists=True, is_directory=p.is_dir(), is_file=p.is_file())

    async def read_bytes(self, path: str, limit: int) -> tuple[bytes, bool]:
        return await _local_read_bytes(path, limit)

    async def read_text(self, path: str, limit: int) -> str:
        return await _local_read_text(path, limit)

    async def serve(self, path: str, *, inline: bool, limit: int) -> Response:
        """Streamed from disk, whatever its size: ``limit`` does not apply."""
        file_path = Path(path)
        if not file_path.exists() or not file_path.is_file():
            raise HTTPException(status_code=404, detail="File not found")
        if not inline:
            return FileResponse(file_path)
        mime, _ = mimetypes.guess_type(path)
        return FileResponse(
            file_path,
            media_type=mime or "application/octet-stream",
            content_disposition_type="inline",
        )

    async def folder_size(self, path: str) -> int | None:
        def _calc_size() -> int:
            total = 0
            base = Path(path)
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
            return await asyncio.to_thread(_calc_size)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    async def zip_folder(self, path: str) -> FolderZip:
        base = Path(_normalize_local_path(path))
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
        try:
            await _local_write_text(path, content)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail="File not found") from exc
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    async def touch(self, path: str) -> None:
        """A name that exists is a 400."""
        await self._write_new(path, b"", overwrite=False, exists_status=400)

    async def upload(self, path: str, data: bytes, *, overwrite: bool) -> None:
        """A name that exists is a 409."""
        await self._write_new(path, data, overwrite=overwrite, exists_status=409)

    async def _write_new(
        self, path: str, data: bytes, *, overwrite: bool, exists_status: int
    ) -> None:
        try:
            await _local_write_new(path, data, overwrite=overwrite, exists_status=exists_status)
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    async def make_folder(self, path: str) -> None:
        path_obj = Path(path)
        if path_obj.exists():
            raise HTTPException(status_code=400, detail="Directory already exists")
        try:
            path_obj.mkdir(parents=False, exist_ok=False)
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    async def land(self, path: str, *, source_mode: int, overwrite: bool) -> _ThreadedLanding:
        await asyncio.to_thread(os.makedirs, os.path.dirname(path), exist_ok=True)
        landing = await asyncio.to_thread(
            _local_landing,
            path,
            overwrite=overwrite,
            source_regular=stat_mod.S_ISREG(source_mode),
            source_mode=source_mode,
        )
        return _ThreadedLanding(landing)

    async def remove_if_unchanged(self, path: str, fingerprint: tuple, target: str) -> None:
        def _check_and_remove() -> None:
            ensure_source_unchanged(fingerprint, local_fingerprint(os.stat(path)), target)
            os.unlink(path)

        await asyncio.to_thread(_check_and_remove)

    async def delete(self, path: str) -> None:
        try:
            await _local_delete_file(path)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail="File not found") from exc
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    async def chmod(self, path: str, mode: int) -> None:
        target = Path(path)
        if not target.exists():
            raise HTTPException(status_code=404, detail="File not found")
        try:
            target.chmod(mode)
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    async def rename(self, path: str, new_name: str, *, overwrite: bool) -> str:
        new_name = PathValidator.validate_filename(new_name)

        def _rename() -> str:
            source = Path(_normalize_local_path(path))
            target = str(source.parent / new_name)
            _local_place(str(source), target, move=True, overwrite=overwrite)
            return target

        try:
            return await asyncio.to_thread(_rename)
        except HTTPException:
            raise
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail="File not found") from exc
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    async def place_into(
        self,
        source: str,
        destination: str,
        new_name: str | None,
        *,
        move: bool,
        overwrite: bool,
        allow_dirs: bool,
    ) -> str:
        if new_name:
            PathValidator.validate_filename(new_name)
        try:
            return await asyncio.to_thread(
                _local_place_into,
                source,
                destination,
                new_name,
                move=move,
                overwrite=overwrite,
                allow_dirs=allow_dirs,
            )
        except HTTPException:
            raise
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Source not found") from exc
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    async def create_archive(
        self, paths: Sequence[str], destination: str, name: str, fmt: str
    ) -> str:
        """Any failure is a 500 with its text."""
        archive_path = Path(_normalize_local_path(destination)) / name
        try:
            await asyncio.to_thread(_local_create_archive, paths, archive_path, fmt)
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        return str(archive_path)

    async def extract_archive(self, archive: str, destination: str) -> str:
        """A missing archive is a 404, then a name that is no archive a 400; any other
        failure (a tar entry the filter refuses among them) is a 500 with its text."""
        archive_path = Path(_normalize_local_path(archive))
        dest_dir = Path(_normalize_local_path(destination))
        if not archive_path.exists():
            raise HTTPException(status_code=404, detail="Archive not found")
        if not is_archive_name(archive_path.name):
            raise HTTPException(status_code=400, detail="Unsupported archive format")
        try:
            await asyncio.to_thread(_local_extract_archive, archive_path, dest_dir)
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        return str(dest_dir)

    async def delete_many(self, paths: Sequence[str]) -> BatchOutcome:
        outcome = BatchOutcome()
        for path in paths:
            try:
                normalized = await asyncio.to_thread(_normalize_local_path, path)
                await _local_batch_delete(normalized)
                outcome.succeeded.append(path)
            except Exception as exc:
                outcome.failed.append({"path": path, "error": str(exc)})
        return outcome

    async def place_many(
        self, paths: Sequence[str], destination: str, *, move: bool, overwrite: bool
    ) -> BatchOutcome:
        outcome = BatchOutcome()
        landed: set[str] = set()
        try:
            dest_dir = await asyncio.to_thread(_local_destination, destination)
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        for path in paths:
            try:
                target = await asyncio.to_thread(
                    _local_batch_item, path, dest_dir, landed, move=move, overwrite=overwrite
                )
            except Exception as exc:
                outcome.failed.append(failure_entry(path, exc))
            else:
                landed.add(target)
                outcome.succeeded.append(path)
        return outcome
