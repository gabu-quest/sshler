"""Tests for session-based authentication."""

from __future__ import annotations

import json

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


class RunningClock:
    """The store's clock for the timer tests: wall time, read when called."""

    def __call__(self) -> float:
        import time

        return time.time()


def _ends(store: SessionStore, session_id: str, count: int, ended: list[str], name: str):
    """Hold *count* sockets on *session_id*; each records *name* in *ended* when ended."""
    return [store.hold(session_id, lambda i=i: ended.append(f"{name}{i}")) for i in range(count)]


async def _one_turn() -> None:
    """Let callbacks scheduled with `call_soon_threadsafe` run (no wall time passes)."""
    import asyncio

    for _ in range(3):
        await asyncio.sleep(0)


class TestSocketsOfASession:
    """The record of which sockets belong to which session, kept by the store."""

    @pytest.mark.asyncio
    async def test_deleting_a_session_ends_each_of_its_sockets_once_and_no_other(
        self, store: SessionStore
    ):
        """Kills: a session's end that reaches no socket, or one of another session."""
        first = store.create_session("a", "a")
        second = store.create_session("b", "b")
        ended: list[str] = []
        _ends(store, first.session_id, 3, ended, "a")
        _ends(store, second.session_id, 1, ended, "b")
        assert store.sockets_held(first.session_id) == 3

        assert store.delete_session(first.session_id) is True
        assert store.delete_session(first.session_id) is False  # logout called twice
        await _one_turn()

        assert sorted(ended) == ["a0", "a1", "a2"]
        assert store.sockets_held(first.session_id) == 0
        assert store.sockets_held(second.session_id) == 1

    @pytest.mark.asyncio
    async def test_a_session_found_expired_ends_its_sockets(
        self, store: SessionStore, clock: FakeClock
    ):
        """Kills: expiry noticed by a request that removes the session but leaves its
        sockets open."""
        session = store.create_session("a", "a", ttl_seconds=60)
        ended: list[str] = []
        _ends(store, session.session_id, 2, ended, "s")
        clock.advance(60)
        assert store.get_session(session.session_id) is None
        await _one_turn()
        assert sorted(ended) == ["s0", "s1"]
        assert store.sockets_held(session.session_id) == 0

    @pytest.mark.asyncio
    async def test_a_socket_that_closes_leaves_the_record(self, store: SessionStore):
        """100 sockets opened and closed on one session leave no entry. Kills: a record
        that grows with every socket ever opened."""
        session = store.create_session("a", "a")
        for _ in range(100):
            hold = store.hold(session.session_id, lambda: None)
            assert hold is not None
            hold.release()
            hold.release()  # a second release changes nothing
        assert store.sockets_held(session.session_id) == 0
        assert store._held == {}

    @pytest.mark.asyncio
    async def test_no_socket_is_held_on_a_session_that_has_ended(
        self, store: SessionStore, clock: FakeClock
    ):
        """The check and the record are one step: a socket let in just before its
        session ended is refused here. Kills: a socket that slips into the record
        after the end and stays open."""
        signed_out = store.create_session("a", "a")
        timed_out = store.create_session("b", "b", ttl_seconds=10)
        store.delete_session(signed_out.session_id)
        clock.advance(10)

        assert store.hold(signed_out.session_id, lambda: None) is None
        assert store.hold(timed_out.session_id, lambda: None) is None
        assert store.count() == 0
        assert store._held == {}

    @pytest.mark.asyncio
    async def test_the_end_of_its_time_ends_a_socket_without_a_request(self):
        """Kills: a session whose time ran out keeping its sockets until the next
        request asks about it."""
        import asyncio

        clock = RunningClock()
        store = SessionStore(clock=clock)
        session = store.create_session("a", "a", ttl_seconds=1)
        ended = asyncio.Event()
        assert store.hold(session.session_id, ended.set) is not None

        await asyncio.wait_for(ended.wait(), timeout=3)
        late = clock() - session.expires_at
        assert 0 <= late < 1.0
        assert store.count() == 0
        assert store.sockets_held(session.session_id) == 0

    @pytest.mark.asyncio
    async def test_time_that_passed_while_the_machine_slept_ends_a_socket(
        self, store: SessionStore, clock: FakeClock, monkeypatch: pytest.MonkeyPatch
    ):
        """While a machine sleeps the event loop's clock stands still and the store's
        does not. Kills: a timer set once for the whole time left, which lets a socket
        outlive its session by as long as the machine slept."""
        import asyncio

        import sshler.session as session_module

        monkeypatch.setattr(session_module, "RECHECK_SECONDS", 0.05, raising=False)
        session = store.create_session("a", "a", ttl_seconds=3600)
        ended = asyncio.Event()
        assert store.hold(session.session_id, ended.set) is not None

        clock.advance(3600)  # the store's clock moved; the loop's did not

        await asyncio.wait_for(ended.wait(), timeout=2)
        assert store.count() == 0
        assert store.sockets_held(session.session_id) == 0

    @pytest.mark.asyncio
    async def test_the_idle_limit_counts_from_the_last_use(self):
        """A session used before its idle limit ends one idle limit after that use, not
        at the first deadline. Kills: a timer that ends a session still in use."""
        import asyncio

        clock = RunningClock()
        store = SessionStore(clock=clock)
        session = store.create_session("a", "a", ttl_seconds=3600)
        ended = asyncio.Event()
        assert store.hold(session.session_id, ended.set, idle_timeout=1) is not None
        asyncio.get_running_loop().call_later(0.5, session.touch)

        await asyncio.wait_for(ended.wait(), timeout=4)
        idle = clock() - session.last_accessed_at
        assert session.last_accessed_at - session.created_at >= 0.5
        assert 1.0 <= idle < 2.0

    def test_use_never_moves_the_deadline_past_the_absolute_expiry(
        self, store: SessionStore, clock: FakeClock
    ):
        """Kills: an idle limit that keeps a session alive past its absolute expiry for
        as long as it is used."""
        session = store.create_session("a", "a", ttl_seconds=60)
        assert session.deadline(idle_timeout=30) == START + 30
        clock.advance(50)
        session.touch()
        assert session.deadline(idle_timeout=30) == START + 60
        assert session.deadline() == START + 60

    @pytest.mark.asyncio
    async def test_clearing_the_store_ends_every_socket(self, store: SessionStore):
        session = store.create_session("a", "a")
        ended: list[str] = []
        _ends(store, session.session_id, 2, ended, "s")
        store.clear()
        await _one_turn()
        assert sorted(ended) == ["s0", "s1"]
        assert store._held == {}


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


