"""Origin/CSRF middleware (`_origin_check_middleware` in sshler/webapp.py).

Every test names the mutation it kills. The headline mutation is the audit's C2:
``app_settings.origin_check_enabled and ...`` -> ``False and ...``; with it, a hostile
origin reaches the route and gets the route's status instead of 403.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from sshler.webapp import ServerSettings, make_app

TOKEN = "origin-test-token"
PUBLIC_URL = "https://sshler.example.test"
HOSTILE = "http://evil.example"
# No route matches this path, so any request the middleware lets through gets
# Starlette's 404 "Not Found" for every method.
UNROUTED = "/origin-probe"
STATE_CHANGING = ["POST", "PUT", "PATCH", "DELETE"]


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("SSHLER_PUBLIC_URL", PUBLIC_URL)
    # No `with`: entering the client would run the lifespan (pool, PDF renderer).
    test_client = TestClient(make_app(ServerSettings(csrf_token=TOKEN)))
    yield test_client
    test_client.close()


@pytest.mark.parametrize("method", STATE_CHANGING)
def test_hostile_origin_rejected_on_every_state_changing_method(client: TestClient, method: str):
    """Kills: `origin_check_enabled and ...` -> `False and ...`, and dropping a method
    from the `("POST", "PUT", "PATCH", "DELETE")` tuple."""
    response = client.request(method, UNROUTED, headers={"Origin": HOSTILE})
    assert response.status_code == 403
    assert response.text == "Invalid Origin header"


@pytest.mark.parametrize("method", STATE_CHANGING)
def test_public_url_origin_accepted(client: TestClient, method: str):
    """Kills: not appending the `public_url`-derived origin to the allow list (the
    public origin would get 403 instead of reaching the router's 404)."""
    response = client.request(method, UNROUTED, headers={"Origin": PUBLIC_URL})
    assert response.status_code == 404
    assert response.text == '{"detail":"Not Found"}'


@pytest.mark.parametrize("method", STATE_CHANGING)
def test_missing_origin_accepted(client: TestClient, method: str):
    """Kills: treating a missing Origin/Referer as hostile (non-browser clients send none)."""
    response = client.request(method, UNROUTED)
    assert response.status_code == 404
    assert response.text == '{"detail":"Not Found"}'


def test_hostile_referer_without_origin_rejected(client: TestClient):
    """Kills: dropping the `or request.headers.get("referer")` fallback."""
    response = client.post(UNROUTED, headers={"Referer": f"{HOSTILE}/some/page"})
    assert response.status_code == 403
    assert response.text == "Invalid Origin header"


def test_public_url_origin_with_path_in_referer_accepted(client: TestClient):
    """Kills: comparing the raw Referer instead of its scheme://netloc."""
    response = client.post(UNROUTED, headers={"Referer": f"{PUBLIC_URL}/app/files"})
    assert response.status_code == 404


def test_get_with_hostile_origin_not_blocked(client: TestClient):
    """Kills: adding GET to the checked methods (GET is not state-changing)."""
    response = client.get(UNROUTED, headers={"Origin": HOSTILE})
    assert response.status_code == 404


def test_cli_allow_origins_accepted(monkeypatch: pytest.MonkeyPatch):
    """Kills: dropping `cli_settings.allow_origins` from `allowed_origins`."""
    monkeypatch.delenv("SSHLER_PUBLIC_URL", raising=False)
    app = make_app(ServerSettings(csrf_token=TOKEN, allow_origins=["http://localhost:5173/"]))
    test_client = TestClient(app)
    allowed = test_client.post(UNROUTED, headers={"Origin": "http://localhost:5173"})
    hostile = test_client.post(UNROUTED, headers={"Origin": HOSTILE})
    assert allowed.status_code == 404
    assert hostile.status_code == 403
    assert hostile.text == "Invalid Origin header"


def test_real_endpoint_hostile_origin_blocked_before_handler(client: TestClient):
    """A real state-changing endpoint: the hostile request never reaches the handler.

    Kills: the C2 mutation on a real route (the hostile POST would return 200).
    """
    headers = {"X-SSHLER-TOKEN": TOKEN}
    hostile = client.post(
        "/api/v1/ping", json={"title": "t"}, headers={**headers, "Origin": HOSTILE}
    )
    allowed = client.post(
        "/api/v1/ping", json={"title": "t"}, headers={**headers, "Origin": PUBLIC_URL}
    )
    assert hostile.status_code == 403
    assert hostile.text == "Invalid Origin header"
    assert allowed.status_code == 200
    assert allowed.json()["ok"] is True
