"""Which address the login limit, the lockout and the rate limits key on.

`get_client_ip` (sshler/api/auth.py) is the one helper every caller uses: the login
route, the `rate_limit_*` dependencies, `_security_middleware` (lockout check and
Basic-auth failure count) and `_rate_limit_middleware` in sshler/webapp.py.
`X-Real-IP` is a header any local process can send, so it is trusted only when
`SSHLER_TRUST_PROXY_HEADERS=true` says a reverse proxy in front of sshler overwrites it,
and only on connections from that proxy (127.0.0.1). Otherwise everything keys on the
socket peer.

Numbers come from the code: the login limiter is `rate=5, per=60` with the default
`capacity_multiplier=1.5`, so 7 logins pass and the 8th gets 429; `AuthFailureTracker()`
locks an address out after 5 failures.

Every test names the mutation it kills.
"""

from __future__ import annotations

import asyncio as real_asyncio
import base64
import time as real_time

import pytest
from argon2 import PasswordHasher as RealArgon2Hasher
from fastapi.testclient import TestClient
from starlette.requests import Request

from sshler import auth, rate_limit, webapp
from sshler.api.auth import get_client_ip
from sshler.settings import SshlerSettings, reset_settings
from sshler.webapp import ServerSettings, make_app

TOKEN = "client-address-test-token"
USER = "admin"
PASSWORD = "Corr3ct-Horse-Battery!"
LOGIN = "/api/v1/auth/login"
UNROUTED = "/client-address-probe"
PROXY = ("127.0.0.1", 50000)
LOGIN_CAPACITY = 7  # int(5 * 1.5)
POST_CAPACITY = 180  # int(120 * 1.5)
LOCKOUT_TEXT = "Too many failed authentication attempts. Try again later."
VICTIM = "203.0.113.9"


class FrozenClock:
    """Stands in for the `time` module: `time()` is fixed, so no bucket refills."""

    def __init__(self, start: float = 1_000_000.0) -> None:
        self.now = start

    def time(self) -> float:
        return self.now

    def __getattr__(self, name: str):
        return getattr(real_time, name)


class NoSleepAsyncio:
    """`asyncio` inside sshler.webapp with the 2 s failed-auth backoff skipped."""

    async def sleep(self, _delay: float, result=None):
        await real_asyncio.sleep(0)
        return result

    def __getattr__(self, name: str):
        return getattr(real_asyncio, name)


def _basic(user: str, password: str) -> str:
    return "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode("ascii")


def _spoofed(i: int) -> str:
    return f"198.51.100.{i}"


@pytest.fixture(autouse=True)
def _frozen(monkeypatch: pytest.MonkeyPatch) -> None:
    # Plain-http TestClient: a Secure cookie would never be sent back, and every
    # request would re-run Basic auth, whose success resets the failure count.
    monkeypatch.setenv("SSHLER_COOKIE_SECURE", "false")
    reset_settings()
    clock = FrozenClock()
    monkeypatch.setattr(rate_limit, "time", clock)
    monkeypatch.setattr(webapp, "time", clock)
    monkeypatch.setattr(webapp, "asyncio", NoSleepAsyncio())
    monkeypatch.setattr(
        auth,
        "Argon2PasswordHasher",
        lambda **_kw: RealArgon2Hasher(time_cost=1, memory_cost=8, parallelism=1),
    )


