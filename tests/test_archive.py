"""Tests for archive creation and extraction API."""

import os
import tarfile
import zipfile
from pathlib import Path

import yaml
from fastapi.testclient import TestClient

from sshler.webapp import ServerSettings, make_app


TEST_TOKEN = "archive-test-token"


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


class TestArchiveCreate:
    def test_create_tar_gz(self, tmp_path):
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
                "/api/v1/boxes/local/archive/create",
                json={
                    "paths": [str(workdir / "a.txt"), str(workdir / "b.txt")],
                    "destination": str(dest),
                    "archive_name": "test.tar.gz",
                    "format": "tar.gz",
                },
                headers=auth_headers(),
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] == "ok"

            archive_path = Path(data["path"])
            assert archive_path.exists()

            # Verify archive contents
            with tarfile.open(str(archive_path), "r:gz") as tf:
                names = tf.getnames()
                assert "a.txt" in names
                assert "b.txt" in names
        finally:
            client.close()

    def test_create_zip(self, tmp_path):
        config_dir = setup_config(tmp_path)
        workdir = tmp_path / "work"
        workdir.mkdir()
        (workdir / "file.txt").write_text("content")
        dest = tmp_path / "dest"
        dest.mkdir()

        client = build_client(config_dir)
        try:
            resp = client.post(
                "/api/v1/boxes/local/archive/create",
                json={
                    "paths": [str(workdir / "file.txt")],
                    "destination": str(dest),
                    "archive_name": "test.zip",
                    "format": "zip",
                },
                headers=auth_headers(),
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] == "ok"

            archive_path = Path(data["path"])
            assert archive_path.exists()

            with zipfile.ZipFile(str(archive_path), "r") as zf:
                assert "file.txt" in zf.namelist()
        finally:
            client.close()

    def test_create_archive_with_directory(self, tmp_path):
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
                "/api/v1/boxes/local/archive/create",
                json={
                    "paths": [str(subdir)],
                    "destination": str(dest),
                    "archive_name": "dirs.tar.gz",
                    "format": "tar.gz",
                },
                headers=auth_headers(),
            )
            assert resp.status_code == 200
            archive_path = Path(resp.json()["path"])
            with tarfile.open(str(archive_path), "r:gz") as tf:
                names = tf.getnames()
                assert any("mydir" in n for n in names)
        finally:
            client.close()

    def test_create_invalid_format(self, tmp_path):
        config_dir = setup_config(tmp_path)
        client = build_client(config_dir)
        try:
            resp = client.post(
                "/api/v1/boxes/local/archive/create",
                json={
                    "paths": ["/tmp/fake"],
                    "destination": "/tmp",
                    "archive_name": "test.rar",
                    "format": "rar",
                },
                headers=auth_headers(),
            )
            assert resp.status_code == 400
        finally:
            client.close()

    def test_create_wrong_extension(self, tmp_path):
        config_dir = setup_config(tmp_path)
        client = build_client(config_dir)
        try:
            resp = client.post(
                "/api/v1/boxes/local/archive/create",
                json={
                    "paths": ["/tmp/fake"],
                    "destination": "/tmp",
                    "archive_name": "test.zip",
                    "format": "tar.gz",
                },
                headers=auth_headers(),
            )
            assert resp.status_code == 400
            assert "must end in" in resp.json()["detail"]
        finally:
            client.close()

    def test_create_too_many_paths(self, tmp_path):
        config_dir = setup_config(tmp_path)
        client = build_client(config_dir)
        try:
            resp = client.post(
                "/api/v1/boxes/local/archive/create",
                json={
                    "paths": [f"/tmp/f{i}" for i in range(101)],
                    "destination": "/tmp",
                    "archive_name": "big.tar.gz",
                    "format": "tar.gz",
                },
                headers=auth_headers(),
            )
            assert resp.status_code == 400
        finally:
            client.close()


class TestArchiveExtract:
    def test_extract_tar_gz(self, tmp_path):
        config_dir = setup_config(tmp_path)
        # Create a tar.gz to extract
        archive_path = tmp_path / "test.tar.gz"
        with tarfile.open(str(archive_path), "w:gz") as tf:
            info = tarfile.TarInfo(name="extracted.txt")
            content = b"extracted content"
            info.size = len(content)
            import io
            tf.addfile(info, io.BytesIO(content))

        dest = tmp_path / "dest"
        dest.mkdir()

        client = build_client(config_dir)
        try:
            resp = client.post(
                "/api/v1/boxes/local/archive/extract",
                json={
                    "archive_path": str(archive_path),
                    "destination": str(dest),
                },
                headers=auth_headers(),
            )
            assert resp.status_code == 200
            assert (dest / "extracted.txt").exists()
            assert (dest / "extracted.txt").read_text() == "extracted content"
        finally:
            client.close()

    def test_extract_zip(self, tmp_path):
        config_dir = setup_config(tmp_path)
        archive_path = tmp_path / "test.zip"
        with zipfile.ZipFile(str(archive_path), "w") as zf:
            zf.writestr("zipped.txt", "zip content")

        dest = tmp_path / "dest"
        dest.mkdir()

        client = build_client(config_dir)
        try:
            resp = client.post(
                "/api/v1/boxes/local/archive/extract",
                json={
                    "archive_path": str(archive_path),
                    "destination": str(dest),
                },
                headers=auth_headers(),
            )
            assert resp.status_code == 200
            assert (dest / "zipped.txt").read_text() == "zip content"
        finally:
            client.close()

    def test_extract_zip_traversal_prevention(self, tmp_path):
        """Verify that zip files with path traversal entries are rejected."""
        config_dir = setup_config(tmp_path)
        archive_path = tmp_path / "evil.zip"

        # Create a zip with a traversal path
        with zipfile.ZipFile(str(archive_path), "w") as zf:
            zf.writestr("../../../etc/evil.txt", "pwned")

        dest = tmp_path / "dest"
        dest.mkdir()

        client = build_client(config_dir)
        try:
            resp = client.post(
                "/api/v1/boxes/local/archive/extract",
                json={
                    "archive_path": str(archive_path),
                    "destination": str(dest),
                },
                headers=auth_headers(),
            )
            assert resp.status_code == 400
            assert "unsafe" in resp.json()["detail"].lower()
        finally:
            client.close()

    def test_extract_not_found(self, tmp_path):
        config_dir = setup_config(tmp_path)
        dest = tmp_path / "dest"
        dest.mkdir()

        client = build_client(config_dir)
        try:
            resp = client.post(
                "/api/v1/boxes/local/archive/extract",
                json={
                    "archive_path": str(tmp_path / "nonexistent.tar.gz"),
                    "destination": str(dest),
                },
                headers=auth_headers(),
            )
            assert resp.status_code == 404
        finally:
            client.close()
