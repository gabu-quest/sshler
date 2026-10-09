"""REST tests for artifact projects and registrations."""

from __future__ import annotations

import socket
from pathlib import Path

import yaml
from fastapi.testclient import TestClient

from sshler import state
from sshler.webapp import ServerSettings, make_app

TOKEN = "artifact-api-token"


def _client(tmp_path: Path, monkeypatch) -> TestClient:
    config = tmp_path / "config"
    config.mkdir()
    (config / "boxes.yaml").write_text(
        yaml.safe_dump({"boxes": []}, sort_keys=False), encoding="utf-8"
    )
    monkeypatch.setenv("SSHLER_CONFIG_DIR", str(config))
    state.reset_state()
    state.initialize(config)
    return TestClient(make_app(ServerSettings(csrf_token=TOKEN)))


def _headers() -> dict[str, str]:
    return {"X-SSHLER-TOKEN": TOKEN}


def test_create_list_rescan_update_and_unregister(tmp_path: Path, monkeypatch) -> None:
    site = tmp_path / "sample-project" / "site"
    site.mkdir(parents=True)
    source = site / "index.html"
    original = "<title>Initial Page</title>"
    source.write_text(original, encoding="utf-8")
    client = _client(tmp_path, monkeypatch)
    try:
        created = client.post(
            "/api/v1/artifacts",
            headers=_headers(),
            json={
                "source_path": str(site),
                "project": "Sample Project",
                "group_path": "design/layout",
            },
        )
        assert created.status_code == 200
        artifact = created.json()
        assert artifact["created"] is True
        assert artifact["mode"] == "site"
        assert artifact["exists"] is True

        repeated = client.post(
            "/api/v1/artifacts",
            headers=_headers(),
            json={
                "source_path": str(site),
                "project": "sample project",
                "group_path": "design/layout",
            },
        )
        assert repeated.status_code == 200
        assert repeated.json()["created"] is False
        assert repeated.json()["id"] == artifact["id"]

        pages = client.get(f"/api/v1/artifacts/{artifact['id']}/pages", headers=_headers())
        assert pages.json()["pages"][0]["title"] == "Initial Page"

        updated = client.patch(
            f"/api/v1/artifacts/{artifact['id']}",
            headers=_headers(),
            json={"title": "Pinned Title", "group_path": "design/final"},
        )
        assert updated.status_code == 200
        assert updated.json()["title"] == "Pinned Title"

        removed = client.delete(f"/api/v1/artifacts/{artifact['id']}", headers=_headers())
        assert removed.json() == {"ok": True, "removed": True}
        assert source.read_text(encoding="utf-8") == original
    finally:
        client.close()


def test_project_delete_requires_explicit_cascade_and_preserves_files(
    tmp_path: Path, monkeypatch
) -> None:
    page = tmp_path / "sample.html"
    page.write_text("<title>Sample</title>", encoding="utf-8")
    client = _client(tmp_path, monkeypatch)
    try:
        artifact = client.post(
            "/api/v1/artifacts",
            headers=_headers(),
            json={"source_path": str(page), "project": "Samples"},
        ).json()
        project_id = artifact["project_id"]

        blocked = client.delete(f"/api/v1/artifact-projects/{project_id}", headers=_headers())
        assert blocked.status_code == 409

        removed = client.delete(
            f"/api/v1/artifact-projects/{project_id}?cascade=true",
            headers=_headers(),
        )
        assert removed.json() == {
            "ok": True,
            "removed": True,
            "registrations_removed": 1,
        }
        assert page.exists()
    finally:
        client.close()


def test_artifact_endpoints_require_token(tmp_path: Path, monkeypatch) -> None:
    client = _client(tmp_path, monkeypatch)
    try:
        assert client.get("/api/v1/artifacts").status_code == 403
        assert client.get("/api/v1/artifact-projects").status_code == 403
    finally:
        client.close()


