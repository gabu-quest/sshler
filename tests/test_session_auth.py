"""Tests for session-based authentication."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from sshler import session as session_module
from sshler.session import Session, SessionStore, get_session_store
from sshler.settings import SshlerSettings, reset_settings

START = 1_000_000.0


class FakeClock:
    """A clock callable that only moves when `advance()` is called."""

    def __init__(self, start: float = START) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class FakeTimeModule:
    """Stands in for the `time` module inside sshler.session (default-clock test)."""

    def __init__(self, clock: FakeClock) -> None:
        self._clock = clock

    def time(self) -> float:
        return self._clock()


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def store(clock: FakeClock) -> SessionStore:
    return SessionStore(clock=clock)


class TestSessionStore:
    """Session store on an injected clock: exact timestamps, exact counts.

    Each test names the mutation it kills.
    """

    def test_create_session(self, store: SessionStore):
        """Kills: timestamps not read from the store's clock, and a wrong `expires_at`."""
        session = store.create_session(username="testuser", user_id="uid-1", ttl_seconds=3600)

        assert len(session.session_id) == 32  # 128 bits = 32 hex chars
        int(session.session_id, 16)  # hex only; raises ValueError otherwise
        assert (session.username, session.user_id) == ("testuser", "uid-1")
        assert session.created_at == START
        assert session.last_accessed_at == START
        assert session.expires_at == START + 3600
        assert store.count() == 1

    def test_session_ids_are_unique(self, store: SessionStore):
        """Kills: a constant or clock-derived session id (both sessions share a timestamp)."""
        first = store.create_session("a", "a")
        second = store.create_session("b", "b")
        assert first.session_id != second.session_id
        assert store.count() == 2

    def test_default_ttl_is_eight_hours(self, store: SessionStore):
        """Kills: a changed default `ttl_seconds`."""
        assert store.create_session("u", "u").expires_at == START + 28800

    def test_get_valid_session_touches_it(self, store: SessionStore, clock: FakeClock):
        """Kills: `get_session` not calling `touch` (last access would stay at creation)."""
        created = store.create_session("testuser", "testuser", ttl_seconds=3600)
        clock.advance(120)
        retrieved = store.get_session(created.session_id)

        assert retrieved is created
        assert retrieved.last_accessed_at == START + 120
        assert retrieved.created_at == START

    def test_get_invalid_session(self, store: SessionStore):
        """Kills: `get_session` returning something for an unknown id."""
        assert store.get_session("nonexistent") is None

    def test_delete_session(self, store: SessionStore):
        """Kills: `delete_session` reporting success without removing the session."""
        session = store.create_session("testuser", "testuser", ttl_seconds=3600)
        assert store.delete_session(session.session_id) is True
        assert store.count() == 0
        assert store.get_session(session.session_id) is None

    def test_delete_nonexistent_session(self, store: SessionStore):
        """Kills: `delete_session` returning True for an unknown id."""
        assert store.delete_session("nonexistent") is False

    def test_expiry_boundary(self, store: SessionStore, clock: FakeClock):
        """Boundary: valid one tick before `expires_at`, expired (and removed) at it.
        Kills: `>=` turned into `>` in the absolute-expiry check, and an expired
        session being returned or left in the store."""
        session = store.create_session("u", "u", ttl_seconds=100)

        clock.advance(99.5)
        assert store.get_session(session.session_id) is session
        assert store.count() == 1

        clock.advance(0.5)  # now == expires_at
        assert store.get_session(session.session_id) is None
        assert store.count() == 0

    def test_zero_ttl_is_expired_at_creation(self, store: SessionStore):
        """`ttl_seconds=0` is expired with the clock frozen at creation time, so this
        no longer depends on clock resolution. Kills: `>=` turned into `>`."""
        session = store.create_session("u", "u", ttl_seconds=0)
        assert session.expires_at == START
        assert store.get_session(session.session_id) is None

    def test_idle_timeout_boundary(self, store: SessionStore, clock: FakeClock):
        """Boundary: idle 59.5 s with a 60 s idle timeout is valid (and the access resets
        the idle timer); idle exactly 60 s is expired. Kills: `>=` turned into `>` in
        the idle check, and the idle timeout being ignored."""
        session = store.create_session("u", "u", ttl_seconds=3600)

        clock.advance(59.5)
        assert store.get_session(session.session_id, idle_timeout=60) is session
        assert session.last_accessed_at == START + 59.5

        clock.advance(60)
        assert store.get_session(session.session_id, idle_timeout=60) is None
        assert store.count() == 0

    def test_idle_timeout_zero_is_disabled(self, store: SessionStore, clock: FakeClock):
        """Kills: treating `idle_timeout=0` as "expire immediately"."""
        session = store.create_session("u", "u", ttl_seconds=3600)
        clock.advance(3000)
        assert store.get_session(session.session_id, idle_timeout=0) is session

    def test_session_touch(self, clock: FakeClock):
        """Kills: `touch` not updating `last_accessed_at`, or not using the clock."""
        session = Session(
            session_id="test",
            user_id="user",
            username="user",
            created_at=START,
            last_accessed_at=START,
            expires_at=START + 3600,
            clock=clock,
        )
        clock.advance(10)
        session.touch()
        assert session.last_accessed_at == START + 10
        assert session.created_at == START

    def test_cleanup_expired_counts_exactly(self, store: SessionStore, clock: FakeClock):
        """Kills: cleanup removing nothing, removing valid sessions, or a wrong count.

        Sessions with TTL 10, 20 and 3600; at +20 the first two are expired.
        """
        short = store.create_session("short", "short", ttl_seconds=10)
        edge = store.create_session("edge", "edge", ttl_seconds=20)
        valid = store.create_session("valid", "valid", ttl_seconds=3600)

        clock.advance(20)
        assert store.cleanup_expired() == 2
        assert store.count() == 1
        assert store.get_session(valid.session_id) is valid
        assert store.get_session(short.session_id) is None
        assert store.get_session(edge.session_id) is None

        assert store.cleanup_expired() == 0

    def test_cleanup_expired_honours_idle_timeout(self, store: SessionStore, clock: FakeClock):
        """Kills: `cleanup_expired` not passing `idle_timeout` to `is_expired`."""
        idle = store.create_session("idle", "idle", ttl_seconds=3600)
        clock.advance(30)
        active = store.create_session("active", "active", ttl_seconds=3600)
        clock.advance(30)  # idle untouched for 60 s, active for 30 s

        assert store.cleanup_expired(idle_timeout=60) == 1
        assert store.count() == 1
        assert store.get_session(active.session_id) is active
        assert store.get_session(idle.session_id) is None

    def test_default_clock_reads_time_at_call_time(self, monkeypatch: pytest.MonkeyPatch):
        """Production wiring keeps `time.time`, looked up when called.
        Kills: binding the store's default clock at import (`clock=time.time`)."""
        fake = FakeClock(start=2_000_000.0)
        monkeypatch.setattr(session_module, "time", FakeTimeModule(fake))
        session = get_session_store().create_session("u", "u", ttl_seconds=5)
        assert session.created_at == 2_000_000.0
        fake.advance(5)
        assert session.is_expired() is True


