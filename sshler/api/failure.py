"""The answer of every failed request: its reason, its status and its body.

Every failure answers ``{"detail": <text>, "reason": <word>}`` with the status of its
reason. ``Failure`` is the one exception a route, an operation or a dependency raises
to answer by reason; ``failure_of`` is the one function that names the reason of any
other failure (of the system, of SFTP or SSH, of a path check, of a time limit, of a
program that is not on the box) for both kinds of box; ``failure_entry`` gives a batch
item the same reason and text; ``failure_body`` is the body itself, which a stream's
``error`` event carries too; the handlers ``install`` registers write the body, and
``answer`` writes it for a check that answers before any route runs.
"""

from __future__ import annotations

import errno
import logging
import os
from collections.abc import Callable, Mapping

import asyncssh
from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from ..ssh import SSHError
from ..validation import ValidationError
from .file_contract import NameRefusedError
from .run_failures import ProgramNotFoundError

logger = logging.getLogger(__name__)

# Each reason and its one status. `invalid` is also the reason of FastAPI's own check of
# a request's shape, which answers 422.
STATUS_OF_REASON: dict[str, int] = {
    "invalid": 400,
    "auth": 401,
    "token": 403,
    "origin": 403,
    "denied": 403,
    "not_found": 404,
    "exists": 409,
    "cannot_replace": 409,
    "too_large": 413,
    "rate_limited": 429,
    "failed": 500,
    "internal": 500,
    "program_missing": 501,
    "unreachable": 502,
    "timeout": 504,
}

REASON_INVALID = "invalid"
REASON_DENIED = "denied"
REASON_NOT_FOUND = "not_found"
REASON_EXISTS = "exists"  # a regular file has the name; `overwrite` would replace it
REASON_CANNOT_REPLACE = "cannot_replace"  # the name is taken; `overwrite` cannot replace it
REASON_FAILED = "failed"
REASON_PROGRAM_MISSING = "program_missing"
REASON_UNREACHABLE = "unreachable"
REASON_TIMEOUT = "timeout"

# The reason of an `HTTPException` raised without one, by its status.
_REASON_OF_STATUS: dict[int, str] = {
    400: "invalid",
    422: "invalid",
    401: "auth",
    403: "token",
    404: "not_found",
    409: "exists",
    413: "too_large",
    429: "rate_limited",
    501: "program_missing",
    502: "unreachable",
    504: "timeout",
}

_TIMED_OUT = "Timed out"


