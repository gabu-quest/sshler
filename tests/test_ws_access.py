"""Who may open `/ws/term`, `/ws/progress` and `/ws/ping`.

One function (`sshler.access.websocket_refusal`) decides for all three sockets, so
each test here drives the real socket route and checks the exact close code. A
refused socket is closed before it is accepted: `websocket_connect` itself raises.
A socket let in on a session cookie closes with 4401 when that session ends.
"""

from __future__ import annotations

import base64
import hmac
import os
import shutil
import subprocess
import threading
import time
import uuid
from pathlib import Path

import anyio
import pytest
import yaml
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from sshler import access, state, webapp
from sshler import session as session_module
from sshler.session import SessionStore, get_session_store
from sshler.settings import get_settings as get_app_settings
from sshler.settings import reset_settings
from sshler.terminal.transport import TerminalOpenError
from sshler.webapp import ServerSettings, make_app

TOKEN = "ws-access-test-token"
USER = "admin"
PASSWORD = "Corr3ct-Horse-Battery!"
PUBLIC_URL = "https://sshler.example.test"
CLI_ORIGIN = "https://cli.example.test:9443"
HOSTILE = "http://evil.example"

SOCKETS = ["term", "progress", "ping"]


def _url(socket: str, *, token: str | None = TOKEN, session: str = "s1") -> str:
    query = [f"host=no-such-box&dir=/tmp&session={session}"] if socket == "term" else []
    if token is not None:
        query.append(f"token={token}")
    return f"/ws/{socket}?" + "&".join(query)


def _basic(user: str, password: str) -> str:
    return "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode("ascii")


def _closed_before_accept(client: TestClient, url: str, **kwargs) -> tuple[int, str]:
    with pytest.raises(WebSocketDisconnect) as excinfo:
        with client.websocket_connect(url, **kwargs):
            pass
    return excinfo.value.code, excinfo.value.reason


def _is_accepted(client: TestClient, url: str, **kwargs) -> bool:
    """True when the handshake completed (the socket was accepted)."""
    try:
        with client.websocket_connect(url, **kwargs):
            return True
    except WebSocketDisconnect:
        return False


@pytest.fixture(autouse=True)
def _state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "boxes.yaml").write_text(
        yaml.safe_dump({"boxes": []}, sort_keys=False), encoding="utf-8"
    )
    monkeypatch.setenv("SSHLER_CONFIG_DIR", str(config_dir))
    monkeypatch.setenv("SSHLER_PUBLIC_URL", PUBLIC_URL)
    state.reset_state()
    state.initialize(config_dir)


@pytest.fixture
def client() -> TestClient:
    test_client = TestClient(make_app(ServerSettings(csrf_token=TOKEN, allow_origins=[CLI_ORIGIN])))
    yield test_client
    test_client.close()


@pytest.fixture
def auth_client() -> TestClient:
    test_client = TestClient(
        make_app(ServerSettings(csrf_token=TOKEN, basic_auth=(USER, PASSWORD)))
    )
    yield test_client
    test_client.close()


# --- one owner ---------------------------------------------------------------------


@pytest.mark.parametrize("socket", SOCKETS)
def test_socket_asks_the_one_owner(
    monkeypatch: pytest.MonkeyPatch, client: TestClient, socket: str
):
    """Kills: a socket that stops calling `websocket_refusal` (it would accept)."""
    monkeypatch.setattr("sshler.webapp.websocket_refusal", lambda ws: (4499, "owner said no"))
    assert _closed_before_accept(client, _url(socket)) == (4499, "owner said no")


# --- token ---------------------------------------------------------------------------


@pytest.mark.parametrize("socket", SOCKETS)
@pytest.mark.parametrize("token", [None, "", "wrong", "t%C3%B6k%C3%A9n%E2%98%83", "a" * 4000])
def test_missing_empty_or_wrong_token_closes_4403(client: TestClient, socket: str, token):
    """Kills: comparing loosely, raising on non-ASCII, or accepting an empty token."""
    assert _closed_before_accept(client, _url(socket, token=token)) == (4403, "Invalid token")


