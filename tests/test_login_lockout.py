"""The sign-in route and the failed-login count (webapp `_security_middleware`)."""

from __future__ import annotations

import base64

import pytest
from argon2 import PasswordHasher as RealArgon2Hasher
from fastapi.testclient import TestClient

from sshler import auth, webapp
from sshler.webapp import ServerSettings, make_app

USER = "admin"
PASSWORD = "Corr3ct-Horse-Battery!"
THRESHOLD = 5


def _basic(user: str, password: str) -> str:
    return "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode("ascii")


class _NoSleep:
    async def sleep(self, delay, result=None):
        return result

    def __getattr__(self, name):
        import asyncio

        return getattr(asyncio, name)


@pytest.fixture
def app_and_client(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        auth,
        "Argon2PasswordHasher",
        lambda **_p: RealArgon2Hasher(time_cost=1, memory_cost=8, parallelism=1),
    )
    monkeypatch.setattr(webapp, "asyncio", _NoSleep())
    app = make_app(ServerSettings(csrf_token="t", basic_auth=(USER, PASSWORD)))
    client = TestClient(app)
    yield app, client
    client.close()


def _login(client: TestClient, path: str, body_password: str):
    client.cookies.clear()
    return client.post(
        path,
        json={"username": USER, "password": body_password},
        headers={"Authorization": _basic(USER, PASSWORD)},
    )


@pytest.mark.parametrize("path", ["/api/v1/auth/login", "/api/v1/auth/login/"])
def test_bad_logins_with_a_valid_basic_header_still_count(app_and_client, path):
    """Kills: the middleware resetting the count on a valid Basic header for the login
    route itself, so the route's own failure never adds up to the lockout."""
    app, client = app_and_client
    for _ in range(THRESHOLD):
        _login(client, path, "wrong")
    counts = app.state.auth_tracker.get_failure_count("testclient")
    assert counts == THRESHOLD


def test_good_login_after_four_bad_ones_resets_the_count(app_and_client):
    """Kills: dropping the reset on a good login."""
    app, client = app_and_client
    for _ in range(THRESHOLD - 1):
        _login(client, "/api/v1/auth/login", "wrong")
    good = _login(client, "/api/v1/auth/login", PASSWORD)
    assert good.status_code == 200
    assert app.state.auth_tracker.get_failure_count("testclient") == 0
