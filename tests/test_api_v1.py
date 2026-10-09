import time
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from sshler import state
from sshler.webapp import ServerSettings, make_app

TEST_TOKEN = "api-token"


def build_client(config_dir: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("SSHLER_CONFIG_DIR", str(config_dir))
    return TestClient(make_app(ServerSettings(csrf_token=TEST_TOKEN)))


def setup_config(tmp_path: Path) -> Path:
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "boxes.yaml").write_text(
        yaml.safe_dump({"boxes": []}, sort_keys=False), encoding="utf-8"
    )
    return config_dir


def auth_headers() -> dict[str, str]:
    return {"X-SSHLER-TOKEN": TEST_TOKEN}


def test_bootstrap_spa_toggle(tmp_path):
    setup_config(tmp_path)
    client = TestClient(make_app(ServerSettings(csrf_token=TEST_TOKEN, serve_spa=False)))
    try:
        resp = client.get("/api/v1/bootstrap")
        assert resp.status_code == 200
        data = resp.json()
        assert data["spa_enabled"] is False
        assert data["spa_base"] == ""
    finally:
        client.close()


def test_api_bootstrap_and_boxes(tmp_path, monkeypatch):
    config_dir = setup_config(tmp_path)
    client = build_client(config_dir, monkeypatch)
    try:
        bootstrap = client.get("/api/v1/bootstrap")
        assert bootstrap.status_code == 200
        data = bootstrap.json()
        assert data["token"] == TEST_TOKEN
        boxes = client.get("/api/v1/boxes", headers=auth_headers())
        assert boxes.status_code == 200
        names = [box["name"] for box in boxes.json()]
        assert "local" in names
        root = client.get("/", follow_redirects=False)
        assert root.status_code == 307
        assert root.headers["location"] == "/app/"
    finally:
        client.close()


def test_local_directory_touch_delete(tmp_path, monkeypatch):
    config_dir = setup_config(tmp_path)
    workdir = tmp_path / "work"
    workdir.mkdir()
    client = build_client(config_dir, monkeypatch)
    try:
        # list directory
        listing = client.get(
            "/api/v1/boxes/local/ls",
            params={"directory": str(workdir)},
            headers=auth_headers(),
        )
        assert listing.status_code == 200

        # touch
        touch_resp = client.post(
            "/api/v1/boxes/local/touch",
            json={"directory": str(workdir), "filename": "demo.txt"},
            headers=auth_headers(),
        )
        assert touch_resp.status_code == 200
        assert (workdir / "demo.txt").exists()

        # delete
        delete_resp = client.post(
            "/api/v1/boxes/local/delete",
            json={"path": str(workdir / "demo.txt")},
            headers=auth_headers(),
        )
        assert delete_resp.status_code == 200
        assert not (workdir / "demo.txt").exists()
    finally:
        client.close()


def test_write_file(tmp_path, monkeypatch):
    config_dir = setup_config(tmp_path)
    workdir = tmp_path / "work"
    workdir.mkdir()
    target = workdir / "edit.txt"
    target.write_text("old", encoding="utf-8")
    client = build_client(config_dir, monkeypatch)
    try:
        write_resp = client.post(
            "/api/v1/boxes/local/write",
            json={"path": str(target), "content": "new-content"},
            headers=auth_headers(),
        )
        assert write_resp.status_code == 200
        assert target.read_text(encoding="utf-8") == "new-content"
    finally:
        client.close()


def test_favorites_and_pin(tmp_path, monkeypatch):
    config_dir = setup_config(tmp_path)
    client = build_client(config_dir, monkeypatch)
    try:
        fav_resp = client.post(
            "/api/v1/boxes/local/fav",
            json={"path": "/tmp", "favorite": True},
            headers=auth_headers(),
        )
        assert fav_resp.status_code == 200
        pin_resp = client.post(
            "/api/v1/boxes/local/pin",
            headers=auth_headers(),
        )
        assert pin_resp.status_code == 200
        status = client.get("/api/v1/boxes/local/status", headers=auth_headers())
        assert status.status_code == 200
        # The local box is never remote, so its status is deterministically online.
        assert status.json() == {"name": "local", "status": "online", "latency_ms": 0.0}
    finally:
        client.close()


