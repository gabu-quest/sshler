"""The interface every kind of box keeps for its files, and the values it returns.

``LocalFiles`` (``local_files.py``) keeps it on the local file system and
``RemoteFiles`` (``remote_files.py``) over SFTP on one pooled connection; a route gets
the right one from ``box_files`` (``box_files.py``) and never asks which it holds.
``tests/test_box_files_contract.py`` proves the interface for each kind.

Paths: ``path``, ``folder`` and ``child`` turn what a client sent into the path that
kind of box acts on; every other method that takes a ``path`` takes one of those.
``rename``, ``place_into``, ``zip_folder``, the two archive methods and the two batch
methods take paths as the client sent them, because each kind checks them in its own
order before it resolves them. A transfer splits ``path`` in two: ``check_path``
before anything is opened, ``resolve`` once the box is reached.

Failures: an operation raises ``Failure`` (``failure.py``) only for an answer it gives
itself (a size limit, a name the existing-name rule refuses, a folder where a file is
needed that SFTP version 3 cannot name); any other failure goes on as raised, and
``box_files`` answers it with ``failure_of``, the same way for both kinds of box.
``as_sent`` gives back the client's spelling of a path the box resolved, so an answer
never names a server path the client did not send.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass, field
from typing import NamedTuple, Protocol

from fastapi.responses import Response

from .models import APIDirectoryEntry


class NameRefusedError(ValueError):
    """``child`` on the local box was given a name that is not one name; answered as
    ``invalid``, as a remote box's ``ValidationError`` for the same name is."""


# A folder was asked for and the path is not one.
NOT_A_FOLDER = "Path is not a directory"

# What a route, and a batch item of the same operation, says of a thing not found.
MISSING_FILE = "File not found"
MISSING_SOURCE = "Source not found"
MISSING_FOLDER = "Directory not found"
MISSING_ARCHIVE = "Archive not found"


class SpelledPaths:
    """The paths a block resolved, each with the spelling the client sent, for
    ``BoxFiles.as_sent``."""

    def __init__(self, separator: str) -> None:
        self._separator = separator
        self._sent: dict[str, str] = {}

    def note(self, resolved: str, sent: str) -> str:
        """Remember ``sent`` for ``resolved``; returns ``resolved``."""
        self._sent.setdefault(resolved, sent)
        return resolved

    def as_sent(self, path: str) -> str | None:
        """``path`` spelled from the longest resolved path that is it or holds it."""
        best: str | None = None
        for resolved in self._sent:
            inside = path == resolved or path.startswith(
                resolved.rstrip(self._separator) + self._separator
            )
            if inside and (best is None or len(resolved) > len(best)):
                best = resolved
        if best is None:
            return None
        rest = path[len(best) :].lstrip(self._separator)
        sent = self._sent[best]
        return f"{sent.rstrip(self._separator)}{self._separator}{rest}" if rest else sent


# The archive names both kinds pack and unpack; a tar is gzipped.
TAR_SUFFIXES = (".tar.gz", ".tgz")
ARCHIVE_SUFFIXES = (*TAR_SUFFIXES, ".zip")


def is_archive_name(name: str) -> bool:
    """Whether ``name`` (a file name, not a path) ends in a suffix of ``ARCHIVE_SUFFIXES``,
    without regard to case."""
    return name.lower().endswith(ARCHIVE_SUFFIXES)


class PathKind(NamedTuple):
    """What ``stat`` found at a path."""

    exists: bool
    is_directory: bool
    is_file: bool


class FolderZip(NamedTuple):
    """A folder zipped into a temp file: the caller owns ``path`` and deletes it."""

    path: str
    name: str  # the folder's name, for the download's file name


class SourceFile(NamedTuple):
    """What a file to be copied is before its first byte is read, links followed."""

    mode: int  # the type and permission bits; 0 when the box sent none
    size: int
    fingerprint: tuple  # what ``remove_if_unchanged`` compares


class Landing(Protocol):
    """A file being written beside its name: ``commit`` puts it in place, ``abort``
    removes what was written (to the end, even when the request is being cancelled)."""

    async def write(self, data: bytes) -> None: ...

    async def commit(self) -> None: ...

    async def abort(self) -> None: ...


# ``read(n)``: up to ``n`` bytes of an open file, ``b""`` at its end.
ReadChunk = Callable[[int], Awaitable[bytes]]


@dataclass
class BatchOutcome:
    """What a batch did: the paths done, and one entry per path that failed."""

    succeeded: list[str] = field(default_factory=list)
    failed: list[dict] = field(default_factory=list)


