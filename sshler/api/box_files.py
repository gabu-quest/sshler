"""The one place a route gets the files of a box; a failure in the block is answered by
``failure_of`` (``failure.py``) on the way out, the same way for both kinds of box."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass

from ..config import AppConfig, Box
from .dependencies import APIDependencies
from .failure import failure_of
from .file_contract import BoxFiles
from .local_files import LocalFiles
from .remote_files import RemoteFiles

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Answers:
    """The texts a route gives a failure, where it has its own: ``missing`` for a thing
    not found, ``timed_out`` for work past its limit; ``log_other`` logs every failure
    first with that line. The reason and the status are always ``failure_of``'s."""

    missing: str | None = None
    timed_out: str | None = None
    log_other: str | None = None


DEFAULT_ANSWERS = Answers()


def is_local(box: Box) -> bool:
    """The one test of the kind of a box: its files and its commands are this
    machine's (the local box) or reached over SSH (every other box)."""
    return box.transport == "local"


@asynccontextmanager
async def box_files(
    deps: APIDependencies,
    box: Box,
    application_config: AppConfig,
    answers: Answers | None = DEFAULT_ANSWERS,
) -> AsyncIterator[BoxFiles]:
    """The files of ``box`` for the block.

    For a remote box the pooled connection is taken on the first operation that needs
    it (none before the checks that come first) and given back when the block ends, and
    the SFTP session opened on it is closed before that, on every way out. A remote
    listing records the folder visit once the connection is back in the pool.

    Every failure in the block leaves it as a ``Failure`` named by ``failure_of``, with
    the texts of ``answers``; with None every failure goes on as it was raised (a
    transfer collects its own).
    """
    files: LocalFiles | RemoteFiles
    if is_local(box):
        files = LocalFiles()
        try:
            yield files
        except Exception as exc:
            answered = exc if answers is None else _answered(exc, files, answers)
            if answered is exc:
                raise
            raise answered from exc
        return

    files = RemoteFiles(box, lambda: deps.connect_for_box(box, application_config))
    try:
        async with files:
            yield files
    except Exception as exc:
        answered = exc if answers is None else _answered(exc, files, answers)
        if answered is exc:
            raise
        raise answered from exc
    await files.after_release()


def _answered(exc: Exception, files: BoxFiles, answers: Answers) -> Exception:
    if answers.log_other is not None:
        logger.warning(f"{answers.log_other}: {exc}")
    return failure_of(
        exc, as_sent=files.as_sent, missing=answers.missing, timed_out=answers.timed_out
    )