def test_mode_change_resets_incompatible_entrypoint(tmp_path: Path, monkeypatch) -> None:
    site = tmp_path / "sample-site"
    site.mkdir()
    (site / "index.html").write_text("<title>Home</title>", encoding="utf-8")
    (site / "report.html").write_text("<title>Report</title>", encoding="utf-8")
    client = _client(tmp_path, monkeypatch)
    try:
        artifact = client.post(
            "/api/v1/artifacts",
            headers=_headers(),
            json={"source_path": str(site), "project": "Samples", "mode": "site"},
        ).json()

        updated = client.patch(
            f"/api/v1/artifacts/{artifact['id']}",
            headers=_headers(),
            json={"mode": "collection"},
        )

        assert updated.status_code == 200
        assert updated.json()["mode"] == "collection"
        assert updated.json()["entrypoint"] is None
    finally:
        client.close()


def test_search_filters_and_stable_links(tmp_path: Path, monkeypatch) -> None:
    collection = tmp_path / "reports"
    collection.mkdir()
    (collection / "summary.html").write_text("<title>Quarterly Summary</title>", encoding="utf-8")
    client = _client(tmp_path, monkeypatch)
    try:
        created = client.post(
            "/api/v1/artifacts",
            headers=_headers(),
            json={
                "source_path": str(collection),
                "project": "Demo Tools",
                "group_path": "published/reports",
                "mode": "collection",
                "slug": "quarterly-reports",
                "mount_path": "published/demo-reports",
            },
        )
        assert created.status_code == 200
        artifact = created.json()
        assert artifact["project_slug"] == "demo-tools"
        assert artifact["alias_path"] == "/r/demo-tools/quarterly-reports/"
        assert artifact["mount_path"] == "/published/demo-reports"

        result = client.get(
            "/api/v1/artifacts",
            headers=_headers(),
            params={
                "q": "quarterly summary",
                "project": "demo-tools",
                "group": "published",
                "mode": "collection",
                "exists": "true",
            },
        )
        assert [item["id"] for item in result.json()["artifacts"]] == [artifact["id"]]

        pages = client.get(f"/api/v1/artifacts/{artifact['id']}/pages", headers=_headers())
        assert pages.json()["pages"][0]["serve_path"] == (
            "/r/demo-tools/quarterly-reports/summary.html"
        )
    finally:
        client.close()


def test_mount_and_alias_conflicts_return_409(tmp_path: Path, monkeypatch) -> None:
    first = tmp_path / "first.html"
    second = tmp_path / "second.html"
    third = tmp_path / "third.html"
    for page in (first, second, third):
        page.write_text("<title>Demo</title>", encoding="utf-8")
    client = _client(tmp_path, monkeypatch)
    try:
        base = {
            "project": "Demo Tools",
            "slug": "shared-name",
            "mount_path": "published/shared",
        }
        assert (
            client.post(
                "/api/v1/artifacts",
                headers=_headers(),
                json={"source_path": str(first), **base},
            ).status_code
            == 200
        )
        assert (
            client.post(
                "/api/v1/artifacts",
                headers=_headers(),
                json={
                    "source_path": str(second),
                    "project": "Demo Tools",
                    "slug": "shared-name",
                },
            ).status_code
            == 409
        )
        assert (
            client.post(
                "/api/v1/artifacts",
                headers=_headers(),
                json={
                    "source_path": str(third),
                    "project": "Other Tools",
                    "mount_path": "published/shared",
                },
            ).status_code
            == 409
        )
    finally:
        client.close()


def test_bootstrap_reports_sidecar_only_during_lifespan(tmp_path: Path, monkeypatch) -> None:
    config = tmp_path / "config"
    config.mkdir()
    (config / "boxes.yaml").write_text(
        yaml.safe_dump({"boxes": []}, sort_keys=False), encoding="utf-8"
    )
    monkeypatch.setenv("SSHLER_CONFIG_DIR", str(config))
    state.reset_state()
    settings = ServerSettings(csrf_token=TOKEN, serve_artifacts=True)

    with TestClient(make_app(settings)) as client:
        bootstrap = client.get("/api/v1/bootstrap", headers=_headers())
        assert bootstrap.status_code == 200
        payload = bootstrap.json()
        assert payload["artifact_server_enabled"] is True
        port = payload["artifact_server_port"]
        assert isinstance(port, int)
        assert port > 0
        assert (
            f"frame-src 'self' http://127.0.0.1:{port}"
            in bootstrap.headers["Content-Security-Policy"]
        )

    assert settings.artifact_server_port_actual is None
    probe = socket.socket()
    try:
        probe.settimeout(0.2)
        assert probe.connect_ex(("127.0.0.1", port)) != 0
    finally:
        probe.close()
