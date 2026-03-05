"""Tests for batch file operations API."""

import os
from pathlib import Path

import yaml
from fastapi.testclient import TestClient

from sshler.webapp import ServerSettings, make_app


TEST_TOKEN = "batch-test-token"


def build_client(config_dir: Path) -> TestClient:
    os.environ["SSHLER_CONFIG_DIR"] = str(config_dir)
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


class TestBatchDelete:
    def test_batch_delete_files(self, tmp_path):
        config_dir = setup_config(tmp_path)
        workdir = tmp_path / "work"
        workdir.mkdir()
        (workdir / "a.txt").write_text("a")
        (workdir / "b.txt").write_text("b")
        (workdir / "c.txt").write_text("c")

        client = build_client(config_dir)
        try:
            resp = client.post(
                "/api/v1/boxes/local/batch/delete",
                json={"paths": [str(workdir / "a.txt"), str(workdir / "b.txt")]},
                headers=auth_headers(),
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] == "ok"
            assert len(data["succeeded"]) == 2
            assert len(data["failed"]) == 0
            assert not (workdir / "a.txt").exists()
            assert not (workdir / "b.txt").exists()
            assert (workdir / "c.txt").exists()
        finally:
            client.close()

    def test_batch_delete_directory(self, tmp_path):
        config_dir = setup_config(tmp_path)
        workdir = tmp_path / "work"
        subdir = workdir / "subdir"
        subdir.mkdir(parents=True)
        (subdir / "file.txt").write_text("content")

        client = build_client(config_dir)
        try:
            resp = client.post(
                "/api/v1/boxes/local/batch/delete",
                json={"paths": [str(subdir)]},
                headers=auth_headers(),
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] == "ok"
            assert len(data["succeeded"]) == 1
            assert not subdir.exists()
        finally:
            client.close()

    def test_batch_delete_partial_failure(self, tmp_path):
        config_dir = setup_config(tmp_path)
        workdir = tmp_path / "work"
        workdir.mkdir()
        (workdir / "exists.txt").write_text("yes")

        client = build_client(config_dir)
        try:
            resp = client.post(
                "/api/v1/boxes/local/batch/delete",
                json={"paths": [str(workdir / "exists.txt"), str(workdir / "nonexistent.txt")]},
                headers=auth_headers(),
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] == "partial"
            assert len(data["succeeded"]) == 1
            assert data["succeeded"][0] == str(workdir / "exists.txt")
            assert len(data["failed"]) == 1
            assert data["failed"][0]["path"] == str(workdir / "nonexistent.txt")
        finally:
            client.close()

    def test_batch_delete_empty_paths(self, tmp_path):
        config_dir = setup_config(tmp_path)
        client = build_client(config_dir)
        try:
            resp = client.post(
                "/api/v1/boxes/local/batch/delete",
                json={"paths": []},
                headers=auth_headers(),
            )
            assert resp.status_code == 400
        finally:
            client.close()

    def test_batch_delete_too_many_paths(self, tmp_path):
        config_dir = setup_config(tmp_path)
        client = build_client(config_dir)
        try:
            paths = [f"/tmp/fake_{i}" for i in range(101)]
            resp = client.post(
                "/api/v1/boxes/local/batch/delete",
                json={"paths": paths},
                headers=auth_headers(),
            )
            assert resp.status_code == 400
            assert "max" in resp.json()["detail"].lower()
        finally:
            client.close()


class TestBatchMove:
    def test_batch_move_files(self, tmp_path):
        config_dir = setup_config(tmp_path)
        workdir = tmp_path / "work"
        workdir.mkdir()
        (workdir / "a.txt").write_text("a")
        (workdir / "b.txt").write_text("b")
        dest = tmp_path / "dest"
        dest.mkdir()

        client = build_client(config_dir)
        try:
            resp = client.post(
                "/api/v1/boxes/local/batch/move",
                json={
                    "paths": [str(workdir / "a.txt"), str(workdir / "b.txt")],
                    "destination": str(dest),
                },
                headers=auth_headers(),
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] == "ok"
            assert len(data["succeeded"]) == 2
            assert (dest / "a.txt").exists()
            assert (dest / "b.txt").exists()
            assert not (workdir / "a.txt").exists()
            assert not (workdir / "b.txt").exists()
        finally:
            client.close()

    def test_batch_move_partial_failure(self, tmp_path):
        config_dir = setup_config(tmp_path)
        workdir = tmp_path / "work"
        workdir.mkdir()
        (workdir / "real.txt").write_text("data")
        dest = tmp_path / "dest"
        dest.mkdir()

        client = build_client(config_dir)
        try:
            resp = client.post(
                "/api/v1/boxes/local/batch/move",
                json={
                    "paths": [str(workdir / "real.txt"), str(workdir / "ghost.txt")],
                    "destination": str(dest),
                },
                headers=auth_headers(),
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] == "partial"
            assert len(data["succeeded"]) == 1
            assert len(data["failed"]) == 1
        finally:
            client.close()


class TestBatchCopy:
    def test_batch_copy_files(self, tmp_path):
        config_dir = setup_config(tmp_path)
        workdir = tmp_path / "work"
        workdir.mkdir()
        (workdir / "a.txt").write_text("alpha")
        (workdir / "b.txt").write_text("beta")
        dest = tmp_path / "dest"
        dest.mkdir()

        client = build_client(config_dir)
        try:
            resp = client.post(
                "/api/v1/boxes/local/batch/copy",
                json={
                    "paths": [str(workdir / "a.txt"), str(workdir / "b.txt")],
                    "destination": str(dest),
                },
                headers=auth_headers(),
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] == "ok"
            assert len(data["succeeded"]) == 2
            # Originals still exist
            assert (workdir / "a.txt").exists()
            assert (workdir / "b.txt").exists()
            # Copies exist
            assert (dest / "a.txt").read_text() == "alpha"
            assert (dest / "b.txt").read_text() == "beta"
        finally:
            client.close()

    def test_batch_copy_directory(self, tmp_path):
        config_dir = setup_config(tmp_path)
        workdir = tmp_path / "work"
        subdir = workdir / "mydir"
        subdir.mkdir(parents=True)
        (subdir / "inner.txt").write_text("inside")
        dest = tmp_path / "dest"
        dest.mkdir()

        client = build_client(config_dir)
        try:
            resp = client.post(
                "/api/v1/boxes/local/batch/copy",
                json={
                    "paths": [str(subdir)],
                    "destination": str(dest),
                },
                headers=auth_headers(),
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] == "ok"
            assert (dest / "mydir" / "inner.txt").read_text() == "inside"
            # Original still exists
            assert (subdir / "inner.txt").exists()
        finally:
            client.close()
