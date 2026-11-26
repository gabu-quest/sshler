"""Who may open a WebSocket, and the comparisons the REST routes share with it."""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import hmac
import logging
from urllib.parse import urlparse

from fastapi import WebSocket

from .session import SocketHold, hold_on_session, live_session
from .settings import SshlerSettings
from .settings import get_settings as get_app_settings

logger = logging.getLogger(__name__)

CLOSE_UNAUTHORIZED = 4401
CLOSE_FORBIDDEN = 4403
SESSION_ENDED = "Session ended"

# Closes started by a session's end, kept until done so none is collected half-way.
_closing: set[asyncio.Task] = set()


def _digest(value: str) -> bytes:
    return hashlib.sha256(value.encode("utf-8", "surrogatepass")).digest()


def token_matches(supplied: str | None, expected: str) -> bool:
    """Compare a supplied token with the server's, in constant time.

    Both sides are hashed first, so the comparison never raises (non-ASCII,
    empty and very long inputs included) and always sees two values of the
    same length: its time says nothing about where a wrong token differs from
    the right one, nor about the right one's length.
    """
    return hmac.compare_digest(_digest(supplied or ""), _digest(expected))


def allowed_origins(cli_settings, app_settings: SshlerSettings) -> list[str]:
    """Every origin a state-changing request or a socket may come from.

    The command-line origins, the ``allowed_origins`` setting, and the origin of
    ``public_url``.
    """
    origins = list(cli_settings.allow_origins) + app_settings.allowed_origins_list
    if app_settings.public_url:
        parsed = urlparse(app_settings.public_url)
        public_origin = f"{parsed.scheme}://{parsed.netloc}"
        if public_origin not in origins:
            origins.append(public_origin)
    return origins


def origin_base(origin: str) -> str:
    """Reduce an Origin or Referer value to ``scheme://host:port``."""
    parsed = urlparse(origin)
    return f"{parsed.scheme}://{parsed.netloc}"


def websocket_refusal(websocket: WebSocket) -> tuple[int, str] | None:
    """Decide whether a socket may be opened; None means it may.

    Checked in this order, and the caller closes the socket before accepting it
    when a refusal comes back: credentials (Basic header, else a live session
    cookie) give 4401; the token (``token`` query parameter, else the
    ``x-sshler-token`` header) gives 4403; an Origin header that is present and
    not allowed gives 4403. A request without an Origin header is not a browser
    and passes, as for a POST. ``origin_check_enabled`` turns the Origin check off.

    A socket let in by a session cookie carries that session's id in
    ``websocket.state.session_id`` for ``hold_socket``; one let in otherwise (a good
    Basic header, or no password set) carries None and belongs to no session.
    """
    cli_settings = websocket.app.state.settings
    app_settings = get_app_settings()
    websocket.state.session_id = None

    if cli_settings.auth_manager:
        authenticated = False
        auth_header = websocket.headers.get("authorization")
        if auth_header and cli_settings.auth_manager.verify_basic_auth_header(auth_header):
            authenticated = True
        if not authenticated:
            cookie_sid = websocket.cookies.get(app_settings.cookie_name)
            if cookie_sid and live_session(cookie_sid):
                authenticated = True
                websocket.state.session_id = cookie_sid
        if not authenticated:
            logger.warning("[Connection] WebSocket refused: no valid credentials")
            return CLOSE_UNAUTHORIZED, "Unauthorized"

    if cli_settings.csrf_token:
        supplied = websocket.query_params.get("token") or websocket.headers.get("x-sshler-token")
        if not token_matches(supplied, cli_settings.csrf_token):
            logger.warning("[Connection] WebSocket refused: invalid token")
            return CLOSE_FORBIDDEN, "Invalid token"

    if app_settings.origin_check_enabled:
        origin = websocket.headers.get("origin")
        if origin is not None and origin_base(origin) not in allowed_origins(
            cli_settings, app_settings
        ):
            logger.warning("[Security] WebSocket refused: origin %r is not allowed", origin)
            return CLOSE_FORBIDDEN, "Invalid origin"

    return None


class _Unheld:
    """The place of a socket that belongs to no session: nothing to release."""

    def release(self) -> None:
        return None


async def hold_socket(websocket: WebSocket) -> SocketHold | _Unheld | None:
    """Put an accepted socket in the record of the session it was let in on.

    When that session ends (signed out, or its time is up) the socket is closed with
    4401, and whatever runs on it ends as on any close. A socket let in without a
    session is not held. None means the session ended between ``websocket_refusal``
    and this call: the socket has been closed with 4401 and the caller returns. The
    caller releases the hold when the socket closes for any other reason.
    """
    session_id = getattr(websocket.state, "session_id", None)
    if session_id is None:
        return _Unheld()
    loop = asyncio.get_running_loop()

    def end() -> None:
        closing = loop.create_task(_close_ended(websocket))
        _closing.add(closing)
        closing.add_done_callback(_closing.discard)

    hold = hold_on_session(session_id, end)
    if hold is None:
        await _close_ended(websocket)
    return hold


async def _close_ended(websocket: WebSocket) -> None:
    logger.info("[Connection] WebSocket closed: its session ended")
    with contextlib.suppress(Exception):  # the socket has already closed
        await websocket.close(code=CLOSE_UNAUTHORIZED, reason=SESSION_ENDED)
