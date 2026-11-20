"""Tests for archive creation and extraction API."""

import tarfile
import zipfile
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from sshler.webapp import ServerSettings, make_app

TEST_TOKEN = "archive-test-token"


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


class TestArchiveCreate:
    def test_create_tar_gz(self, tmp_path, monkeypatch):
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

    def test_create_zip(self, tmp_path, monkeypatch):
        config_dir = setup_config(tmp_path)
        workdir = tmp_path / "work"
        workdir.mkdir()
        (workdir / "file.txt").write_text("content")
        dest = tmp_path / "dest"
        dest.mkdir()

        client = build_client(config_dir, monkeypatch)
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

    def test_create_archive_with_directory(self, tmp_path, monkeypatch):
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

    def test_create_invalid_format(self, tmp_path, monkeypatch):
        config_dir = setup_config(tmp_path)
        client = build_client(config_dir, monkeypatch)
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

    def test_create_wrong_extension(self, tmp_path, monkeypatch):
        config_dir = setup_config(tmp_path)
        client = build_client(config_dir, monkeypatch)
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

    def test_create_too_many_paths(self, tmp_path, monkeypatch):
        config_dir = setup_config(tmp_path)
        client = build_client(config_dir, monkeypatch)
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
    def test_extract_tar_gz(self, tmp_path, monkeypatch):
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

        client = build_client(config_dir, monkeypatch)
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

    def test_extract_zip(self, tmp_path, monkeypatch):
        config_dir = setup_config(tmp_path)
        archive_path = tmp_path / "test.zip"
        with zipfile.ZipFile(str(archive_path), "w") as zf:
            zf.writestr("zipped.txt", "zip content")

        dest = tmp_path / "dest"
        dest.mkdir()

        client = build_client(config_dir, monkeypatch)
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

    def test_extract_zip_traversal_prevention(self, tmp_path, monkeypatch):
        """Verify that zip files with path traversal entries are rejected."""
        config_dir = setup_config(tmp_path)
        archive_path = tmp_path / "evil.zip"

        # Create a zip with a traversal path
        with zipfile.ZipFile(str(archive_path), "w") as zf:
            zf.writestr("../../../etc/evil.txt", "pwned")

        dest = tmp_path / "dest"
        dest.mkdir()

        client = build_client(config_dir, monkeypatch)
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

    def test_extract_zip_backslash_traversal_prevention(self, tmp_path, monkeypatch):
        """Verify that backslash-based path traversal is also blocked."""
        config_dir = setup_config(tmp_path)
        archive_path = tmp_path / "evil_backslash.zip"

        with zipfile.ZipFile(str(archive_path), "w") as zf:
            zf.writestr("..\\..\\..\\etc\\evil.txt", "pwned")

        dest = tmp_path / "dest"
        dest.mkdir()

        client = build_client(config_dir, monkeypatch)
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

    def test_extract_not_found(self, tmp_path, monkeypatch):
        config_dir = setup_config(tmp_path)
        dest = tmp_path / "dest"
        dest.mkdir()

        client = build_client(config_dir, monkeypatch)
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


def _extract(client: TestClient, archive: Path, dest: Path):
    return client.post(
        "/api/v1/boxes/local/archive/extract",
        json={"archive_path": str(archive), "destination": str(dest)},
        headers=auth_headers(),
    )


def _tar_with(archive: Path, *members: tarfile.TarInfo) -> None:
    import io

    with tarfile.open(str(archive), "w:gz") as tf:
        for info in members:
            if info.isreg():
                tf.addfile(info, io.BytesIO(b"x" * info.size))
            else:
                tf.addfile(info)


def _member(name: str, *, link_to: str | None = None) -> tarfile.TarInfo:
    info = tarfile.TarInfo(name=name)
    if link_to is None:
        info.size = 1
    else:
        info.type = tarfile.SYMTYPE
        info.linkname = link_to
    return info


