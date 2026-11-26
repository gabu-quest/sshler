"""Compress and extract never write over an existing name unless the caller said replace."""

# ruff: noqa: F811
from __future__ import annotations

import asyncio
import io
import os
import shlex
import tarfile
import warnings
import zipfile
from pathlib import Path

import pytest
from existing_name_support import (
    BOXES,
    EXISTS,
    NOT_REGULAR,
    REMOTE,
    TAKEN,
    Side,
    env,  # noqa: F401
    files_under,
    local_env,  # noqa: F401
    side,
)

from sshler import ssh_pool
from sshler.api import local_files as local_files_api
from sshler.api import remote_files as remote_files_api

pytestmark = pytest.mark.posix_only("hard links, chmod and an SFTP server rooted in a temp folder")


# --- compress never writes over an archive unasked, and lands whole ----------------------

ARCHIVES = [("tar.gz", "o.tar.gz"), ("zip", "o.zip")]


def entries(archive: Path) -> list[str]:
    if archive.name.endswith(".zip"):
        # The files of a zip: a zip made by `zip -r` also lists each folder.
        with zipfile.ZipFile(archive) as opened:
            return sorted(n for n in opened.namelist() if not n.endswith("/"))
    with tarfile.open(archive, "r:gz") as opened:
        return sorted(opened.getnames())


def make_old_archive(s: Side, name: str, member: str = "old.txt") -> Path:
    """A real archive in folder ``d`` that holds one member."""
    s.disk("d").mkdir(exist_ok=True)
    old = s.disk(f"d/{name}")
    source = s.disk("old-source")
    source.mkdir(exist_ok=True)
    (source / member).write_bytes(b"old")
    if name.endswith(".zip"):
        with zipfile.ZipFile(old, "w") as opened:
            opened.write(source / member, member)
    else:
        with tarfile.open(old, "w:gz") as opened:
            opened.add(source / member, arcname=member)
    return old


def compress(s: Side, names: list[str], fmt: str, name: str, **extra):
    return s.post(
        "archive/create",
        {
            "paths": [s.path(n) for n in names],
            "destination": s.path("d"),
            "archive_name": name,
            "format": fmt,
            **extra,
        },
    )


@pytest.mark.parametrize("box", BOXES)
@pytest.mark.parametrize(("fmt", "name"), ARCHIVES)
def test_a_compress_onto_an_existing_archive_is_409_and_changes_nothing(env, box, fmt, name):
    s = side(env, box)
    old = make_old_archive(s, name)
    s.disk("a.txt").write_bytes(b"alpha")
    before = files_under(s.base)
    old_bytes = old.read_bytes()

    resp = compress(s, ["a.txt"], fmt, name)

    assert (resp.status_code, resp.json()) == (409, EXISTS)
    assert old.read_bytes() == old_bytes
    assert files_under(s.base) == before


@pytest.mark.parametrize("box", BOXES)
@pytest.mark.parametrize(("fmt", "name"), ARCHIVES)
def test_a_compress_with_overwrite_replaces_the_archive_and_keeps_no_old_entry(env, box, fmt, name):
    s = side(env, box)
    make_old_archive(s, name)
    s.disk("a.txt").write_bytes(b"alpha")

    resp = compress(s, ["a.txt"], fmt, name, overwrite=True)

    assert (resp.status_code, resp.json()["path"]) == (200, s.path(f"d/{name}"))
    assert entries(s.disk(f"d/{name}")) == ["a.txt"]
    assert sorted(p.name for p in s.disk("d").iterdir()) == [name]


@pytest.mark.parametrize("box", BOXES)
@pytest.mark.parametrize(("fmt", "name"), ARCHIVES)
def test_a_compress_to_a_free_name_makes_the_archive_and_nothing_else(env, box, fmt, name):
    s = side(env, box)
    s.disk("d").mkdir()
    s.disk("a.txt").write_bytes(b"alpha")

    resp = compress(s, ["a.txt"], fmt, name)

    assert (resp.status_code, resp.json()["path"]) == (200, s.path(f"d/{name}"))
    assert entries(s.disk(f"d/{name}")) == ["a.txt"]
    assert sorted(p.name for p in s.disk("d").iterdir()) == [name]