@pytest.mark.parametrize("socket", SOCKETS)
def test_token_in_header_only_opens_the_socket(client: TestClient, socket: str):
    """Kills: a socket that reads the query parameter only."""
    assert _is_accepted(client, _url(socket, token=None), headers={"X-SSHLER-TOKEN": TOKEN})


@pytest.mark.parametrize("socket", SOCKETS)
def test_right_token_opens_the_socket(client: TestClient, socket: str):
    """Positive control for every refusal here."""
    assert _is_accepted(client, _url(socket))


@pytest.mark.parametrize(
    "supplied", ["", "wrong", "tökén☃", "\ud800", "a" * 1_000_000, TOKEN + "x"]
)
def test_token_matches_never_raises_and_refuses_a_wrong_token(supplied: str):
    """Kills: `hmac.compare_digest` on `str`, which raises for non-ASCII."""
    assert access.token_matches(supplied, TOKEN) is False


def test_token_matches_accepts_the_right_token_and_not_none():
    assert access.token_matches(TOKEN, TOKEN) is True
    assert access.token_matches(None, TOKEN) is False


def test_token_matches_compares_equal_length_values_in_constant_time(
    monkeypatch: pytest.MonkeyPatch,
):
    """A wrong token of any length reaches `compare_digest` as a value the same size as
    the right one's. Kills: comparing the raw tokens (the length would show in the time)."""
    seen: list[tuple[int, int]] = []
    real = hmac.compare_digest

    def spy(a, b):
        seen.append((len(a), len(b)))
        return real(a, b)

    monkeypatch.setattr(access.hmac, "compare_digest", spy)
    access.token_matches("x", TOKEN)
    access.token_matches("y" * 5000, TOKEN)
    assert len(seen) == 2
    assert seen[0] == seen[1]
    assert seen[0][0] == seen[0][1]


# --- origin --------------------------------------------------------------------------


@pytest.mark.parametrize("socket", SOCKETS)
@pytest.mark.parametrize(
    "origin", [HOSTILE, "null", f"{HOSTILE}/", "https://sshler.example.test.evil"]
)
def test_foreign_origin_with_the_right_token_closes_4403_before_accept(
    client: TestClient, socket: str, origin: str
):
    """Kills: a socket that never checks the Origin header."""
    assert _closed_before_accept(client, _url(socket), headers={"Origin": origin}) == (
        4403,
        "Invalid origin",
    )


@pytest.mark.parametrize("socket", SOCKETS)
@pytest.mark.parametrize(
    "origin",
    [PUBLIC_URL, f"{PUBLIC_URL}/", f"{PUBLIC_URL}/app/terminal", CLI_ORIGIN],
)
def test_allowed_origin_opens_the_socket(client: TestClient, socket: str, origin: str):
    """Kills: refusing the public URL's origin, or one with a slash or a path, or a
    command-line origin."""
    assert _is_accepted(client, _url(socket), headers={"Origin": origin})


@pytest.mark.parametrize("socket", SOCKETS)
def test_no_origin_header_opens_the_socket(client: TestClient, socket: str):
    """Kills: treating a client that is not a browser as hostile."""
    assert _is_accepted(client, _url(socket))


@pytest.mark.parametrize("socket", SOCKETS)
def test_origin_check_switch_turns_the_socket_check_off(
    monkeypatch: pytest.MonkeyPatch, socket: str
):
    """Kills: a socket that checks the Origin even when the switch is off."""
    monkeypatch.setenv("SSHLER_ORIGIN_CHECK_ENABLED", "false")
    reset_settings()
    test_client = TestClient(make_app(ServerSettings(csrf_token=TOKEN)))
    try:
        assert _is_accepted(test_client, _url(socket), headers={"Origin": HOSTILE})
    finally:
        test_client.close()


