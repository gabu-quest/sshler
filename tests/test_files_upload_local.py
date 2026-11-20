"""Local upload and new file: "already exists" is 409, replace needs the flag, links are
never written through (wave 5d, WS-AG).

User: "it shoudl offer to replace it, default choice is no." Local and remote both answer
409 for "already exists" on upload so one client path covers both; without the overwrite
flag the server never replaces. Review: `stat` follows symlinks, so a dangling link read
as free and the write went through it. Each test names its mutation.

A returned path is in the local file API's POSIX form (`C:/Users/x` on Windows, see
`_compose_local_child_path`), which is what the frontend parses; hence `as_posix()`.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient
from platform_support import make_symlink

from sshler import state
from sshler.webapp import ServerSettings, make_app

TOKEN = "upload-token"
HEADERS = {"X-SSHLER-TOKEN": TOKEN}


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "boxes.yaml").write_text(yaml.safe_dump({"boxes": []}), encoding="utf-8")
    monkeypatch.setenv("SSHLER_CONFIG_DIR", str(config_dir))
    test_client = TestClient(make_app(ServerSettings(csrf_token=TOKEN)))
    try:
        yield test_client
    finally:
        test_client.close()
        state.reset_state()


@pytest.fixture
def work(tmp_path: Path) -> Path:
    path = tmp_path / "work"
    path.mkdir()
    return path


def upload(client: TestClient, directory: Path, name: str, data: bytes, overwrite=None):
    form = {"directory": str(directory)}
    if overwrite is not None:
        form["overwrite"] = overwrite
    return client.post(
        "/api/v1/boxes/local/upload",
        data=form,
        files={"file": (name, data, "application/octet-stream")},
        headers=HEADERS,
    )


def touch(client: TestClient, directory: Path, name: str):
    return client.post(
        "/api/v1/boxes/local/touch",
        json={"directory": str(directory), "filename": name},
        headers=HEADERS,
    )


def test_upload_onto_an_existing_local_file_answers_409_and_keeps_its_bytes(client, work):
    """Mutation: answer 400 for the local "already exists" (the pre-5d status); the
    client's replace offer, keyed on 409, would never appear for a local box."""
    (work / "a.bin").write_bytes(b"precious")
    resp = upload(client, work, "a.bin", b"replacement")
    assert (resp.status_code, resp.json()) == (
        409,
        {"detail": "File already exists", "reason": "exists"},
    )
    assert (work / "a.bin").read_bytes() == b"precious"


def test_upload_with_overwrite_replaces_an_existing_local_file(client, work):
    """Mutation: ignore the `overwrite` form field; the answer is 409 and the old bytes
    stay."""
    (work / "a.bin").write_bytes(b"precious")
    resp = upload(client, work, "a.bin", b"replacement", overwrite="true")
    assert (resp.status_code, resp.json()["path"]) == (200, (work / "a.bin").as_posix())
    assert (work / "a.bin").read_bytes() == b"replacement"


def test_upload_with_overwrite_false_still_refuses_locally(client, work):
    """Mutation: replace whenever the field is present; "false" would replace."""
    (work / "a.bin").write_bytes(b"precious")
    resp = upload(client, work, "a.bin", b"replacement", overwrite="false")
    assert (resp.status_code, resp.json()) == (
        409,
        {"detail": "File already exists", "reason": "exists"},
    )
    assert (work / "a.bin").read_bytes() == b"precious"


def test_upload_and_touch_refuse_a_dangling_local_symlink(client, work, tmp_path):
    """Mutation: check with `Path.exists()` (follows the link) and open "wb"; the
    dangling link reads as free and the write creates its target outside `work`."""
    target = tmp_path / "outside.txt"
    make_symlink(work / "link", target)
    resp = upload(client, work, "link", b"payload")
    assert (resp.status_code, resp.json()) == (
        409,
        {"detail": "File already exists", "reason": "cannot_replace"},
    )
    resp = touch(client, work, "link")
    assert (resp.status_code, resp.json()) == (
        400,
        {"detail": "File already exists", "reason": "cannot_replace"},
    )
    assert target.exists() is False


