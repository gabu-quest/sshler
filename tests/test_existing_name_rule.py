"""No file lands on an existing name unless the caller said replace.

User: "the server refuses unless told to replace, and the file browser asks "Replace?" as
it does for an upload". The rule, as the upload already had it: a request may carry
``overwrite`` (a JSON boolean, default false); a single rename, move or copy onto a taken
name answers 409 "File already exists" and changes nothing; with ``overwrite`` a regular
file replaces a regular file, and anything else (a folder, a link at the destination, a
folder as the source) answers 400 "Only a regular file can be replaced". A batch does
every item whose name is free and lists each refused one with a ``reason``: "exists"
(``overwrite`` would replace it), "cannot_replace" or "failed". A cross-box transfer
reports a refused file the same way in its ``done`` event, and a move never removes a
source whose copy was refused.

The remote side runs against a real asyncssh server on the loopback interface (SFTP
rooted in a temp folder), started in its own thread so the app's test client can reach
it. It runs as two servers: asyncssh's own, whose plain rename refuses a taken name, and
one whose rename replaces in silence and that has no hard links, as some servers do.
Tests that name them use variants that behave differently again: no posix-rename (and
renames that fail on top of that), and names looked up without regard to case.
The order "the name is free at the check, taken at the write" is forced by wrapping the
owner's check, never by sleeping; so is every other order these tests need.
"""

# ruff: noqa: F811
from __future__ import annotations

import asyncio
import contextlib
import errno
import os
import shutil
import signal
import socket
import threading
import tracemalloc
from collections.abc import Iterator
from pathlib import Path

import asyncssh
import httpx
import pytest
import uvicorn
from existing_name_support import (
    BOXES,
    COUNTED_REQUESTS,
    EXISTS,
    HEADERS,
    NOT_REGULAR,
    REMOTE,
    SAME_FILE,
    SERVERS,
    TAKEN,
    Side,
    env,  # noqa: F401
    files_under,
    local_env,  # noqa: F401
    side,
    single,
    target_of,
    transfer,
)

from sshler.api import local_files as local_files_api
from sshler.api import remote_files as remote_files_api
from sshler.api.failure import Failure

pytestmark = pytest.mark.posix_only("hard links, chmod and an SFTP server rooted in a temp folder")


@pytest.mark.parametrize("box", BOXES)
@pytest.mark.parametrize("op", ["rename", "move", "copy"])
def test_onto_an_existing_file_is_409_and_changes_nothing(env, box, op):
    """Before: the local move and both copies replaced the file in silence; the remote
    rename and move were left to the server (the replacing one lost the file)."""
    s = side(env, box)
    s.disk("a.txt").write_bytes(b"source")
    target = target_of(op, "a.txt")
    if op != "rename":
        s.disk("d").mkdir()
    s.disk(target).write_bytes(b"precious")
    before = files_under(s.base)

    resp = single(s, op, "a.txt")

    assert (resp.status_code, resp.json()) == (409, EXISTS)
    assert files_under(s.base) == before


@pytest.mark.parametrize("box", BOXES)
@pytest.mark.parametrize("op", ["rename", "move", "copy"])
def test_with_overwrite_a_regular_file_replaces_a_regular_file(env, box, op):
    s = side(env, box)
    s.disk("a.txt").write_bytes(b"source")
    target = target_of(op, "a.txt")
    if op != "rename":
        s.disk("d").mkdir()
    s.disk(target).write_bytes(b"old")

    resp = single(s, op, "a.txt", overwrite=True)

    assert (resp.status_code, resp.json()["path"]) == (200, s.path(target))
    expected: dict[str, object] = {target: b"source"}
    if op != "rename":
        expected["d"] = "dir"
    if op == "copy":
        expected["a.txt"] = b"source"
    if box != "local":
        expected["home"] = "dir"
    assert files_under(s.base) == expected


@pytest.mark.parametrize("box", BOXES)
@pytest.mark.parametrize("op", ["rename", "move", "copy"])
@pytest.mark.parametrize("kind", ["folder", "dangling link", "link to a file elsewhere"])
def test_what_is_not_a_regular_file_is_never_replaced(env, box, op, kind):
    """Without overwrite: 409. With it: 400, and a link's target keeps its bytes."""
    s = side(env, box)
    s.disk("a.txt").write_bytes(b"source")
    s.disk("elsewhere.txt").write_bytes(b"keep me")
    target = target_of(op, "a.txt")
    if op != "rename":
        s.disk("d").mkdir()
    if kind == "folder":
        s.disk(target).mkdir()
        s.disk(target).joinpath("inner.txt").write_bytes(b"inner")
    elif kind == "dangling link":
        s.disk(target).symlink_to(s.disk("nowhere"))
    else:
        s.disk(target).symlink_to(s.disk("elsewhere.txt"))
    before = files_under(s.base)

    refused = single(s, op, "a.txt")
    replaced = single(s, op, "a.txt", overwrite=True)

    assert (refused.status_code, refused.json()) == (409, TAKEN)
    assert (replaced.status_code, replaced.json()) == (400, NOT_REGULAR)
    assert files_under(s.base) == before


@pytest.mark.parametrize("box", BOXES)
@pytest.mark.parametrize("op", ["rename", "move"])
def test_a_folder_as_the_source_never_replaces_a_file(env, box, op):
    s = side(env, box)
    s.disk("src").mkdir()
    s.disk("src/inner.txt").write_bytes(b"inner")
    target = target_of(op, "src")
    if op != "rename":
        s.disk("d").mkdir()
    s.disk(target).write_bytes(b"precious")
    before = files_under(s.base)

    refused = single(s, op, "src")
    replaced = single(s, op, "src", overwrite=True)

    assert (refused.status_code, refused.json()) == (409, TAKEN)
    assert (replaced.status_code, replaced.json()) == (400, NOT_REGULAR)
    assert files_under(s.base) == before


@pytest.mark.parametrize("box", BOXES)
@pytest.mark.parametrize("op", ["rename", "move"])
def test_a_folder_moves_to_a_free_name(env, box, op):
    s = side(env, box)
    s.disk("src/sub").mkdir(parents=True)
    s.disk("src/sub/inner.txt").write_bytes(b"inner")
    if op != "rename":
        s.disk("d").mkdir()
    target = target_of(op, "src")

    resp = single(s, op, "src")

    assert (resp.status_code, resp.json()["path"]) == (200, s.path(target))
    assert s.disk("src").exists() is False
    assert s.disk(f"{target}/sub/inner.txt").read_bytes() == b"inner"


@pytest.mark.parametrize("box", BOXES)
@pytest.mark.parametrize("op", ["rename", "move", "copy"])
def test_a_free_name_still_works(env, box, op):
    s = side(env, box)
    s.disk("a.txt").write_bytes(b"source")
    s.disk("a.txt").chmod(0o640)
    if op != "rename":
        s.disk("d").mkdir()
    target = target_of(op, "a.txt")

    resp = single(s, op, "a.txt")

    assert (resp.status_code, resp.json()["path"]) == (200, s.path(target))
    assert s.disk(target).read_bytes() == b"source"
    assert s.disk(target).stat().st_mode & 0o777 == 0o640
    assert s.disk("a.txt").exists() is (op == "copy")


@pytest.mark.parametrize("box", BOXES)
def test_a_remote_or_local_rename_of_a_missing_file_is_404(env, box):
    """Before: the remote rename turned the SFTP "no such file" into a 500."""
    s = side(env, box)
    resp = single(s, "rename", "ghost.txt", "new.txt")
    assert (resp.status_code, resp.json()) == (
        404,
        {"detail": "File not found", "reason": "not_found"},
    )


@pytest.mark.parametrize("value", ["true", 1, "false", 0])
@pytest.mark.parametrize("route", ["rename", "move", "copy", "batch/move", "batch/copy"])
def test_overwrite_must_be_a_json_boolean(local_env, route, value):
    """A string or a number is refused (422), never read as "replace"."""
    s = side(local_env, "local")
    s.disk("a.txt").write_bytes(b"source")
    s.disk("d").mkdir()
    s.disk("d/a.txt").write_bytes(b"precious")
    if route == "rename":
        body = {"path": s.path("a.txt"), "new_name": "b.txt"}
    elif route.startswith("batch"):
        body = {"paths": [s.path("a.txt")], "destination": s.path("d")}
    else:
        body = {"source": s.path("a.txt"), "destination": s.path("d")}
    resp = s.post(route, {**body, "overwrite": value})
    assert (resp.status_code, resp.json()) == (
        422,
        {"detail": "body.overwrite: Input should be a valid boolean", "reason": "invalid"},
    )
    assert files_under(s.base) == {"a.txt": b"source", "d": "dir", "d/a.txt": b"precious"}


# --- the same file by another spelling, a hard link, a change of case ---------------


def test_moving_a_file_into_its_own_folder_by_another_spelling_changes_nothing(local_env):
    s = side(local_env, "local")
    s.disk("a.txt").write_bytes(b"source")
    resp = s.post("move", {"source": f"{s.base}/./a.txt", "destination": str(s.base)})
    assert (resp.status_code, resp.json()["path"]) == (200, s.path("a.txt"))
    assert files_under(s.base) == {"a.txt": b"source"}


@pytest.mark.parametrize("box", BOXES)
def test_copying_a_file_onto_itself_is_refused(env, box):
    s = side(env, box)
    s.disk("a.txt").write_bytes(b"source")
    folder = str(s.base) if box == "local" else "/"
    body = {"source": s.path("a.txt"), "destination": folder}
    refused = s.post("copy", body)
    replaced = s.post("copy", {**body, "overwrite": True})
    assert (refused.status_code, refused.json()) == (409, TAKEN)
    assert (replaced.status_code, replaced.json()) == (400, SAME_FILE)
    assert s.disk("a.txt").read_bytes() == b"source"


@pytest.mark.parametrize("op", ["move", "copy"])
def test_a_hard_link_of_the_source_is_never_replaced_by_it(local_env, op):
    """Replacing a hard link of the source with the source would lose nothing on a
    move but empty both names on a copy that truncates; both are refused."""
    s = side(local_env, "local")
    s.disk("a.txt").write_bytes(b"source")
    s.disk("d").mkdir()
    os.link(s.disk("a.txt"), s.disk("d/a.txt"))
    refused = single(s, op, "a.txt")
    replaced = single(s, op, "a.txt", overwrite=True)
    assert (refused.status_code, refused.json()) == (409, TAKEN)
    assert (replaced.status_code, replaced.json()) == (400, SAME_FILE)
    assert files_under(s.base) == {"a.txt": b"source", "d": "dir", "d/a.txt": b"source"}
    assert s.disk("a.txt").stat().st_nlink == 2


@pytest.mark.parametrize("box", BOXES)
def test_a_rename_that_changes_only_letter_case_is_done(env, box):
    s = side(env, box)
    s.disk("a.txt").write_bytes(b"source")
    resp = single(s, "rename", "a.txt", "A.txt")
    assert (resp.status_code, resp.json()["path"]) == (200, s.path("A.txt"))
    assert s.disk("A.txt").read_bytes() == b"source"
    assert s.disk("a.txt").exists() is False


@pytest.mark.parametrize("box", BOXES)
def test_a_rename_to_its_own_name_is_not_refused(env, box):
    s = side(env, box)
    s.disk("a.txt").write_bytes(b"source")
    resp = single(s, "rename", "a.txt", "a.txt")
    assert (resp.status_code, resp.json()["path"]) == (200, s.path("a.txt"))
    assert s.disk("a.txt").read_bytes() == b"source"