def test_allowed_origins_is_one_list_for_the_middleware_and_the_sockets(
    monkeypatch: pytest.MonkeyPatch, client: TestClient
):
    """Kills: a second copy of the list that drifts (a POST and a socket must agree)."""
    app_settings = get_app_settings()
    cli_settings = client.app.state.settings
    expected = [*cli_settings.allow_origins, *app_settings.allowed_origins_list, PUBLIC_URL]
    assert access.allowed_origins(cli_settings, app_settings) == expected

    # As configured, both doors refuse the foreign origin and take the listed one.
    assert client.post("/origin-probe", headers={"Origin": HOSTILE}).status_code == 403
    assert client.post("/origin-probe", headers={"Origin": CLI_ORIGIN}).status_code == 404
    assert _closed_before_accept(client, _url("ping"), headers={"Origin": HOSTILE}) == (
        4403,
        "Invalid origin",
    )

    # Both doors ask the same function: when it lists the foreign origin, both let it in.
    assert webapp.allowed_origins is access.allowed_origins
    listing_the_foreign_origin = lambda cli, app: [HOSTILE]  # noqa: E731
    monkeypatch.setattr(access, "allowed_origins", listing_the_foreign_origin)
    monkeypatch.setattr(webapp, "allowed_origins", listing_the_foreign_origin)
    assert client.post("/origin-probe", headers={"Origin": HOSTILE}).status_code == 404
    assert _is_accepted(client, _url("ping"), headers={"Origin": HOSTILE})
    assert client.post("/origin-probe", headers={"Origin": CLI_ORIGIN}).status_code == 403


# --- session and auth ----------------------------------------------------------------


def test_session_cookie_without_the_token_closes_4403(auth_client: TestClient):
    """Kills: a cookie that stands in for the token."""
    sid = get_session_store().create_session(username=USER, user_id=USER, ttl_seconds=3600)
    auth_client.cookies.set(get_app_settings().cookie_name, sid.session_id)
    assert _closed_before_accept(auth_client, _url("ping", token=None)) == (4403, "Invalid token")


def test_session_cookie_with_the_token_opens_the_socket(auth_client: TestClient):
    sid = get_session_store().create_session(username=USER, user_id=USER, ttl_seconds=3600)
    auth_client.cookies.set(get_app_settings().cookie_name, sid.session_id)
    assert _is_accepted(auth_client, _url("ping"))


@pytest.mark.parametrize("socket", SOCKETS)
def test_expired_session_cookie_closes_4401(auth_client: TestClient, socket: str):
    """Kills: accepting a cookie without asking the store whether it is still valid."""
    sid = get_session_store().create_session(username=USER, user_id=USER, ttl_seconds=-10)
    auth_client.cookies.set(get_app_settings().cookie_name, sid.session_id)
    assert _closed_before_accept(auth_client, _url(socket)) == (4401, "Unauthorized")


@pytest.mark.parametrize("socket", SOCKETS)
def test_basic_auth_and_token_open_the_socket(auth_client: TestClient, socket: str):
    assert _is_accepted(
        auth_client, _url(socket), headers={"Authorization": _basic(USER, PASSWORD)}
    )


def test_bad_session_name_without_a_token_closes_4403_not_4400(client: TestClient):
    """Kills: validating the session name before the caller is known (it would tell a
    caller without a token which names are valid)."""
    assert _closed_before_accept(client, _url("term", token=None, session="bad;name")) == (
        4403,
        "Invalid token",
    )
    assert _closed_before_accept(client, _url("term", token="wrong", session="")) == (
        4403,
        "Invalid token",
    )


def test_bad_session_name_without_credentials_closes_4401_not_4400(auth_client: TestClient):
    assert _closed_before_accept(auth_client, _url("term", session="")) == (
        4401,
        "Unauthorized",
    )