@pytest.mark.parametrize("box", BOXES)
@pytest.mark.parametrize("kind", ["folder", "link"])
@pytest.mark.parametrize("overwrite", [False, True])
def test_a_compress_onto_a_folder_or_a_link_answers_as_an_upload_does(env, box, kind, overwrite):
    s = side(env, box)
    s.disk("d").mkdir()
    s.disk("elsewhere.txt").write_bytes(b"keep me")
    s.disk("a.txt").write_bytes(b"alpha")
    if kind == "folder":
        s.disk("d/o.zip").mkdir()
    else:
        s.disk("d/o.zip").symlink_to(s.disk("elsewhere.txt"))
    before = files_under(s.base)

    resp = compress(s, ["a.txt"], "zip", "o.zip", overwrite=overwrite)

    expected = (400, NOT_REGULAR) if overwrite else (409, TAKEN)
    assert (resp.status_code, resp.json()) == expected
    assert files_under(s.base) == before


@pytest.mark.parametrize("value", ["true", 1, "false", 0])
def test_a_compress_overwrite_must_be_a_json_boolean(local_env, value):
    s = side(local_env, "local")
    s.disk("d").mkdir()
    s.disk("d/o.zip").write_bytes(b"precious")
    s.disk("a.txt").write_bytes(b"alpha")

    resp = compress(s, ["a.txt"], "zip", "o.zip", overwrite=value)

    assert (resp.status_code, resp.json()) == (
        422,
        {"detail": "body.overwrite: Input should be a valid boolean", "reason": "invalid"},
    )
    assert s.disk("d/o.zip").read_bytes() == b"precious"


@pytest.mark.parametrize("box", BOXES)
@pytest.mark.parametrize(("fmt", "name"), ARCHIVES)
@pytest.mark.parametrize("taken", [True, False])
def test_a_compress_that_fails_leaves_the_old_archive_and_no_other_file(env, box, fmt, name, taken):
    s = side(env, box)
    s.disk("d").mkdir()
    if taken:
        make_old_archive(s, name)
    s.disk("a.txt").write_bytes(b"alpha")
    before = files_under(s.base)

    resp = compress(s, ["a.txt", "gone.txt"], fmt, name, overwrite=True)

    expected = (404, "not_found") if box == "local" else (500, "failed")
    assert (resp.status_code, resp.json()["reason"]) == (expected[0], expected[1])
    assert files_under(s.base) == before


@pytest.mark.parametrize("box", BOXES)
@pytest.mark.parametrize(("fmt", "name"), ARCHIVES)
def test_a_compress_of_a_folder_that_holds_the_archive_never_packs_the_unfinished_file(
    env, box, fmt, name
):
    s = side(env, box)
    make_old_archive(s, name)
    s.disk("d/a.txt").write_bytes(b"alpha")

    resp = compress(s, ["d"], fmt, name, overwrite=True)

    assert resp.status_code == 200
    folders = [] if fmt == "zip" else ["d"]
    assert entries(s.disk(f"d/{name}")) == sorted([*folders, "d/a.txt", f"d/{name}"])
    assert sorted(p.name for p in s.disk("d").iterdir()) == sorted(["a.txt", name])


def _pool_holds_a_connection() -> int:
    return ssh_pool.get_pool().stats().get(REMOTE, {}).get("active_connections", 0)


@pytest.mark.parametrize("box", ["lb-refusing"])
def test_a_compress_past_its_limit_is_504_closes_the_connection_and_leaves_no_file(
    env, box, monkeypatch
):
    s = side(env, box)
    s.disk("d").mkdir()
    s.disk("a.txt").write_bytes(b"alpha")

    async def slow(command: str) -> int:
        words = shlex.split(command)
        (env.root / words[2].lstrip("/")).write_bytes(b"partial")
        await asyncio.sleep(30)
        return 0

    env.server.stand_in = slow
    monkeypatch.setattr(remote_files_api, "ARCHIVE_TIMEOUT", 0.3)

    resp = compress(s, ["a.txt"], "tar.gz", "o.tar.gz")

    assert (resp.status_code, resp.json()) == (
        504,
        {"detail": "Archive creation timed out", "reason": "timeout"},
    )
    assert _pool_holds_a_connection() == 0
    assert sorted(p.name for p in s.disk("d").iterdir()) == []


