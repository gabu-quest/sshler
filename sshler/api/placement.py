"""The rule for a name that already exists, and the parts of it that local and remote
boxes share.
"""

from __future__ import annotations

import os
import stat as stat_mod
from collections.abc import Awaitable, Callable
from typing import NoReturn

import anyio

from .failure import (
    REASON_CANNOT_REPLACE,
    REASON_EXISTS,
    REASON_FAILED,
    Failure,
    Taken,
    failure_of,
)

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

COPY_CHUNK_BYTES = 256 * 1024


class DestinationRefused(Failure):
    """A destination the rule refuses, by reason (``exists`` or ``cannot_replace``),
    with the status the rule gives it (and the names it is about, for many)."""

    def __init__(
        self, status_code: int, detail: str, reason: str, *, taken: Taken | None = None
    ) -> None:
        super().__init__(reason, detail, status=status_code, taken=taken)


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


# The names a refusal of many names lists; the rest are counted.
NAMES_SHOWN = 5
_WOULD_REPLACE = "Files of the archive that already exist here"
_CANNOT_REPLACE_ANY = "Names of the archive taken here by something that cannot be replaced"


def refuse_taken_entries(taken: list[tuple[str, int, bool]], *, overwrite: bool) -> None:
    """The rule for the many names one extract places at once, before any is placed.
    ``taken`` holds each name whose destination exists (and is not a folder that takes
    a folder): the name, the destination's ``lstat`` mode and whether the entry is a
    regular file. Each is decided by ``_refuse_or_replace``; if any is refused, the
    whole request is, by the reason that decides it (``cannot_replace`` before
    ``exists``: a Yes to replace could not place those), with the count and the first
    names of that reason. Returns when every name may be replaced."""
    refused: dict[str, list[str]] = {}
    status: dict[str, int] = {}
    for name, dest_mode, regular in taken:
        try:
            _refuse_or_replace(dest_mode, overwrite=overwrite, source_regular=regular)
        except DestinationRefused as exc:
            refused.setdefault(exc.reason, []).append(name)
            status[exc.reason] = exc.status_code
    for reason, words in (
        (REASON_CANNOT_REPLACE, _CANNOT_REPLACE_ANY),
        (REASON_EXISTS, _WOULD_REPLACE),
    ):
        names = refused.get(reason)
        if not names:
            continue
        shown = ", ".join(names[:NAMES_SHOWN])
        more = len(names) - NAMES_SHOWN
        detail = f"{words} ({len(names)}): {shown}" + (f" and {more} more" if more > 0 else "")
        raise DestinationRefused(
            status[reason],
            detail,
            reason,
            taken=Taken(count=len(names), names=tuple(names[:NAMES_SHOWN])),
        )


def unpacked_not_removed(exc: Exception | None, unpacked: str, cause: BaseException) -> Exception:
    """The answer of an extract whose unpacked folder could not be removed for
    ``cause``: after the failure ``exc`` it keeps that failure's reason and adds where the
    folder is (a timeout stays a ``TimeoutError``, so the connection it ran on is closed,
    not pooled); after a completed extract (``exc`` None) it says the entries are in
    place."""
    where = f"the unpacked folder at {unpacked} could not be removed: {_reason(cause)}"
    if exc is None:
        return Failure(REASON_FAILED, f"Extracted, but {where}")
    if isinstance(exc, TimeoutError):
        return TimeoutError(f"{exc}; {where}")
    found = failure_of(exc)
    return Failure(found.reason, f"{found.detail}; {where}", status=found.status_code)


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


def temp_stem(name: str) -> str:
    """``name`` as the middle of a temp name beside it (a dot, this, a dot, random
    characters and a suffix): cut to 200 bytes, never through a character, so the temp
    name fits where the name itself fits (a file system takes 255 bytes)."""
    return name.encode("utf-8", "surrogateescape")[:200].decode("utf-8", "ignore")


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
        raise Failure(
            REASON_FAILED,
            f"Copied to {target}, but the source changed during the copy and was kept",
        )


def source_not_removed(target: str, cause: str) -> Failure:
    """A move whose copy landed at ``target`` and whose source could not be removed
    for ``cause``: the one failure a single move, a batch and a transfer give it."""
    return Failure(
        REASON_FAILED, f"Copied to {target}, but the source could not be removed: {cause}"
    )


async def run_to_end[T](call: Awaitable[T]) -> T:
    """Await a cleanup to its end even when the request is being cancelled (a client
    that went away cancels a streaming response, and every further await with it)."""
    with anyio.CancelScope(shield=True):
        result = await call
    return result
