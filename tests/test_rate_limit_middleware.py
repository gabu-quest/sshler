"""Rate-limit middleware and auth lockout wiring in sshler/webapp.py.

`tests/test_rate_limit.py` covers the `RateLimiter` class; these tests cover the
middleware that must call it (`_rate_limit_middleware`) and the lockout branch of
`_security_middleware`. Limits are read from the code: POST is `rate=120, per=60` and
GET is `rate=600, per=60`, each with the default `capacity_multiplier=1.5`, so with
a frozen clock exactly 180 POSTs and 900 GETs pass before the next gets 429. The auth
lockout is `AuthFailureTracker()` defaults: 5 failures lock the IP out for 300 s.

Every test names the mutation it kills.
"""

from __future__ import annotations

import asyncio as real_asyncio
import base64
import time as real_time

import pytest
from argon2 import PasswordHasher as RealArgon2Hasher
from fastapi.testclient import TestClient

from sshler import auth, rate_limit, webapp
from sshler.settings import reset_settings
from sshler.webapp import ServerSettings, make_app

TOKEN = "rate-limit-test-token"
UNROUTED = "/rate-limit-probe"
USER = "admin"
PASSWORD = "Corr3ct-Horse-Battery!"
POST_CAPACITY = 180  # int(120 * 1.5)
GET_CAPACITY = 900  # int(600 * 1.5)
LOCKOUT_THRESHOLD = 5
LOCKOUT_SECONDS = 300


