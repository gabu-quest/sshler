"""The interface every kind of box keeps for its files, and the values it returns.

``LocalFiles`` (``local_files.py``) keeps it on the local file system and
``RemoteFiles`` (``remote_files.py``) over SFTP on one pooled connection; a route gets
the right one from ``box_files`` (``box_files.py``) and never asks which it holds.
``tests/test_box_files_contract.py`` proves the interface for each kind.

Paths: ``path``, ``folder`` and ``child`` turn what a client sent into the path that
kind of box acts on; every other method that takes a ``path`` takes one of those.
``rename``, ``place_into``, ``zip_folder`` and the two batch methods take paths as the
client sent them, because each kind checks them in its own order before it resolves
them.

Answers: an operation raises ``HTTPException`` for the answers it gives itself (a
missing directory, a name the existing-name rule refuses, a file too large); any other
failure is answered by ``box_files`` as the route asked, or not at all where the kind
of box never answered it.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import NamedTuple, Protocol

from fastapi.responses import Response

from .models import APIDirectoryEntry


class NameRefusedError(ValueError):
    """``child`` on the local box was given a name that is not one name.

    The local routes answer it differently (an upload with a 400, a new file or folder
    not at all), so it is raised for the route; a remote box refuses the name with a
    ``ValidationError``, which ``box_files`` answers.
    """


class PathKind(NamedTuple):
    """What ``stat`` found at a path."""

    exists: bool
    is_directory: bool
    is_file: bool


class FolderZip(NamedTuple):
    """A folder zipped into a temp file: the caller owns ``path`` and deletes it."""

    path: str
    name: str  # the folder's name, for the download's file name


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

    async def delete_many(self, paths: Sequence[str]) -> BatchOutcome:
        """Delete each path, a folder with everything in it."""
        ...

    async def place_many(
        self, paths: Sequence[str], destination: str, *, move: bool, overwrite: bool
    ) -> BatchOutcome:
        """Move or copy each path into ``destination`` under the existing-name rule;
        a second item with the same name as one placed before it is refused."""
        ...