def test_bad_session_name_with_the_right_token_closes_4400(client: TestClient):
    """Validation still runs, after auth."""
    assert _closed_before_accept(client, _url("term", session="")) == (
        4400,
        "Invalid session name",
    )


def test_foreign_origin_does_not_change_the_code_of_a_caller_without_credentials(
    auth_client: TestClient,
):
    """A caller without credentials gets 4401 whatever Origin it sends."""
    for headers in ({}, {"Origin": HOSTILE}):
        assert _closed_before_accept(auth_client, _url("ping", token=None), headers=headers) == (
            4401,
            "Unauthorized",
        )


def test_the_token_is_not_in_a_log_line_or_a_close_reason(
    client: TestClient, caplog: pytest.LogCaptureFixture
):
    caplog.set_level("DEBUG")
    code, reason = _closed_before_accept(client, _url("term", token="secret-wrong-token"))
    assert (code, reason) == (4403, "Invalid token")
    assert "secret-wrong-token" not in caplog.text
    assert "refused: invalid token" in caplog.text
    # An accepted socket and a refusal for its Origin both had the right token in hand.
    assert _is_accepted(client, _url("ping"))
    assert _closed_before_accept(client, _url("term"), headers={"Origin": HOSTILE}) == (
        4403,
        "Invalid origin",
    )
    assert TOKEN not in caplog.text


@pytest.mark.parametrize("socket", SOCKETS)
def test_idle_session_cookie_closes_4401(
    monkeypatch: pytest.MonkeyPatch, auth_client: TestClient, socket: str
):
    """Kills: a socket that judges a session live without the idle timeout the rest of
    the server applies."""
    now = [1_000_000.0]
    monkeypatch.setattr(session_module, "_session_store", SessionStore(clock=lambda: now[0]))
    monkeypatch.setenv("SSHLER_SESSION_IDLE_TIMEOUT_SECONDS", "600")
    reset_settings()
    try:
        sid = get_session_store().create_session(username=USER, user_id=USER, ttl_seconds=28800)
        auth_client.cookies.set(get_app_settings().cookie_name, sid.session_id)
        now[0] += 1200
        assert _closed_before_accept(auth_client, _url(socket)) == (4401, "Unauthorized")
    finally:
        reset_settings()


# --- a session that ends closes its sockets ----------------------------------------
#
# Each socket below is let in on a session cookie; the session then ends (sign-out
# through the route, or its time is up) and the socket must close with 4401 at once,
# with no request needed to notice. `_close_within` waits on the close itself.

ENDED_LIMIT = 3.0


def _close_within(ws, limit: float = ENDED_LIMIT) -> int:  # type: ignore[no-untyped-def]
    """Read the socket until the server closes it, for at most *limit* seconds; the code."""

    async def until_close() -> int:
        with anyio.fail_after(limit):
            while True:
                message = await ws._send_rx.receive()
                if message["type"] == "websocket.close":
                    return int(message.get("code", 1000))

    return ws.portal.call(until_close)


def _term_url(workdir: Path, session: str = "s1") -> str:
    return f"/ws/term?host=local&dir={workdir}&session={session}&cols=80&rows=24&token={TOKEN}"


def _receive_until(ws, marker: bytes, count: int = 1, limit: float = 5.0) -> bytes:  # type: ignore[no-untyped-def]
    """Terminal output until *marker* has arrived *count* times, for at most *limit* seconds."""

    async def read() -> bytes:
        seen = b""
        with anyio.fail_after(limit):
            while seen.count(marker) < count:
                message = await ws._send_rx.receive()
                seen += message.get("bytes") or b""
        return seen

    return ws.portal.call(read)


def _sign_in(client: TestClient, store: SessionStore | None = None, ttl: int = 3600) -> str:
    """A session made in the store and its cookie set on *client*; the session id."""
    session = (store or get_session_store()).create_session(
        username=USER, user_id=USER, ttl_seconds=ttl
    )
    client.cookies.set(get_app_settings().cookie_name, session.session_id)
    return session.session_id