class FrozenClock:
    """Stands in for the `time` module: `time()` is fixed until `advance()`.

    Every other attribute is the real `time` module's.
    """

    def __init__(self, start: float = 1_000_000.0) -> None:
        self.now = start

    def time(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds

    def __getattr__(self, name: str):
        return getattr(real_time, name)


class RecordingAsyncio:
    """Stands in for `asyncio` inside sshler.webapp: `sleep()` is recorded, not slept."""

    def __init__(self) -> None:
        self.sleeps: list[float] = []

    async def sleep(self, delay: float, result=None):
        self.sleeps.append(delay)
        await real_asyncio.sleep(0)
        return result

    def __getattr__(self, name: str):
        return getattr(real_asyncio, name)


def _basic(user: str, password: str) -> str:
    return "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode("ascii")


@pytest.fixture
def limiter_clock(monkeypatch: pytest.MonkeyPatch) -> FrozenClock:
    clock = FrozenClock()
    monkeypatch.setattr(rate_limit, "time", clock)
    return clock


@pytest.fixture
def client() -> TestClient:
    # No `with`: entering the client would run the lifespan (pool, PDF renderer).
    test_client = TestClient(make_app(ServerSettings(csrf_token=TOKEN)))
    yield test_client
    test_client.close()


def _drain(limiter_name: str, key: str, count: int) -> None:
    """Spend `count` tokens from the bucket the middleware created for `key`.

    Faster than `count` HTTP requests; the request after the drain still goes through
    the middleware, so the 429 proves the middleware reads this exact bucket.
    """
    limiter = rate_limit._rate_limiters[limiter_name]
    assert [limiter.check(key) for _ in range(count)] == [True] * count


def test_post_past_limit_gets_429_with_retry_after(limiter_clock: FrozenClock, client: TestClient):
    """Kills: the middleware never calling `limiter.check` (audit C3), and a changed
    POST rate or capacity."""
    assert client.post(UNROUTED).status_code == 404
    assert rate_limit._rate_limiters["post"].capacity == POST_CAPACITY
    _drain("post", "testclient", POST_CAPACITY - 2)
    assert client.post(UNROUTED).status_code == 404  # the last token

    limited = client.post(UNROUTED)
    assert limited.status_code == 429
    assert limited.headers["Retry-After"] == "60"
    assert limited.text == "Rate limit exceeded. Please try again later."


def test_get_past_limit_gets_429_with_retry_after(limiter_clock: FrozenClock, client: TestClient):
    """Kills: GET sharing the POST limiter (no "general" limiter would exist, and the
    drained bucket would not be the one checked), a changed GET rate or capacity, and a
    middleware that never calls `limiter.check`."""
    assert client.get(UNROUTED).status_code == 404
    assert rate_limit._rate_limiters["general"].capacity == GET_CAPACITY
    _drain("general", "testclient", GET_CAPACITY - 2)
    assert client.get(UNROUTED).status_code == 404  # the last token

    limited = client.get(UNROUTED)
    assert limited.status_code == 429
    assert limited.headers["Retry-After"] == "60"


def test_limit_recovers_after_refill(limiter_clock: FrozenClock, client: TestClient):
    """Kills: a limiter that never refills (POST refills at 120/60 = 2 tokens per second)."""
    assert client.post(UNROUTED).status_code == 404
    _drain("post", "testclient", POST_CAPACITY - 1)
    assert client.post(UNROUTED).status_code == 429

    limiter_clock.advance(1.0)  # exactly two tokens back
    assert [client.post(UNROUTED).status_code for _ in range(3)] == [404, 404, 429]


def test_limit_is_per_client_ip(limiter_clock: FrozenClock, monkeypatch: pytest.MonkeyPatch):
    """Kills: keying the bucket on a constant instead of `get_client_ip(request)`.

    With SSHLER_TRUST_PROXY_HEADERS on, requests from 127.0.0.1 are keyed on
    `X-Real-IP` (the trusted proxy header).
    """
    monkeypatch.setenv("SSHLER_TRUST_PROXY_HEADERS", "true")
    reset_settings()
    proxied = TestClient(
        make_app(ServerSettings(csrf_token=TOKEN)), client=("127.0.0.1", 50000)
    )
    first = {"X-Real-IP": "203.0.113.1"}
    second = {"X-Real-IP": "203.0.113.2"}
    assert proxied.post(UNROUTED, headers=first).status_code == 404
    _drain("post", "203.0.113.1", POST_CAPACITY - 1)
    assert proxied.post(UNROUTED, headers=first).status_code == 429
    assert proxied.post(UNROUTED, headers=second).status_code == 404
    proxied.close()


def cheap_argon2(**_production_params) -> RealArgon2Hasher:
    """Argon2id with minimum cost. Production uses 100 MB per verify (about 50 ms), which
    these tests pay on every Basic-auth request; the lockout logic does not depend on it."""
    return RealArgon2Hasher(time_cost=1, memory_cost=8, parallelism=1)


@pytest.fixture
def auth_env(monkeypatch: pytest.MonkeyPatch) -> tuple[FrozenClock, RecordingAsyncio]:
    monkeypatch.setattr(auth, "Argon2PasswordHasher", cheap_argon2)
    clock = FrozenClock()
    fake_asyncio = RecordingAsyncio()
    monkeypatch.setattr(webapp, "time", clock)
    monkeypatch.setattr(webapp, "asyncio", fake_asyncio)
    return clock, fake_asyncio


@pytest.fixture
def auth_client(auth_env) -> TestClient:
    test_client = TestClient(
        make_app(ServerSettings(csrf_token=TOKEN, basic_auth=(USER, PASSWORD)))
    )
    yield test_client
    test_client.close()


def test_auth_lockout_after_five_failures_returns_429(auth_env, auth_client: TestClient):
    """Kills: dropping the `is_locked_out` branch, dropping `record_failure`, and the
    2 s failure backoff being removed (the recorded sleeps would be empty)."""
    _clock, fake_asyncio = auth_env
    bad = {"Authorization": _basic(USER, "wrong-password")}
    failures = [auth_client.get(UNROUTED, headers=bad) for _ in range(LOCKOUT_THRESHOLD)]
    assert [r.status_code for r in failures] == [401] * LOCKOUT_THRESHOLD
    assert [r.headers["WWW-Authenticate"] for r in failures] == [
        'Basic realm="sshler"'
    ] * LOCKOUT_THRESHOLD
    assert fake_asyncio.sleeps == [2] * LOCKOUT_THRESHOLD

    # Even correct credentials are refused while locked out.
    locked = auth_client.get(UNROUTED, headers={"Authorization": _basic(USER, PASSWORD)})
    assert locked.status_code == 429
    assert locked.headers["Retry-After"] == str(LOCKOUT_SECONDS)
    assert locked.text == "Too many failed authentication attempts. Try again later."


def test_auth_lockout_retry_after_counts_down_and_expires(auth_env, auth_client: TestClient):
    """Kills: a constant `Retry-After`, and a lockout that never expires."""
    clock, _ = auth_env
    bad = {"Authorization": _basic(USER, "wrong-password")}
    good = {"Authorization": _basic(USER, PASSWORD)}
    for _ in range(LOCKOUT_THRESHOLD):
        auth_client.get(UNROUTED, headers=bad)

    clock.advance(100)
    assert auth_client.get(UNROUTED, headers=good).headers["Retry-After"] == "200"

    clock.advance(200)
    assert auth_client.get(UNROUTED, headers=good).status_code == 404


def test_successful_auth_resets_failure_count(auth_env, auth_client: TestClient):
    """Kills: dropping `auth_tracker.reset_failures(client_ip)` after a good login
    (4 + 4 failures would then lock the client out)."""
    bad = {"Authorization": _basic(USER, "wrong-password")}
    good = {"Authorization": _basic(USER, PASSWORD)}
    for _ in range(LOCKOUT_THRESHOLD - 1):
        auth_client.get(UNROUTED, headers=bad)
    assert auth_client.get(UNROUTED, headers=good).status_code == 404
    # The good request set a session cookie; drop it so the next requests use Basic auth.
    auth_client.cookies.clear()
    statuses = [
        auth_client.get(UNROUTED, headers=bad).status_code
        for _ in range(LOCKOUT_THRESHOLD - 1)
    ]
    assert statuses == [401] * (LOCKOUT_THRESHOLD - 1)


WS_UPGRADES = 16  # one past the burst a 10/min limiter (capacity int(10 * 1.5) = 15) allows


def test_websocket_upgrades_are_not_rate_limited(limiter_clock: FrozenClock, client: TestClient):
    """WebSocket upgrades bypass the http rate limiter (the dead `/ws/` branch of
    `_rate_limit_middleware` was deleted, user decision 2026-10-08).

    Kills: rate limiting websocket upgrades anywhere on the way to a socket (e.g. a
    10/min `get_rate_limiter("websocket", ...)` check before `accept()`); the 16th
    upgrade would be refused. The GET bucket is drained first, so counting upgrades
    against the GET limiter also fails this test.
    """
    assert client.get(UNROUTED).status_code == 404
    _drain("general", "testclient", GET_CAPACITY - 1)
    assert client.get(UNROUTED).status_code == 429

    accepted = 0
    for _ in range(WS_UPGRADES):
        with client.websocket_connect(f"/ws/ping?token={TOKEN}"):
            accepted += 1
    assert accepted == WS_UPGRADES
    assert sorted(rate_limit._rate_limiters) == ["general"]
