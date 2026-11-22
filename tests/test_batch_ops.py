"""Tests for batch file operations API."""

import asyncio
import os
import sys
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from sshler.webapp import ServerSettings, make_app

TEST_TOKEN = "batch-test-token"


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


class TestBatchDelete:
    def test_batch_delete_files(self, tmp_path, monkeypatch):
        config_dir = setup_config(tmp_path)
        workdir = tmp_path / "work"
        workdir.mkdir()
        (workdir / "a.txt").write_text("a")
        (workdir / "b.txt").write_text("b")
        (workdir / "c.txt").write_text("c")

        client = build_client(config_dir, monkeypatch)
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

    def test_batch_delete_directory(self, tmp_path, monkeypatch):
        config_dir = setup_config(tmp_path)
        workdir = tmp_path / "work"
        subdir = workdir / "subdir"
        subdir.mkdir(parents=True)
        (subdir / "file.txt").write_text("content")

        client = build_client(config_dir, monkeypatch)
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

    def test_batch_delete_partial_failure(self, tmp_path, monkeypatch):
        config_dir = setup_config(tmp_path)
        workdir = tmp_path / "work"
        workdir.mkdir()
        (workdir / "exists.txt").write_text("yes")

        client = build_client(config_dir, monkeypatch)
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

    def test_batch_delete_empty_paths(self, tmp_path, monkeypatch):
        config_dir = setup_config(tmp_path)
        client = build_client(config_dir, monkeypatch)
        try:
            resp = client.post(
                "/api/v1/boxes/local/batch/delete",
                json={"paths": []},
                headers=auth_headers(),
            )
            assert resp.status_code == 400
        finally:
            client.close()

    def test_batch_delete_too_many_paths(self, tmp_path, monkeypatch):
        config_dir = setup_config(tmp_path)
        client = build_client(config_dir, monkeypatch)
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
    def test_batch_move_files(self, tmp_path, monkeypatch):
        config_dir = setup_config(tmp_path)
        workdir = tmp_path / "work"
        workdir.mkdir()
        (workdir / "a.txt").write_text("a")
        (workdir / "b.txt").write_text("b")
        dest = tmp_path / "dest"
        dest.mkdir()

        client = build_client(config_dir, monkeypatch)
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

    def test_batch_move_partial_failure(self, tmp_path, monkeypatch):
        config_dir = setup_config(tmp_path)
        workdir = tmp_path / "work"
        workdir.mkdir()
        (workdir / "real.txt").write_text("data")
        dest = tmp_path / "dest"
        dest.mkdir()

        client = build_client(config_dir, monkeypatch)
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
    @pytest.mark.parametrize("overwrite", [False, True], ids=["free-name", "replace"])
    def test_batch_copy_works_where_os_has_no_fchmod(self, tmp_path, monkeypatch, overwrite):
        """Windows before Python 3.13 has no ``os.fchmod``."""
        config_dir = setup_config(tmp_path)
        (tmp_path / "a.txt").write_text("new")
        dest = tmp_path / "dest"
        dest.mkdir()
        if overwrite:
            (dest / "a.txt").write_text("old")
        monkeypatch.delattr(os, "fchmod", raising=False)

        client = build_client(config_dir, monkeypatch)
        try:
            resp = client.post(
                "/api/v1/boxes/local/batch/copy",
                json={
                    "paths": [str(tmp_path / "a.txt")],
                    "destination": str(dest),
                    "overwrite": overwrite,
                },
                headers=auth_headers(),
            )
        finally:
            client.close()
        assert (resp.status_code, resp.json()["status"]) == (200, "ok")
        assert (dest / "a.txt").read_text() == "new"
        assert [p.name for p in dest.iterdir()] == ["a.txt"]

    def test_batch_copy_files(self, tmp_path, monkeypatch):
        config_dir = setup_config(tmp_path)
        workdir = tmp_path / "work"
        workdir.mkdir()
        (workdir / "a.txt").write_text("alpha")
        (workdir / "b.txt").write_text("beta")
        dest = tmp_path / "dest"
        dest.mkdir()

        client = build_client(config_dir, monkeypatch)
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

    def test_batch_copy_directory(self, tmp_path, monkeypatch):
        config_dir = setup_config(tmp_path)
        workdir = tmp_path / "work"
        subdir = workdir / "mydir"
        subdir.mkdir(parents=True)
        (subdir / "inner.txt").write_text("inside")
        dest = tmp_path / "dest"
        dest.mkdir()

        client = build_client(config_dir, monkeypatch)
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


