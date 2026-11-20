"""The one place a route gets the files of a box, and how a failure on a remote box is
answered."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass

from fastapi import HTTPException

from ..config import AppConfig, Box
from ..ssh import SSHError
from ..validation import ValidationError
from .dependencies import APIDependencies
from .file_contract import BoxFiles
from .local_files import LocalFiles
from .remote_files import RemoteFiles

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Answers:
    """How a route answers a failure that no operation answered itself.

    On both kinds of box a ``ValidationError`` (a path or a name that fails its check)
    is a 400 with its text. On a remote box, in this order: a failure of a type in
    ``unreachable`` is a 502 with its text; a ``TimeoutError`` is a 504 with
    ``timed_out`` when that is set; anything else is ``other_status`` with
    ``other_prefix`` and its text, logged first when ``log_other`` is set. On the local
    box any other failure goes on unanswered, as each local route left it.
    """

    unreachable: tuple[type[Exception], ...] = (SSHError,)
    timed_out: str | None = None
    other_status: int = 500
    other_prefix: str = ""
    log_other: str | None = None

    def remote(self, exc: Exception) -> HTTPException:
        if isinstance(exc, ValidationError):
            return HTTPException(status_code=400, detail=str(exc))
        if isinstance(exc, self.unreachable):
            return HTTPException(status_code=502, detail=str(exc))
        if self.timed_out is not None and isinstance(exc, TimeoutError):
            return HTTPException(status_code=504, detail=self.timed_out)
        if self.log_other is not None:
            logger.warning(f"{self.log_other}: {exc}")
        return HTTPException(status_code=self.other_status, detail=f"{self.other_prefix}{exc}")


# A remote failure of SSH itself is a 502, anything else a 500 with its text.
DEFAULT_ANSWERS = Answers()


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

    ``answers`` turns each failure into its answer once, on the way out of the block;
    with None every failure goes on as it was raised (a batch collects its own).
    """
    if box.transport == "local":
        try:
            yield LocalFiles()
        except ValidationError as exc:
            if answers is None:
                raise
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return

    remote = RemoteFiles(box, lambda: deps.connect_for_box(box, application_config))
    try:
        async with remote:
            yield remote
    except Exception as exc:
        if answers is None or isinstance(exc, HTTPException):
            raise
        raise answers.remote(exc) from exc
    await remote.after_release()