# --- item 1: the name is taken between the check and the write ---------------------


def _intrude_after_local_check(monkeypatch, target: Path, make) -> list[str]:
    """The owner's check finds ``target`` free; then ``make`` creates it, before the
    write. Returns the checked paths."""
    checked: list[str] = []
    real = local_files_api._local_lstat

    def check_then_intrude(path: str):
        found = real(path)
        checked.append(path)
        if path == str(target) and found is None and not target.exists():
            make(target)
        return found

    monkeypatch.setattr(local_files_api, "_local_lstat", check_then_intrude)
    return checked


def _intrude_after_remote_check(monkeypatch, root: Path, target: str, make) -> list[str]:
    checked: list[str] = []
    real = remote_files_api._remote_lstat_mode

    async def check_then_intrude(sftp_client, path: str):
        found = await real(sftp_client, path)
        checked.append(path)
        disk = root / target.lstrip("/")
        if path == target and found is None and not os.path.lexists(disk):
            make(disk)
        return found

    monkeypatch.setattr(remote_files_api, "_remote_lstat_mode", check_then_intrude)
    return checked


def _intrude(env, monkeypatch, s: Side, target_rel: str, make) -> list[str]:
    if s.box == "local":
        return _intrude_after_local_check(monkeypatch, s.disk(target_rel), make)
    return _intrude_after_remote_check(monkeypatch, env.root, s.path(target_rel), make)


@pytest.mark.parametrize("box", BOXES)
@pytest.mark.parametrize("op", ["rename", "move", "copy"])
@pytest.mark.parametrize("source_kind", ["file", "folder"])
def test_a_name_taken_after_the_check_is_refused_not_replaced(
    env, monkeypatch, box, op, source_kind
):
    """Before: every route checked (or did not) and then renamed or opened "wb", so a
    file made in between was replaced. Now the write itself fails on a taken name."""
    if op == "copy" and source_kind == "folder":
        pytest.skip("a single copy refuses folders before any write")
    s = side(env, box)
    if source_kind == "file":
        s.disk("a").write_bytes(b"source")
    else:
        s.disk("a").mkdir()
        s.disk("a/inner.txt").write_bytes(b"inner")
    if op != "rename":
        s.disk("d").mkdir()
    target = target_of(op, "a", "d" if op != "rename" else "b")
    checked = _intrude(env, monkeypatch, s, target, lambda p: p.write_bytes(b"intruder"))

    resp = single(s, op, "a", "d" if op != "rename" else "b")

    assert (resp.status_code, resp.json()) == (409, EXISTS if source_kind == "file" else TAKEN)
    assert checked[0] == s.path(target)
    assert s.disk(target).read_bytes() == b"intruder"
    if source_kind == "file":
        assert s.disk("a").read_bytes() == b"source"
    else:
        assert s.disk("a/inner.txt").read_bytes() == b"inner"


@pytest.mark.parametrize("box", BOXES)
def test_a_dangling_link_made_after_the_check_is_refused_and_not_written_through(
    env, monkeypatch, box
):
    s = side(env, box)
    s.disk("a.txt").write_bytes(b"source")
    s.disk("d").mkdir()
    outside = s.disk("outside.txt")
    _intrude(env, monkeypatch, s, "d/a.txt", lambda p: p.symlink_to(outside))

    resp = single(s, "copy", "a.txt")

    assert (resp.status_code, resp.json()) == (409, TAKEN)
    assert outside.exists() is False
    assert os.readlink(s.disk("d/a.txt")) == str(outside)


# --- a move whose copy landed and whose delete fails --------------------------------


@pytest.mark.parametrize("box", BOXES)
def test_a_move_that_cannot_remove_its_source_says_so_and_keeps_both(env, box):
    if os.geteuid() == 0:
        pytest.skip("root removes files from a read-only folder")
    s = side(env, box)
    s.disk("src").mkdir()
    s.disk("src/a.txt").write_bytes(b"source")
    s.disk("d").mkdir()
    s.disk("src").chmod(0o555)
    try:
        resp = s.post("move", {"source": s.path("src/a.txt"), "destination": s.path("d")})
    finally:
        s.disk("src").chmod(0o755)
    assert resp.status_code == 500
    assert resp.json()["detail"].startswith(
        f"Copied to {s.path('d/a.txt')}, but the source could not be removed: "
    )
    assert s.disk("src/a.txt").read_bytes() == b"source"
    assert s.disk("d/a.txt").read_bytes() == b"source"


# --- item 3: batch move and copy -----------------------------------------------------


def batch(s: Side, op: str, names: list[str], dest: str = "d", **extra):
    return s.post(
        f"batch/{op}",
        {"paths": [s.path(n) for n in names], "destination": s.path(dest), **extra},
    )


@pytest.mark.parametrize("box", BOXES)
@pytest.mark.parametrize("op", ["move", "copy"])
def test_a_batch_does_the_free_names_and_lists_each_refused_item_with_its_reason(env, box, op):
    """Before: a taken name was replaced (a file) or the source nested into it (a folder,
    and the remote copy's `cp -r`); nothing told the two apart."""
    s = side(env, box)
    for name in ["one.txt", "file.txt", "folder.txt", "three.txt"]:
        s.disk(name).write_bytes(name.encode())
    s.disk("d/folder.txt").mkdir(parents=True)
    s.disk("d/file.txt").write_bytes(b"precious")
    names = ["one.txt", "file.txt", "folder.txt", "ghost.txt", "three.txt"]

    resp = batch(s, op, names)

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "partial"
    assert body["succeeded"] == [s.path("one.txt"), s.path("three.txt")]
    assert len(body["failed"]) == 3
    assert body["failed"][0] == {
        "path": s.path("file.txt"),
        "error": "File already exists",
        "reason": "exists",
    }
    assert body["failed"][1] == {
        "path": s.path("folder.txt"),
        "error": "File already exists",
        "reason": "cannot_replace",
    }
    assert (body["failed"][2]["path"], body["failed"][2]["reason"]) == (
        s.path("ghost.txt"),
        "not_found",
    )
    assert s.disk("d/file.txt").read_bytes() == b"precious"
    assert files_under(s.disk("d/folder.txt")) == {}
    assert s.disk("d/one.txt").read_bytes() == b"one.txt"
    assert s.disk("d/three.txt").read_bytes() == b"three.txt"
    assert s.disk("file.txt").read_bytes() == b"file.txt"
    assert s.disk("one.txt").exists() is (op == "copy")


@pytest.mark.parametrize("box", BOXES)
@pytest.mark.parametrize("op", ["move", "copy"])
def test_overwrite_on_a_batch_replaces_each_regular_file_and_nothing_else(env, box, op):
    s = side(env, box)
    for name in ["file.txt", "folder.txt"]:
        s.disk(name).write_bytes(name.encode())
    s.disk("d/folder.txt").mkdir(parents=True)
    s.disk("d/file.txt").write_bytes(b"precious")

    resp = batch(s, op, ["file.txt", "folder.txt"], overwrite=True)

    body = resp.json()
    assert (resp.status_code, body["succeeded"]) == (200, [s.path("file.txt")])
    assert body["failed"] == [
        {
            "path": s.path("folder.txt"),
            "error": "Only a regular file can be replaced",
            "reason": "cannot_replace",
        }
    ]
    assert s.disk("d/file.txt").read_bytes() == b"file.txt"
    assert s.disk("folder.txt").read_bytes() == b"folder.txt"


@pytest.mark.parametrize("box", BOXES)
@pytest.mark.parametrize("op", ["move", "copy"])
def test_two_items_with_the_same_name_never_replace_each_other(env, box, op):
    """Even with overwrite: the second would replace the first item of the same batch."""
    s = side(env, box)
    s.disk("x").mkdir()
    s.disk("y").mkdir()
    s.disk("x/a.txt").write_bytes(b"from x")
    s.disk("y/a.txt").write_bytes(b"from y")
    s.disk("d").mkdir()

    resp = batch(s, op, ["x/a.txt", "y/a.txt"], overwrite=True)

    body = resp.json()
    assert (resp.status_code, body["succeeded"]) == (200, [s.path("x/a.txt")])
    assert body["failed"] == [
        {
            "path": s.path("y/a.txt"),
            "error": "Another item of this request has the same name",
            "reason": "cannot_replace",
        }
    ]
    assert s.disk("d/a.txt").read_bytes() == b"from x"
    assert s.disk("y/a.txt").read_bytes() == b"from y"


@pytest.mark.parametrize("box", BOXES)
def test_a_batch_copy_of_a_folder_copies_its_tree_to_a_free_name(env, box):
    s = side(env, box)
    s.disk("tree/sub").mkdir(parents=True)
    s.disk("tree/top.txt").write_bytes(b"top")
    s.disk("tree/sub/deep.txt").write_bytes(b"deep")
    s.disk("d").mkdir()

    resp = batch(s, "copy", ["tree"])

    assert (resp.status_code, resp.json()["succeeded"]) == (200, [s.path("tree")])
    assert files_under(s.disk("d")) == {
        "tree": "dir",
        "tree/sub": "dir",
        "tree/sub/deep.txt": b"deep",
        "tree/top.txt": b"top",
    }
    assert s.disk("tree/top.txt").read_bytes() == b"top"


@pytest.mark.parametrize("box", BOXES)
def test_a_batch_item_whose_name_is_taken_after_the_check_is_refused(env, monkeypatch, box):
    s = side(env, box)
    s.disk("a.txt").write_bytes(b"source")
    s.disk("d").mkdir()
    _intrude(env, monkeypatch, s, "d/a.txt", lambda p: p.write_bytes(b"intruder"))

    resp = batch(s, "move", ["a.txt"])

    assert resp.json()["failed"] == [
        {"path": s.path("a.txt"), "error": "File already exists", "reason": "exists"}
    ]
    assert s.disk("d/a.txt").read_bytes() == b"intruder"
    assert s.disk("a.txt").read_bytes() == b"source"


@pytest.mark.parametrize("server", sorted(SERVERS))
def test_remote_batch_and_single_routes_resolve_tilde_on_the_server(env, server):
    """`~` is the remote home (here `/home`) for the routes that now place through
    the rule."""
    s = side(env, REMOTE)
    for name in ["r.txt", "m.txt", "c.txt", "bm.txt", "bc.txt"]:
        s.disk(f"home/{name}").write_bytes(name.encode())
    s.disk("home/d").mkdir()

    responses = [
        s.post("rename", {"path": "~/r.txt", "new_name": "r2.txt"}),
        s.post("move", {"source": "~/m.txt", "destination": "~/d"}),
        s.post("copy", {"source": "~/c.txt", "destination": "~/d"}),
        s.post("batch/move", {"paths": ["~/bm.txt"], "destination": "~/d"}),
        s.post("batch/copy", {"paths": ["~/bc.txt"], "destination": "~/d"}),
    ]

    assert [r.status_code for r in responses] == [200] * 5
    assert [r.json().get("path") for r in responses[:3]] == [
        "/home/r2.txt",
        "/home/d/m.txt",
        "/home/d/c.txt",
    ]
    assert files_under(s.disk("home")) == {
        "bc.txt": b"bc.txt",
        "c.txt": b"c.txt",
        "d": "dir",
        "d/bc.txt": b"bc.txt",
        "d/bm.txt": b"bm.txt",
        "d/c.txt": b"c.txt",
        "d/m.txt": b"m.txt",
        "r2.txt": b"r.txt",
    }


# --- item 4: cross-box transfer ------------------------------------------------------


