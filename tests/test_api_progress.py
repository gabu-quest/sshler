"""REST tests for the global progress-bar API."""

from __future__ import annotations

import os
from pathlib import Path

import yaml
from fastapi.testclient import TestClient

from sshler import state
from sshler.webapp import ServerSettings, make_app


TEST_TOKEN = "api-token"


def setup_config(tmp_path: Path) -> Path:
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "boxes.yaml").write_text(
        yaml.safe_dump({"boxes": []}, sort_keys=False), encoding="utf-8"
    )
    return config_dir


def build_client(config_dir: Path) -> TestClient:
    os.environ["SSHLER_CONFIG_DIR"] = str(config_dir)
    state.reset_state()
    state.initialize(config_dir)
    return TestClient(make_app(ServerSettings(csrf_token=TEST_TOKEN)))


def auth_headers() -> dict[str, str]:
    return {"X-SSHLER-TOKEN": TEST_TOKEN}


def test_push_creates_bar(tmp_path):
    client = build_client(setup_config(tmp_path))
    try:
        resp = client.post(
            "/api/v1/progress/build-ci",
            headers=auth_headers(),
            json={"current": 30, "total": 100, "color": "blue", "label": "build"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "build-ci"
        assert data["current"] == 30
        assert data["total"] == 100
        assert data["color"] == "blue"
        assert data["label"] == "build"
        assert data["status"] == "running"
        assert data["created_at"] == data["updated_at"]
    finally:
        client.close()


def test_push_is_idempotent_upsert(tmp_path):
    client = build_client(setup_config(tmp_path))
    try:
        first = client.post(
            "/api/v1/progress/job",
            headers=auth_headers(),
            json={"current": 10, "total": 100},
        )
        assert first.status_code == 200
        first_data = first.json()

        second = client.post(
            "/api/v1/progress/job",
            headers=auth_headers(),
            json={"current": 50, "total": 100, "status": "running"},
        )
        assert second.status_code == 200
        second_data = second.json()
        assert second_data["name"] == "job"
        assert second_data["current"] == 50
        assert second_data["created_at"] == first_data["created_at"]
        assert second_data["updated_at"] >= first_data["updated_at"]

        # Exactly one row exists for this name.
        listing = client.get("/api/v1/progress", headers=auth_headers()).json()
        matching = [b for b in listing["bars"] if b["name"] == "job"]
        assert len(matching) == 1
        assert matching[0]["current"] == 50
    finally:
        client.close()


def test_list_returns_all_bars_newest_first(tmp_path):
    client = build_client(setup_config(tmp_path))
    try:
        client.post(
            "/api/v1/progress/first",
            headers=auth_headers(),
            json={"current": 1, "total": 10},
        )
        client.post(
            "/api/v1/progress/second",
            headers=auth_headers(),
            json={"current": 5, "total": 10},
        )
        resp = client.get("/api/v1/progress", headers=auth_headers())
        assert resp.status_code == 200
        bars = resp.json()["bars"]
        assert len(bars) == 2
        # Most recently updated first.
        assert bars[0]["name"] == "second"
        assert bars[1]["name"] == "first"
    finally:
        client.close()


def test_get_named_bar(tmp_path):
    client = build_client(setup_config(tmp_path))
    try:
        client.post(
            "/api/v1/progress/single",
            headers=auth_headers(),
            json={"current": 7, "total": 8, "label": "task"},
        )
        resp = client.get("/api/v1/progress/single", headers=auth_headers())
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "single"
        assert data["current"] == 7
        assert data["total"] == 8
        assert data["label"] == "task"
    finally:
        client.close()


def test_get_missing_returns_404(tmp_path):
    client = build_client(setup_config(tmp_path))
    try:
        resp = client.get("/api/v1/progress/nope", headers=auth_headers())
        assert resp.status_code == 404
    finally:
        client.close()


def test_delete_removes_bar(tmp_path):
    client = build_client(setup_config(tmp_path))
    try:
        client.post(
            "/api/v1/progress/temp",
            headers=auth_headers(),
            json={"current": 1, "total": 2},
        )
        resp = client.delete("/api/v1/progress/temp", headers=auth_headers())
        assert resp.status_code == 200
        assert resp.json() == {"ok": True, "removed": True}

        listing = client.get("/api/v1/progress", headers=auth_headers()).json()
        assert all(b["name"] != "temp" for b in listing["bars"])
    finally:
        client.close()


def test_delete_missing_is_idempotent(tmp_path):
    client = build_client(setup_config(tmp_path))
    try:
        resp = client.delete("/api/v1/progress/never-existed", headers=auth_headers())
        assert resp.status_code == 200
        assert resp.json() == {"ok": True, "removed": False}
    finally:
        client.close()


def test_auth_required(tmp_path):
    client = build_client(setup_config(tmp_path))
    try:
        # POST without token → 403
        resp = client.post(
            "/api/v1/progress/x", json={"current": 1, "total": 10}
        )
        assert resp.status_code == 403

        # GET list without token → 403
        resp = client.get("/api/v1/progress")
        assert resp.status_code == 403
    finally:
        client.close()


def test_invalid_name_rejected(tmp_path):
    client = build_client(setup_config(tmp_path))
    try:
        resp = client.post(
            "/api/v1/progress/has spaces",
            headers=auth_headers(),
            json={"current": 1, "total": 10},
        )
        assert resp.status_code == 400

        resp = client.post(
            "/api/v1/progress/" + ("a" * 65),
            headers=auth_headers(),
            json={"current": 1, "total": 10},
        )
        assert resp.status_code == 400
    finally:
        client.close()


def test_invalid_status_rejected(tmp_path):
    client = build_client(setup_config(tmp_path))
    try:
        resp = client.post(
            "/api/v1/progress/bar",
            headers=auth_headers(),
            json={"current": 1, "total": 10, "status": "garbage"},
        )
        assert resp.status_code == 400
    finally:
        client.close()


def test_negative_or_zero_total_rejected(tmp_path):
    client = build_client(setup_config(tmp_path))
    try:
        resp = client.post(
            "/api/v1/progress/bar",
            headers=auth_headers(),
            json={"current": 0, "total": 0},
        )
        assert resp.status_code == 422

        resp = client.post(
            "/api/v1/progress/bar",
            headers=auth_headers(),
            json={"current": -1, "total": 10},
        )
        assert resp.status_code == 422
    finally:
        client.close()


def test_status_done_persisted(tmp_path):
    client = build_client(setup_config(tmp_path))
    try:
        resp = client.post(
            "/api/v1/progress/finishing",
            headers=auth_headers(),
            json={"current": 100, "total": 100, "status": "done"},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "done"
        # Round-trip through GET.
        fetched = client.get("/api/v1/progress/finishing", headers=auth_headers()).json()
        assert fetched["status"] == "done"
    finally:
        client.close()


def test_valid_metadata_round_trips(tmp_path):
    client = build_client(setup_config(tmp_path))
    try:
        resp = client.post(
            "/api/v1/progress/m",
            headers=auth_headers(),
            json={"current": 1, "total": 2, "metadata": {"stage": "compile", "warnings": 3}},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["metadata"] == {"stage": "compile", "warnings": 3}
        assert data["metadata_error"] is None
        fetched = client.get("/api/v1/progress/m", headers=auth_headers()).json()
        assert fetched["metadata"] == {"stage": "compile", "warnings": 3}
    finally:
        client.close()


def test_metadata_replaces_by_default(tmp_path):
    client = build_client(setup_config(tmp_path))
    try:
        client.post(
            "/api/v1/progress/m",
            headers=auth_headers(),
            json={"current": 1, "total": 10, "metadata": {"a": 1}},
        )
        resp = client.post(
            "/api/v1/progress/m",
            headers=auth_headers(),
            json={"current": 2, "total": 10, "metadata": {"b": 2}},
        )
        assert resp.status_code == 200
        assert resp.json()["metadata"] == {"b": 2}  # replaced, not merged
    finally:
        client.close()


def test_metadata_merge_flag_accumulates(tmp_path):
    client = build_client(setup_config(tmp_path))
    try:
        client.post(
            "/api/v1/progress/m",
            headers=auth_headers(),
            json={"current": 1, "total": 10, "metadata": {"a": 1}},
        )
        resp = client.post(
            "/api/v1/progress/m",
            headers=auth_headers(),
            json={"current": 2, "total": 10, "metadata": {"b": 2}, "merge": True},
        )
        assert resp.status_code == 200
        assert resp.json()["metadata"] == {"a": 1, "b": 2}
    finally:
        client.close()


def test_omitted_metadata_field_leaves_bag_intact(tmp_path):
    client = build_client(setup_config(tmp_path))
    try:
        client.post(
            "/api/v1/progress/m",
            headers=auth_headers(),
            json={"current": 1, "total": 3300, "metadata": {"stage": "link"}},
        )
        # A plain progress tick (no metadata field) must NOT wipe metadata.
        resp = client.post(
            "/api/v1/progress/m",
            headers=auth_headers(),
            json={"current": 3299, "total": 3300},
        )
        assert resp.status_code == 200
        assert resp.json()["metadata"] == {"stage": "link"}
    finally:
        client.close()


def test_empty_metadata_clears_bag(tmp_path):
    client = build_client(setup_config(tmp_path))
    try:
        client.post(
            "/api/v1/progress/m",
            headers=auth_headers(),
            json={"current": 1, "total": 10, "metadata": {"stage": "link"}},
        )
        resp = client.post(
            "/api/v1/progress/m",
            headers=auth_headers(),
            json={"current": 2, "total": 10, "metadata": {}},
        )
        assert resp.status_code == 200
        assert resp.json()["metadata"] == {}
    finally:
        client.close()


def test_malformed_metadata_keeps_last_good_and_advances(tmp_path):
    client = build_client(setup_config(tmp_path))
    try:
        client.post(
            "/api/v1/progress/m",
            headers=auth_headers(),
            json={"current": 1, "total": 10, "metadata": {"stage": "link"}},
        )
        # metadata is a list, not an object → 200, error recorded, bag retained,
        # progress STILL advances.
        resp = client.post(
            "/api/v1/progress/m",
            headers=auth_headers(),
            json={"current": 5, "total": 10, "metadata": [1, 2, 3]},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["current"] == 5  # progress applied
        assert data["metadata"] == {"stage": "link"}  # last-good kept
        assert data["metadata_error"] == "metadata must be a JSON object"
    finally:
        client.close()


def test_oversized_metadata_flags_error(tmp_path):
    client = build_client(setup_config(tmp_path))
    try:
        big = {"k": "x" * 5000}
        resp = client.post(
            "/api/v1/progress/m",
            headers=auth_headers(),
            json={"current": 1, "total": 10, "metadata": big},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["metadata"] == {}  # nothing stored
        assert data["metadata_error"] == "metadata too large (>4096 bytes)"
    finally:
        client.close()


def test_good_metadata_push_clears_prior_error(tmp_path):
    client = build_client(setup_config(tmp_path))
    try:
        client.post(
            "/api/v1/progress/m",
            headers=auth_headers(),
            json={"current": 1, "total": 10, "metadata": "bad"},
        )
        resp = client.post(
            "/api/v1/progress/m",
            headers=auth_headers(),
            json={"current": 2, "total": 10, "metadata": {"ok": True}},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["metadata"] == {"ok": True}
        assert data["metadata_error"] is None
    finally:
        client.close()
