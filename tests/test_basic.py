from fastapi.testclient import TestClient

from sshler.config import ensure_config, load_config
from sshler.webapp import ServerSettings, make_app


def test_config_created() -> None:
    config_path = ensure_config()
    assert config_path.exists()
    application_config = load_config()
    assert [box.name for box in application_config.boxes] == ["local"]


def test_root_redirects_to_spa() -> None:
    app = make_app(ServerSettings(csrf_token="test-token"))
    client = TestClient(app)
    response = client.get("/", follow_redirects=False)
    assert response.status_code == 307
    assert response.headers["location"] == "/app/"


def test_csp_header_is_exact() -> None:
    """Pin the exact Content-Security-Policy header the app sends today.

    This pins CURRENT policy (including 'unsafe-inline' styles and the unpkg and
    jsdelivr CDNs); it is not an endorsement of that policy. Mutation killed:
    any change to a directive, source or ordering of the header.
    """
    app = make_app(ServerSettings(csrf_token="test-token"))
    client = TestClient(app)
    response = client.get("/health")
    assert response.status_code == 200
    assert response.headers["Content-Security-Policy"] == (
        "default-src 'self'; img-src 'self' data:; "
        "style-src 'self' 'unsafe-inline' https://unpkg.com https://cdn.jsdelivr.net; "
        "script-src 'self' https://unpkg.com https://cdn.jsdelivr.net; "
        "connect-src 'self' https://unpkg.com; frame-src 'self';"
    )
