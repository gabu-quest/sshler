"""Deleting, moving or renaming the home folder or a folder above it is refused, and
deleting a link removes the link, never what it points to.

Run on the contract's two kinds of box (a temp folder, and the same tree through a
loopback SFTP server whose home is ``/home``)."""

from __future__ import annotations

import os
import subprocess
import sys
import types

import pytest
import test_box_files_contract as contract
from platform_support import make_symlink
from test_box_files_contract import Kind, _outcome

kind = contract.kind  # the fixture: a temp folder, and the same tree over loopback SFTP

pytestmark = pytest.mark.asyncio

REFUSED = "the home folder or a folder above it"


def _home_for(kind: Kind, monkeypatch) -> str:
    """The path the box calls home: on the local box it is moved into the tree."""
    if kind.name == "local":
        monkeypatch.setenv("HOME", str(kind.root / "sub"))
        monkeypatch.setenv("USERPROFILE", str(kind.root / "sub"))
        return kind.at("sub")
    return kind.home


def _refused(result) -> bool:
    return (
        isinstance(result, tuple) and result[:2] == ("answered", 400) and REFUSED in str(result[2])
    )


async def test_a_batch_delete_holding_home_is_refused_whole(kind, monkeypatch):
    home = _home_for(kind, monkeypatch)

    async def op(files, kind):
        return await files.delete_many([kind.at("a.txt"), home])

    assert _refused(await _outcome(kind, op))
    assert (kind.root / "a.txt").exists() and (kind.root / "sub" / "inner.txt").exists()


async def test_a_batch_delete_of_a_folder_above_home_is_refused(kind, monkeypatch):
    _home_for(kind, monkeypatch)
    above = kind.at("") if kind.name == "local" else "/"

    async def op(files, kind):
        return await files.delete_many([above, kind.at("a.txt")])

    assert _refused(await _outcome(kind, op))
    assert (kind.root / "a.txt").exists()


async def test_a_batch_move_of_home_is_refused_whole(kind, monkeypatch):
    home = _home_for(kind, monkeypatch)
    (kind.root / "dest").mkdir()

    async def op(files, kind):
        return await files.place_many(
            [kind.at("a.txt"), home], kind.at("dest"), move=True, overwrite=False
        )

    assert _refused(await _outcome(kind, op))
    assert (kind.root / "a.txt").exists() and not (kind.root / "dest" / "a.txt").exists()


async def test_a_move_of_home_into_a_folder_is_refused(kind, monkeypatch):
    home = _home_for(kind, monkeypatch)
    (kind.root / "dest").mkdir()

    async def op(files, kind):
        return await files.place_into(
            home, kind.at("dest"), None, move=True, overwrite=False, allow_dirs=True
        )

    assert _refused(await _outcome(kind, op))
    assert (kind.root / "sub" / "inner.txt").exists()


async def test_a_rename_of_home_is_refused(kind, monkeypatch):
    home = _home_for(kind, monkeypatch)

    async def op(files, kind):
        return await files.rename(home, "elsewhere", overwrite=False)

    assert _refused(await _outcome(kind, op))
    assert (kind.root / "sub" / "inner.txt").exists()


async def test_a_batch_copy_from_home_is_not_refused(kind, monkeypatch):
    home = _home_for(kind, monkeypatch)
    (kind.root / "dest").mkdir()

    async def op(files, kind):
        outcome = await files.place_many([home], kind.at("dest"), move=False, overwrite=False)
        return outcome.failed

    if kind.name == "remote":
        pytest.skip("the remote home has no folder in the tree to copy")
    assert await _outcome(kind, op) == []
    assert (kind.root / "dest" / "sub" / "inner.txt").exists()


async def test_a_batch_delete_of_a_link_to_home_is_refused(kind, monkeypatch):
    if kind.name != "local":
        pytest.skip("a remote link to home needs a real remote home folder")
    _home_for(kind, monkeypatch)
    make_symlink(kind.root / "home_link", kind.root / "sub", target_is_directory=True)

    async def op(files, kind):
        return await files.delete_many([kind.at("home_link")])

    assert _refused(await _outcome(kind, op))
    assert (kind.root / "sub" / "inner.txt").exists()