REFUSED = {"detail": "Authentication required", "reason": "auth"}
WRONG = {"detail": "Invalid credentials", "reason": "auth"}
LOCKED = {
    "detail": "Too many failed authentication attempts. Try again later.",
    "reason": "rate_limited",
}
# A path no route serves: a 404 there means the request passed the door.
PROBE = "/door-probe"
PASSED = {"detail": "Not Found", "reason": "not_found"}
INDEX_HTML = "<!doctype html><title>page</title>"


def _login(client: TestClient, password: str = AUTH_PASSWORD):
    return client.post("/api/v1/auth/login", json={"username": AUTH_USER, "password": password})


class _FrozenTime:
    """`time` for sshler.webapp: the lockout's clock stands still."""

    def __init__(self) -> None:
        import time

        self._time = time
        self.now = 1_000_000.0

    def time(self) -> float:
        return self.now

    def __getattr__(self, name: str):
        return getattr(self._time, name)


@pytest.fixture
def frozen_lockout_clock(monkeypatch: pytest.MonkeyPatch) -> _FrozenTime:
    clock = _FrozenTime()
    monkeypatch.setattr("sshler.webapp.time", clock)
    return clock


@pytest.fixture
def page_client(auth_client: TestClient, tmp_path, monkeypatch: pytest.MonkeyPatch):
    """`auth_client`'s settings with a built page of two files, and a file beside the
    page's folder that must never be served."""
    from sshler.webapp import make_app

    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text(INDEX_HTML, encoding="utf-8")
    (dist / "assets" / "app.js").write_text("console.log(1)", encoding="utf-8")
    (tmp_path / "secret.txt").write_text("outside the page", encoding="utf-8")
    monkeypatch.setattr("sshler.spa.SPA_DIST_DIR", dist)
    client = TestClient(make_app(auth_client.app.state.settings))
    yield client
    client.close()