def _sign_out(client: TestClient, session_id: str) -> None:
    """The sign-out route, as another tab of the same browser sends it."""
    response = client.post(
        "/api/v1/auth/logout",
        headers={
            "Cookie": f"{get_app_settings().cookie_name}={session_id}",
            "X-SSHLER-TOKEN": TOKEN,
        },
    )
    assert response.status_code == 200


def _ping_everyone(client: TestClient, title: str) -> None:
    response = client.post(
        "/api/v1/ping",
        headers={"Authorization": _basic(USER, PASSWORD), "X-SSHLER-TOKEN": TOKEN},
        json={"title": title},
    )
    assert response.status_code == 200


@pytest.fixture
def workdir(tmp_path: Path) -> Path:
    path = tmp_path / "work"
    path.mkdir()
    return path


@pytest.fixture
def quiet_terminal(monkeypatch: pytest.MonkeyPatch, fake_tmux) -> None:  # type: ignore[no-untyped-def]
    """`/ws/term` on the local box runs the fake tmux (`cat` on a PTY) and records nothing."""

    async def no_history(_session: str, _directory: str) -> None:
        return None

    monkeypatch.setattr("sshler.webapp.record_ts_history", no_history)


def _open(client: TestClient, socket: str, workdir: Path):  # type: ignore[no-untyped-def]
    return client.websocket_connect(_term_url(workdir) if socket == "term" else _url(socket))


def _ready(ws, socket: str) -> None:  # type: ignore[no-untyped-def]
    """Return once the socket is carrying: a terminal echoes, progress sends its snapshot."""
    if socket == "term":
        ws.send_bytes(b"ready-4c1\n")
        # Once from the tty's echo, once from `cat`: after both, the terminal is quiet.
        _receive_until(ws, b"ready-4c1", count=2)
    elif socket == "progress":
        assert ws.receive_json()["type"] == "snapshot"


@pytest.mark.posix_only("a PTY running the sh fake tmux behind /ws/term")
@pytest.mark.parametrize("socket", SOCKETS)
def test_sign_out_in_another_tab_closes_the_socket_4401(
    auth_client: TestClient, quiet_terminal: None, workdir: Path, socket: str
):
    """Kills: a socket checked only at its connect, left open after its session ends."""
    session_id = _sign_in(auth_client)
    with _open(auth_client, socket, workdir) as ws:
        _ready(ws, socket)
        _sign_out(auth_client, session_id)
        assert _close_within(ws) == 4401
    assert get_session_store().sockets_held(session_id) == 0


@pytest.mark.posix_only("a PTY running the sh fake tmux behind /ws/term")
@pytest.mark.parametrize("socket", SOCKETS)
def test_the_end_of_the_sessions_time_closes_the_socket_4401_without_a_request(
    monkeypatch: pytest.MonkeyPatch,
    auth_client: TestClient,
    quiet_terminal: None,
    workdir: Path,
    socket: str,
):
    """The session's time runs out on the store's clock while its socket is open: the
    socket closes with no request to notice it. Kills: a time limit that is noticed
    only by the next request."""
    now = [time.time()]
    store = SessionStore(clock=lambda: now[0])
    monkeypatch.setattr(session_module, "_session_store", store)
    monkeypatch.setattr(session_module, "RECHECK_SECONDS", 0.05)
    session_id = _sign_in(auth_client, store, ttl=3600)
    with _open(auth_client, socket, workdir) as ws:
        _ready(ws, socket)
        assert store.sockets_held(session_id) == 1
        now[0] += 3600
        assert _close_within(ws) == 4401
    assert store.count() == 0