@pytest.mark.parametrize("server", sorted(SERVERS))
@pytest.mark.parametrize(("src", "dest"), [("local", REMOTE), (REMOTE, "local")])
@pytest.mark.parametrize("mode", ["copy", "move"])
def test_a_transfer_refuses_a_taken_name_and_a_move_keeps_that_source(env, server, src, dest, mode):
    """Before: the destination was opened "wb" and replaced; a move then removed the
    source too, so both copies of the user's file were gone."""
    a, b = side(env, src), side(env, dest)
    a.disk("x.txt").write_bytes(b"source x")
    a.disk("y.txt").write_bytes(b"source y")
    b.disk("in").mkdir()
    b.disk("in/x.txt").write_bytes(b"precious")

    events = transfer(env, src, dest, [a.path("x.txt"), a.path("y.txt")], b.path("in"), mode=mode)

    assert events[-1] == (
        "done",
        {
            "succeeded": [a.path("y.txt")],
            "failed": [
                {"path": a.path("x.txt"), "error": "File already exists", "reason": "exists"}
            ],
        },
    )
    assert b.disk("in/x.txt").read_bytes() == b"precious"
    assert b.disk("in/y.txt").read_bytes() == b"source y"
    assert a.disk("x.txt").read_bytes() == b"source x"
    assert a.disk("y.txt").exists() is (mode == "copy")


@pytest.mark.parametrize("server", sorted(SERVERS))
@pytest.mark.parametrize(("src", "dest"), [("local", REMOTE), (REMOTE, "local")])
def test_a_transfer_with_overwrite_replaces_a_file_but_not_a_folder(env, server, src, dest):
    a, b = side(env, src), side(env, dest)
    a.disk("x.txt").write_bytes(b"source x")
    a.disk("y.txt").write_bytes(b"source y")
    b.disk("in/y.txt").mkdir(parents=True)
    b.disk("in/x.txt").write_bytes(b"old")

    events = transfer(
        env,
        src,
        dest,
        [a.path("x.txt"), a.path("y.txt")],
        b.path("in"),
        mode="move",
        overwrite=True,
    )

    assert events[-1] == (
        "done",
        {
            "succeeded": [a.path("x.txt")],
            "failed": [
                {
                    "path": a.path("y.txt"),
                    "error": "Only a regular file can be replaced",
                    "reason": "cannot_replace",
                }
            ],
        },
    )
    assert b.disk("in/x.txt").read_bytes() == b"source x"
    assert a.disk("x.txt").exists() is False
    assert a.disk("y.txt").read_bytes() == b"source y"


@pytest.mark.parametrize("server", sorted(SERVERS))
@pytest.mark.parametrize(("src", "dest"), [("local", REMOTE), (REMOTE, "local")])
def test_a_transfer_move_that_cannot_remove_its_source_reports_it(env, server, src, dest):
    if os.geteuid() == 0:
        pytest.skip("root removes files from a read-only folder")
    a, b = side(env, src), side(env, dest)
    a.disk("src").mkdir()
    a.disk("src/x.txt").write_bytes(b"source x")
    b.disk("in").mkdir()
    a.disk("src").chmod(0o555)
    try:
        events = transfer(env, src, dest, [a.path("src/x.txt")], b.path("in"), mode="move")
    finally:
        a.disk("src").chmod(0o755)

    kind, done = events[-1]
    assert (kind, done["succeeded"], len(done["failed"])) == ("done", [], 1)
    assert done["failed"][0]["reason"] == "failed"
    assert done["failed"][0]["error"].startswith(
        f"Copied to {b.path('in/x.txt')}, but the source could not be removed: "
    )
    assert a.disk("src/x.txt").read_bytes() == b"source x"
    assert b.disk("in/x.txt").read_bytes() == b"source x"


@pytest.mark.parametrize("server", sorted(SERVERS))
@pytest.mark.parametrize(("src", "dest"), [("local", REMOTE), (REMOTE, "local")])
def test_a_transfer_to_a_name_taken_after_the_check_is_refused(env, server, monkeypatch, src, dest):
    a, b = side(env, src), side(env, dest)
    a.disk("x.txt").write_bytes(b"source x")
    b.disk("in").mkdir()
    _intrude(env, monkeypatch, b, "in/x.txt", lambda p: p.write_bytes(b"intruder"))

    events = transfer(env, src, dest, [a.path("x.txt")], b.path("in"), mode="move")

    assert events[-1] == (
        "done",
        {
            "succeeded": [],
            "failed": [
                {"path": a.path("x.txt"), "error": "File already exists", "reason": "exists"}
            ],
        },
    )
    assert b.disk("in/x.txt").read_bytes() == b"intruder"
    assert a.disk("x.txt").read_bytes() == b"source x"


# --- item 5: local file work leaves the event loop ----------------------------------


def _on_loop() -> bool:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return False
    return True


@pytest.mark.parametrize(
    ("route", "calls"),
    [
        ("rename", ["resolve", "link", "unlink"]),
        ("move", ["resolve", "resolve", "link", "unlink"]),
        ("copy", ["resolve", "resolve", "copyfileobj"]),
        ("batch/move", ["resolve", "resolve", "link", "unlink"]),
        ("batch/copy", ["resolve", "resolve", "copyfileobj"]),
    ],
)
def test_local_file_work_runs_off_the_event_loop(local_env, monkeypatch, route, calls):
    """Before: the single move and copy resolved, stat-ed, renamed and read on the loop
    thread, so a large copy froze every terminal."""
    import shutil

    s = side(local_env, "local")
    s.disk("a.txt").write_bytes(b"source")
    s.disk("d").mkdir()
    seen: list[tuple[str, bool]] = []

    def spy(owner, name):
        real = getattr(owner, name)

        def wrapper(*args, **kwargs):
            seen.append((name, _on_loop()))
            return real(*args, **kwargs)

        monkeypatch.setattr(owner, name, wrapper)

    spy(os, "link")
    spy(os, "unlink")
    spy(shutil, "copyfileobj")
    spy(Path, "resolve")
    if route == "rename":
        body = {"path": s.path("a.txt"), "new_name": "b.txt"}
    elif route.startswith("batch"):
        body = {"paths": [s.path("a.txt")], "destination": s.path("d")}
    else:
        body = {"source": s.path("a.txt"), "destination": s.path("d")}

    resp = s.post(route, body)

    assert resp.status_code == 200, resp.text
    assert seen == [(name, False) for name in calls]