def test_sessions_crud(tmp_path, monkeypatch):
    """Session rows are created, listed, updated and deleted without touching tmux."""
    config_dir = setup_config(tmp_path)
    client = build_client(config_dir, monkeypatch)
    try:
        create = client.post(
            "/api/v1/boxes/local/sessions",
            json={"session_name": "s1", "working_directory": "/"},
            headers=auth_headers(),
        )
        assert create.status_code == 200
        session_id = create.json()["id"]

        listing = client.get(
            "/api/v1/boxes/local/sessions",
            headers=auth_headers(),
        )
        assert listing.status_code == 200
        assert any(item["id"] == session_id for item in listing.json())

        update = client.patch(
            f"/api/v1/boxes/local/sessions/{session_id}",
            json={"active": False, "window_count": 2, "metadata": {"cols": 80}},
            headers=auth_headers(),
        )
        assert update.status_code == 200
        payload = update.json()
        assert payload["active"] is False
        assert payload["window_count"] == 2
        assert payload["metadata"]["cols"] == 80

        delete_resp = client.delete(
            f"/api/v1/boxes/local/sessions/{session_id}",
            headers=auth_headers(),
        )
        assert delete_resp.status_code == 200
        # download path
        download = client.get(
            "/api/v1/boxes/local/download",
            params={"path": str(tmp_path / "config" / "boxes.yaml")},
            headers=auth_headers(),
        )
        assert download.status_code == 200
    finally:
        client.close()
        state.reset_state()


@pytest.mark.posix_only("a PTY running the sh fake tmux behind /ws/term")
def test_local_ws_term_echoes_and_records_its_session(
    tmp_path, monkeypatch, fake_tmux, tmux_tripwire
):
    """A local /ws/term opens `new -As` in the work dir, echoes bytes and records the row."""

    async def no_history(_session: str, _directory: str) -> None:
        return None

    monkeypatch.setattr("sshler.webapp.record_ts_history", no_history)
    workdir = tmp_path / "work"
    workdir.mkdir()
    config_dir = setup_config(tmp_path)
    client = build_client(config_dir, monkeypatch)
    try:
        echoed = b""
        with client.websocket_connect(
            f"/ws/term?host=local&dir={workdir}&session=test&cols=10&rows=5&token={TEST_TOKEN}"
        ) as ws:
            ws.send_bytes(b"crud-ping-3b8e\n")
            deadline = time.monotonic() + 5.0
            while echoed.count(b"crud-ping-3b8e") < 2 and time.monotonic() < deadline:
                message = ws.receive()
                if message.get("bytes"):
                    echoed += message["bytes"]
        # Once from the tty's own echo, once from `cat` writing back what it read.
        assert echoed.count(b"crud-ping-3b8e") == 2
        assert [call for call in fake_tmux.calls() if call.startswith("new ")] == [
            f"new -As test -c {workdir}"
        ]

        tracked = client.get("/api/v1/boxes/local/sessions", headers=auth_headers())
        assert tracked.status_code == 200
        terminal_rows = [item for item in tracked.json() if item["session_name"] == "test"]
        assert [(row["box"], row["working_directory"]) for row in terminal_rows] == [
            ("local", str(workdir))
        ]
    finally:
        client.close()
        state.reset_state()
    assert not tmux_tripwire.exists(), tmux_tripwire.read_text()