@pytest.mark.parametrize("box", ["lb-refusing"])
def test_an_extract_past_its_limit_is_504_and_closes_the_connection(env, box, monkeypatch):
    s = side(env, box)
    make_old_archive(s, "o.tar.gz")

    async def slow(command: str) -> int:
        await asyncio.sleep(30)
        return 0

    env.server.stand_in = slow
    monkeypatch.setattr(remote_files_api, "ARCHIVE_TIMEOUT", 0.3)

    resp = s.post(
        "archive/extract", {"archive_path": s.path("d/o.tar.gz"), "destination": s.path("out")}
    )

    assert (resp.status_code, resp.json()) == (
        504,
        {"detail": "Extraction timed out", "reason": "timeout"},
    )
    assert _pool_holds_a_connection() == 0


# --- extract writes over nothing unasked, and is all or nothing ---------------------------
#
# Each archive is built here, in the test's temporary folder, and only handed to the
# route. "outside" is a folder beside the destination that must stay as it was.

Member = bytes | str | tuple[str, str]  # bytes: a file; "dir": a folder; ("link"|"hard", target)


def make_archive(path: Path, members: list[tuple[str, Member]]) -> Path:
    """An archive at ``path`` (a ``.zip`` or a ``.tar.gz`` by its name) holding
    ``members`` in order, with the names exactly as given."""
    if path.name.endswith(".zip"):
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)  # zipfile's "Duplicate name"
            with zipfile.ZipFile(path, "w") as opened:
                for name, value in members:
                    if value == "dir":
                        opened.writestr(name + "/", b"")
                    else:
                        assert isinstance(value, bytes)
                        opened.writestr(name, value)
        return path
    with tarfile.open(path, "w:gz") as opened:
        for name, value in members:
            info = tarfile.TarInfo(name)
            if value == "dir":
                info.type, info.mode = tarfile.DIRTYPE, 0o755
                opened.addfile(info)
            elif isinstance(value, tuple):
                info.type = tarfile.SYMTYPE if value[0] == "link" else tarfile.LNKTYPE
                info.linkname = value[1]
                opened.addfile(info)
            else:
                assert isinstance(value, bytes)
                info.size, info.mode = len(value), 0o644
                opened.addfile(info, io.BytesIO(value))
    return path


def extract(s: Side, archive: str, dest: str = "d", **extra):
    return s.post(
        "archive/extract", {"archive_path": s.path(archive), "destination": s.path(dest), **extra}
    )


FORMATS = ["tar.gz", "zip"]

# What the destination holds before the archive below is unpacked into it.
COLLIDING_ARCHIVE: list[tuple[str, Member]] = [
    ("a.txt", b"new a"),
    ("new.txt", b"new"),
    ("sub", "dir"),
    ("sub/b.txt", b"new b"),
    ("sub/new.txt", b"new in sub"),
]


def _destination_with_two_of_its_files(s: Side) -> None:
    s.disk("d/sub").mkdir(parents=True)
    s.disk("d/a.txt").write_bytes(b"old a")
    s.disk("d/a.txt").chmod(0o640)
    s.disk("d/keep.txt").write_bytes(b"keep")
    s.disk("d/sub/b.txt").write_bytes(b"old b")


@pytest.mark.parametrize("box", BOXES)
@pytest.mark.parametrize("fmt", FORMATS)
def test_an_extract_onto_existing_files_is_409_names_them_and_changes_nothing(env, box, fmt):
    s = side(env, box)
    _destination_with_two_of_its_files(s)
    make_archive(s.disk(f"in.{fmt}"), COLLIDING_ARCHIVE)
    before = files_under(s.base)

    resp = extract(s, f"in.{fmt}")

    assert (resp.status_code, resp.json()) == (
        409,
        {
            "detail": "Files of the archive that already exist here (2): a.txt, sub/b.txt",
            "reason": "exists",
            "taken": {"count": 2, "names": ["a.txt", "sub/b.txt"]},
        },
    )
    assert files_under(s.base) == before


@pytest.mark.parametrize("box", BOXES)
@pytest.mark.parametrize("fmt", FORMATS)
def test_an_extract_with_overwrite_replaces_files_and_leaves_the_others(env, box, fmt):
    s = side(env, box)
    _destination_with_two_of_its_files(s)
    make_archive(s.disk(f"in.{fmt}"), COLLIDING_ARCHIVE)

    resp = extract(s, f"in.{fmt}", overwrite=True)

    assert (resp.status_code, resp.json()["path"]) == (200, s.path("d"))
    assert files_under(s.disk("d")) == {
        "a.txt": b"new a",
        "keep.txt": b"keep",
        "new.txt": b"new",
        "sub": "dir",
        "sub/b.txt": b"new b",
        "sub/new.txt": b"new in sub",
    }
    assert s.disk("d/a.txt").stat().st_mode & 0o7777 == 0o640