def _raw_get(client: TestClient, path: str, headers: dict[str, str] | None = None):
    """GET `path` exactly as written: an HTTP client would resolve `..` before
    sending, a server receives it as sent."""
    import asyncio

    app = client.app
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "root_path": "",
        "query_string": b"",
        "headers": [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()],
        "client": ("127.0.0.1", 50000),
        "server": ("testserver", 80),
    }
    sent: list[dict] = []

    async def run() -> None:
        requested = False
        done = asyncio.Event()

        async def receive():
            nonlocal requested
            if not requested:
                requested = True
                return {"type": "http.request", "body": b"", "more_body": False}
            # The client stays connected until the whole answer has been sent.
            await done.wait()
            return {"type": "http.disconnect"}

        async def send(message):
            sent.append(message)
            if message["type"] == "http.response.body" and not message.get("more_body"):
                done.set()

        await app(scope, receive, send)

    asyncio.run(run())
    start = next(m for m in sent if m["type"] == "http.response.start")
    body = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
    names = {k.decode().lower() for k, _ in start["headers"]}
    return start["status"], names, body


class TestDoorDecision:
    """`door_of`: the one decision of what passes without credentials."""

    @pytest.mark.parametrize(
        ("method", "path", "door"),
        [
            ("OPTIONS", "/api/v1/boxes", "exempt"),
            ("GET", "/health", "exempt"),
            ("POST", "/health", "exempt"),
            ("GET", "/", "open"),
            ("HEAD", "/", "open"),
            ("GET", "/app", "open"),
            ("HEAD", "/app", "open"),
            ("GET", "/app/", "open"),
            ("GET", "/app/settings", "open"),
            ("GET", "/app/assets/index-abc.js", "open"),
            ("HEAD", "/app/assets/index-abc.js", "open"),
            ("GET", "/api/v1/bootstrap", "open"),
            ("GET", "/api/v1/auth/me", "open"),
            ("POST", "/api/v1/auth/logout", "open"),
            ("POST", "/api/v1/auth/login", "sign_in"),
            ("GET", "/api/v1/boxes", "closed"),
            ("GET", "/api/v1/boxes/local/download", "closed"),
            ("POST", "/app/", "closed"),
            ("DELETE", "/app/x", "closed"),
            ("POST", "/", "closed"),
            ("GET", "/appx", "closed"),
            ("GET", "/appx/", "closed"),
            ("GET", "/app/../api/v1/boxes", "closed"),
            ("GET", "/app/..", "closed"),
            ("GET", "/app/../", "closed"),
            ("GET", "/app/./x", "closed"),
            ("GET", "/app//x", "closed"),
            ("GET", "//app", "closed"),
            ("GET", "/app/../../etc/passwd", "closed"),
            ("POST", "/api/v1/bootstrap", "closed"),
            ("HEAD", "/api/v1/bootstrap", "closed"),
            ("GET", "/api/v1/bootstrap/", "closed"),
            ("GET", "/api/v1/auth/login", "closed"),
            ("GET", "/api/v1/auth/logout", "closed"),
            ("POST", "/api/v1/auth/me", "closed"),
            ("POST", "/api/v1/auth/login/", "closed"),
            ("GET", "/api/v1/auth/../bootstrap", "closed"),
            ("GET", "app", "closed"),
            ("GET", "", "closed"),
        ],
    )
    def test_door_of_each_method_and_path(self, method: str, path: str, door: str):
        from sshler.webapp import door_of

        assert door_of(method, path).value == door


