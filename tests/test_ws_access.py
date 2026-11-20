"""Who may open `/ws/term`, `/ws/progress` and `/ws/ping`.

One function (`sshler.access.websocket_refusal`) decides for all three sockets, so
each test here drives the real socket route and checks the exact close code. A
refused socket is closed before it is accepted: `websocket_connect` itself raises.
"""

from __future__ import annotations

import base64
import hmac
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from sshler import access, state, webapp
from sshler.session import get_session_store
from sshler.settings import get_settings as get_app_settings
from sshler.settings import reset_settings
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