class TestSettings:
    """Tests for SshlerSettings."""

    def setup_method(self):
        """Reset settings before each test."""
        reset_settings()

    def test_default_settings(self):
        """Test default settings values (ignoring .env file)."""
        settings = SshlerSettings(_env_file=None)

        assert settings.host == "127.0.0.1"
        assert settings.port == 8822
        assert settings.cookie_secure is True
        assert settings.cookie_samesite == "lax"
        assert settings.session_ttl_seconds == 28800  # 8 hours
        assert settings.require_auth is True

    def test_cors_disabled_by_default(self):
        """Test that CORS is disabled when no origins configured."""
        settings = SshlerSettings(_env_file=None)

        assert settings.cors_enabled is False
        assert settings.allowed_origins_list == []

    def test_cors_enabled_with_origins(self):
        """Test CORS enabled when origins are configured."""
        settings = SshlerSettings(allowed_origins="http://localhost:3000,http://example.com")

        assert settings.cors_enabled is True
        assert len(settings.allowed_origins_list) == 2
        assert "http://localhost:3000" in settings.allowed_origins_list
        assert "http://example.com" in settings.allowed_origins_list


# HTTP auth path: `_security_middleware` in sshler/webapp.py plus the
# /api/v1/auth/{login,me,logout} endpoints from sshler/api/auth.py.
AUTH_USER = "admin"
AUTH_PASSWORD = "Corr3ct-Horse-Battery!"
CHALLENGE = 'Basic realm="sshler"'