class TestHttpAuth:
    """Cookie and Basic auth over HTTP. Each test names the mutation it kills."""

    def test_no_credentials_on_an_api_route_is_refused_without_a_challenge(
        self, auth_client: TestClient
    ):
        """Kills: letting requests without credentials through, and the Basic challenge
        that made browsers show their own prompt."""
        response = auth_client.get("/api/v1/boxes")
        assert response.status_code == 401
        assert response.json() == REFUSED
        assert "www-authenticate" not in response.headers
        assert "set-cookie" not in response.headers

    def test_no_credentials_on_a_download_is_refused(self, auth_client: TestClient):
        response = auth_client.get("/api/v1/boxes/local/download", params={"path": "/etc/hosts"})
        assert response.status_code == 401
        assert response.json() == REFUSED
        assert "www-authenticate" not in response.headers

    @pytest.mark.parametrize(
        "path", ["/app/../api/v1/boxes", "/appx", "/app/../", "/app/../../etc/passwd"]
    )
    def test_a_path_that_only_looks_like_the_page_is_refused(
        self, page_client: TestClient, path: str
    ):
        """Kills: deciding by a bare `/app` prefix, or on the path before it is
        normalized."""
        status, header_names, body = _raw_get(page_client, path)
        assert status == 401
        assert json.loads(body) == REFUSED
        assert "www-authenticate" not in header_names

    def test_a_traversal_with_credentials_never_serves_a_file_outside_the_page(
        self, page_client: TestClient
    ):
        status, _names, body = _raw_get(
            page_client,
            "/app/../secret.txt",
            headers={"Authorization": _basic(AUTH_USER, AUTH_PASSWORD)},
        )
        assert status == 200
        assert body.decode() == INDEX_HTML

    def test_a_sibling_folder_with_the_page_folders_name_as_prefix_is_never_served(
        self, page_client: TestClient, tmp_path
    ):
        """Kills: containment by a string prefix, which `dist-x` passes."""
        (tmp_path / "dist-x").mkdir()
        (tmp_path / "dist-x" / "secret.txt").write_text("beside the page", encoding="utf-8")
        status, _names, body = _raw_get(
            page_client,
            "/app/../dist-x/secret.txt",
            headers={"Authorization": _basic(AUTH_USER, AUTH_PASSWORD)},
        )
        assert status == 200
        assert body.decode() == INDEX_HTML

    def test_a_path_that_cannot_be_a_file_name_gets_the_index(self, page_client: TestClient):
        """Kills: a 500 from resolving a path with a NUL character."""
        response = page_client.get("/app/a%00b")
        assert (response.status_code, response.text) == (200, INDEX_HTML)

    def test_the_page_and_its_files_load_without_credentials(self, page_client: TestClient):
        """Kills: keeping the page's shell behind the door (the browser's prompt was the
        only way in)."""
        index = page_client.get("/app/")
        asset = page_client.get("/app/assets/app.js")
        assert (index.status_code, index.text) == (200, INDEX_HTML)
        assert (asset.status_code, asset.text) == (200, "console.log(1)")
        assert "set-cookie" not in index.headers

    def test_missing_credentials_do_not_count_toward_lockout(self, auth_client: TestClient):
        """A request without credentials must not count as a failure.

        Kills: dropping the early `not auth_header` 401 (the request would fall through to
        `record_failure`, and the sixth credential-less request would get 429).
        """
        statuses = [auth_client.get("/api/v1/boxes").status_code for _ in range(6)]
        assert statuses == [401] * 6

    def test_wrong_basic_password_is_refused_without_a_challenge(self, auth_client: TestClient):
        """Kills: accepting any `Basic` header without `verify_basic_auth_header`."""
        response = auth_client.get(
            "/api/v1/boxes", headers={"Authorization": _basic(AUTH_USER, "wrong-password")}
        )
        assert response.status_code == 401
        assert response.json() == WRONG
        assert "www-authenticate" not in response.headers

    def test_a_good_basic_header_passes_and_makes_no_session(self, auth_client: TestClient):
        """Kills: the door making a session and a cookie after Basic auth (only the
        sign-in route makes one)."""
        before = get_session_store().count()
        good = {"Authorization": _basic(AUTH_USER, AUTH_PASSWORD)}
        answers = [auth_client.get(PROBE, headers=good) for _ in range(10)]
        assert [a.status_code for a in answers] == [404] * 10
        assert answers[0].json() == PASSED
        assert ["set-cookie" in a.headers for a in answers] == [False] * 10
        assert get_session_store().count() == before
        assert auth_client.get("/api/v1/boxes").status_code == 401

    def test_wrong_basic_five_times_locks_out_a_right_one(
        self, auth_client: TestClient, frozen_lockout_clock: _FrozenTime
    ):
        bad = {"Authorization": _basic(AUTH_USER, "wrong-password")}
        statuses = [auth_client.get(PROBE, headers=bad).status_code for _ in range(5)]
        locked = auth_client.get(PROBE, headers={"Authorization": _basic(AUTH_USER, AUTH_PASSWORD)})
        assert statuses == [401] * 5
        assert locked.status_code == 429
        assert locked.json() == LOCKED
        assert locked.headers["Retry-After"] == "300"

    def test_an_unknown_or_expired_cookie_is_refused_on_the_api_and_not_on_the_page(
        self, page_client: TestClient
    ):
        """Kills: treating any present cookie as authenticated (skipping `get_session`)."""
        expired = get_session_store().create_session(AUTH_USER, AUTH_USER, ttl_seconds=0)
        for sid in ("forged", expired.session_id):
            api = page_client.get("/api/v1/boxes", headers={"Cookie": f"sshler_session={sid}"})
            page = page_client.get("/app/", headers={"Cookie": f"sshler_session={sid}"})
            assert (api.status_code, api.json()) == (401, REFUSED)
            assert "www-authenticate" not in api.headers
            assert (page.status_code, page.text) == (200, INDEX_HTML)

    @pytest.mark.parametrize(
        ("method", "path"), [("POST", "/api/v1/bootstrap"), ("GET", "/api/v1/auth/login")]
    )
    def test_another_method_on_an_open_path_is_not_open(
        self, auth_client: TestClient, method: str, path: str
    ):
        response = auth_client.request(method, path)
        assert response.status_code == 401
        assert response.json() == REFUSED

    def test_login_with_correct_credentials_makes_the_session(self, auth_client: TestClient):
        """Kills: `login` skipping `auth_manager.authenticate` (paired with the next
        test), and the sign-in route staying behind the door."""
        before = get_session_store().count()
        response = _login(auth_client)
        assert response.status_code == 200
        assert response.json() == {"success": True, "message": "Login successful"}
        assert get_session_store().count() == before + 1
        assert auth_client.get(PROBE).status_code == 404

    def test_login_with_wrong_password_is_401(self, auth_client: TestClient):
        """Kills: `login` accepting any credentials."""
        response = _login(auth_client, "wrong-password")
        assert response.status_code == 401
        assert response.json() == {"detail": "Invalid username or password", "reason": "auth"}
        assert "www-authenticate" not in response.headers

    def test_logout_destroys_session(self, auth_client: TestClient):
        """Kills: `logout` clearing the cookie without `delete_session` (the old cookie
        would still authenticate)."""
        _login(auth_client)
        session_id = auth_client.cookies["sshler_session"]
        assert auth_client.get(PROBE).status_code == 404

        logout = auth_client.post("/api/v1/auth/logout")
        assert logout.status_code == 200
        assert logout.json() == {"success": True, "message": "Logged out successfully"}

        auth_client.cookies.clear()
        replay = auth_client.get(
            "/api/v1/boxes", headers={"Cookie": f"sshler_session={session_id}"}
        )
        assert replay.status_code == 401
        assert replay.json() == REFUSED
        assert "www-authenticate" not in replay.headers

    def test_login_while_locked_out_says_how_long(
        self, auth_client: TestClient, frozen_lockout_clock: _FrozenTime
    ):
        """Kills: the sign-in route escaping the lockout."""
        statuses = [_login(auth_client, "wrong-password").status_code for _ in range(5)]
        locked = _login(auth_client)
        assert statuses == [401] * 5
        assert locked.status_code == 429
        assert locked.json() == LOCKED
        assert locked.headers["Retry-After"] == "300"

    def test_while_locked_out_the_page_and_the_bootstrap_still_load(
        self, page_client: TestClient, frozen_lockout_clock: _FrozenTime
    ):
        """Kills: the lockout stopping the open routes (the form could not load to say
        how long to wait), and a locked-out Basic header being checked there."""
        for _ in range(5):
            _login(page_client, "wrong-password")
        good = {"Authorization": _basic(AUTH_USER, AUTH_PASSWORD)}
        page = page_client.get("/app/")
        bootstrap = page_client.get("/api/v1/bootstrap", headers=good)
        assert (page.status_code, page.text) == (200, INDEX_HTML)
        assert bootstrap.status_code == 200
        assert (bootstrap.json()["authenticated"], bootstrap.json()["token"]) == (False, None)
        assert page_client.get(PROBE).status_code == 429