class TestLocalArchiveGuards:
    """Every entry stays inside the destination on the local box. The archives are
    built in the test's own temporary folder; ``outside`` is a folder beside the
    destination that must stay empty."""

    @pytest.fixture
    def place(self, tmp_path, monkeypatch):
        config_dir = setup_config(tmp_path)
        (tmp_path / "dest").mkdir()
        (tmp_path / "outside").mkdir()
        client = build_client(config_dir, monkeypatch)
        yield tmp_path.resolve(), client
        client.close()

    def test_a_name_that_is_no_archive_is_400_on_the_local_box(self, place):
        root, client = place
        (root / "notes.txt").write_text("plain")
        resp = _extract(client, root / "notes.txt", root / "dest")
        assert (resp.status_code, resp.json()) == (400, {"detail": "Unsupported archive format"})
        assert list((root / "dest").iterdir()) == []

    def test_a_tar_entry_with_a_parent_reference_is_refused(self, place):
        root, client = place
        _tar_with(root / "up.tar.gz", _member("../outside/x.txt"))
        resp = _extract(client, root / "up.tar.gz", root / "dest")
        target = root / "outside" / "x.txt"
        assert (resp.status_code, resp.json()["detail"]) == (
            500,
            f"'../outside/x.txt' would be extracted to {str(target)!r}, "
            "which is outside the destination",
        )
        assert list((root / "outside").iterdir()) == []

    def test_a_tar_entry_with_an_absolute_name_lands_inside_the_destination(self, place):
        root, client = place
        absolute = root / "outside" / "abs.txt"
        _tar_with(root / "abs.tar.gz", _member(str(absolute)))
        resp = _extract(client, root / "abs.tar.gz", root / "dest")
        assert resp.status_code == 200
        assert list((root / "outside").iterdir()) == []
        assert (root / "dest" / str(absolute).lstrip("/")).read_bytes() == b"x"

    def test_a_tar_link_pointing_outside_is_refused(self, place):
        root, client = place
        _tar_with(root / "ln.tar.gz", _member("ln", link_to="../outside"))
        resp = _extract(client, root / "ln.tar.gz", root / "dest")
        assert (resp.status_code, resp.json()["detail"]) == (
            500,
            f"'ln' would link to {str(root / 'outside')!r}, which is outside the destination",
        )
        assert list((root / "dest").iterdir()) == []

    def test_a_zip_entry_with_an_absolute_name_lands_inside_the_destination(self, place):
        root, client = place
        absolute = root / "outside" / "abs.txt"
        with zipfile.ZipFile(str(root / "abs.zip"), "w") as zf:
            zf.writestr(str(absolute), "z")
        resp = _extract(client, root / "abs.zip", root / "dest")
        assert resp.status_code == 200
        assert list((root / "outside").iterdir()) == []
        assert (root / "dest" / str(absolute).lstrip("/")).read_text() == "z"

    def test_a_zip_entry_with_a_parent_reference_writes_nothing_outside(self, place):
        root, client = place
        with zipfile.ZipFile(str(root / "up.zip"), "w") as zf:
            zf.writestr("first.txt", "1")
            zf.writestr("../outside/x.txt", "pwned")
        resp = _extract(client, root / "up.zip", root / "dest")
        assert (resp.status_code, resp.json()) == (
            400,
            {"detail": "Archive contains unsafe path: ../outside/x.txt"},
        )
        assert list((root / "outside").iterdir()) == []


@pytest.mark.parametrize(
    "archive_name",
    ["-a.zip", "a b.zip", "it's.zip", "$HOME.zip", "`x`.zip", "a;b.zip", "a\nb.zip", "..a.zip"],
)
def test_a_local_archive_name_is_one_file_name_whatever_it_holds(
    tmp_path, monkeypatch, archive_name
):
    config_dir = setup_config(tmp_path)
    (tmp_path / "file.txt").write_text("content")
    dest = (tmp_path / "dest").resolve()
    dest.mkdir()
    client = build_client(config_dir, monkeypatch)
    try:
        resp = client.post(
            "/api/v1/boxes/local/archive/create",
            json={
                "paths": [str(tmp_path / "file.txt")],
                "destination": str(dest),
                "archive_name": archive_name,
                "format": "zip",
            },
            headers=auth_headers(),
        )
    finally:
        client.close()
    assert resp.json() == {
        "status": "ok",
        "message": f"Created {archive_name}",
        "path": str(dest / archive_name),
    }
    assert [p.name for p in dest.iterdir()] == [archive_name]
    with zipfile.ZipFile(str(dest / archive_name)) as zf:
        assert zf.namelist() == ["file.txt"]


def test_a_local_archive_of_a_missing_source_is_500_with_its_text(tmp_path, monkeypatch):
    config_dir = setup_config(tmp_path)
    dest = (tmp_path / "dest").resolve()
    dest.mkdir()
    missing = (tmp_path / "nope.txt").resolve()
    client = build_client(config_dir, monkeypatch)
    try:
        resp = client.post(
            "/api/v1/boxes/local/archive/create",
            json={
                "paths": [str(missing)],
                "destination": str(dest),
                "archive_name": "o.tar.gz",
                "format": "tar.gz",
            },
            headers=auth_headers(),
        )
    finally:
        client.close()
    assert (resp.status_code, resp.json()) == (
        500,
        {"detail": f"[Errno 2] No such file or directory: {str(missing)!r}"},
    )