@pytest.mark.posix_only("a PTY running the sh fake tmux behind /ws/term")
def test_a_session_that_ends_while_its_terminal_is_opening_ends_without_an_error(
    monkeypatch: pytest.MonkeyPatch,
    auth_client: TestClient,
    quiet_terminal: None,
    workdir: Path,
):
    """The session ends while the terminal is still opening, and the open then fails:
    the socket is already closed, and the handler ends cleanly. Leaving the block joins
    the handler, so an error raised in it is raised here. A guard: the framework ends a
    handler quietly when it sends on a socket it has closed, and this pins that."""
    released = threading.Event()

    async def slow_failing_open(*_args: object) -> None:
        while not released.is_set():
            await anyio.sleep(0.01)
        raise TerminalOpenError("cannot open\r\n", 4502, "tmux cannot start")

    monkeypatch.setattr(webapp, "_open_terminal", slow_failing_open)
    session_id = _sign_in(auth_client)
    with _open(auth_client, "term", workdir) as ws:
        _sign_out(auth_client, session_id)
        assert _close_within(ws) == 4401
        released.set()
    assert get_session_store().sockets_held(session_id) == 0


def test_ending_one_session_leaves_another_sessions_socket_open(auth_client: TestClient):
    """Kills: a session's end that closes every socket, not only its own."""
    ended = _sign_in(auth_client)
    other_client = TestClient(auth_client.app)
    try:
        _sign_in(other_client)
        with other_client.websocket_connect(_url("ping")) as kept:
            with auth_client.websocket_connect(_url("ping")) as closed:
                _sign_out(auth_client, ended)
                assert _close_within(closed) == 4401
            _ping_everyone(auth_client, "still here")
            assert kept.receive_json()["title"] == "still here"
    finally:
        other_client.close()


def test_a_socket_let_in_by_a_basic_header_stays_open_when_a_session_ends(
    auth_client: TestClient,
):
    """A Basic header makes no session, so no session's end closes its socket."""
    session_id = _sign_in(auth_client)
    basic = {"Authorization": _basic(USER, PASSWORD)}
    stranger = TestClient(auth_client.app)
    try:
        with stranger.websocket_connect(_url("ping"), headers=basic) as kept:
            with auth_client.websocket_connect(_url("ping")) as closed:
                _sign_out(auth_client, session_id)
                assert _close_within(closed) == 4401
            _ping_everyone(auth_client, "basic stays")
            assert kept.receive_json()["title"] == "basic stays"
    finally:
        stranger.close()


@pytest.mark.posix_only("a PTY running the sh fake tmux behind /ws/term")
def test_nothing_typed_after_the_end_reaches_the_terminal(
    monkeypatch: pytest.MonkeyPatch, auth_client: TestClient, quiet_terminal: None, workdir: Path
):
    """Kills: a terminal that still takes input once its session has ended."""
    from sshler.terminal.local import LocalPtyTransport

    written: list[bytes] = []
    real_write = LocalPtyTransport.write

    async def recording_write(self, data: bytes) -> None:  # type: ignore[no-untyped-def]
        written.append(data)
        await real_write(self, data)

    finished = threading.Event()
    real_serve = webapp._serve_terminal

    async def serve_then_tell(*args, **kwargs):  # type: ignore[no-untyped-def]
        try:
            await real_serve(*args, **kwargs)
        finally:
            finished.set()

    monkeypatch.setattr(LocalPtyTransport, "write", recording_write)
    monkeypatch.setattr("sshler.webapp._serve_terminal", serve_then_tell)
    session_id = _sign_in(auth_client)
    with auth_client.websocket_connect(_term_url(workdir)) as ws:
        _ready(ws, "term")
        _sign_out(auth_client, session_id)
        assert _close_within(ws) == 4401
        # Typed after the close, then the client goes: the terminal sees them in order.
        ws.send_bytes(b"after-the-end\n")
        ws.send({"type": "websocket.disconnect", "code": 1000})
        assert finished.wait(ENDED_LIMIT)
    assert written == [b"ready-4c1\n"]


def test_a_cookie_of_a_session_that_ended_is_refused_4401_at_connect(auth_client: TestClient):
    session_id = _sign_in(auth_client)
    _sign_out(auth_client, session_id)
    assert _closed_before_accept(auth_client, _url("ping")) == (4401, "Unauthorized")