@pytest.fixture
def trust_proxy(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SSHLER_TRUST_PROXY_HEADERS", "true")
    reset_settings()


def _client(*, basic_auth: bool) -> TestClient:
    # No `with`: entering the client would run the lifespan (pool, PDF renderer).
    settings = ServerSettings(
        csrf_token=TOKEN, basic_auth=(USER, PASSWORD) if basic_auth else None
    )
    return TestClient(make_app(settings), client=PROXY)


def _failed_login(client: TestClient, real_ip: str) -> int:
    return client.post(
        LOGIN,
        json={"username": USER, "password": "wrong-password"},
        headers={"Authorization": _basic(USER, PASSWORD), "X-Real-IP": real_ip},
    ).status_code


# --- the setting -------------------------------------------------------------


def test_proxy_headers_are_untrusted_by_default():
    """Mutation: default `trust_proxy_headers=True` (every install trusts the header)."""
    assert SshlerSettings(_env_file=None).trust_proxy_headers is False


def test_proxy_trust_is_read_from_the_environment(trust_proxy):
    """Mutation: a field name or alias that `SSHLER_TRUST_PROXY_HEADERS` does not set."""
    assert SshlerSettings(_env_file=None).trust_proxy_headers is True


# --- the helper --------------------------------------------------------------


def _request(peer: str, real_ip: str | None) -> Request:
    headers = [(b"x-real-ip", real_ip.encode())] if real_ip else []
    return Request({"type": "http", "headers": headers, "client": (peer, 5000)})


@pytest.mark.parametrize(
    ("peer", "real_ip", "expected"),
    [
        ("127.0.0.1", VICTIM, "127.0.0.1"),
        ("127.0.0.1", None, "127.0.0.1"),
        ("192.0.2.4", VICTIM, "192.0.2.4"),
    ],
)
def test_untrusted_helper_returns_the_peer(peer: str, real_ip: str | None, expected: str):
    """Mutation: c2a8048's `get_client_ip`, which returns `X-Real-IP` for any
    loopback peer (the first case answers 203.0.113.9)."""
    assert get_client_ip(_request(peer, real_ip)) == expected


@pytest.mark.parametrize(
    ("peer", "real_ip", "expected"),
    [
        ("127.0.0.1", VICTIM, VICTIM),
        ("127.0.0.1", None, "127.0.0.1"),
        ("192.0.2.4", VICTIM, "192.0.2.4"),
    ],
)
def test_trusted_helper_uses_the_header_only_from_the_proxy(
    trust_proxy, peer: str, real_ip: str | None, expected: str
):
    """Mutations: ignoring the setting (first case answers 127.0.0.1); dropping the
    loopback-peer check (third case, a direct LAN client, answers 203.0.113.9)."""
    assert get_client_ip(_request(peer, real_ip)) == expected


# --- untrusted: varying X-Real-IP does not escape any limit -------------------


def test_spoofed_header_does_not_escape_the_login_lockout():
    """Five failed logins, each claiming a new X-Real-IP; the 6th login is locked out
    because all five landed on the peer, 127.0.0.1.

    Mutation: c2a8048's `get_client_ip` (each failure gets its own counter, so the 6th
    answers 401 and 127.0.0.1 is never locked out).
    """
    client = _client(basic_auth=True)
    try:
        statuses = [_failed_login(client, _spoofed(i)) for i in range(1, 6)]
        sixth = client.post(
            LOGIN,
            json={"username": USER, "password": "wrong-password"},
            headers={"Authorization": _basic(USER, PASSWORD), "X-Real-IP": _spoofed(6)},
        )
        tracker = client.app.state.auth_tracker
    finally:
        client.close()

    assert statuses == [401] * 5
    assert sixth.status_code == 429
    assert sixth.text == LOCKOUT_TEXT
    assert tracker.is_locked_out("127.0.0.1") is True
    assert tracker.is_locked_out(_spoofed(1)) is False


def test_spoofed_header_cannot_lock_out_a_victim():
    """Five failed logins naming the victim in X-Real-IP lock out the sender, not the
    victim.

    Mutation: c2a8048's `get_client_ip` (the victim's address is locked out).
    """
    client = _client(basic_auth=True)
    try:
        statuses = [_failed_login(client, VICTIM) for _ in range(5)]
        tracker = client.app.state.auth_tracker
    finally:
        client.close()

    assert statuses == [401] * 5
    assert tracker.is_locked_out(VICTIM) is False
    assert tracker.is_locked_out("127.0.0.1") is True


def test_spoofed_header_does_not_escape_the_basic_auth_failure_count():
    """Five wrong Basic-auth headers, each with a new X-Real-IP, lock out the peer: the
    6th request (correct password) gets 429.

    Mutation: c2a8048's `get_client_ip` in `_security_middleware` (the 6th, with the
    right password, would pass with 404 on the unrouted path).
    """
    client = _client(basic_auth=True)
    try:
        statuses = [
            client.get(
                UNROUTED,
                headers={"Authorization": _basic(USER, "wrong"), "X-Real-IP": _spoofed(i)},
            ).status_code
            for i in range(1, 6)
        ]
        sixth = client.get(
            UNROUTED,
            headers={"Authorization": _basic(USER, PASSWORD), "X-Real-IP": _spoofed(6)},
        )
    finally:
        client.close()

    assert statuses == [401] * 5
    assert sixth.status_code == 429
    assert sixth.text == LOCKOUT_TEXT


def test_spoofed_header_does_not_escape_the_login_rate_limit():
    """Auth disabled, so every login answers 400 and only the login limiter counts:
    7 pass, the 8th gets 429 although each claimed a new X-Real-IP.

    Mutation: c2a8048's `get_client_ip` in `create_rate_limit_dependency` (each
    request gets a fresh bucket; the 8th answers 400).
    """
    client = _client(basic_auth=False)
    try:
        statuses = [
            client.post(
                LOGIN,
                json={"username": USER, "password": "x"},
                headers={"X-Real-IP": _spoofed(i)},
            ).status_code
            for i in range(1, LOGIN_CAPACITY + 2)
        ]
    finally:
        client.close()

    assert statuses == [400] * LOGIN_CAPACITY + [429]


def test_spoofed_header_does_not_escape_the_post_rate_limit():
    """The general POST limiter keys on the peer: with 127.0.0.1's bucket drained, a
    POST claiming another X-Real-IP is refused.

    Mutation: c2a8048's `get_client_ip` in `_rate_limit_middleware` (the claimed
    address has a full bucket; the POST answers 404).
    """
    client = _client(basic_auth=False)
    try:
        assert client.post(UNROUTED).status_code == 404
        limiter = rate_limit._rate_limiters["post"]
        assert [limiter.check("127.0.0.1") for _ in range(POST_CAPACITY - 1)] == [
            True
        ] * (POST_CAPACITY - 1)
        refused = client.post(UNROUTED, headers={"X-Real-IP": _spoofed(1)})
    finally:
        client.close()

    assert refused.status_code == 429
    assert refused.text == "Rate limit exceeded. Please try again later."


# --- trusted: the forwarded address is used -----------------------------------


def test_trusted_proxy_login_failures_lock_out_the_forwarded_address(trust_proxy):
    """Behind the proxy the forwarded client is locked out after 5 failures; the proxy
    itself and other forwarded clients are not.

    Mutation: `get_client_ip` ignoring the setting (failures land on 127.0.0.1, which
    would lock out every client behind the proxy).
    """
    client = _client(basic_auth=True)
    try:
        statuses = [_failed_login(client, VICTIM) for _ in range(5)]
        locked = client.get(
            "/api/v1/auth/me",
            headers={"Authorization": _basic(USER, PASSWORD), "X-Real-IP": VICTIM},
        )
        other = client.get(
            "/api/v1/auth/me",
            headers={"Authorization": _basic(USER, PASSWORD), "X-Real-IP": _spoofed(1)},
        )
        tracker = client.app.state.auth_tracker
    finally:
        client.close()

    assert statuses == [401] * 5
    assert locked.status_code == 429
    assert locked.text == LOCKOUT_TEXT
    assert tracker.is_locked_out(VICTIM) is True
    assert tracker.is_locked_out("127.0.0.1") is False
    # The lockout check runs before the cookie check, so another forwarded client
    # reaching /me with the session cookie proves it is not locked out.
    assert other.status_code == 200
    assert other.json() == {"username": USER, "user_id": USER, "authenticated": True}


def test_trusted_proxy_login_rate_limit_is_per_forwarded_address(trust_proxy):
    """Behind the proxy each forwarded client has its own login bucket.

    Mutation: `get_client_ip` ignoring the setting (one shared bucket; the second
    client's login answers 429).
    """
    client = _client(basic_auth=False)
    try:
        first = [
            client.post(
                LOGIN, json={"username": USER, "password": "x"}, headers={"X-Real-IP": VICTIM}
            ).status_code
            for _ in range(LOGIN_CAPACITY + 1)
        ]
        second = client.post(
            LOGIN, json={"username": USER, "password": "x"}, headers={"X-Real-IP": _spoofed(1)}
        ).status_code
    finally:
        client.close()

    assert first == [400] * LOGIN_CAPACITY + [429]
    assert second == 400