def _stranger_body(**overrides):
    from sshler import __version__

    body = {
        "version": __version__,
        "token_header": "X-SSHLER-TOKEN",
        "token": None,
        "auth_required": True,
        "authenticated": False,
        "allow_origins": [],
        "spa_base": "/app/",
        "spa_enabled": True,
        "wsl_distro": None,
        "pdf_available": False,
        "platform": "posix",
        "windows_shells": [],
        "default_shell": None,
        "artifact_server_enabled": False,
        "artifact_server_port": None,
    }
    body.update(overrides)
    return body


class TestBootstrapSaysWhoAsks:
    """The bootstrap is open; it gives the token only to an authenticated request."""

    def test_without_credentials(self, auth_client: TestClient):
        """Kills: the token handed to anyone who can reach the page."""
        response = auth_client.get("/api/v1/bootstrap")
        body = response.json()
        assert response.status_code == 200
        assert (body["token"], body["auth_required"], body["authenticated"]) == (None, True, False)
        assert "basic_auth_required" not in body
        assert "set-cookie" not in response.headers

    def test_with_a_session(self, auth_client: TestClient):
        _login(auth_client)
        body = auth_client.get("/api/v1/bootstrap").json()
        assert (body["token"], body["auth_required"], body["authenticated"]) == ("t", True, True)

    def test_a_stranger_gets_only_what_the_sign_in_page_needs(
        self, auth_client: TestClient, monkeypatch: pytest.MonkeyPatch
    ):
        """Kills: the allowed origins, the artifact port and the platform details given
        to a request that has not signed in."""
        from sshler.api import config as config_module

        monkeypatch.setattr(config_module, "LOCAL_IS_WINDOWS", False)
        monkeypatch.setattr(config_module, "_WINDOWS_SHELLS", [])
        monkeypatch.setattr(config_module, "_WSL_DISTRO", "Ubuntu")
        monkeypatch.setattr(config_module, "_DEFAULT_SHELL", "pwsh")
        monkeypatch.setattr(config_module.PDF_RENDERER, "available", True)
        settings = auth_client.app.state.settings
        settings.allow_origins = ["https://elsewhere.example"]
        settings.serve_artifacts = True
        settings.artifact_server_port_actual = 4321
        assert auth_client.get("/api/v1/bootstrap").json() == _stranger_body()

        _login(auth_client)
        body = auth_client.get("/api/v1/bootstrap").json()
        assert body == _stranger_body(
            token="t",
            authenticated=True,
            allow_origins=["https://elsewhere.example"],
            wsl_distro="Ubuntu",
            default_shell="pwsh",
            pdf_available=True,
            artifact_server_enabled=True,
            artifact_server_port=4321,
        )

    def test_with_a_good_basic_header(self, auth_client: TestClient):
        response = auth_client.get(
            "/api/v1/bootstrap", headers={"Authorization": _basic(AUTH_USER, AUTH_PASSWORD)}
        )
        body = response.json()
        assert (body["token"], body["authenticated"]) == ("t", True)
        assert "set-cookie" not in response.headers

    def test_with_a_wrong_basic_header(self, auth_client: TestClient):
        response = auth_client.get(
            "/api/v1/bootstrap", headers={"Authorization": _basic(AUTH_USER, "wrong-password")}
        )
        body = response.json()
        assert response.status_code == 200
        assert (body["token"], body["authenticated"]) == (None, False)
        assert auth_client.app.state.auth_tracker.get_failure_count("testclient") == 1

    def test_a_password_from_the_environment_only(self, monkeypatch: pytest.MonkeyPatch):
        """Kills: `auth_required` read from the command-line password alone (false when
        the password comes from the environment, so the page never asked to sign in)."""
        from argon2 import PasswordHasher as RealArgon2Hasher

        from sshler.webapp import ServerSettings, make_app

        made_up_hash = RealArgon2Hasher(time_cost=1, memory_cost=8, parallelism=1).hash(
            "Env-Only-Pass-42!"
        )
        monkeypatch.setenv("SSHLER_USERNAME", "envuser")
        monkeypatch.setenv("SSHLER_PASSWORD_HASH", made_up_hash)
        monkeypatch.delenv("SSHLER_PASSWORD", raising=False)
        reset_settings()
        client = TestClient(make_app(ServerSettings(csrf_token="t")))
        try:
            body = client.get("/api/v1/bootstrap").json()
        finally:
            client.close()
        assert (body["token"], body["auth_required"], body["authenticated"]) == (None, True, False)

    def test_with_no_password_set(self, monkeypatch: pytest.MonkeyPatch):
        from sshler.webapp import ServerSettings, make_app

        monkeypatch.delenv("SSHLER_USERNAME", raising=False)
        reset_settings()
        client = TestClient(make_app(ServerSettings(csrf_token="t")))
        try:
            body = client.get("/api/v1/bootstrap").json()
        finally:
            client.close()
        assert (body["token"], body["auth_required"], body["authenticated"]) == ("t", False, True)


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
    headers = {"X-Real-IP": forwarded}
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
        locked = client.get(
            "/api/v1/boxes",
            headers={**headers, "Authorization": _basic(AUTH_USER, AUTH_PASSWORD)},
        )
    finally:
        client.close()

    assert statuses == [401] * 5
    assert tracker.is_locked_out(forwarded) is True
    assert tracker.is_locked_out("127.0.0.1") is False
    assert locked.status_code == 429
    assert locked.json() == LOCKED