def test_a_sign_out_between_the_check_and_the_record_closes_the_socket_4401(
    monkeypatch: pytest.MonkeyPatch, auth_client: TestClient
):
    """The session ends after `websocket_refusal` let the socket in and before the
    socket is in the record. Kills: a socket that slips in after its session's end."""
    session_id = _sign_in(auth_client)
    real_refusal = webapp.websocket_refusal

    def refusal_then_sign_out(websocket):  # type: ignore[no-untyped-def]
        refusal = real_refusal(websocket)
        get_session_store().delete_session(session_id)
        return refusal

    monkeypatch.setattr("sshler.webapp.websocket_refusal", refusal_then_sign_out)
    with auth_client.websocket_connect(_url("ping")) as ws:
        assert _close_within(ws) == 4401
    assert get_session_store().sockets_held(session_id) == 0


def test_a_hundred_sockets_opened_and_closed_leave_no_entry(auth_client: TestClient):
    """Kills: a record that keeps a socket after it closed for any other reason."""
    session_id = _sign_in(auth_client)
    for _ in range(100):
        with auth_client.websocket_connect(_url("ping")):
            pass
    assert get_session_store().sockets_held(session_id) == 0
    assert get_session_store()._held == {}


@pytest.mark.posix_only("a real tmux on a PTY behind /ws/term")
@pytest.mark.skipif(shutil.which("tmux") is None, reason="tmux not installed")
def test_a_terminal_closed_by_the_end_leaves_no_process_and_keeps_its_tmux_session(
    monkeypatch: pytest.MonkeyPatch, auth_client: TestClient, workdir: Path
):
    """The end arrives while the terminal floods output (`yes`). Its attach process is
    gone; the tmux session, and `yes` in it, run on. Kills: a pump left running, an
    attach process left behind, or an end that kills the tmux session."""
    monkeypatch.setenv("SHELL", "/bin/sh")
    tmux_session = f"session-end-{uuid.uuid4().hex[:6]}"
    tmux_command = ["tmux", "-L", f"ts-{tmux_session}"]
    started: list[int] = []
    real_open = webapp._open_local_pty_tmux

    async def recording_open(*args, **kwargs):  # type: ignore[no-untyped-def]
        process = await real_open(*args, **kwargs)
        started.append(process.pid)
        return process

    async def no_history(_session: str, _directory: str) -> None:
        return None

    monkeypatch.setattr("sshler.webapp._open_local_pty_tmux", recording_open)
    monkeypatch.setattr("sshler.webapp.record_ts_history", no_history)
    session_id = _sign_in(auth_client)
    try:
        with auth_client.websocket_connect(_term_url(workdir, tmux_session)) as ws:
            ws.send_bytes(b"echo mark-$((6*7))\n")
            _receive_until(ws, b"mark-42")
            ws.send_bytes(b"yes\n")
            # tmux redraws its screen, so the flood shows as many `y`s, not as lines.
            _receive_until(ws, b"y", count=500)
            _sign_out(auth_client, session_id)
            assert _close_within(ws) == 4401

        assert len(started) == 1
        assert _alive_after(started, seconds=3.0) == []
        running = subprocess.run(
            [*tmux_command, "display-message", "-p", "-t", tmux_session, "#{pane_current_command}"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        assert (running.returncode, running.stdout.strip()) == (0, "yes")
    finally:
        subprocess.run(
            [*tmux_command, "kill-session", "-t", tmux_session],
            capture_output=True,
            timeout=5,
        )


def _alive_after(pids: list[int], seconds: float) -> list[int]:
    """The pids still alive once *seconds* have passed or all have gone."""
    deadline = time.monotonic() + seconds
    while True:
        alive = []
        for pid in pids:
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                continue
            alive.append(pid)
        if not alive or time.monotonic() >= deadline:
            return alive
        time.sleep(0.05)