def test_upload_with_overwrite_never_writes_through_a_local_symlink(client, work, tmp_path):
    """Mutation: let overwrite replace any existing name (drop the regular-file check);
    the bytes land in the link's target."""
    target = tmp_path / "outside.txt"
    target.write_bytes(b"keep me")
    make_symlink(work / "link", target)
    resp = upload(client, work, "link", b"payload", overwrite="true")
    assert (resp.status_code, resp.json()) == (
        400,
        {"detail": "Only a regular file can be replaced", "reason": "cannot_replace"},
    )
    assert target.read_bytes() == b"keep me"


def test_upload_and_touch_still_create_a_new_local_file(client, work):
    """Mutation: answer "already exists" for every name; a free name must still be
    created with the exact bytes."""
    resp = upload(client, work, "new.bin", b"data")
    assert (resp.status_code, resp.json()["path"]) == (200, (work / "new.bin").as_posix())
    resp = touch(client, work, "new.txt")
    assert (resp.status_code, resp.json()["path"]) == (200, (work / "new.txt").as_posix())
    assert sorted(p.name for p in work.iterdir()) == ["new.bin", "new.txt"]
    assert (work / "new.bin").read_bytes() == b"data"
    assert (work / "new.txt").read_bytes() == b""


def rename(client: TestClient, path: Path, new_name: str):
    return client.post(
        "/api/v1/boxes/local/rename",
        json={"path": str(path), "new_name": new_name},
        headers=HEADERS,
    )


@pytest.mark.parametrize(
    "bad_name,message",
    [
        ("", "Filename cannot be empty"),
        (".", "Filename cannot be '.' or '..'"),
        ("..", "Filename cannot be '.' or '..'"),
        ("a/b", "Filename cannot contain path separators"),
        ("..\\x", "Filename cannot contain path separators"),
        ("../../x", "Filename cannot contain path separators"),
        ("a\0b", "Filename cannot contain null bytes"),
    ],
)
def test_local_rename_refuses_an_invalid_new_name_and_moves_nothing(
    client, work, tmp_path, bad_name, message
):
    (work / "src.txt").write_text("keep")
    resp = rename(client, work / "src.txt", bad_name)
    assert resp.status_code == 400
    assert resp.json()["detail"] == message
    assert sorted(p.name for p in work.iterdir()) == ["src.txt"]
    assert sorted(p.name for p in tmp_path.iterdir() if p.is_file()) == []


def test_local_rename_to_a_name_that_is_the_source_itself_is_not_refused(client, work):
    # What a change of letter case is on a filesystem that ignores case.
    (work / "src.txt").write_text("source")
    resp = rename(client, work / "src.txt", "src.txt")
    assert resp.status_code == 200
    assert sorted(p.name for p in work.iterdir()) == ["src.txt"]
    assert (work / "src.txt").read_text() == "source"


@pytest.mark.parametrize("existing_kind", ["file", "folder"])
def test_local_rename_onto_an_existing_name_answers_409_and_keeps_both(client, work, existing_kind):
    (work / "src.txt").write_text("source")
    if existing_kind == "file":
        (work / "dst").write_text("other")
    elif existing_kind == "folder":
        (work / "dst").mkdir()
        (work / "dst" / "inner.txt").write_text("inner")
    resp = rename(client, work / "src.txt", "dst")
    assert resp.status_code == 409
    assert (work / "src.txt").read_text() == "source"
    if existing_kind == "file":
        assert (work / "dst").read_text() == "other"
    if existing_kind == "folder":
        assert (work / "dst" / "inner.txt").read_text() == "inner"


def test_local_rename_to_a_free_name_still_works(client, work):
    (work / "src.txt").write_text("source")
    resp = rename(client, work / "src.txt", "dst.txt")
    assert resp.status_code == 200
    assert resp.json()["path"] == (work / "dst.txt").as_posix()
    assert not (work / "src.txt").exists()
    assert (work / "dst.txt").read_text() == "source"
