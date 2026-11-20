"""The rule for a name that already exists, and the parts of it that local and remote
boxes share.
"""

from __future__ import annotations

import os
import stat as stat_mod
from collections.abc import Awaitable, Callable
from typing import NoReturn

import anyio

from .failure import REASON_CANNOT_REPLACE, REASON_EXISTS, Failure
from .failure import REASON_FAILED as REASON_FAILED  # the transfer's own failed items
from .failure import failure_entry as failure_entry  # a batch's and a transfer's items

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
    with the status the rule gives it."""

    def __init__(self, status_code: int, detail: str, reason: str) -> None:
        super().__init__(reason, detail, status=status_code)


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
        raise Failure(
            REASON_FAILED,
            f"Copied to {target}, but the source changed during the copy and was kept",
        )


async def run_to_end[T](call: Awaitable[T]) -> T:
    """Await a cleanup to its end even when the request is being cancelled (a client
    that went away cancels a streaming response, and every further await with it)."""
    with anyio.CancelScope(shield=True):
        result = await call
    return result