@pytest.mark.parametrize("box", BOXES)
def test_a_copy_does_not_hold_the_whole_file_in_memory(env, box):
    """Before: the single copy read the whole file (`read_bytes`, `await read()`)."""
    s = side(env, box)
    size = 24 * 1024 * 1024
    payload = os.urandom(1024) * (size // 1024)
    s.disk("big.bin").write_bytes(payload)
    s.disk("d").mkdir()
    tracemalloc.start()
    try:
        resp = single(s, "copy", "big.bin")
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert resp.status_code == 200, resp.text
    assert s.disk("d/big.bin").read_bytes() == payload
    assert peak < 8 * 1024 * 1024


# === paths that fail half way and servers that behave differently =================

INTO_ITSELF = "A folder cannot be placed inside itself"
SAME_NAME = "Another item of this request has the same name"


def source_changed(target: str) -> str:
    return f"Copied to {target}, but the source changed during the copy and was kept"


# --- a folder is never copied into itself ---------------------------------------------


@pytest.mark.parametrize("box", BOXES)
@pytest.mark.parametrize("op", ["batch/copy", "batch/move", "move"])
@pytest.mark.parametrize("into", ["a", "a/sub", "a/sub/..", "link"])
def test_a_folder_is_never_copied_or_moved_into_itself(env, box, op, into):
    """Before: the copy made the target folder, then listed the source, so it descended
    into its own copy and left the nest behind; a move answered 500."""
    s = side(env, box)
    s.disk("a/sub").mkdir(parents=True)
    s.disk("a/top.txt").write_bytes(b"top")
    s.disk("a/sub/deep.txt").write_bytes(b"deep")
    s.disk("link").symlink_to("a")
    before = files_under(s.base)

    if op == "move":
        resp = s.post("move", {"source": s.path("a"), "destination": s.path(into)})
        assert (resp.status_code, resp.json()) == (
            400,
            {"detail": INTO_ITSELF, "reason": "cannot_replace"},
        )
    else:
        resp = s.post(op, {"paths": [s.path("a")], "destination": s.path(into)})
        assert (resp.status_code, resp.json()) == (
            200,
            {
                "status": "partial",
                "succeeded": [],
                "failed": [{"path": s.path("a"), "error": INTO_ITSELF, "reason": "cannot_replace"}],
            },
        )
    assert files_under(s.base) == before


# --- two sources of one name in a transfer --------------------------------------------


@pytest.mark.parametrize("server", ["refusing-rename", "replacing-rename"])
@pytest.mark.parametrize(("src", "dest"), [("local", REMOTE), (REMOTE, "local")])
@pytest.mark.parametrize("overwrite", [False, True])
def test_two_sources_of_one_name_never_replace_each_other_in_a_transfer(
    env, server, src, dest, overwrite
):
    """Before: with overwrite the second replaced the first, both were reported done and
    a move removed both sources; without it the second said "exists", which invites a
    Replace that loses the first."""
    a, b = side(env, src), side(env, dest)
    a.disk("x").mkdir()
    a.disk("y").mkdir()
    a.disk("x/f").write_bytes(b"from x")
    a.disk("y/f").write_bytes(b"from y")
    b.disk("in").mkdir()

    events = transfer(
        env,
        src,
        dest,
        [a.path("x/f"), a.path("y/f")],
        b.path("in"),
        mode="move",
        overwrite=overwrite,
    )

    assert events[-1] == (
        "done",
        {
            "succeeded": [a.path("x/f")],
            "failed": [{"path": a.path("y/f"), "error": SAME_NAME, "reason": "cannot_replace"}],
        },
    )
    assert files_under(b.disk("in")) == {"f": b"from x"}
    assert a.disk("x/f").exists() is False
    assert a.disk("y/f").read_bytes() == b"from y"


# --- a folder copy that fails half way leaves nothing -----------------------------------


def _tree_with_an_unreadable_file(s: Side) -> None:
    s.disk("tree/sub").mkdir(parents=True)
    s.disk("tree/one.txt").write_bytes(b"one")
    s.disk("tree/sub/two.txt").write_bytes(b"two")
    s.disk("tree/three.txt").write_bytes(b"three")
    s.disk("tree/three.txt").chmod(0)
    s.disk("d").mkdir()


def _unreadable_reason(s: Side) -> str:
    return "Permission denied"


def test_a_folder_copy_whose_failure_is_not_a_files_answers_the_systems_text(tmp_path, monkeypatch):
    """``copytree`` keeps a failure outside a file's copy (a folder's times and modes) as
    a text only; the answer is that text, never the list ``copytree`` raises."""
    (tmp_path / "tree").mkdir()
    (tmp_path / "tree" / "one.txt").write_bytes(b"one")
    source, target = str(tmp_path / "tree"), str(tmp_path / "copy")

    copystat = shutil.copystat

    def refuse_for_a_folder(src, dst, *, follow_symlinks=True):
        if os.path.isdir(src):
            raise OSError(errno.EPERM, "Operation not permitted")
        copystat(src, dst, follow_symlinks=follow_symlinks)

    monkeypatch.setattr(shutil, "copystat", refuse_for_a_folder)

    with pytest.raises(Failure) as raised:
        local_files_api._local_copy_folder_new(source, target, symlinks=True)

    assert (raised.value.status_code, raised.value.reason, raised.value.detail) == (
        500,
        "failed",
        f"Could not copy {source} to {target}: [Errno 1] Operation not permitted",
    )
    assert os.path.lexists(target) is False


@pytest.mark.parametrize("box", BOXES)
def test_a_folder_copy_that_fails_half_way_leaves_nothing_under_its_name(env, box):
    """Before: the partial folder stayed, so a retry answered "exists"."""
    if os.geteuid() == 0:
        pytest.skip("root reads a file of mode 0")
    s = side(env, box)
    _tree_with_an_unreadable_file(s)
    try:
        resp = batch(s, "copy", ["tree"])
    finally:
        s.disk("tree/three.txt").chmod(0o644)

    reason = _unreadable_reason(s)
    error = f"Could not copy {s.path('tree')} to {s.path('d/tree')}: {reason}"
    assert (resp.status_code, resp.json()) == (
        200,
        {
            "status": "partial",
            "succeeded": [],
            "failed": [{"path": s.path("tree"), "error": error, "reason": "denied"}],
        },
    )
    assert files_under(s.disk("d")) == {}
    assert files_under(s.disk("tree")) == {
        "one.txt": b"one",
        "sub": "dir",
        "sub/two.txt": b"two",
        "three.txt": b"three",
    }


def _no_rename_across(monkeypatch, source: Path) -> None:
    """``os.rename`` of ``source`` fails as across two filesystems (EXDEV)."""
    real = os.rename

    def rename(src, dst, *args, **kwargs):
        if str(src) == str(source):
            raise OSError(errno.EXDEV, "Invalid cross-device link")
        return real(src, dst, *args, **kwargs)

    monkeypatch.setattr(os, "rename", rename)


def test_a_cross_device_folder_move_that_fails_half_way_leaves_nothing(local_env, monkeypatch):
    if os.geteuid() == 0:
        pytest.skip("root reads a file of mode 0")
    s = side(local_env, "local")
    _tree_with_an_unreadable_file(s)
    _no_rename_across(monkeypatch, s.disk("tree"))
    try:
        resp = s.post("move", {"source": s.path("tree"), "destination": s.path("d")})
    finally:
        s.disk("tree/three.txt").chmod(0o644)

    error = f"Could not copy {s.path('tree')} to {s.path('d/tree')}: {_unreadable_reason(s)}"
    assert (resp.status_code, resp.json()) == (403, {"detail": error, "reason": "denied"})
    assert files_under(s.disk("d")) == {}
    assert s.disk("tree/three.txt").read_bytes() == b"three"


@pytest.mark.parametrize("box", BOXES)
def test_a_partial_folder_that_cannot_be_removed_is_named(env, monkeypatch, box):
    """The cleanup fails too (on a remote box: the connection is gone): the error says
    where the partial copy is."""
    if os.geteuid() == 0:
        pytest.skip("root reads a file of mode 0")
    s = side(env, box)
    _tree_with_an_unreadable_file(s)
    if box == "local":

        def gone(path, *args, **kwargs):
            raise OSError("connection gone")

        monkeypatch.setattr(shutil, "rmtree", gone)
    else:

        async def gone(self, path, *args, **kwargs):
            raise asyncssh.SFTPConnectionLost("connection gone")

        monkeypatch.setattr(asyncssh.SFTPClient, "rmdir", gone)
    try:
        resp = batch(s, "copy", ["tree"])
    finally:
        s.disk("tree/three.txt").chmod(0o644)

    error = (
        f"Could not copy {s.path('tree')} to {s.path('d/tree')}: {_unreadable_reason(s)}; "
        f"the partial copy at {s.path('d/tree')} could not be removed: connection gone"
    )
    assert resp.json()["failed"] == [{"path": s.path("tree"), "error": error, "reason": "denied"}]
    assert s.disk("d/tree").is_dir() is True


# --- a replaced file keeps its permission bits ---------------------------------------


@pytest.mark.parametrize("box", [*BOXES, "lb-no-posix"])
@pytest.mark.parametrize("op", ["rename", "move", "copy", "batch/move", "batch/copy"])
def test_a_replaced_file_keeps_its_permission_bits(env, box, op):
    """Before: a replace gave the destination the source's bits (or a temp file's)."""
    s = side(env, box)
    s.disk("a.txt").write_bytes(b"source")
    s.disk("a.txt").chmod(0o600)
    target = "b.txt" if op == "rename" else "d/a.txt"
    s.disk("d").mkdir()
    s.disk(target).write_bytes(b"old")
    s.disk(target).chmod(0o640)

    if op == "rename":
        resp = single(s, "rename", "a.txt", "b.txt", overwrite=True)
    elif op.startswith("batch"):
        resp = batch(s, op.split("/")[1], ["a.txt"], overwrite=True)
    else:
        resp = single(s, op, "a.txt", overwrite=True)

    assert resp.status_code == 200, resp.text
    assert s.disk(target).read_bytes() == b"source"
    assert s.disk(target).stat().st_mode & 0o7777 == 0o640


@pytest.mark.parametrize("server", ["refusing-rename", "replacing-rename", "no-posix-rename"])
@pytest.mark.parametrize(("src", "dest"), [("local", REMOTE), (REMOTE, "local")])
def test_a_file_replaced_by_a_transfer_keeps_its_permission_bits(env, server, src, dest):
    """Before: the local temp file was made 0600 and the remote one got the server's
    default, and the rename carried that over the old file's bits."""
    a, b = side(env, src), side(env, dest)
    a.disk("x.txt").write_bytes(b"source x")
    a.disk("x.txt").chmod(0o600)
    b.disk("in").mkdir()
    b.disk("in/x.txt").write_bytes(b"old")
    b.disk("in/x.txt").chmod(0o640)

    events = transfer(env, src, dest, [a.path("x.txt")], b.path("in"), overwrite=True)

    assert events[-1] == ("done", {"succeeded": [a.path("x.txt")], "failed": []})
    assert b.disk("in/x.txt").read_bytes() == b"source x"
    assert b.disk("in/x.txt").stat().st_mode & 0o7777 == 0o640


# --- a client that disconnects in the middle of a transfer ----------------------------


class LiveServer:
    """The app behind a real uvicorn on the loopback interface, on its own thread, so a
    client can drop the connection in the middle of a response."""

    def __init__(self, app) -> None:
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        self.port = sock.getsockname()[1]
        started = threading.Event()

        class Server(uvicorn.Server):
            async def startup(self, sockets=None) -> None:
                await super().startup(sockets=sockets)
                started.set()

        config = uvicorn.Config(
            app, lifespan="off", log_level="warning", timeout_graceful_shutdown=5
        )
        self.server = Server(config)
        self.thread = threading.Thread(
            target=self.server.run, kwargs={"sockets": [sock]}, daemon=True
        )
        self.thread.start()
        assert started.wait(30)

    def close(self) -> None:
        self.server.should_exit = True
        self.thread.join(timeout=30)


@pytest.mark.parametrize("server", ["refusing-rename"])
@pytest.mark.parametrize(("src", "dest"), [("local", REMOTE), (REMOTE, "local")])
@pytest.mark.parametrize("overwrite", [False, True])
def test_a_client_that_disconnects_mid_transfer_leaves_no_partial_file(
    env, monkeypatch, server, src, dest, overwrite
):
    """The copy holds after its second chunk until the server sees the client gone; the
    test then waits for the landing's cleanup to have run and lists the folder."""
    a, b = side(env, src), side(env, dest)
    payload = os.urandom(1024) * 1024
    a.disk("big.bin").write_bytes(payload)
    b.disk("in").mkdir()
    if overwrite:
        b.disk("in/big.bin").write_bytes(b"old")
    before = files_under(b.disk("in"))
    mid, cleaned = threading.Event(), threading.Event()
    writes = [0]

    if dest == "local":
        owner, real_write = local_files_api._ThreadedLanding, local_files_api._ThreadedLanding.write
        real_abort = local_files_api._LocalLanding.abort

        def abort(self):
            try:
                real_abort(self)
            finally:
                cleaned.set()

        monkeypatch.setattr(local_files_api._LocalLanding, "abort", abort)
    else:
        owner, real_write = remote_files_api._RemoteLanding, remote_files_api._RemoteLanding.write
        real_remote_abort = remote_files_api._RemoteLanding.abort

        async def remote_abort(self):
            try:
                await real_remote_abort(self)
            finally:
                cleaned.set()

        monkeypatch.setattr(remote_files_api._RemoteLanding, "abort", remote_abort)

    async def write(self, data):
        await real_write(self, data)
        writes[0] += 1
        if writes[0] == 2:
            mid.set()
            await asyncio.wait_for(asyncio.Event().wait(), 30)  # until the disconnect

    monkeypatch.setattr(owner, "write", write)
    live = LiveServer(env.client.app)
    try:
        with httpx.Client(base_url=f"http://127.0.0.1:{live.port}", timeout=30) as http:
            body = {
                "src_box": src,
                "dest_box": dest,
                "paths": [a.path("big.bin")],
                "destination": b.path("in"),
                "mode": "move",
                "overwrite": overwrite,
            }
            with http.stream("POST", "/api/v1/transfer", json=body, headers=HEADERS) as resp:
                assert resp.status_code == 200
                assert mid.wait(30)
            # Leaving the block closes the connection with the response half read.
        assert cleaned.wait(30)
    finally:
        live.close()

    assert files_under(b.disk("in")) == before
    assert a.disk("big.bin").read_bytes() == payload


# --- a server without posix-rename ----------------------------------------------------


@pytest.mark.parametrize("box", ["lb-no-posix"])
@pytest.mark.parametrize("op", ["rename", "move", "copy"])
def test_a_replace_on_a_server_without_posix_rename_leaves_no_backup(env, box, op):
    s = side(env, box)
    s.disk("a.txt").write_bytes(b"source")
    target = target_of(op, "a.txt", "b.txt" if op == "rename" else "d")
    s.disk("d").mkdir()
    s.disk(target).write_bytes(b"old")

    resp = single(s, op, "a.txt", "b.txt" if op == "rename" else "d", overwrite=True)

    assert (resp.status_code, resp.json()["path"]) == (200, s.path(target))
    expected: dict[str, object] = {"d": "dir", "home": "dir", target: b"source"}
    if op == "copy":
        expected["a.txt"] = b"source"
    assert files_under(s.base) == expected


@pytest.mark.parametrize("box", ["lb-temp-rename-fails"])
@pytest.mark.parametrize("op", ["rename", "move", "copy"])
def test_a_replace_whose_second_rename_fails_puts_the_old_file_back(env, box, op):
    """Before: the old file was removed first, so a failed rename left neither."""
    s = side(env, box)
    s.disk("a.txt").write_bytes(b"source")
    s.disk("d").mkdir()
    target = target_of(op, "a.txt", "b.txt" if op == "rename" else "d")
    s.disk(target).write_bytes(b"old")
    before = files_under(s.base)

    resp = single(s, op, "a.txt", "b.txt" if op == "rename" else "d", overwrite=True)

    assert (resp.status_code, resp.json()) == (
        500,
        {"detail": "rename refused", "reason": "failed"},
    )
    assert files_under(s.base) == before


@pytest.mark.parametrize("box", ["lb-no-rename-back"])
def test_a_backup_that_cannot_be_put_back_is_named(env, box):
    s = side(env, box)
    s.disk("a.txt").write_bytes(b"source")
    s.disk("d").mkdir()
    s.disk("d/a.txt").write_bytes(b"old")

    resp = single(s, "copy", "a.txt", overwrite=True)

    backups = [p.name for p in s.disk("d").iterdir() if p.name != "a.txt"]
    assert len(backups) == 1
    backup = f"/d/{backups[0]}"
    assert (resp.status_code, resp.json()) == (
        500,
        {
            "detail": (
                f"Could not replace /d/a.txt: rename refused; the old file is kept at {backup}"
            ),
            "reason": "failed",
        },
    )
    assert files_under(s.disk("d")) == {backups[0]: b"old"}
    assert s.disk("a.txt").read_bytes() == b"source"


@pytest.mark.parametrize("box", ["lb-no-posix"])
@pytest.mark.parametrize("op", ["rename", "move"])
def test_a_folder_moves_on_a_server_without_posix_rename(env, box, op):
    """The folder move's fallback there (drop the empty folder, plain rename) moves the
    folder whole and refuses a taken name; nothing is lost."""
    s = side(env, box)
    s.disk("src/sub").mkdir(parents=True)
    s.disk("src/sub/inner.txt").write_bytes(b"inner")
    s.disk("d").mkdir()
    s.disk("taken").mkdir()
    s.disk("e/src").mkdir(parents=True)
    target = target_of(op, "src", "moved" if op == "rename" else "d")

    moved = single(s, op, "src", "moved" if op == "rename" else "d")
    if op == "rename":
        refused = single(s, "rename", "moved", "taken")
    else:
        refused = s.post("move", {"source": s.path("d/src"), "destination": s.path("e")})

    assert (moved.status_code, moved.json()["path"]) == (200, s.path(target))
    assert s.disk(f"{target}/sub/inner.txt").read_bytes() == b"inner"
    assert (refused.status_code, refused.json()) == (409, TAKEN)
    assert s.disk(f"{target}/sub/inner.txt").read_bytes() == b"inner"


# --- a move removes its source only if it is still the file that was copied ----------


@pytest.mark.parametrize("server", ["refusing-rename", "replacing-rename"])
@pytest.mark.parametrize("src", ["local", REMOTE])
def test_a_transfer_between_two_boxes_that_reach_one_file_keeps_it(env, server, src):
    """The local box and an SSH box to the same machine: a move with overwrite replaced
    the file with its own copy and then removed "the source", the same file."""
    disk = env.root / "x.txt"
    disk.write_bytes(b"precious")
    os.utime(disk, (1_600_000_000, 1_600_000_000))
    local_path, remote_path = str(disk), "/x.txt"
    if src == "local":
        path, dest, folder, target = local_path, REMOTE, "/", remote_path
    else:
        path, dest, folder, target = remote_path, "local", str(env.root), local_path

    events = transfer(env, src, dest, [path], folder, mode="move", overwrite=True)

    assert events[-1] == (
        "done",
        {
            "succeeded": [],
            "failed": [{"path": path, "error": source_changed(target), "reason": "failed"}],
        },
    )
    assert disk.read_bytes() == b"precious"


def _append_before_commit(monkeypatch, local_dest: bool, source: Path) -> None:
    """The source grows after its bytes were read and before the copy is committed."""
    if local_dest:
        real = local_files_api._LocalLanding.commit

        def commit(self):
            with open(source, "ab") as handle:
                handle.write(b" and more")
            real(self)

        monkeypatch.setattr(local_files_api._LocalLanding, "commit", commit)
    else:
        real_remote = remote_files_api._RemoteLanding.commit

        async def remote_commit(self):
            with open(source, "ab") as handle:
                handle.write(b" and more")
            await real_remote(self)

        monkeypatch.setattr(remote_files_api._RemoteLanding, "commit", remote_commit)


@pytest.mark.parametrize("server", ["refusing-rename"])
@pytest.mark.parametrize(("src", "dest"), [("local", REMOTE), (REMOTE, "local")])
def test_a_source_written_to_during_a_transfer_move_is_kept(env, monkeypatch, server, src, dest):
    a, b = side(env, src), side(env, dest)
    a.disk("x.txt").write_bytes(b"source x")
    os.utime(a.disk("x.txt"), (1_600_000_000, 1_600_000_000))
    b.disk("in").mkdir()
    _append_before_commit(monkeypatch, dest == "local", a.disk("x.txt"))

    events = transfer(env, src, dest, [a.path("x.txt")], b.path("in"), mode="move")

    assert events[-1] == (
        "done",
        {
            "succeeded": [],
            "failed": [
                {
                    "path": a.path("x.txt"),
                    "error": source_changed(b.path("in/x.txt")),
                    "reason": "failed",
                }
            ],
        },
    )
    assert b.disk("in/x.txt").read_bytes() == b"source x"
    assert a.disk("x.txt").read_bytes() == b"source x and more"


def test_a_cross_device_local_move_keeps_a_source_written_to_during_the_copy(
    local_env, monkeypatch
):
    s = side(local_env, "local")
    s.disk("a.txt").write_bytes(b"source")
    s.disk("d").mkdir()
    real_link = os.link

    def no_link(src, dst, *args, **kwargs):
        if str(src) == str(s.disk("a.txt")):
            raise OSError(errno.EXDEV, "Invalid cross-device link")
        return real_link(src, dst, *args, **kwargs)

    monkeypatch.setattr(os, "link", no_link)
    _append_before_commit(monkeypatch, True, s.disk("a.txt"))

    resp = single(s, "move", "a.txt")

    assert (resp.status_code, resp.json()) == (
        500,
        {"detail": source_changed(s.path("d/a.txt")), "reason": "failed"},
    )
    assert s.disk("d/a.txt").read_bytes() == b"source"
    assert s.disk("a.txt").read_bytes() == b"source and more"


@pytest.mark.parametrize(("box", "overwrite"), [("lb-replacing", False), ("lb-no-posix", True)])
def test_a_remote_move_by_copy_keeps_a_source_written_to_during_the_copy(
    env, monkeypatch, box, overwrite
):
    """lb-replacing has no hard links and lb-no-posix no posix-rename: both move a file
    by copying it, the first to a free name, the second over an old file."""
    s = side(env, box)
    s.disk("a.txt").write_bytes(b"source")
    os.utime(s.disk("a.txt"), (1_600_000_000, 1_600_000_000))
    s.disk("d").mkdir()
    if overwrite:
        s.disk("d/a.txt").write_bytes(b"old")
    _append_before_commit(monkeypatch, False, s.disk("a.txt"))

    resp = single(s, "move", "a.txt", overwrite=overwrite)

    assert (resp.status_code, resp.json()) == (
        500,
        {"detail": source_changed("/d/a.txt"), "reason": "failed"},
    )
    assert s.disk("d/a.txt").read_bytes() == b"source"
    assert s.disk("a.txt").read_bytes() == b"source and more"


# --- a rename that only changes letter case on a server that ignores case --------------


@pytest.mark.parametrize("box", ["lb-case-blind"])
@pytest.mark.parametrize("overwrite", [False, True])
def test_a_case_only_rename_works_on_a_case_blind_server(env, box, overwrite):
    """Before: `lstat` of the new spelling found the source itself, so the rename
    answered 409 where the old plain rename worked."""
    s = side(env, box)
    s.disk("Readme.md").write_bytes(b"read me")

    resp = single(s, "rename", "Readme.md", "README.md", overwrite=overwrite)

    assert (resp.status_code, resp.json()["path"]) == (200, "/README.md")
    assert files_under(s.base) == {"README.md": b"read me", "home": "dir"}


@pytest.mark.parametrize("box", ["lb-refusing", "lb-replacing"])
def test_a_case_only_rename_onto_a_real_second_file_follows_the_rule(env, box):
    s = side(env, box)
    s.disk("Readme.md").write_bytes(b"read me")
    s.disk("README.md").write_bytes(b"precious")
    before = files_under(s.base)

    resp = single(s, "rename", "Readme.md", "README.md")

    assert (resp.status_code, resp.json()) == (409, EXISTS)
    assert files_under(s.base) == before


# --- a refused folder-into-itself leaves nothing, also for a destination not made yet ---


@pytest.mark.parametrize("box", BOXES)
@pytest.mark.parametrize("op", ["batch/copy", "batch/move", "move", "copy"])
@pytest.mark.parametrize("into", ["a/new", "a/new/deeper"])
def test_a_folder_into_a_missing_folder_under_itself_leaves_nothing(env, box, op, into):
    """Before: the missing destination was made first, so the refusal left an empty
    folder inside the source."""
    s = side(env, box)
    s.disk("a/sub").mkdir(parents=True)
    s.disk("a/top.txt").write_bytes(b"top")
    before = files_under(s.base)

    if op in ("move", "copy"):
        resp = s.post(op, {"source": s.path("a"), "destination": s.path(into)})
        assert (resp.status_code, resp.json()) == (
            400,
            {"detail": INTO_ITSELF, "reason": "cannot_replace"},
        )
    else:
        resp = s.post(op, {"paths": [s.path("a")], "destination": s.path(into)})
        assert (resp.status_code, resp.json()) == (
            200,
            {
                "status": "partial",
                "succeeded": [],
                "failed": [{"path": s.path("a"), "error": INTO_ITSELF, "reason": "cannot_replace"}],
            },
        )
    assert files_under(s.base) == before


def test_a_batch_makes_its_destination_for_the_item_that_passed_the_guard(local_env):
    s = side(local_env, "local")
    s.disk("a").mkdir()
    s.disk("b.txt").write_bytes(b"bee")

    resp = s.post(
        "batch/copy", {"paths": [s.path("a"), s.path("b.txt")], "destination": s.path("a/new")}
    )

    assert (resp.status_code, resp.json()) == (
        200,
        {
            "status": "partial",
            "succeeded": [s.path("b.txt")],
            "failed": [{"path": s.path("a"), "error": INTO_ITSELF, "reason": "cannot_replace"}],
        },
    )
    assert files_under(s.base) == {
        "a": "dir",
        "a/new": "dir",
        "a/new/b.txt": b"bee",
        "b.txt": b"bee",
    }


def test_a_batch_whose_destination_cannot_be_made_fails_each_item(local_env):
    """The destination is under a file, so no folder can be made there."""
    s = side(local_env, "local")
    s.disk("a.txt").write_bytes(b"a")
    s.disk("b.txt").write_bytes(b"bee")
    before = files_under(s.base)

    resp = s.post(
        "batch/copy",
        {"paths": [s.path("a.txt"), s.path("b.txt")], "destination": s.path("b.txt/in")},
    )

    reason = f"Not a directory: {s.path('b.txt/in')}"
    assert (resp.status_code, resp.json()) == (
        200,
        {
            "status": "partial",
            "succeeded": [],
            "failed": [
                {"path": s.path("a.txt"), "error": reason, "reason": "not_found"},
                {"path": s.path("b.txt"), "error": reason, "reason": "not_found"},
            ],
        },
    )
    assert files_under(s.base) == before


# --- a replace over SFTP gives the replaced file's exact permission bits ---------------


@pytest.fixture
def umask_022():
    """The server in these tests runs in this process and hands the mode to ``os.open``,
    so the process umask is the server's."""
    old = os.umask(0o022)
    try:
        yield
    finally:
        os.umask(old)


@pytest.mark.parametrize("box", ["lb-refusing", "lb-no-posix"])
@pytest.mark.parametrize("mode", [0o664, 0o775])
@pytest.mark.parametrize("op", ["copy", "batch/copy"])
def test_a_remote_replace_keeps_group_write_under_umask_022(env, umask_022, box, mode, op):
    """Before: the temp file was opened with the wanted mode and never chmodded, so the
    server's umask narrowed 0664 to 0644 and 0775 to 0755."""
    s = side(env, box)
    s.disk("d").mkdir()
    s.disk("a.txt").write_bytes(b"source")
    s.disk("d/a.txt").write_bytes(b"old")
    s.disk("d/a.txt").chmod(mode)

    resp = (
        single(s, "copy", "a.txt", overwrite=True)
        if op == "copy"
        else batch(s, "copy", ["a.txt"], overwrite=True)
    )

    assert resp.status_code == 200, resp.text
    assert s.disk("d/a.txt").read_bytes() == b"source"
    assert s.disk("d/a.txt").stat().st_mode & 0o7777 == mode


@pytest.mark.parametrize("server", ["refusing-rename", "no-posix-rename"])
@pytest.mark.parametrize("mode", [0o664, 0o775])
def test_a_transfer_to_a_box_keeps_group_write_of_the_replaced_file(env, umask_022, server, mode):
    a, b = side(env, "local"), side(env, REMOTE)
    a.disk("x.txt").write_bytes(b"source x")
    b.disk("in").mkdir()
    b.disk("in/x.txt").write_bytes(b"old")
    b.disk("in/x.txt").chmod(mode)

    events = transfer(env, "local", REMOTE, [a.path("x.txt")], b.path("in"), overwrite=True)

    assert events[-1] == ("done", {"succeeded": [a.path("x.txt")], "failed": []})
    assert b.disk("in/x.txt").read_bytes() == b"source x"
    assert b.disk("in/x.txt").stat().st_mode & 0o7777 == mode


# --- one rule for the bits a replace keeps: the nine permission bits ------------------


@pytest.mark.parametrize("box", [*BOXES, "lb-no-posix"])
@pytest.mark.parametrize("op", ["rename", "move", "copy", "batch/move", "batch/copy"])
def test_a_replace_never_carries_setuid_to_the_new_file(env, box, op):
    s = side(env, box)
    s.disk("a.txt").write_bytes(b"source")
    s.disk("a.txt").chmod(0o600)
    target = "b.txt" if op == "rename" else "d/a.txt"
    s.disk("d").mkdir()
    s.disk(target).write_bytes(b"old")
    s.disk(target).chmod(0o4755)

    if op == "rename":
        resp = single(s, "rename", "a.txt", "b.txt", overwrite=True)
    elif op.startswith("batch"):
        resp = batch(s, op.split("/")[1], ["a.txt"], overwrite=True)
    else:
        resp = single(s, op, "a.txt", overwrite=True)

    assert resp.status_code == 200, resp.text
    assert s.disk(target).read_bytes() == b"source"
    assert s.disk(target).stat().st_mode & 0o7777 == 0o755


@pytest.mark.parametrize("server", ["refusing-rename"])
@pytest.mark.parametrize("src", ["local", REMOTE])
def test_a_transfer_replace_never_carries_setuid_to_the_new_file(env, server, src):
    dest = REMOTE if src == "local" else "local"
    a, b = side(env, src), side(env, dest)
    a.disk("x.txt").write_bytes(b"source x")
    b.disk("in").mkdir()
    b.disk("in/x.txt").write_bytes(b"old")
    b.disk("in/x.txt").chmod(0o4755)

    events = transfer(env, src, dest, [a.path("x.txt")], b.path("in"), overwrite=True)

    assert events[-1] == ("done", {"succeeded": [a.path("x.txt")], "failed": []})
    assert b.disk("in/x.txt").stat().st_mode & 0o7777 == 0o755


# --- a move pays for no check that cannot find anything -------------------------------


@pytest.mark.parametrize("box", ["lb-counting"])
def test_a_remote_file_rename_in_its_own_folder_is_four_requests(env, box):
    """Before: the move by hard link ended in one more `lstat` of the source."""
    s = side(env, box)
    s.disk("a.txt").write_bytes(b"source")
    COUNTED_REQUESTS.clear()

    resp = single(s, "rename", "a.txt", "b.txt")

    assert (resp.status_code, resp.json()["path"]) == (200, "/b.txt")
    assert COUNTED_REQUESTS == ["lstat", "lstat", "link", "remove"]
    assert files_under(s.base) == {"b.txt": b"source", "home": "dir"}


@pytest.mark.parametrize("box", ["lb-counting"])
def test_a_remote_folder_rename_in_its_own_folder_sends_no_realpath(env, box):
    s = side(env, box)
    s.disk("a").mkdir()
    s.disk("a/f.txt").write_bytes(b"f")
    COUNTED_REQUESTS.clear()

    resp = single(s, "rename", "a", "b")

    assert (resp.status_code, resp.json()["path"]) == (200, "/b")
    assert COUNTED_REQUESTS == ["lstat", "lstat", "mkdir", "posix_rename"]
    assert files_under(s.base) == {"b": "dir", "b/f.txt": b"f", "home": "dir"}


# --- a folder moved across file systems is kept when anything in it changed ------------


def _cross_device_tree(s: Side) -> None:
    """A folder with a file, a sub folder with a file, an empty folder, a link to a
    folder and a link to nothing."""
    s.disk("tree/sub").mkdir(parents=True)
    s.disk("tree/one.txt").write_bytes(b"one")
    s.disk("tree/sub/deep.txt").write_bytes(b"deep")
    s.disk("tree/empty").mkdir()
    s.disk("tree/to-sub").symlink_to("sub")
    s.disk("tree/to-nothing").symlink_to("gone")
    s.disk("d").mkdir()


@pytest.mark.parametrize("change", ["append", "add"])
def test_a_cross_device_folder_move_keeps_a_source_changed_in_a_sub_folder(
    local_env, monkeypatch, change
):
    """Before: only the folder's own size and mtime were compared, and they change only
    when a direct entry comes or goes, so the removal took bytes that were not copied."""
    s = side(local_env, "local")
    _cross_device_tree(s)
    _no_rename_across(monkeypatch, s.disk("tree"))
    real_copytree = shutil.copytree

    def copy2(src, dst, *args, **kwargs):
        result = shutil.copy2(src, dst, *args, **kwargs)
        if os.fspath(src) == str(s.disk("tree/sub/deep.txt")):
            if change == "append":
                with open(src, "ab") as handle:
                    handle.write(b" and more")
            else:
                s.disk("tree/sub/new.txt").write_bytes(b"new")
        return result

    def copytree(src, dst, *args, **kwargs):
        # The route's call names its arguments; copytree's own calls for sub folders
        # pass them by position, the copy function among them.
        if not args:
            kwargs["copy_function"] = copy2
        return real_copytree(src, dst, *args, **kwargs)

    monkeypatch.setattr(shutil, "copytree", copytree)

    resp = s.post("move", {"source": s.path("tree"), "destination": s.path("d")})

    assert (resp.status_code, resp.json()) == (
        500,
        {"detail": source_changed(s.path("d/tree")), "reason": "failed"},
    )
    expected_deep = b"deep and more" if change == "append" else b"deep"
    assert s.disk("tree/sub/deep.txt").read_bytes() == expected_deep
    assert s.disk("tree/sub/new.txt").exists() is (change == "add")
    assert s.disk("tree/one.txt").read_bytes() == b"one"
    assert s.disk("d/tree/one.txt").read_bytes() == b"one"


def test_a_cross_device_folder_move_of_an_unchanged_tree_removes_the_source(local_env, monkeypatch):
    s = side(local_env, "local")
    _cross_device_tree(s)
    _no_rename_across(monkeypatch, s.disk("tree"))

    resp = s.post("move", {"source": s.path("tree"), "destination": s.path("d")})

    assert (resp.status_code, resp.json()["path"]) == (200, s.path("d/tree"))
    assert files_under(s.base) == {
        "d": "dir",
        "d/tree": "dir",
        "d/tree/empty": "dir",
        "d/tree/one.txt": b"one",
        "d/tree/sub": "dir",
        "d/tree/sub/deep.txt": b"deep",
        "d/tree/to-nothing": "-> gone",
        "d/tree/to-sub": "-> sub",
    }


# --- the local fingerprint includes the inode ------------------------------------------


def test_a_cross_device_move_keeps_a_source_replaced_by_a_file_of_equal_size_and_time(
    local_env, monkeypatch
):
    s = side(local_env, "local")
    s.disk("a.txt").write_bytes(b"source")
    s.disk("d").mkdir()
    real_link = os.link

    def no_link(src, dst, *args, **kwargs):
        if str(src) == str(s.disk("a.txt")):
            raise OSError(errno.EXDEV, "Invalid cross-device link")
        return real_link(src, dst, *args, **kwargs)

    monkeypatch.setattr(os, "link", no_link)
    real = local_files_api._LocalLanding.commit

    def commit(self):
        found = os.stat(s.disk("a.txt"))
        other = s.disk("other.tmp")
        other.write_bytes(b"SOURCE")
        os.utime(other, ns=(found.st_atime_ns, found.st_mtime_ns))
        os.replace(other, s.disk("a.txt"))
        real(self)

    monkeypatch.setattr(local_files_api._LocalLanding, "commit", commit)

    resp = single(s, "move", "a.txt")

    assert (resp.status_code, resp.json()) == (
        500,
        {"detail": source_changed(s.path("d/a.txt")), "reason": "failed"},
    )
    assert s.disk("d/a.txt").read_bytes() == b"source"
    assert s.disk("a.txt").read_bytes() == b"SOURCE"


# --- a failed local write removes its partial file even when closing fails too ---------


class _HandleWhoseCloseFails:
    def __init__(self, handle) -> None:
        self.handle = handle

    def close(self) -> None:
        self.handle.close()
        raise OSError(errno.ENOSPC, "No space left on device")


def test_a_local_write_that_fails_removes_its_partial_file_when_close_fails_too(
    local_env, monkeypatch
):
    """Before: the close in the cleanup raised again and the unlink was never reached,
    so the truncated file stayed under the final name and a retry answered 409."""
    s = side(local_env, "local")
    s.disk("a.txt").write_bytes(b"source")
    s.disk("d").mkdir()

    def commit(self):
        self.handle.write(b"so")
        self.handle = _HandleWhoseCloseFails(self.handle)
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(local_files_api._LocalLanding, "commit", commit)

    resp = single(s, "copy", "a.txt")

    assert (resp.status_code, resp.json()) == (
        500,
        {"detail": "No space left on device", "reason": "failed"},
    )
    assert files_under(s.base) == {"a.txt": b"source", "d": "dir"}


# --- a local copy's new name is checked like every other new name ---------------------


@pytest.mark.parametrize(
    ("new_name", "message"),
    [
        ("a/b", "Filename cannot contain path separators"),
        ("..", "Filename cannot be '.' or '..'"),
    ],
)
def test_a_local_copy_validates_its_new_name(local_env, new_name, message):
    """Before: the name went into the target path unchecked."""
    s = side(local_env, "local")
    s.disk("a.txt").write_bytes(b"source")
    s.disk("d").mkdir()
    before = files_under(s.base)

    resp = s.post(
        "copy", {"source": s.path("a.txt"), "destination": s.path("d"), "new_name": new_name}
    )

    assert (resp.status_code, resp.json()) == (400, {"detail": message, "reason": "invalid"})
    assert files_under(s.base) == before


# --- the response of a refused single operation, and the paths a transfer answers with ---


@pytest.mark.parametrize("box", BOXES)
@pytest.mark.parametrize("op", ["rename", "move", "copy"])
@pytest.mark.parametrize("overwrite", [False, True])
def test_a_refused_single_operation_says_why_and_a_folder_in_the_way_cannot_be_replaced(
    env, box, op, overwrite
):
    """Before: the body was only ``detail``, so a view could not tell a name it may ask
    to replace from a folder it never can."""
    s = side(env, box)
    s.disk("a.txt").write_bytes(b"source")
    s.disk("taken.txt").write_bytes(b"precious")
    if op != "rename":
        s.disk("d/a.txt").mkdir(parents=True)
        s.disk("d/taken.txt").write_bytes(b"precious")
    else:
        s.disk("folder").mkdir()
    free = {"overwrite": True} if overwrite else {}
    before = files_under(s.base)

    onto_folder = single(s, op, "a.txt", "folder" if op == "rename" else "d", **free)
    onto_file = single(
        s,
        op,
        "a.txt" if op == "rename" else "taken.txt",
        "taken.txt" if op == "rename" else "d",
        **free,
    )

    expected_folder = (
        (400, {"detail": "Only a regular file can be replaced", "reason": "cannot_replace"})
        if overwrite
        else (409, {"detail": "File already exists", "reason": "cannot_replace"})
    )
    assert (onto_folder.status_code, onto_folder.json()) == expected_folder
    if not overwrite:
        assert (onto_file.status_code, onto_file.json()) == (
            409,
            {"detail": "File already exists", "reason": "exists"},
        )
    if not overwrite and op != "rename":
        assert files_under(s.base) == before


@pytest.mark.parametrize("box", BOXES)
def test_a_rename_onto_a_link_says_it_cannot_be_replaced(env, box):
    s = side(env, box)
    s.disk("a.txt").write_bytes(b"source")
    s.disk("target.txt").write_bytes(b"other")
    os.symlink("target.txt", s.disk("link.txt"))

    resp = single(s, "rename", "a.txt", "link.txt")

    assert (resp.status_code, resp.json()) == (
        409,
        {"detail": "File already exists", "reason": "cannot_replace"},
    )


def _through_a_linked_folder(s: Side) -> tuple[str, str]:
    """``real/`` holds the files; ``link`` is a link to it. Returns the two paths as a
    pane would show them, through the link."""
    s.disk("real").mkdir()
    s.disk("real/a.txt").write_bytes(b"source a")
    s.disk("real/b.txt").write_bytes(b"source b")
    os.symlink(s.disk("real"), s.disk("link"))
    return str(s.disk("link/a.txt")), str(s.disk("link/b.txt"))


@pytest.mark.parametrize("server", ["refusing-rename"])
@pytest.mark.parametrize("mode", ["copy", "move"])
def test_a_cross_box_transfer_answers_with_the_paths_it_was_sent(env, server, mode):
    """Before: a local source was resolved first and the answer carried the resolved path,
    which no row of the pane matched."""
    a, b = side(env, "local"), side(env, REMOTE)
    sent_a, sent_b = _through_a_linked_folder(a)
    b.disk("in").mkdir()
    b.disk("in/a.txt").write_bytes(b"precious")

    events = transfer(env, "local", REMOTE, [sent_a, sent_b], "/in", mode=mode)

    assert events[-1] == (
        "done",
        {
            "succeeded": [sent_b],
            "failed": [{"path": sent_a, "error": "File already exists", "reason": "exists"}],
        },
    )
    # The refused item is said when it is refused, before the next item's progress.
    assert [(kind, data.get("file") or data.get("path")) for kind, data in events[:-1]] == [
        ("failed", sent_a),
        ("progress", sent_b),
    ]
    assert events[0][1] == {"path": sent_a, "error": "File already exists", "reason": "exists"}
    assert b.disk("in/a.txt").read_bytes() == b"precious"
    assert b.disk("in/b.txt").read_bytes() == b"source b"
    # A move removes the source it copied, through the link, and keeps the refused one.
    assert a.disk("real/a.txt").read_bytes() == b"source a"
    assert a.disk("real/b.txt").exists() == (mode == "copy")


@pytest.mark.parametrize("op", ["copy", "move"])
def test_a_same_box_batch_answers_with_the_paths_it_was_sent(local_env, op):
    s = side(local_env, "local")
    sent_a, sent_b = _through_a_linked_folder(s)
    s.disk("d").mkdir()
    s.disk("d/a.txt").write_bytes(b"precious")

    resp = s.post(f"batch/{op}", {"paths": [sent_a, sent_b], "destination": s.path("d")})

    assert resp.status_code == 200
    assert resp.json() == {
        "status": "partial",
        "succeeded": [sent_b],
        "failed": [{"path": sent_a, "error": "File already exists", "reason": "exists"}],
    }


# --- a file placed under a new name has the source's permission bits -------------------


@pytest.fixture
def set_umask():
    """Set the process umask (the SFTP server here runs in this process, so it is the
    server's too) and restore it afterwards."""
    old = os.umask(0o022)

    def apply(value: int) -> None:
        os.umask(value)

    try:
        yield apply
    finally:
        os.umask(old)


TRANSFER_ENDS = [("local", REMOTE), (REMOTE, "local")]


@pytest.mark.parametrize("server", ["refusing-rename"])
@pytest.mark.parametrize(("src", "dest"), TRANSFER_ENDS)
@pytest.mark.parametrize("mode", ["copy", "move"])
@pytest.mark.parametrize(
    ("source_bits", "umask", "arrives"),
    [
        (0o600, 0o022, 0o600),
        (0o755, 0o077, 0o755),
        (0o664, 0o022, 0o664),
        (0o4755, 0o022, 0o755),
        (0o400, 0o022, 0o400),
    ],
)
def test_a_transfer_to_a_new_name_gives_the_file_the_source_permission_bits(
    env, set_umask, server, src, dest, mode, source_bits, umask, arrives
):
    a, b = side(env, src), side(env, dest)
    a.disk("x.txt").write_bytes(b"source x")
    a.disk("x.txt").chmod(source_bits)
    b.disk("in").mkdir()
    set_umask(umask)

    events = transfer(env, src, dest, [a.path("x.txt")], b.path("in"), mode=mode)

    assert events[-1] == ("done", {"succeeded": [a.path("x.txt")], "failed": []})
    assert b.disk("in/x.txt").read_bytes() == b"source x"
    assert b.disk("in/x.txt").stat().st_mode & 0o7777 == arrives
    if mode == "move":
        assert a.disk("x.txt").exists() is False


@pytest.mark.parametrize("server", ["refusing-rename"])
@pytest.mark.parametrize(("src", "dest"), TRANSFER_ENDS)
@pytest.mark.parametrize(("source_bits", "umask"), [(0o600, 0o000), (0o755, 0o077)])
def test_a_transfer_file_has_the_source_bits_at_every_write(
    env, monkeypatch, set_umask, server, src, dest, source_bits, umask
):
    """The mode of the file being written, read at each write: the source's bits from
    the first byte on, so a 0600 file is never readable by another user."""
    a, b = side(env, src), side(env, dest)
    a.disk("x.txt").write_bytes(b"source x")
    a.disk("x.txt").chmod(source_bits)
    b.disk("in").mkdir()
    set_umask(umask)
    seen: list[int] = []

    def watch(owner):
        real = owner.write

        def write(self, data):
            on_disk = Path(self.target) if dest == "local" else env.root / self.target.lstrip("/")
            seen.append(on_disk.stat().st_mode & 0o7777)
            return real(self, data)

        monkeypatch.setattr(owner, "write", write)

    watch(local_files_api._LocalLanding if dest == "local" else remote_files_api._RemoteLanding)

    events = transfer(env, src, dest, [a.path("x.txt")], b.path("in"))

    assert events[-1] == ("done", {"succeeded": [a.path("x.txt")], "failed": []})
    assert seen == [source_bits]


@pytest.mark.parametrize("box", ["lb-refusing", "lb-replacing"])
@pytest.mark.parametrize("mode", [0o664, 0o775, 0o400])
@pytest.mark.parametrize("op", ["copy", "batch/copy"])
def test_a_remote_copy_to_a_new_name_gives_exactly_the_source_bits_under_umask_022(
    env, umask_022, box, mode, op
):
    """The bits go on the open's attributes, which the server's umask narrows; they
    are set again exactly before the first byte."""
    s = side(env, box)
    s.disk("d").mkdir()
    s.disk("a.txt").write_bytes(b"source")
    s.disk("a.txt").chmod(mode)

    resp = single(s, "copy", "a.txt") if op == "copy" else batch(s, "copy", ["a.txt"])

    assert resp.status_code == 200, resp.text
    assert s.disk("d/a.txt").read_bytes() == b"source"
    assert s.disk("d/a.txt").stat().st_mode & 0o7777 == mode


@pytest.mark.parametrize("box", BOXES)
@pytest.mark.parametrize("op", ["copy", "batch/copy", "move", "batch/move"])
def test_a_replaced_file_with_no_permission_bits_keeps_none(env, box, op):
    """A file locked with `chmod 000` stays locked when it is replaced."""
    s = side(env, box)
    s.disk("a.txt").write_bytes(b"source")
    s.disk("a.txt").chmod(0o644)
    s.disk("d").mkdir()
    s.disk("d/a.txt").write_bytes(b"old")
    s.disk("d/a.txt").chmod(0o000)

    if op.startswith("batch"):
        resp = batch(s, op.split("/")[1], ["a.txt"], overwrite=True)
    else:
        resp = single(s, op, "a.txt", overwrite=True)

    assert resp.status_code == 200, resp.text
    assert s.disk("d/a.txt").stat().st_mode & 0o7777 == 0o000
    s.disk("d/a.txt").chmod(0o600)
    assert s.disk("d/a.txt").read_bytes() == b"source"


@pytest.mark.parametrize("server", ["refusing-rename"])
@pytest.mark.parametrize(("src", "dest"), TRANSFER_ENDS)
def test_a_file_with_no_permission_bits_replaced_by_a_transfer_keeps_none(env, server, src, dest):
    a, b = side(env, src), side(env, dest)
    a.disk("x.txt").write_bytes(b"source x")
    a.disk("x.txt").chmod(0o644)
    b.disk("in").mkdir()
    b.disk("in/x.txt").write_bytes(b"old")
    b.disk("in/x.txt").chmod(0o000)

    events = transfer(env, src, dest, [a.path("x.txt")], b.path("in"), overwrite=True)

    assert events[-1] == ("done", {"succeeded": [a.path("x.txt")], "failed": []})
    assert b.disk("in/x.txt").stat().st_mode & 0o7777 == 0o000
    b.disk("in/x.txt").chmod(0o600)
    assert b.disk("in/x.txt").read_bytes() == b"source x"


@pytest.mark.parametrize("op", ["copy", "batch/copy"])
def test_a_local_copy_has_the_source_bits_from_its_first_byte(
    local_env, monkeypatch, umask_022, op
):
    """A 0600 file copied on the local box is never readable by another user: the new
    file has the source's bits before the copy writes to it."""
    s = side(local_env, "local")
    s.disk("d").mkdir()
    s.disk("a.txt").write_bytes(b"source")
    s.disk("a.txt").chmod(0o600)
    seen: list[int] = []
    real = shutil.copyfileobj

    def copyfileobj(src, dst, *rest):
        seen.append(os.fstat(dst.fileno()).st_mode & 0o7777)
        return real(src, dst, *rest)

    monkeypatch.setattr(local_files_api.shutil, "copyfileobj", copyfileobj)

    resp = single(s, "copy", "a.txt") if op == "copy" else batch(s, "copy", ["a.txt"])

    assert resp.status_code == 200, resp.text
    assert seen == [0o600]
    assert s.disk("d/a.txt").read_bytes() == b"source"
    assert s.disk("d/a.txt").stat().st_mode & 0o7777 == 0o600


def test_a_local_copy_keeps_the_times_and_the_extended_attributes(local_env):
    s = side(local_env, "local")
    s.disk("d").mkdir()
    s.disk("a.txt").write_bytes(b"source")
    try:
        os.setxattr(s.disk("a.txt"), "user.note", b"kept")
    except OSError:
        pytest.skip("this file system keeps no user attributes")
    os.utime(s.disk("a.txt"), ns=(1_600_000_000_000_000_000, 1_600_000_001_000_000_000))

    resp = single(s, "copy", "a.txt")

    assert resp.status_code == 200, resp.text
    assert os.getxattr(s.disk("d/a.txt"), "user.note") == b"kept"
    assert s.disk("d/a.txt").stat().st_mtime_ns == 1_600_000_001_000_000_000


@pytest.mark.parametrize("box", BOXES)
@pytest.mark.parametrize("op", ["copy", "batch/copy"])
def test_a_copy_to_a_new_name_never_carries_setuid(env, umask_022, box, op):
    s = side(env, box)
    s.disk("d").mkdir()
    s.disk("a.txt").write_bytes(b"source")
    s.disk("a.txt").chmod(0o4755)

    resp = single(s, "copy", "a.txt") if op == "copy" else batch(s, "copy", ["a.txt"])

    assert resp.status_code == 200, resp.text
    assert s.disk("d/a.txt").read_bytes() == b"source"
    assert s.disk("d/a.txt").stat().st_mode & 0o7777 == 0o755


@pytest.mark.parametrize("op", ["rename", "move", "batch/move"])
def test_a_local_move_over_a_file_has_the_replaced_bits_when_it_takes_the_name(
    local_env, monkeypatch, op
):
    """A 0666 file moved over a 0600 one is never at that name with its own bits."""
    s = side(local_env, "local")
    s.disk("a.txt").write_bytes(b"source")
    s.disk("a.txt").chmod(0o666)
    target = "b.txt" if op == "rename" else "d/a.txt"
    s.disk("d").mkdir()
    s.disk(target).write_bytes(b"old")
    s.disk(target).chmod(0o600)
    seen: list[int] = []
    real = os.replace

    def replace(src, dst, **rest):
        seen.append(os.stat(src).st_mode & 0o7777)
        return real(src, dst, **rest)

    monkeypatch.setattr(local_files_api.os, "replace", replace)

    if op == "rename":
        resp = single(s, "rename", "a.txt", "b.txt", overwrite=True)
    elif op.startswith("batch"):
        resp = batch(s, "move", ["a.txt"], overwrite=True)
    else:
        resp = single(s, "move", "a.txt", overwrite=True)

    assert resp.status_code == 200, resp.text
    assert seen == [0o600]
    assert s.disk(target).read_bytes() == b"source"
    assert s.disk(target).stat().st_mode & 0o7777 == 0o600


@pytest.mark.parametrize("box", ["lb-no-setstat"])
@pytest.mark.parametrize("op", ["copy", "batch/copy"])
def test_a_server_that_cannot_set_bits_still_takes_a_copy(env, umask_022, box, op):
    """The new file keeps the bits its open asked for, narrowed by the server's umask:
    never wider than the source's."""
    s = side(env, box)
    s.disk("d").mkdir()
    s.disk("a.txt").write_bytes(b"source")
    s.disk("a.txt").chmod(0o664)

    resp = single(s, "copy", "a.txt") if op == "copy" else batch(s, "copy", ["a.txt"])

    assert resp.status_code == 200, resp.text
    assert s.disk("d/a.txt").read_bytes() == b"source"
    assert s.disk("d/a.txt").stat().st_mode & 0o7777 == 0o644


@pytest.mark.parametrize("server", ["no-setstat"])
def test_a_server_that_cannot_set_bits_still_takes_a_transfer_and_a_replace(env, umask_022, server):
    a, b = side(env, "local"), side(env, REMOTE)
    a.disk("x.txt").write_bytes(b"source x")
    a.disk("x.txt").chmod(0o600)
    a.disk("y.txt").write_bytes(b"source y")
    b.disk("in").mkdir()
    b.disk("in/y.txt").write_bytes(b"old")
    b.disk("in/y.txt").chmod(0o640)

    events = transfer(
        env, "local", REMOTE, [a.path("x.txt"), a.path("y.txt")], b.path("in"), overwrite=True
    )

    assert events[-1] == ("done", {"succeeded": [a.path("x.txt"), a.path("y.txt")], "failed": []})
    assert b.disk("in/x.txt").read_bytes() == b"source x"
    assert b.disk("in/x.txt").stat().st_mode & 0o7777 == 0o600
    assert b.disk("in/y.txt").read_bytes() == b"source y"
    assert b.disk("in/y.txt").stat().st_mode & 0o7777 == 0o640


@pytest.mark.parametrize("box", ["lb-setstat-fails"])
def test_a_copy_whose_bits_cannot_be_set_leaves_no_file(env, umask_022, box):
    s = side(env, box)
    s.disk("d").mkdir()
    s.disk("a.txt").write_bytes(b"source")
    s.disk("a.txt").chmod(0o664)

    resp = single(s, "copy", "a.txt")

    assert resp.status_code == 500, resp.text
    assert "fsetstat refused" in resp.json()["detail"]
    assert s.disk("d/a.txt").exists() is False
    assert s.disk("a.txt").read_bytes() == b"source"


# --- touch and upload answer every refusal with its reason -----------------------------


def upload_to(s: Side, directory: str, name: str, **extra):
    return s.env.client.post(
        f"/api/v1/boxes/{s.box}/upload",
        data={"directory": s.path(directory), **extra},
        files={"file": (name, b"uploaded", "application/octet-stream")},
        headers=HEADERS,
    )


def make_taken(s: Side, kind: str) -> None:
    s.disk("in").mkdir()
    s.disk("elsewhere.txt").write_bytes(b"keep me")
    if kind == "file":
        s.disk("in/x.txt").write_bytes(b"precious")
    elif kind == "folder":
        s.disk("in/x.txt").mkdir()
    else:
        s.disk("in/x.txt").symlink_to(s.disk("elsewhere.txt"))


@pytest.mark.parametrize("box", BOXES)
@pytest.mark.parametrize(
    ("kind", "answer"),
    [("file", EXISTS), ("folder", TAKEN), ("link", TAKEN)],
)
def test_an_upload_onto_a_taken_name_answers_with_the_reason(env, box, kind, answer):
    s = side(env, box)
    make_taken(s, kind)
    before = files_under(s.base)

    resp = upload_to(s, "in", "x.txt")

    assert (resp.status_code, resp.json()) == (409, answer)
    assert files_under(s.base) == before


@pytest.mark.parametrize("box", BOXES)
@pytest.mark.parametrize("kind", ["folder", "link"])
def test_an_upload_with_overwrite_onto_what_is_not_a_file_answers_with_the_reason(env, box, kind):
    s = side(env, box)
    make_taken(s, kind)
    before = files_under(s.base)

    resp = upload_to(s, "in", "x.txt", overwrite="true")

    assert (resp.status_code, resp.json()) == (400, NOT_REGULAR)
    assert files_under(s.base) == before


@pytest.mark.parametrize("box", BOXES)
@pytest.mark.parametrize(
    ("kind", "answer"),
    [("file", EXISTS), ("folder", TAKEN), ("link", TAKEN)],
)
def test_a_touch_onto_a_taken_name_answers_with_the_reason(env, box, kind, answer):
    s = side(env, box)
    make_taken(s, kind)
    before = files_under(s.base)

    resp = s.post("touch", {"directory": s.path("in"), "filename": "x.txt"})

    assert (resp.status_code, resp.json()) == (400 if box == "local" else 409, answer)
    assert files_under(s.base) == before


# --- an upload that replaces a file lands whole or not at all ---------------------------


FILE_SIZE_LIMIT = 256 * 1024


@contextlib.contextmanager
def _a_write_stops_part_way(monkeypatch: pytest.MonkeyPatch, box: str) -> Iterator[None]:
    """The first big write to a file is cut short and fails, as on a disk that fills or a
    connection that drops. Local: no file of this process can grow past a size limit.
    Remote: the client's write sends half of the bytes, then raises."""
    if box != "local":
        real = asyncssh.SFTPClientFile.write

        async def write(self, data: bytes, offset: int | None = None) -> int:
            await real(self, data[: len(data) // 2], offset)
            raise OSError(errno.ENOSPC, "No space left on device")

        monkeypatch.setattr(asyncssh.SFTPClientFile, "write", write)
        yield
        return
    import resource  # POSIX only, as the module is: imported here, not at collection

    soft, hard = resource.getrlimit(resource.RLIMIT_FSIZE)
    previous = signal.signal(signal.SIGXFSZ, signal.SIG_IGN)
    resource.setrlimit(resource.RLIMIT_FSIZE, (FILE_SIZE_LIMIT, hard))
    try:
        yield
    finally:
        resource.setrlimit(resource.RLIMIT_FSIZE, (soft, hard))
        signal.signal(signal.SIGXFSZ, previous)


@pytest.mark.parametrize("box", BOXES)
@pytest.mark.parametrize("taken", [True, False])
def test_an_upload_that_fails_part_way_leaves_the_old_file_and_no_other_file(
    env, box, taken, monkeypatch
):
    s = side(env, box)
    s.disk("in").mkdir()
    if taken:
        s.disk("in/x.txt").write_bytes(b"precious")
    before = files_under(s.base)
    with _a_write_stops_part_way(monkeypatch, box):
        resp = s.env.client.post(
            f"/api/v1/boxes/{s.box}/upload",
            data={"directory": s.path("in"), "overwrite": "true"},
            files={"file": ("x.txt", b"n" * (3 * FILE_SIZE_LIMIT), "application/octet-stream")},
            headers=HEADERS,
        )

    assert (resp.status_code, resp.json()["reason"]) == (500, "failed")
    assert files_under(s.base) == before


@pytest.mark.parametrize("box", BOXES)
def test_an_upload_into_a_folder_that_takes_no_new_file_keeps_the_old_file_whole(env, box):
    s = side(env, box)
    s.disk("in").mkdir()
    s.disk("in/x.txt").write_bytes(b"precious")
    s.disk("in").chmod(0o555)
    try:
        resp = upload_to(s, "in", "x.txt", overwrite="true")
        found = files_under(s.base)
    finally:
        s.disk("in").chmod(0o755)

    assert (resp.status_code, resp.json()["reason"]) == (403, "denied")
    assert resp.json()["detail"].startswith("Permission denied")
    assert found == {"in": "dir", "in/x.txt": b"precious"} | (
        {"home": "dir"} if box != "local" else {}
    )


@pytest.mark.parametrize("box", BOXES + ["lb-no-posix"])
def test_an_upload_that_replaces_a_file_keeps_its_bits_and_leaves_no_other_file(env, box):
    s = side(env, box)
    s.disk("in").mkdir()
    s.disk("in/x.txt").write_bytes(b"precious")
    s.disk("in/x.txt").chmod(0o640)

    resp = upload_to(s, "in", "x.txt", overwrite="true")

    assert (resp.status_code, resp.json()["path"]) == (200, s.path("in/x.txt"))
    assert (s.disk("in/x.txt").stat().st_mode & 0o7777, files_under(s.disk("in"))) == (
        0o640,
        {"x.txt": b"uploaded"},
    )