def _basic(user: str, password: str) -> str:
    import base64

    return "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode("ascii")


class _NoSleepAsyncio:
    """`asyncio` for sshler.webapp with the 2 s failed-auth backoff skipped."""

    def __getattr__(self, name: str):
        import asyncio

        return getattr(asyncio, name)

    async def sleep(self, _delay: float, result=None):
        return result


@pytest.fixture
def auth_client(monkeypatch: pytest.MonkeyPatch):
    from sshler.webapp import ServerSettings, make_app

    # Plain-http TestClient: a Secure cookie would never be sent back.
    monkeypatch.setenv("SSHLER_COOKIE_SECURE", "false")
    monkeypatch.setattr("sshler.webapp.asyncio", _NoSleepAsyncio())
    # Minimum-cost Argon2id: production's 100 MB per verify costs ~50 ms a request.
    from argon2 import PasswordHasher as RealArgon2Hasher

    monkeypatch.setattr(
        "sshler.auth.Argon2PasswordHasher",
        lambda **_kw: RealArgon2Hasher(time_cost=1, memory_cost=8, parallelism=1),
    )
    reset_settings()
    # No `with`: entering the client would run the lifespan (pool, PDF renderer).
    settings = ServerSettings(csrf_token="t", basic_auth=(AUTH_USER, AUTH_PASSWORD))
    client = TestClient(make_app(settings))
    yield client
    client.close()


class TestHttpAuth:
    """Cookie and Basic auth over HTTP. Each test names the mutation it kills."""

    def test_no_credentials_gets_basic_challenge(self, auth_client: TestClient):
        """Kills: letting requests without credentials through the middleware."""
        response = auth_client.get("/api/v1/auth/me")
        assert response.status_code == 401
        assert response.headers["WWW-Authenticate"] == CHALLENGE
        assert "set-cookie" not in response.headers

    def test_missing_credentials_do_not_count_toward_lockout(self, auth_client: TestClient):
        """A browser's first request carries no credentials; it must not count as a failure.

        Kills: dropping the early `not auth_header` 401 (the request would fall through to
        `record_failure`, and the sixth credential-less request would get 429).
        """
        statuses = [auth_client.get("/api/v1/auth/me").status_code for _ in range(6)]
        assert statuses == [401] * 6

    def test_wrong_basic_password_gets_basic_challenge(self, auth_client: TestClient):
        """Kills: accepting any `Basic` header without `verify_basic_auth_header`."""
        response = auth_client.get(
            "/api/v1/auth/me", headers={"Authorization": _basic(AUTH_USER, "wrong-password")}
        )
        assert response.status_code == 401
        assert response.headers["WWW-Authenticate"] == CHALLENGE

    def test_basic_auth_issues_cookie_that_authenticates_alone(self, auth_client: TestClient):
        """Kills: dropping the session cookie the middleware sets after Basic auth, and
        dropping the cookie branch of the middleware (the second request would 401)."""
        first = auth_client.get(
            "/api/v1/auth/me", headers={"Authorization": _basic(AUTH_USER, AUTH_PASSWORD)}
        )
        # The request itself carried no cookie, so /me cannot see a session yet.
        assert first.status_code == 401
        assert first.json() == {"detail": "Not authenticated"}
        session_id = first.cookies["sshler_session"]
        assert get_session_store().get_session(session_id).username == AUTH_USER

        second = auth_client.get("/api/v1/auth/me")
        assert second.status_code == 200
        assert second.json() == {"username": AUTH_USER, "user_id": AUTH_USER, "authenticated": True}

    def test_unknown_cookie_gets_basic_challenge(self, auth_client: TestClient):
        """Kills: treating any present cookie as authenticated (skipping `get_session`)."""
        response = auth_client.get("/api/v1/auth/me", headers={"Cookie": "sshler_session=forged"})
        assert response.status_code == 401
        assert response.headers["WWW-Authenticate"] == CHALLENGE

    def test_login_with_correct_credentials(self, auth_client: TestClient):
        """Kills: `login` skipping `auth_manager.authenticate` (paired with the next test)."""
        response = auth_client.post(
            "/api/v1/auth/login",
            json={"username": AUTH_USER, "password": AUTH_PASSWORD},
            headers={"Authorization": _basic(AUTH_USER, AUTH_PASSWORD)},
        )
        assert response.status_code == 200
        assert response.json() == {"success": True, "message": "Login successful"}

    def test_login_with_wrong_password_is_401(self, auth_client: TestClient):
        """Kills: `login` accepting any credentials."""
        response = auth_client.post(
            "/api/v1/auth/login",
            json={"username": AUTH_USER, "password": "wrong-password"},
            headers={"Authorization": _basic(AUTH_USER, AUTH_PASSWORD)},
        )
        assert response.status_code == 401
        assert response.json() == {"detail": "Invalid username or password"}

    def test_logout_destroys_session(self, auth_client: TestClient):
        """Kills: `logout` clearing the cookie without `delete_session` (the old cookie
        would still authenticate)."""
        good = {"Authorization": _basic(AUTH_USER, AUTH_PASSWORD)}
        auth_client.get("/api/v1/auth/me", headers=good)
        session_id = auth_client.cookies["sshler_session"]
        assert auth_client.get("/api/v1/auth/me").status_code == 200

        logout = auth_client.post("/api/v1/auth/logout")
        assert logout.status_code == 200
        assert logout.json() == {"success": True, "message": "Logged out successfully"}

        auth_client.cookies.clear()
        replay = auth_client.get(
            "/api/v1/auth/me", headers={"Cookie": f"sshler_session={session_id}"}
        )
        assert replay.status_code == 401
        assert replay.headers["WWW-Authenticate"] == CHALLENGE