@pytest.mark.parametrize("box", BOXES)
def test_an_extract_refusal_names_the_first_five_and_counts_all(env, box):
    s = side(env, box)
    s.disk("d").mkdir()
    names = [f"f{i}.txt" for i in range(1, 8)]
    for name in names:
        s.disk(f"d/{name}").write_bytes(b"old")
    make_archive(s.disk("in.zip"), [(name, b"new") for name in names])
    before = files_under(s.base)

    resp = extract(s, "in.zip")

    assert (resp.status_code, resp.json()) == (
        409,
        {
            "detail": "Files of the archive that already exist here (7): "
            "f1.txt, f2.txt, f3.txt, f4.txt, f5.txt and 2 more",
            "reason": "exists",
            "taken": {"count": 7, "names": names[:5]},
        },
    )
    assert files_under(s.base) == before


@pytest.mark.parametrize("box", BOXES)
@pytest.mark.parametrize("kind", ["file over folder", "folder over file", "file over link"])
@pytest.mark.parametrize("overwrite", [False, True])
def test_an_extract_onto_what_cannot_be_replaced_is_refused_whole(env, box, kind, overwrite):
    s = side(env, box)
    s.disk("d").mkdir()
    s.disk("d/a.txt").write_bytes(b"old a")
    s.disk("elsewhere.txt").write_bytes(b"keep me")
    members: list[tuple[str, Member]] = [("a.txt", b"new a")]
    if kind == "file over folder":
        s.disk("d/x").mkdir()
        s.disk("d/x/in.txt").write_bytes(b"in")
        members.append(("x", b"file"))
    elif kind == "folder over file":
        s.disk("d/x").write_bytes(b"file")
        members += [("x", "dir"), ("x/in.txt", b"in")]
    else:
        s.disk("d/x").symlink_to(s.disk("elsewhere.txt"))
        members.append(("x", b"file"))
    make_archive(s.disk("in.zip"), members)
    before = files_under(s.base)

    resp = extract(s, "in.zip", overwrite=overwrite)

    assert (resp.status_code, resp.json()) == (
        400 if overwrite else 409,
        {
            "detail": "Names of the archive taken here by something that cannot be replaced (1): x",
            "reason": "cannot_replace",
            "taken": {"count": 1, "names": ["x"]},
        },
    )
    assert files_under(s.base) == before


ODD_ENTRY_NAMES = ["line\nbreak.txt", 'it\'s "q".txt', "-rf", "-x.txt"]


@pytest.mark.parametrize("box", BOXES)
@pytest.mark.parametrize("fmt", FORMATS)
def test_an_extract_reads_entry_names_from_the_file_system_not_a_listing(env, box, fmt):
    s = side(env, box)
    s.disk("d").mkdir()
    s.disk("d/line\nbreak.txt").write_bytes(b"old")
    make_archive(s.disk(f"in.{fmt}"), [(name, name.encode()) for name in ODD_ENTRY_NAMES])
    before = files_under(s.base)

    refused = extract(s, f"in.{fmt}")
    after_refusal = files_under(s.base)
    replaced = extract(s, f"in.{fmt}", overwrite=True)

    assert (refused.status_code, refused.json()["taken"]) == (
        409,
        {"count": 1, "names": ["line\nbreak.txt"]},
    )
    assert after_refusal == before
    assert replaced.status_code == 200
    assert files_under(s.disk("d")) == {name: name.encode() for name in ODD_ENTRY_NAMES}


@pytest.mark.parametrize("box", BOXES)
def test_a_tar_entry_with_a_parent_reference_after_a_good_one_changes_nothing(env, box):
    s = side(env, box)
    s.disk("d").mkdir()
    s.disk("outside").mkdir()
    s.disk("d/keep.txt").write_bytes(b"keep")
    make_archive(s.disk("in.tar.gz"), [("first.txt", b"first"), ("../outside/x.txt", b"pwned")])
    before = files_under(s.base)

    resp = extract(s, "in.tar.gz")

    assert (resp.status_code, resp.json()["reason"]) == (500, "failed")
    assert files_under(s.base) == before