def test_login_when_auth_disabled_is_400():
    """Kills: `login` creating sessions when no auth manager is configured."""
    from sshler.webapp import ServerSettings, make_app

    client = TestClient(make_app(ServerSettings(csrf_token="t")))
    response = client.post("/api/v1/auth/login", json={"username": "a", "password": "b"})
    client.close()
    assert response.status_code == 400
    assert response.json() == {"detail": "Authentication is not enabled", "reason": "invalid"}


def test_me_without_credentials_is_401(auth_client: TestClient):
    """Kills: the current-user route answering a stranger."""
    response = auth_client.get("/api/v1/auth/me")
    assert response.status_code == 401
    assert response.json() == {"detail": "Not authenticated", "reason": "auth"}


def test_logout_without_a_session_answers_and_sets_no_session(auth_client: TestClient):
    """Kills: logout refusing a request that has no session, or making one."""
    before = get_session_store().count()
    response = auth_client.post("/api/v1/auth/logout")
    assert response.status_code == 200
    assert response.json() == {"success": True, "message": "Logged out successfully"}
    assert get_session_store().count() == before
    cookie = response.headers["set-cookie"]
    assert cookie.startswith('sshler_session=""; ')
    assert "Max-Age=0" in cookie


def test_a_switch_that_never_opened_the_door_does_not_stop_signing_in(
    auth_client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    """Kills: a second switch (`SSHLER_REQUIRE_AUTH`) that closed the sign-in route
    while the door stayed closed, so nobody could sign in."""
    monkeypatch.setenv("SSHLER_REQUIRE_AUTH", "false")
    reset_settings()
    from sshler.webapp import make_app

    client = TestClient(make_app(auth_client.app.state.settings))
    try:
        before = get_session_store().count()
        login = _login(client)
        assert login.status_code == 200
        assert login.json() == {"success": True, "message": "Login successful"}
        assert get_session_store().count() == before + 1
        client.cookies.clear()
        refused = client.get("/api/v1/boxes")
        assert refused.status_code == 401
        assert refused.json() == REFUSED
    finally:
        client.close()


@pytest.fixture
def store_clock(monkeypatch: pytest.MonkeyPatch) -> FakeClock:
    """A session store on a clock that only moves when told. Request it before
    `auth_client`, which builds the app after it is set."""
    fake = FakeClock()
    monkeypatch.setattr(session_module, "_session_store", SessionStore(clock=fake))
    return fake


@pytest.fixture
def idle_clock(store_clock: FakeClock, monkeypatch: pytest.MonkeyPatch) -> FakeClock:
    """`store_clock` and a 600 s idle timeout."""
    monkeypatch.setenv("SSHLER_SESSION_IDLE_TIMEOUT_SECONDS", "600")
    yield store_clock
    reset_settings()


def test_an_idle_session_is_not_signed_in_for_the_door_the_bootstrap_or_the_api(
    idle_clock: FakeClock, auth_client: TestClient
):
    """Kills: the door and the bootstrap judging a session live without the idle
    timeout that `/api/v1/auth/me` applies."""
    assert _login(auth_client).status_code == 200
    assert auth_client.get("/api/v1/auth/me").status_code == 200
    idle_clock.advance(1200)

    body = auth_client.get("/api/v1/bootstrap").json()
    assert (body["authenticated"], body["token"]) == (False, None)
    api = auth_client.get("/api/v1/boxes")
    assert api.status_code == 401
    assert api.json() == REFUSED


def test_an_idle_timeout_of_zero_changes_nothing(store_clock: FakeClock, auth_client: TestClient):
    assert _login(auth_client).status_code == 200
    store_clock.advance(1200)
    body = auth_client.get("/api/v1/bootstrap").json()
    assert (body["authenticated"], body["token"]) == (True, "t")