def test_login_failures_lock_out_the_forwarded_client_address(
    auth_client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    """Behind the reverse proxy (SSHLER_TRUST_PROXY_HEADERS on, requests arrive from
    127.0.0.1 carrying `X-Real-IP`), failed logins are recorded under the forwarded address, the
    one the middleware checks, so that client gets 429 afterwards.

    Kills: `login` recording under `request.client.host` instead of the shared
    client-address helper (the failures land on 127.0.0.1; the forwarded
    client stays unlocked: is_locked_out(forwarded) is False).
    """
    monkeypatch.setenv("SSHLER_TRUST_PROXY_HEADERS", "true")
    reset_settings()
    forwarded = "203.0.113.9"
    client = TestClient(auth_client.app, client=("127.0.0.1", 50000))
    headers = {"Authorization": _basic(AUTH_USER, AUTH_PASSWORD), "X-Real-IP": forwarded}
    try:
        statuses = [
            client.post(
                "/api/v1/auth/login",
                json={"username": AUTH_USER, "password": "wrong-password"},
                headers=headers,
            ).status_code
            for _ in range(5)
        ]
        tracker = auth_client.app.state.auth_tracker
        locked = client.get("/api/v1/auth/me", headers=headers)
    finally:
        client.close()

    assert statuses == [401] * 5
    assert tracker.is_locked_out(forwarded) is True
    assert tracker.is_locked_out("127.0.0.1") is False
    assert locked.status_code == 429
    assert locked.text == "Too many failed authentication attempts. Try again later."


def test_login_when_auth_disabled_is_400():
    """Kills: `login` creating sessions when no auth manager is configured."""
    from sshler.webapp import ServerSettings, make_app

    client = TestClient(make_app(ServerSettings(csrf_token="t")))
    response = client.post("/api/v1/auth/login", json={"username": "a", "password": "b"})
    client.close()
    assert response.status_code == 400
    assert response.json() == {"detail": "Authentication is not enabled"}