@pytest.mark.parametrize("box", BOXES)
def test_a_tar_that_writes_through_a_link_pointing_outside_changes_nothing(env, box):
    s = side(env, box)
    s.disk("d").mkdir()
    s.disk("outside").mkdir()
    make_archive(s.disk("in.tar.gz"), [("ln", ("link", "../outside")), ("ln/x.txt", b"pwned")])
    before = files_under(s.base)

    resp = extract(s, "in.tar.gz")

    assert (resp.status_code, resp.json()["reason"]) == (500, "failed")
    assert files_under(s.base) == before


def _cut_short(path: Path) -> None:
    """Keep the first half of an archive whose second member is 200 kB of noise, so the
    first member is whole and the second is cut."""
    data = path.read_bytes()
    path.write_bytes(data[: len(data) // 2])


@pytest.mark.parametrize("box", BOXES)
@pytest.mark.parametrize("fmt", FORMATS)
@pytest.mark.parametrize("dest", ["d", "n/m"])
def test_an_archive_cut_short_leaves_the_destination_as_it_was(env, box, fmt, dest):
    s = side(env, box)
    s.disk("d").mkdir()
    s.disk("d/keep.txt").write_bytes(b"keep")
    archive = make_archive(
        s.disk(f"in.{fmt}"), [("first.txt", b"first"), ("noise.bin", os.urandom(200_000))]
    )
    _cut_short(archive)
    before = files_under(s.base)

    resp = extract(s, f"in.{fmt}", dest)

    assert (resp.status_code, resp.json()["reason"]) == (500, "failed")
    assert files_under(s.base) == before


@pytest.mark.parametrize("box", BOXES)
def test_a_zip_with_two_entries_of_one_name_keeps_the_later_one(env, box):
    s = side(env, box)
    s.disk("d").mkdir()
    make_archive(s.disk("in.zip"), [("dup.txt", b"first"), ("dup.txt", b"second")])

    resp = extract(s, "in.zip")

    assert resp.status_code == 200
    assert files_under(s.disk("d")) == {"dup.txt": b"second"}


@pytest.mark.parametrize("box", BOXES)
def test_an_extract_into_a_folder_that_takes_no_new_file_is_denied(env, box):
    s = side(env, box)
    s.disk("d").mkdir()
    s.disk("d/keep.txt").write_bytes(b"keep")
    make_archive(s.disk("in.tar.gz"), [("a.txt", b"a")])
    s.disk("d").chmod(0o555)
    try:
        before = files_under(s.base)
        resp = extract(s, "in.tar.gz")
        after = files_under(s.base)
    finally:
        s.disk("d").chmod(0o755)

    assert (resp.status_code, resp.json()["reason"]) == (403, "denied")
    assert after == before


@pytest.mark.parametrize("box", BOXES)
@pytest.mark.parametrize("fmt", FORMATS)
def test_an_archive_unpacked_into_its_own_folder_is_kept(env, box, fmt):
    s = side(env, box)
    s.disk("d").mkdir()
    archive = make_archive(s.disk(f"d/o.{fmt}"), [("a.txt", b"a")])
    archive_bytes = archive.read_bytes()

    resp = extract(s, f"d/o.{fmt}")

    assert resp.status_code == 200
    assert files_under(s.disk("d")) == {f"o.{fmt}": archive_bytes, "a.txt": b"a"}


@pytest.mark.parametrize("box", BOXES)
@pytest.mark.parametrize("fmt", FORMATS)
def test_an_archive_that_holds_its_own_name_never_replaces_itself_unasked(env, box, fmt):
    s = side(env, box)
    s.disk("d").mkdir()
    make_archive(s.disk(f"d/o.{fmt}"), [("a.txt", b"a"), (f"o.{fmt}", b"inner")])
    before = files_under(s.base)

    refused = extract(s, f"d/o.{fmt}")
    after_refusal = files_under(s.base)
    replaced = extract(s, f"d/o.{fmt}", overwrite=True)

    assert (refused.status_code, refused.json()["taken"]) == (
        409,
        {"count": 1, "names": [f"o.{fmt}"]},
    )
    assert after_refusal == before
    assert replaced.status_code == 200
    assert files_under(s.disk("d")) == {f"o.{fmt}": b"inner", "a.txt": b"a"}


@pytest.mark.parametrize("box", BOXES)
def test_an_extract_into_a_missing_folder_makes_it(env, box):
    s = side(env, box)
    make_archive(s.disk("in.tar.gz"), [("a.txt", b"a")])

    resp = extract(s, "in.tar.gz", "n/m")

    assert (resp.status_code, resp.json()["path"]) == (200, s.path("n/m"))
    assert files_under(s.disk("n")) == {"m": "dir", "m/a.txt": b"a"}


@pytest.mark.parametrize("box", ["lb-refusing"])
def test_an_extract_past_its_limit_leaves_the_destination_as_it_was(env, box, monkeypatch):
    s = side(env, box)
    s.disk("d").mkdir()
    s.disk("d/keep.txt").write_bytes(b"keep")
    make_archive(s.disk("in.tar.gz"), [("a.txt", b"a")])
    before = files_under(s.base)

    async def slow(command: str) -> int:
        words = shlex.split(command)
        (env.root / words[4].lstrip("/") / "a.txt").write_bytes(b"partial")
        await asyncio.sleep(30)
        return 0

    env.server.stand_in = slow
    monkeypatch.setattr(remote_files_api, "ARCHIVE_TIMEOUT", 0.3)

    resp = extract(s, "in.tar.gz")

    assert (resp.status_code, resp.json()) == (
        504,
        {"detail": "Extraction timed out", "reason": "timeout"},
    )
    assert _pool_holds_a_connection() == 0
    assert files_under(s.base) == before


@pytest.mark.parametrize("value", ["true", 1, "false", 0])
def test_an_extract_overwrite_must_be_a_json_boolean(local_env, value):
    s = side(local_env, "local")
    s.disk("d").mkdir()
    s.disk("d/a.txt").write_bytes(b"precious")
    make_archive(s.disk("in.zip"), [("a.txt", b"new")])

    resp = extract(s, "in.zip", overwrite=value)

    assert (resp.status_code, resp.json()) == (
        422,
        {"detail": "body.overwrite: Input should be a valid boolean", "reason": "invalid"},
    )
    assert s.disk("d/a.txt").read_bytes() == b"precious"


# --- keep both: a new archive, or a new folder for an extract, beside what is there ----

INVALID_BOTH = {"detail": "overwrite and keep_both cannot both be set", "reason": "invalid"}


@pytest.mark.parametrize("box", BOXES)
@pytest.mark.parametrize(("fmt", "name"), ARCHIVES)
def test_a_compress_with_keep_both_lands_under_the_first_free_name(env, box, fmt, name):
    s = side(env, box)
    old = make_old_archive(s, name)
    old_bytes = old.read_bytes()
    s.disk("a.txt").write_bytes(b"alpha")
    landed = name.replace(".", " (2).", 1)

    resp = compress(s, ["a.txt"], fmt, name, keep_both=True)

    assert (resp.status_code, resp.json()["path"]) == (200, s.path(f"d/{landed}"))
    assert resp.json()["message"] == f"Created {landed}"
    assert old.read_bytes() == old_bytes
    assert entries(s.disk(f"d/{landed}")) == ["a.txt"]
    assert sorted(p.name for p in s.disk("d").iterdir()) == sorted([name, landed])


@pytest.mark.parametrize("box", BOXES)
@pytest.mark.parametrize(("fmt", "name"), ARCHIVES)
def test_a_compress_with_keep_both_that_fails_leaves_no_file(env, box, fmt, name):
    s = side(env, box)
    make_old_archive(s, name)
    s.disk("a.txt").write_bytes(b"alpha")
    before = files_under(s.base)

    resp = compress(s, ["a.txt", "gone.txt"], fmt, name, keep_both=True)

    expected = (404, "not_found") if box == "local" else (500, "failed")
    assert (resp.status_code, resp.json()["reason"]) == expected
    assert files_under(s.base) == before


@pytest.mark.parametrize("box", BOXES)
@pytest.mark.parametrize("taken_by", ["nothing", "folder", "file"])
@pytest.mark.parametrize("fmt", FORMATS)
def test_an_extract_with_keep_both_unpacks_into_a_new_folder_named_after_the_archive(
    env, box, taken_by, fmt
):
    s = side(env, box)
    s.disk("d").mkdir()
    s.disk("d/a.txt").write_bytes(b"old a")
    if taken_by == "folder":
        s.disk("d/backup").mkdir()
        s.disk("d/backup/a.txt").write_bytes(b"old in backup")
    elif taken_by == "file":
        s.disk("d/backup").write_bytes(b"a file")
    make_archive(
        s.disk(f"backup.{fmt}"), [("a.txt", b"new a"), ("sub", "dir"), ("sub/b.txt", b"b")]
    )
    before = files_under(s.base)
    folder = "backup" if taken_by == "nothing" else "backup (2)"

    resp = extract(s, f"backup.{fmt}", keep_both=True)

    assert (resp.status_code, resp.json()["path"]) == (200, s.path(f"d/{folder}"))
    after = files_under(s.base)
    added = {k: v for k, v in after.items() if k not in before}
    assert added == {
        f"d/{folder}": "dir",
        f"d/{folder}/a.txt": b"new a",
        f"d/{folder}/sub": "dir",
        f"d/{folder}/sub/b.txt": b"b",
    }
    assert {k: after[k] for k in before} == before


@pytest.mark.parametrize("box", BOXES)
@pytest.mark.parametrize(("archive", "folder"), [("a.tar.gz", "a"), (".zip", ".zip")])
def test_an_extract_with_keep_both_names_the_folder_by_the_archive_without_its_extension(
    env, box, archive, folder
):
    s = side(env, box)
    s.disk("d").mkdir()
    make_archive(s.disk(archive), [("x.txt", b"x")])

    resp = extract(s, archive, keep_both=True)

    assert (resp.status_code, resp.json()["path"]) == (200, s.path(f"d/{folder}"))
    assert s.disk(f"d/{folder}/x.txt").read_bytes() == b"x"


@pytest.mark.parametrize("box", BOXES)
def test_an_extract_with_keep_both_of_an_archive_holding_its_own_name_goes_one_level_deeper(
    env, box
):
    s = side(env, box)
    s.disk("d/backup").mkdir(parents=True)
    s.disk("d/backup/old.txt").write_bytes(b"old")
    make_archive(s.disk("backup.zip"), [("backup", "dir"), ("backup/new.txt", b"new")])

    resp = extract(s, "backup.zip", keep_both=True)

    assert (resp.status_code, resp.json()["path"]) == (200, s.path("d/backup (2)"))
    assert files_under(s.disk("d")) == {
        "backup": "dir",
        "backup/old.txt": b"old",
        "backup (2)": "dir",
        "backup (2)/backup": "dir",
        "backup (2)/backup/new.txt": b"new",
    }


@pytest.mark.parametrize("box", BOXES)
def test_an_extract_with_keep_both_that_the_guard_refuses_leaves_no_folder(env, box):
    s = side(env, box)
    s.disk("d").mkdir()
    s.disk("outside").mkdir()
    make_archive(s.disk("in.tar.gz"), [("first.txt", b"first"), ("../outside/x.txt", b"pwned")])
    before = files_under(s.base)

    resp = extract(s, "in.tar.gz", keep_both=True)

    assert (resp.status_code, resp.json()["reason"]) == (500, "failed")
    assert files_under(s.base) == before


def test_two_local_extracts_of_one_archive_with_keep_both_at_once_make_two_folders(local_env):
    s = side(local_env, "local")
    s.disk("d").mkdir()
    make_archive(s.disk("in.zip"), [("x.txt", b"x")])

    async def both():
        files = local_files_api.LocalFiles()
        return await asyncio.gather(
            files.extract_archive(s.path("in.zip"), s.path("d"), keep_both=True),
            files.extract_archive(s.path("in.zip"), s.path("d"), keep_both=True),
        )

    landed = asyncio.run(both())

    assert sorted(landed) == [s.path("d/in"), s.path("d/in (2)")]
    assert files_under(s.disk("d")) == {
        "in": "dir",
        "in/x.txt": b"x",
        "in (2)": "dir",
        "in (2)/x.txt": b"x",
    }


@pytest.mark.parametrize("box", BOXES)
@pytest.mark.parametrize("route", ["create", "extract"])
def test_an_archive_with_overwrite_and_keep_both_is_refused_and_changes_nothing(env, box, route):
    s = side(env, box)
    make_old_archive(s, "o.zip")
    s.disk("a.txt").write_bytes(b"alpha")
    before = files_under(s.base)

    if route == "create":
        resp = compress(s, ["a.txt"], "zip", "o.zip", overwrite=True, keep_both=True)
    else:
        resp = extract(s, "d/o.zip", overwrite=True, keep_both=True)

    assert (resp.status_code, resp.json()) == (400, INVALID_BOTH)
    assert files_under(s.base) == before