def _on_loop() -> bool:
    """True when called on a thread that runs an event loop."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return False
    return True


class TestLocalWorkLeavesTheEventLoop:
    """Local delete, move, copy and listing run in a worker thread, not the loop thread."""

    @pytest.mark.parametrize("op", ["delete", "move", "copy"])
    def test_batch_op_filesystem_call_runs_off_the_loop_thread(self, tmp_path, monkeypatch, op):
        import shutil

        config_dir = setup_config(tmp_path)
        workdir = tmp_path / "work"
        workdir.mkdir()
        (workdir / "a.txt").write_text("a")
        (workdir / "d").mkdir()
        (workdir / "d" / "f.txt").write_text("f")
        seen: list[bool] = []
        # The call that does each operation's file work: a move takes the new name
        # with a hard link (a rename on Windows), a copy streams the bytes.
        take_name = "rename" if sys.platform == "win32" else "link"
        real = {
            "rmtree": shutil.rmtree,
            take_name: getattr(os, take_name),
            "copyfileobj": shutil.copyfileobj,
        }

        def spy(name):
            def wrapper(*args, **kwargs):
                seen.append(_on_loop())
                return real[name](*args, **kwargs)

            return wrapper

        monkeypatch.setattr(shutil, "rmtree", spy("rmtree"))
        monkeypatch.setattr(os, take_name, spy(take_name))
        monkeypatch.setattr(shutil, "copyfileobj", spy("copyfileobj"))
        client = build_client(config_dir, monkeypatch)
        try:
            body: dict = {"paths": [str(workdir / ("d" if op == "delete" else "a.txt"))]}
            if op != "delete":
                body["destination"] = str(tmp_path / "dest")
            resp = client.post(f"/api/v1/boxes/local/batch/{op}", json=body, headers=auth_headers())
            assert resp.status_code == 200
            assert resp.json()["succeeded"] == body["paths"]
            assert len(seen) == 1
            assert seen == [False]
        finally:
            client.close()

    def test_batch_delete_of_a_vanished_path_is_reported_failed(self, tmp_path, monkeypatch):
        config_dir = setup_config(tmp_path)
        gone = str(tmp_path / "gone.txt")
        client = build_client(config_dir, monkeypatch)
        try:
            resp = client.post(
                "/api/v1/boxes/local/batch/delete", json={"paths": [gone]}, headers=auth_headers()
            )
            data = resp.json()
            assert data["status"] == "partial"
            assert data["succeeded"] == []
            assert [f["path"] for f in data["failed"]] == [gone]
        finally:
            client.close()

    def test_listing_keeps_order_and_a_broken_symlink_and_runs_off_the_loop(
        self, tmp_path, monkeypatch
    ):
        config_dir = setup_config(tmp_path)
        workdir = tmp_path / "work"
        workdir.mkdir()
        (workdir / "b.txt").write_text("bb")
        (workdir / "A").mkdir()
        (workdir / "dangling").symlink_to(workdir / "nowhere")
        threads: list[bool] = []
        real_iterdir = Path.iterdir

        def spy_iterdir(self):
            threads.append(_on_loop())
            return real_iterdir(self)

        monkeypatch.setattr(Path, "iterdir", spy_iterdir)
        client = build_client(config_dir, monkeypatch)
        try:
            resp = client.get(
                "/api/v1/boxes/local/ls", params={"directory": str(workdir)}, headers=auth_headers()
            )
            assert resp.status_code == 200
            entries = resp.json()["entries"]
            assert [(e["name"], e["is_directory"], e["size"]) for e in entries] == [
                ("A", True, None),
                ("b.txt", False, 2),
            ]
            assert len(threads) == 1
            assert threads == [False]
        finally:
            client.close()