# --- a link is removed, not what it points to -----------------------------------------


async def test_a_batch_delete_of_a_link_to_a_file_keeps_the_file(kind):
    async def op(files, kind):
        return await files.delete_many([kind.at("link_out")])

    outcome = await _outcome(kind, op)
    assert (outcome.succeeded, outcome.failed) == ([kind.at("link_out")], [])
    assert not os.path.lexists(kind.root / "link_out")
    assert (kind.outside / "secret.txt").read_bytes() == b"secret\n"


async def test_a_batch_delete_of_a_link_to_a_folder_keeps_the_folder(kind):
    if kind.name != "local":
        pytest.skip("a remote folder delete runs rm, which the loopback server refuses")
    make_symlink(kind.root / "link_dir", kind.outside, target_is_directory=True)

    async def op(files, kind):
        return await files.delete_many([kind.at("link_dir")])

    outcome = await _outcome(kind, op)
    assert (outcome.succeeded, outcome.failed) == ([kind.at("link_dir")], [])
    assert not os.path.lexists(kind.root / "link_dir")
    assert (kind.outside / "secret.txt").read_bytes() == b"secret\n"


@pytest.mark.skipif(sys.platform != "win32", reason="a directory junction exists only on Windows")
@pytest.mark.parametrize("single", [False, True], ids=["batch", "single"])
async def test_a_delete_of_a_junction_keeps_the_folder_it_points_to(kind, single):
    """A junction is no symlink to Python (`is_symlink()` is False), so it was taken for
    the folder it points to and that folder was deleted."""
    if kind.name != "local":
        pytest.skip("a junction is a local Windows folder link")
    junction = kind.root / "junction"
    subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(junction), str(kind.outside)],
        check=True,
        capture_output=True,
    )

    async def op(files, kind):
        if single:
            return await files.delete(kind.at("junction"))
        return await files.delete_many([kind.at("junction")])

    await _outcome(kind, op)
    assert not os.path.lexists(junction)
    assert (kind.outside / "secret.txt").read_bytes() == b"secret\n"


async def test_a_batch_delete_of_a_dangling_link_removes_it(kind):
    make_symlink(kind.root / "dangling", "no-such-target")

    async def op(files, kind):
        return await files.delete_many([kind.at("dangling")])

    outcome = await _outcome(kind, op)
    assert (outcome.succeeded, outcome.failed) == ([kind.at("dangling")], [])
    assert not os.path.lexists(kind.root / "dangling")


async def test_a_batch_move_of_a_link_moves_the_link(kind):
    if kind.name != "local":
        pytest.skip("the loopback server's chroot cannot read a link (readlink: not found)")
    (kind.root / "dest").mkdir()
    make_symlink(kind.root / "link_a", "a.txt")  # inside the tree, so the server holds it

    async def op(files, kind):
        outcome = await files.place_many(
            [kind.at("link_a")], kind.at("dest"), move=True, overwrite=False
        )
        return outcome.failed

    assert await _outcome(kind, op) == []
    assert os.path.islink(kind.root / "dest" / "link_a")
    assert not os.path.lexists(kind.root / "link_a")
    assert (kind.root / "a.txt").read_bytes() == b"hello\n"


async def test_a_rename_of_a_link_renames_the_link(kind):
    if kind.name != "local":
        pytest.skip("the loopback server's chroot cannot read a link (readlink: not found)")
    make_symlink(kind.root / "link_a", "a.txt")

    async def op(files, kind):
        return await files.rename(kind.at("link_a"), "link_new", overwrite=False)

    await _outcome(kind, op)
    assert os.path.islink(kind.root / "link_new")
    assert not os.path.lexists(kind.root / "link_a")
    assert (kind.root / "a.txt").read_bytes() == b"hello\n"


# --- the single-file delete -----------------------------------------------------------