class BoxFiles(Protocol):
    """The files of one box, for the length of one ``box_files`` block."""

    # --- paths -----------------------------------------------------------------------

    async def path(self, path: str) -> str:
        """The path to act on. A local path is normalised (``~`` expanded, links
        resolved); a remote one is checked, then ``~`` is resolved to the home."""
        ...

    async def folder(self, directory: str) -> str:
        """The folder to list: as ``path``, except that a remote folder is not checked
        and an empty one is ``/``."""
        ...

    async def child(self, directory: str, name: str) -> str:
        """``name`` in folder ``directory``; a name that is not one name is refused
        (``NameRefusedError`` on the local box)."""
        ...

    def parent(self, path: str) -> str:
        """The folder that holds ``path``."""
        ...

    def check_path(self, path: str) -> str:
        """``path`` checked without reaching the box (a blocking call: a transfer runs
        it in a worker thread). A local path is normalised (``~`` expanded, links
        resolved); a remote one is validated and kept as spelled, its ``~`` left for
        ``resolve``. A bad path raises ``ValidationError`` or ``ValueError``."""
        ...

    async def resolve(self, checked: str) -> str:
        """The path to act on, from one ``check_path`` gave: a remote ``~`` is resolved
        to the home on the box's connection; a local path is already final."""
        ...

    def name(self, path: str) -> str:
        """The last part of ``path``, as this kind of box spells paths."""
        ...

    def join(self, folder: str, name: str) -> str:
        """``name`` in ``folder``, as this kind of box spells paths; nothing is checked."""
        ...

    def as_sent(self, path: str) -> str | None:
        """The client's spelling of ``path`` (or of a path under it), a path this block
        resolved from what the client sent; None for any other path."""
        ...

    async def prepare(self) -> None:
        """Reach the box now rather than at the first operation that needs it. On a
        remote box the pooled connection and the SFTP session are taken here, in that
        order, so a box that cannot be reached fails at this call; the local box has
        nothing to take."""
        ...

    # --- reading ---------------------------------------------------------------------

    async def list_folder(self, directory: str) -> list[APIDirectoryEntry]:
        """The entries of a folder, folders first, then by name without regard to case."""
        ...

    async def stat(self, path: str) -> PathKind: ...

    async def read_bytes(self, path: str, limit: int) -> tuple[bytes, bool]:
        """Up to ``limit`` bytes, and whether the file is larger (its bytes are then
        not to be used)."""
        ...

    async def read_text(self, path: str, limit: int) -> str:
        """Up to ``limit`` bytes as UTF-8; bytes that are not UTF-8 are replaced."""
        ...

    async def serve(self, path: str, *, inline: bool, limit: int) -> Response:
        """The file as a download, or ``inline`` with its own media type for the
        browser to show."""
        ...

    async def folder_size(self, path: str) -> int | None:
        """The bytes in a folder; None when it exists but its size cannot be told."""
        ...

    async def zip_folder(self, path: str) -> FolderZip:
        """A folder as a zip in a temp file."""
        ...

    async def source(self, path: str) -> SourceFile:
        """A file about to be copied: its mode, its size and its fingerprint."""
        ...

    def open_read(self, path: str) -> AbstractAsyncContextManager[ReadChunk]:
        """The file open for reading in chunks for the block, never held whole in
        memory; a local read runs in a worker thread."""
        ...

    # --- writing ---------------------------------------------------------------------

    async def write_text(self, path: str, content: str) -> None: ...

    async def touch(self, path: str) -> None:
        """A new, empty file; a name that exists is refused."""
        ...

    async def upload(self, path: str, data: bytes, *, overwrite: bool) -> None:
        """A new file with ``data``; a regular file on the name is replaced only with
        ``overwrite``."""
        ...

    async def make_folder(self, path: str) -> None: ...

    async def land(self, path: str, *, source_mode: int, overwrite: bool) -> Landing:
        """A landing for the bytes of a file at ``path``, its folder made if missing.

        The name is decided by the existing-name rule (``DestinationRefused``), and a
        source that is not a regular file is refused. A replaced file keeps its
        permission bits; a free name gets ``source_mode``'s nine permission bits from
        before the first byte (the box's default bits when it is 0 on a remote box).
        A local landing writes in a worker thread."""
        ...

    async def remove_if_unchanged(self, path: str, fingerprint: tuple, target: str) -> None:
        """Remove a moved source only if its fingerprint is still ``fingerprint``
        (``ensure_source_unchanged``, which names ``target``); otherwise keep it."""
        ...

    async def delete(self, path: str) -> None:
        """Delete one file."""
        ...

    async def chmod(self, path: str, mode: int) -> None: ...

    async def rename(self, path: str, new_name: str, *, overwrite: bool) -> str:
        """Give ``path`` the name ``new_name`` in its own folder under the existing-name
        rule; returns the new path."""
        ...

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
        """Move or copy ``source`` into folder ``destination`` as ``new_name`` (its own
        name when None) under the existing-name rule; returns the new path."""
        ...

    async def create_archive(
        self, paths: Sequence[str], destination: str, name: str, fmt: str
    ) -> str:
        """Pack ``paths`` into a new archive ``name`` in folder ``destination``; ``fmt``
        (``tar.gz``, ``tgz`` or ``zip``) and ``name`` are checked by the route. Returns
        the archive's path. A folder is packed with everything in it, under its own
        name."""
        ...

    async def extract_archive(self, archive: str, destination: str) -> str:
        """Unpack ``archive`` (a ``.tar.gz``, ``.tgz`` or ``.zip`` by its name; any other
        name is a 400) into folder ``destination``; returns the destination's path. On
        the local box no entry is written outside ``destination``."""
        ...

    async def delete_many(self, paths: Sequence[str]) -> BatchOutcome:
        """Delete each path, a folder with everything in it."""
        ...

    async def place_many(
        self, paths: Sequence[str], destination: str, *, move: bool, overwrite: bool
    ) -> BatchOutcome:
        """Move or copy each path into ``destination`` under the existing-name rule;
        a second item with the same name as one placed before it is refused."""
        ...
