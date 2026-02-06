import os
import tempfile

from fastapi.testclient import TestClient

os.environ["SSHLER_CONFIG_DIR"] = tempfile.mkdtemp(prefix="sshler_")

from sshler.config import ensure_config, load_config
from sshler.webapp import ServerSettings, make_app


def test_config_created() -> None:
    config_path = ensure_config()
    assert config_path.exists()
    application_config = load_config()
    assert len(application_config.boxes) >= 1


def test_root_redirects_to_spa() -> None:
    app = make_app(ServerSettings(csrf_token="test-token"))
    client = TestClient(app)
    response = client.get("/", follow_redirects=False)
    assert response.status_code in {302, 307}
    assert response.headers["location"] == "/app/"


def test_csp_allows_inline_styles() -> None:
    """Test that Content-Security-Policy allows inline styles for terminal rendering."""
    app = make_app(ServerSettings(csrf_token="test-token"))
    client = TestClient(app)
    response = client.get("/health")
    assert response.status_code == 200
    csp_header = response.headers.get("Content-Security-Policy")
    assert csp_header is not None
    assert "'unsafe-inline'" in csp_header
    assert "style-src 'self' 'unsafe-inline' https://unpkg.com" in csp_header
