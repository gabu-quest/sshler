import os
import tempfile

from fastapi.testclient import TestClient

os.environ["SSHLER_CONFIG_DIR"] = tempfile.mkdtemp(prefix="sshler_")

from sshler.config import ensure_config, load_config
from sshler.webapp import make_app


def test_config_created() -> None:
    config_path = ensure_config()
    assert config_path.exists()
    application_config = load_config()
    assert len(application_config.boxes) >= 1


def test_boxes_route() -> None:
    app = make_app()
    client = TestClient(app)
    response = client.get("/boxes")
    assert response.status_code == 200
    assert "Boxes" in response.text
