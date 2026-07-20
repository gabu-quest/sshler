"""REST tests for the Diff Notebook server-side persistence API."""

from __future__ import annotations

import os
import re
from pathlib import Path

import yaml
from fastapi.testclient import TestClient

from sshler import state
from sshler.webapp import ServerSettings, make_app


TEST_TOKEN = "diff-api-token"

_ID_RE = re.compile(r"^[A-Za-z0-9_-]{8,32}$")


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


def sample_envelope(label_suffix: str = "") -> dict:
    return {
        "v": 1,
        "cells": [
            {
                "l": {"box": "local", "directory": "/r", "ref": "main", "path": f"a{label_suffix}.ts"},
                "r": {"box": "local", "directory": "/r", "ref": "feat", "path": f"a{label_suffix}.ts"},
            }
        ],
    }


def test_create_returns_id_label_and_round_trippable_envelope(tmp_path):
    client = build_client(setup_config(tmp_path))
    try:
        env = sample_envelope()
        resp = client.post(
            "/api/v1/diff/notebooks",
            headers=auth_headers(),
            json={"envelope": env, "label": "ssh refactor"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert _ID_RE.match(data["id"])
        assert data["label"] == "ssh refactor"
        assert data["cell_count"] == 1
        assert data["envelope"] == env
        assert data["created_at"] == data["updated_at"]
    finally:
        client.close()


def test_create_then_get_round_trips(tmp_path):
    client = build_client(setup_config(tmp_path))
    try:
        env = sample_envelope("x")
        created = client.post(
            "/api/v1/diff/notebooks",
            headers=auth_headers(),
            json={"envelope": env, "label": "round-trip"},
        ).json()
        got = client.get(
            f"/api/v1/diff/notebooks/{created['id']}",
            headers=auth_headers(),
        )
        assert got.status_code == 200
        body = got.json()
        assert body["id"] == created["id"]
        assert body["envelope"] == env
        assert body["label"] == "round-trip"
    finally:
        client.close()


def test_list_returns_meta_only_no_envelope(tmp_path):
    client = build_client(setup_config(tmp_path))
    try:
        for i in range(3):
            client.post(
                "/api/v1/diff/notebooks",
                headers=auth_headers(),
                json={"envelope": sample_envelope(str(i)), "label": f"nb-{i}"},
            )
        resp = client.get("/api/v1/diff/notebooks", headers=auth_headers())
        assert resp.status_code == 200
        body = resp.json()
        assert len(body["notebooks"]) == 3
        labels = sorted(nb["label"] for nb in body["notebooks"])
        assert labels == ["nb-0", "nb-1", "nb-2"]
        for nb in body["notebooks"]:
            # Envelope must NOT be present on the list endpoint — it's the whole
            # point of having a separate "meta" shape.
            assert "envelope" not in nb
            assert nb["cell_count"] == 1
    finally:
        client.close()


def test_list_is_newest_first(tmp_path):
    client = build_client(setup_config(tmp_path))
    try:
        # Insert with deliberate small delays so created_at differs.
        import time as _t
        ids: list[str] = []
        for i in range(3):
            r = client.post(
                "/api/v1/diff/notebooks",
                headers=auth_headers(),
                json={"envelope": sample_envelope(str(i)), "label": f"order-{i}"},
            )
            ids.append(r.json()["id"])
            _t.sleep(0.01)
        listed = client.get("/api/v1/diff/notebooks", headers=auth_headers()).json()
        assert [nb["id"] for nb in listed["notebooks"]] == list(reversed(ids))
    finally:
        client.close()


def test_delete_first_call_removed_true_second_call_false(tmp_path):
    client = build_client(setup_config(tmp_path))
    try:
        created = client.post(
            "/api/v1/diff/notebooks",
            headers=auth_headers(),
            json={"envelope": sample_envelope(), "label": "tmp"},
        ).json()
        first = client.delete(
            f"/api/v1/diff/notebooks/{created['id']}",
            headers=auth_headers(),
        )
        assert first.status_code == 200
        assert first.json() == {"ok": True, "removed": True}
        second = client.delete(
            f"/api/v1/diff/notebooks/{created['id']}",
            headers=auth_headers(),
        )
        assert second.status_code == 200
        assert second.json() == {"ok": True, "removed": False}
    finally:
        client.close()


def test_get_unknown_id_returns_404(tmp_path):
    client = build_client(setup_config(tmp_path))
    try:
        resp = client.get(
            "/api/v1/diff/notebooks/abcd1234",
            headers=auth_headers(),
        )
        assert resp.status_code == 404
    finally:
        client.close()


def test_get_malformed_id_returns_404_not_500(tmp_path):
    """Path-traversal-shaped ids must be refused at the validation layer."""
    client = build_client(setup_config(tmp_path))
    try:
        # FastAPI's Path(min_length=8) catches the short input first → 422; the
        # regex catches longer-but-invalid shapes → 404. Both are acceptable
        # "definitely not a real id" responses. Assert neither is 500.
        for bad_id in ["..!!!!!!", "../etc/passwd", "x" * 33]:
            r = client.get(
                f"/api/v1/diff/notebooks/{bad_id}",
                headers=auth_headers(),
            )
            assert r.status_code in (404, 422), f"unexpected status for {bad_id}: {r.status_code}"
    finally:
        client.close()


def test_create_with_v2_envelope_is_422(tmp_path):
    """Pydantic rejects envelope versions the server doesn't understand."""
    client = build_client(setup_config(tmp_path))
    try:
        bad_envelope = {"v": 2, "cells": []}
        resp = client.post(
            "/api/v1/diff/notebooks",
            headers=auth_headers(),
            json={"envelope": bad_envelope},
        )
        assert resp.status_code == 422
    finally:
        client.close()


def test_create_with_missing_envelope_is_422(tmp_path):
    client = build_client(setup_config(tmp_path))
    try:
        resp = client.post(
            "/api/v1/diff/notebooks",
            headers=auth_headers(),
            json={"label": "no envelope"},
        )
        assert resp.status_code == 422
    finally:
        client.close()


def test_no_token_returns_4xx(tmp_path):
    """Token gate covers diff notebook routes like every other API route."""
    client = build_client(setup_config(tmp_path))
    try:
        # The exact status depends on the auth layer (403 by default for
        # missing/invalid token in this project). Just assert "not 2xx".
        resp = client.get("/api/v1/diff/notebooks")
        assert resp.status_code in (401, 403)
        resp2 = client.post(
            "/api/v1/diff/notebooks",
            json={"envelope": sample_envelope()},
        )
        assert resp2.status_code in (401, 403)
    finally:
        client.close()


def test_create_with_default_repo_preserved(tmp_path):
    """The optional `def` field round-trips correctly through the `def_` alias."""
    client = build_client(setup_config(tmp_path))
    try:
        env = {
            "v": 1,
            "cells": [{"l": {"box": "local", "directory": "/r", "ref": "main", "path": "a.ts"},
                        "r": {"box": "local", "directory": "/r", "ref": "feat", "path": "a.ts"}}],
            "def": {"box": "local", "directory": "/r"},
        }
        created = client.post(
            "/api/v1/diff/notebooks",
            headers=auth_headers(),
            json={"envelope": env, "label": "with default"},
        ).json()
        got = client.get(
            f"/api/v1/diff/notebooks/{created['id']}",
            headers=auth_headers(),
        ).json()
        assert got["envelope"]["def"] == {"box": "local", "directory": "/r"}
    finally:
        client.close()


def test_oversized_envelope_returns_413(tmp_path):
    """1 MB cap protects the DB from a runaway payload."""
    client = build_client(setup_config(tmp_path))
    try:
        # Build an envelope that JSON-serializes past 1 MB. Each cell is ~150 bytes;
        # need ~7000 cells. We pack the path with junk to inflate faster.
        huge_path = "x" * 1000
        big_env = {
            "v": 1,
            "cells": [
                {
                    "l": {"box": "local", "directory": "/r", "ref": "main", "path": huge_path},
                    "r": {"box": "local", "directory": "/r", "ref": "feat", "path": huge_path},
                }
                for _ in range(600)
            ],
        }
        resp = client.post(
            "/api/v1/diff/notebooks",
            headers=auth_headers(),
            json={"envelope": big_env},
        )
        assert resp.status_code == 413
    finally:
        client.close()