async def test_a_single_delete_of_home_is_refused(kind, monkeypatch):
    home = _home_for(kind, monkeypatch)

    async def op(files, kind):
        return await files.delete(home)

    assert _refused(await _outcome(kind, op))
    assert (kind.root / "sub" / "inner.txt").exists()


async def test_a_single_delete_of_a_folder_above_home_is_refused(kind, monkeypatch):
    _home_for(kind, monkeypatch)

    async def op(files, kind):
        return await files.delete(kind.at(""))

    assert _refused(await _outcome(kind, op))
    assert (kind.root / "a.txt").exists()


async def test_a_single_delete_of_a_link_to_home_is_refused(kind, monkeypatch):
    if kind.name != "local":
        pytest.skip("a remote link to home needs a real remote home folder")
    _home_for(kind, monkeypatch)
    make_symlink(kind.root / "home_link", kind.root / "sub", target_is_directory=True)

    async def op(files, kind):
        return await files.delete(kind.at("home_link"))

    assert _refused(await _outcome(kind, op))
    assert (kind.root / "sub" / "inner.txt").exists()
    assert os.path.islink(kind.root / "home_link")


async def test_a_single_delete_of_a_link_to_a_file_keeps_the_file(kind):
    async def op(files, kind):
        return await files.delete(kind.at("link_out"))

    await _outcome(kind, op)
    assert not os.path.lexists(kind.root / "link_out")
    assert (kind.outside / "secret.txt").read_bytes() == b"secret\n"


def test_the_delete_route_removes_a_link_not_its_target(tmp_path, monkeypatch):
    """The route resolved the path first, and a resolved link is its target."""
    from fastapi.testclient import TestClient

    from sshler.webapp import ServerSettings, make_app

    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "boxes.yaml").write_text("boxes: []\n", encoding="utf-8")
    target = tmp_path / "target.txt"
    target.write_text("keep")
    link = tmp_path / "link.txt"
    make_symlink(link, target)
    monkeypatch.setenv("SSHLER_CONFIG_DIR", str(config_dir))
    client = TestClient(make_app(ServerSettings(csrf_token="t")))
    try:
        resp = client.post(
            "/api/v1/boxes/local/delete", json={"path": str(link)}, headers={"X-SSHLER-TOKEN": "t"}
        )
        assert resp.status_code == 200, resp.text
    finally:
        client.close()
    assert not os.path.lexists(link)
    assert target.read_text() == "keep"


# --- the remote rule compares the folders paths name, not how they are spelled ---------


class _RealpathSFTP:
    """Answers realpath from a table; a path not in it is its own real path."""

    def __init__(self, real: dict[str, str]):
        self.real = real

    async def realpath(self, path: str) -> str:
        return self.real.get(path, path)

    async def lstat(self, path: str):
        return types.SimpleNamespace(permissions=0o040755)  # a folder, not a link


@pytest.mark.parametrize(
    ("path", "real"),
    [
        ("//home/me", {}),  # Linux reads `//home/me` as `/home/me`; normpath keeps the `//`
        ("//home", {}),
        ("/export/home/me", {"/home/me": "/export/home/me"}),  # home itself is behind a link
        ("/srv/h/me", {"/srv/h/me": "/home/me"}),  # a link earlier in the path leads home
        ("/srv/h", {"/srv/h": "/home"}),
    ],
)
async def test_the_remote_rule_refuses_home_however_it_is_reached(monkeypatch, path, real):
    from sshler.api import failure, remote_files

    async def home(_connection):
        return "/home/me"

    monkeypatch.setattr(remote_files, "remote_home", home)
    with pytest.raises(failure.Failure, match=REFUSED):
        await remote_files._remote_refuse_home_or_above(object(), _RealpathSFTP(real), [path])


@pytest.mark.parametrize("path", ["/home/me/projects/app", "/home/mean", "/srv/data"])
async def test_the_remote_rule_lets_a_folder_below_or_beside_home_through(monkeypatch, path):
    from sshler.api import remote_files

    async def home(_connection):
        return "/home/me"

    monkeypatch.setattr(remote_files, "remote_home", home)
    await remote_files._remote_refuse_home_or_above(object(), _RealpathSFTP({}), [path])