class Failure(HTTPException):
    """A failed request, raised by its reason: it answers the reason's status (or
    ``status`` where an answer kept its own, as the existing-name rule does) and
    ``{"detail": detail, "reason": reason}``, with ``headers``."""

    def __init__(
        self,
        reason: str,
        detail: str,
        *,
        status: int | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> None:
        if reason not in STATUS_OF_REASON:
            raise ValueError(f"not a reason of a failed request: {reason!r}")
        super().__init__(
            status_code=STATUS_OF_REASON[reason] if status is None else status,
            detail=detail,
            headers=dict(headers) if headers else None,
        )
        self.reason = reason


def _reason_of_status(status: int) -> str:
    if status in _REASON_OF_STATUS:
        return _REASON_OF_STATUS[status]
    return "failed" if status >= 500 else "invalid"


# The errno of a system error and its reason; any other errno is `failed`.
_REASON_OF_ERRNO: dict[int, str] = {
    errno.ENOENT: "not_found",
    # A path through a file: SFTP version 3 and OpenSSH answer it as "no such file".
    errno.ENOTDIR: "not_found",
    errno.EACCES: "denied",
    errno.EPERM: "denied",
    errno.EISDIR: "invalid",
    errno.EINVAL: "invalid",
    errno.ENAMETOOLONG: "invalid",
    errno.ELOOP: "invalid",
    errno.EILSEQ: "invalid",
}

# The SFTP error types and their reason; any other SFTP error is `failed`.
_REASON_OF_SFTP: tuple[tuple[type[asyncssh.SFTPError], str], ...] = (
    (asyncssh.SFTPNoSuchFile, "not_found"),
    (asyncssh.SFTPNoSuchPath, "not_found"),
    (asyncssh.SFTPPermissionDenied, "denied"),
    (asyncssh.SFTPNotADirectory, "not_found"),
    (asyncssh.SFTPFileIsADirectory, "invalid"),
    (asyncssh.SFTPInvalidFilename, "invalid"),
    (asyncssh.SFTPInvalidParameter, "invalid"),
    (asyncssh.SFTPLinkLoop, "invalid"),
    (asyncssh.SFTPNoConnection, "unreachable"),
    (asyncssh.SFTPConnectionLost, "unreachable"),
)


def _text(value: object) -> str:
    """A text that encodes as UTF-8 (a lone surrogate, as a file name that is not
    UTF-8 decodes to, becomes "?")."""
    text = value if isinstance(value, str) else str(value)
    return text.encode("utf-8", "replace").decode("utf-8")


def _system_words(exc: OSError, as_sent: Callable[[str], str | None] | None) -> str:
    """The system's message, with the path as the client sent it when the failure names
    a path the client sent (never a server path the client did not send)."""
    words = exc.strerror or (os.strerror(exc.errno) if exc.errno else str(exc))
    filename = exc.filename
    if isinstance(filename, bytes):
        filename = os.fsdecode(filename)
    if isinstance(filename, str) and as_sent is not None:
        sent = as_sent(filename)
        if sent is not None:
            return f"{words}: {sent}"
    return words


def failure_of(
    exc: BaseException,
    *,
    as_sent: Callable[[str], str | None] | None = None,
    missing: str | None = None,
    timed_out: str | None = None,
) -> Failure:
    """The answer of ``exc``, decided by its type (and errno or SFTP code), never by
    its words.

    ``as_sent`` gives back the client's spelling of a path the box resolved; ``missing``
    and ``timed_out`` are the texts a route gives a thing not found and work past its
    limit. A failure with no known reason is ``internal``, logged with its traceback.
    """
    found = _named(exc, as_sent, timed_out)
    if missing is not None and found.reason == REASON_NOT_FOUND and not _through_a_file(exc):
        return Failure(REASON_NOT_FOUND, missing)
    return found


def _through_a_file(exc: BaseException) -> bool:
    """A path that goes on through a file: not the thing asked for gone, so it keeps the
    system's words (which name the path) instead of the route's text for a thing not
    found. A failure raised by reason keeps its own text too."""
    if isinstance(exc, (Failure, asyncssh.SFTPNotADirectory)):
        return True
    return isinstance(exc, OSError) and exc.errno == errno.ENOTDIR


def _named(
    exc: BaseException,
    as_sent: Callable[[str], str | None] | None,
    timed_out: str | None,
) -> Failure:
    if isinstance(exc, Failure):
        return exc
    if isinstance(exc, StarletteHTTPException):
        return Failure(
            _reason_of_status(exc.status_code),
            _text(exc.detail),
            status=exc.status_code,
            headers=exc.headers,
        )
    if isinstance(exc, (ValidationError, NameRefusedError)):
        return Failure(REASON_INVALID, _text(exc))
    if isinstance(exc, ProgramNotFoundError):
        return Failure(REASON_PROGRAM_MISSING, _text(exc))
    if isinstance(exc, (SSHError, ConnectionError, asyncssh.DisconnectError)):
        return Failure(REASON_UNREACHABLE, _text(exc))
    if isinstance(exc, TimeoutError):
        return Failure(REASON_TIMEOUT, timed_out or _text(exc) or _TIMED_OUT)
    if isinstance(exc, asyncssh.SFTPError):
        for kind, reason in _REASON_OF_SFTP:
            if isinstance(exc, kind):
                return Failure(reason, _text(exc.reason or exc))
        return Failure(REASON_FAILED, _text(exc.reason or exc))
    if isinstance(exc, (asyncssh.ProcessError, asyncssh.ChannelOpenError)):
        return Failure(REASON_FAILED, _text(_process_words(exc)))
    if isinstance(exc, OSError):
        reason = _REASON_OF_ERRNO.get(exc.errno or 0, REASON_FAILED)
        return Failure(reason, _text(_system_words(exc, as_sent)))
    logger.error("Unanswered failure: %s", exc, exc_info=exc)
    return Failure("internal", _text(f"{type(exc).__name__}: {exc}"))


def _process_words(exc: asyncssh.ProcessError | asyncssh.ChannelOpenError) -> str:
    """A program's error output, else its exit; a refused channel's reason."""
    if isinstance(exc, asyncssh.ProcessError):
        stderr = exc.stderr
        if isinstance(stderr, bytes):
            stderr = stderr.decode("utf-8", "replace")
        if stderr and stderr.strip():
            return stderr.strip()
        return f"exited with status {exc.exit_status}"
    return exc.reason or str(exc)


def failure_entry(
    path: str,
    exc: BaseException,
    *,
    as_sent: Callable[[str], str | None] | None = None,
    missing: str | None = None,
) -> dict[str, str]:
    """One entry of a batch's or a transfer's failed list: the reason and text a single
    request on ``path`` would answer."""
    found = failure_of(exc, as_sent=as_sent, missing=missing)
    return {"path": path, "error": str(found.detail), "reason": found.reason}


def failure_body(exc: BaseException) -> dict[str, str]:
    """The body of the failure ``exc``: ``{"detail": <text>, "reason": <word>}``, as a
    failed request answers it and as a stream's ``error`` event carries it."""
    found = failure_of(exc)
    return {"detail": _text(found.detail), "reason": found.reason}


def answer(
    reason: str, detail: str, *, status: int | None = None, headers: Mapping[str, str] | None = None
) -> JSONResponse:
    """The body of a failed request, for a check that answers before any route runs."""
    return _body(Failure(reason, detail, status=status, headers=headers))


def _body(failure: Failure) -> JSONResponse:
    return JSONResponse(
        failure_body(failure),
        status_code=failure.status_code,
        headers=failure.headers,
    )


def _place(location: tuple[int | str, ...]) -> str:
    return ".".join(str(part) for part in location)


async def _http_failure(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, StarletteHTTPException)
    return _body(failure_of(exc))


async def _request_shape(request: Request, exc: Exception) -> JSONResponse:
    """FastAPI's own check of a request's shape: one text of each error's place and
    message."""
    assert isinstance(exc, RequestValidationError)
    parts = [
        f"{_place(tuple(error.get('loc', ())))}: {error.get('msg', '')}" for error in exc.errors()
    ]
    return answer(REASON_INVALID, "; ".join(parts) or "Invalid request", status=422)


async def _unanswered(request: Request, exc: Exception) -> JSONResponse:
    return _body(failure_of(exc))


def install(application: FastAPI) -> None:
    """Register the handlers that write every failure's body."""
    application.add_exception_handler(StarletteHTTPException, _http_failure)
    application.add_exception_handler(RequestValidationError, _request_shape)
    application.add_exception_handler(Exception, _unanswered)