def test_refresh_box_clears_overrides(tmp_path, monkeypatch):
    """Refreshing a box removes stored connection overrides but preserves favorites."""
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "boxes.yaml").write_text(
        yaml.safe_dump(
            {
                "boxes": [
                    {
                        "name": "demo-box",
                        "host": "stale-host",
                        "user": "override",
                        "ssh_alias": "override-alias",
                    }
                ]
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    ssh_config = tmp_path / "ssh_config"
    ssh_config.write_text(
        "Host demo-box\n  HostName fresh.example\n  User deploy",
        encoding="utf-8",
    )
    monkeypatch.setenv("SSHLER_SSH_CONFIG", str(ssh_config))

    client = build_client(config_dir, monkeypatch)
    try:
        resp = client.post(
            "/api/v1/boxes/demo-box/refresh",
            headers=auth_headers(),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "demo-box"
        assert data["refreshed"] is True

        stored = yaml.safe_load((config_dir / "boxes.yaml").read_text(encoding="utf-8"))
        assert stored["boxes"] == []
    finally:
        client.close()


def test_bootstrap_exposes_pdf_available(tmp_path, monkeypatch):
    """Bootstrap must report pdf_available reflecting the renderer's current state."""
    from sshler import pdf as pdf_module

    monkeypatch.setattr(pdf_module.PDF_RENDERER, "available", True)
    config_dir = setup_config(tmp_path)
    client = build_client(config_dir, monkeypatch)
    try:
        resp = client.get("/api/v1/bootstrap")
        assert resp.status_code == 200
        assert resp.json()["pdf_available"] is True
    finally:
        client.close()


def test_pdf_render_requires_token(tmp_path, monkeypatch):
    """The /pdf/render endpoint inherits global token gating."""
    config_dir = setup_config(tmp_path)
    client = build_client(config_dir, monkeypatch)
    try:
        resp = client.post(
            "/api/v1/pdf/render",
            json={"html": "<html></html>", "filename": "x.pdf"},
        )
        assert resp.status_code == 403
    finally:
        client.close()


def test_pdf_render_returns_503_when_unavailable(tmp_path, monkeypatch):
    """When the Playwright renderer is unavailable, the endpoint must return 503
    with an actionable install hint — the frontend uses this to keep the button hidden,
    and a stale UI state must produce a clear error rather than a generic 500."""
    from sshler import pdf as pdf_module

    monkeypatch.setattr(pdf_module.PDF_RENDERER, "available", False)
    config_dir = setup_config(tmp_path)
    client = build_client(config_dir, monkeypatch)
    try:
        resp = client.post(
            "/api/v1/pdf/render",
            headers=auth_headers(),
            json={"html": "<html></html>", "filename": "x.pdf"},
        )
        assert resp.status_code == 503
        assert "playwright install chromium" in resp.json()["detail"]
    finally:
        client.close()


def test_pdf_render_streams_pdf_when_available(tmp_path, monkeypatch):
    """Happy path: when the renderer reports available, the endpoint must call
    PDFRenderer.render() with the request HTML and stream the bytes back as
    application/pdf with a sanitized Content-Disposition filename."""
    from sshler import pdf as pdf_module

    captured: dict[str, str] = {}

    async def fake_render(html: str) -> bytes:
        captured["html"] = html
        return b"%PDF-1.4\nfake-pdf-bytes\n%%EOF\n"

    monkeypatch.setattr(pdf_module.PDF_RENDERER, "available", True)
    monkeypatch.setattr(pdf_module.PDF_RENDERER, "render", fake_render)
    config_dir = setup_config(tmp_path)
    client = build_client(config_dir, monkeypatch)
    try:
        resp = client.post(
            "/api/v1/pdf/render",
            headers=auth_headers(),
            json={"html": "<h1>title</h1>", "filename": "report"},
        )
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "application/pdf"
        # The filename gets sanitized + .pdf appended if missing
        assert resp.headers["content-disposition"] == 'attachment; filename="report.pdf"'
        assert resp.content == b"%PDF-1.4\nfake-pdf-bytes\n%%EOF\n"
        assert captured["html"] == "<h1>title</h1>"
    finally:
        client.close()


def test_pdf_render_sanitizes_filename(tmp_path, monkeypatch):
    """Filename must be stripped of path separators and quote characters so it
    can't break the Content-Disposition header or escape the downloads folder."""
    from sshler import pdf as pdf_module

    async def fake_render(html: str) -> bytes:
        return b"%PDF-1.4\n%%EOF\n"

    monkeypatch.setattr(pdf_module.PDF_RENDERER, "available", True)
    monkeypatch.setattr(pdf_module.PDF_RENDERER, "render", fake_render)
    config_dir = setup_config(tmp_path)
    client = build_client(config_dir, monkeypatch)
    try:
        resp = client.post(
            "/api/v1/pdf/render",
            headers=auth_headers(),
            json={"html": "<h1>x</h1>", "filename": '../../etc/passwd"; rm -rf /'},
        )
        assert resp.status_code == 200
        cd = resp.headers["content-disposition"]
        assert "/" not in cd.split("filename=")[1]
        assert '"' not in cd.split("filename=")[1].strip('"')
        assert cd.endswith('.pdf"')
    finally:
        client.close()


def test_pdf_render_rejects_oversize_payload(tmp_path, monkeypatch):
    """20 MB cap protects against accidental huge uploads."""
    from sshler import pdf as pdf_module

    monkeypatch.setattr(pdf_module.PDF_RENDERER, "available", True)
    config_dir = setup_config(tmp_path)
    client = build_client(config_dir, monkeypatch)
    try:
        oversized = "<p>" + ("a" * 21_000_000) + "</p>"
        resp = client.post(
            "/api/v1/pdf/render",
            headers=auth_headers(),
            json={"html": oversized, "filename": "big.pdf"},
        )
        assert resp.status_code == 413
        assert resp.json()["detail"] == "HTML payload too large"
    finally:
        client.close()
