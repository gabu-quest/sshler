"""Keep both: a destination name that is taken is neither replaced nor refused.

User: "Maybe suggest a suffix or something if not replace?", then "A: `report (2).pdf`".
Every route that takes ``overwrite`` takes ``keep_both`` (a strict boolean, default
false): the new thing lands beside the taken name under the first free name of one
series (``placement.free_name``), taken by the same exclusive call that writes, and the
answer names where it landed. ``overwrite`` and ``keep_both`` together are refused as
``invalid`` before anything is touched.

The remote side runs on the loopback asyncssh servers of ``existing_name_support``:
asyncssh's own, which answers an exclusive open of a taken name with a generic failure
(SFTP version 3 has no "exists" status), and one whose plain rename replaces and that
has no hard links, so a move there copies into a name opened exclusively.
"""

# ruff: noqa: F811
from __future__ import annotations

import pytest
from existing_name_support import (
    BOXES,
    HEADERS,
    REMOTE,
    SERVERS,
    Side,
    env,  # noqa: F401
    files_under,
    side,
    single,
    transfer,
)

from sshler.api import local_files
from sshler.api.batch import APIBatchResult
from sshler.api.placement import DestinationRefused, candidate_paths, free_name

pytestmark = pytest.mark.posix_only("loopback SFTP servers rooted in a temp folder")

INVALID_BOTH = {"detail": "overwrite and keep_both cannot both be set", "reason": "invalid"}


# --- the series of free names ---------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "folder", "number", "expected"),
    [
        ("report.pdf", False, 2, "report (2).pdf"),
        ("report.pdf", False, 3, "report (3).pdf"),
        ("site.tar.gz", False, 2, "site (2).tar.gz"),
        ("site.tar.bz2", False, 2, "site (2).tar.bz2"),
        ("site.tar.xz", False, 2, "site (2).tar.xz"),
        ("site.tar.zst", False, 2, "site (2).tar.zst"),
        ("archive.v2.zip", False, 2, "archive.v2 (2).zip"),
        (".env", False, 2, ".env (2)"),
        (".gitignore", False, 2, ".gitignore (2)"),
        ("photos", True, 2, "photos (2)"),
        ("photos.2024.v1", True, 2, "photos.2024.v1 (2)"),
        ("site.tar.gz", True, 2, "site.tar.gz (2)"),
        ("report (2).pdf", False, 2, "report (2) (2).pdf"),
        ("report.", False, 2, "report. (2)"),
        ("report", False, 999, "report (999)"),
    ],
)
def test_the_series_of_free_names_follows_the_table(name, folder, number, expected):
    assert free_name(name, number, folder=folder) == expected


def test_a_taken_file_name_of_multibyte_characters_is_cut_to_255_bytes_between_characters():
    name = "é" * 125 + ".pdf"  # 254 bytes
    candidate = free_name(name, 2, folder=False)
    assert candidate == "é" * 123 + " (2).pdf"
    assert len(candidate.encode()) == 254


def test_a_taken_folder_name_of_255_bytes_is_cut_between_characters():
    name = "あ" * 85  # 255 bytes
    candidate = free_name(name, 10, folder=True)
    assert candidate == "あ" * 83 + " (10)"
    assert len(candidate.encode()) == 254


def test_the_candidates_are_the_name_then_2_to_999_then_a_refusal():
    found = []
    with pytest.raises(DestinationRefused) as refused:
        for candidate in candidate_paths("/d/report.pdf", keep_both=True, folder=False):
            found.append(candidate)
    assert found[:3] == ["/d/report.pdf", "/d/report (2).pdf", "/d/report (3).pdf"]
    assert found[-1] == "/d/report (999).pdf"
    assert len(found) == 999
    assert (refused.value.status_code, refused.value.reason) == (409, "exists")


def test_without_keep_both_the_only_candidate_is_the_name_asked():
    assert list(candidate_paths("/d/report.pdf", keep_both=False, folder=False)) == [
        "/d/report.pdf"
    ]


# --- rename, move, copy and upload land beside a taken name ---------------------------


def _tree(s: Side) -> dict[str, object]:
    """Every entry the test made on the box (the remote server's own `/home` left out)."""
    found = files_under(s.base)
    if s.box == REMOTE:
        found.pop("home")
    return found


def _make_taken(s: Side, rel: str, taken: str) -> None:
    path = s.disk(rel)
    path.parent.mkdir(parents=True, exist_ok=True)
    if taken == "file":
        path.write_bytes(b"precious")
    elif taken == "folder":
        path.mkdir()
        (path / "inside.txt").write_bytes(b"inside")
    else:
        path.symlink_to("nowhere")


def _taken_entries(rel: str, taken: str) -> dict[str, object]:
    if taken == "file":
        return {rel: b"precious"}
    if taken == "folder":
        return {rel: "dir", f"{rel}/inside.txt": b"inside"}
    return {rel: "-> nowhere"}


@pytest.mark.parametrize("box", BOXES)
@pytest.mark.parametrize("taken", ["file", "folder", "dangling link"])
@pytest.mark.parametrize(
    ("op", "dest", "taken_rel", "landed_rel", "source_kept"),
    [
        ("rename", "x.txt", "x.txt", "x (2).txt", False),
        ("move", "d", "d/a.txt", "d/a (2).txt", False),
        ("copy", "d", "d/a.txt", "d/a (2).txt", True),
    ],
)
def test_a_taken_name_gets_the_first_free_name_and_the_answer_names_it(
    env, box, taken, op, dest, taken_rel, landed_rel, source_kept
):
    s = side(env, box)
    s.disk("a.txt").write_bytes(b"new")
    if op != "rename":
        s.disk("d").mkdir()
    _make_taken(s, taken_rel, taken)

    resp = single(s, op, "a.txt", dest, keep_both=True)

    assert resp.status_code == 200, resp.text
    assert resp.json()["path"] == s.path(landed_rel)
    expected = {**_taken_entries(taken_rel, taken), landed_rel: b"new"}
    if op != "rename":
        expected["d"] = "dir"
    if source_kept:
        expected["a.txt"] = b"new"
    assert _tree(s) == expected


@pytest.mark.parametrize("box", BOXES)
@pytest.mark.parametrize(
    ("op", "dest"),
    [("rename", "a (2).txt"), ("move", "d"), ("copy", "d")],
)
def test_a_free_name_is_used_as_asked(env, box, op, dest):
    s = side(env, box)
    s.disk("a.txt").write_bytes(b"new")
    s.disk("d").mkdir()
    target = dest if op == "rename" else "d/a.txt"

    resp = single(s, op, "a.txt", dest, keep_both=True)

    assert (resp.status_code, resp.json()["path"]) == (200, s.path(target))
    assert s.disk(target).read_bytes() == b"new"


@pytest.mark.parametrize("box", BOXES)
def test_a_copy_onto_its_own_path_makes_a_duplicate(env, box):
    s = side(env, box)
    s.disk("d").mkdir()
    s.disk("d/a.txt").write_bytes(b"one")

    resp = single(s, "copy", "d/a.txt", "d", keep_both=True)

    assert (resp.status_code, resp.json()["path"]) == (200, s.path("d/a (2).txt"))
    assert _tree(s) == {"d": "dir", "d/a.txt": b"one", "d/a (2).txt": b"one"}


@pytest.mark.parametrize("box", BOXES)
@pytest.mark.parametrize(("op", "dest"), [("rename", "a.txt"), ("move", "d")])
def test_a_move_or_rename_onto_its_own_path_is_still_nothing_to_do(env, box, op, dest):
    s = side(env, box)
    s.disk("d").mkdir()
    s.disk("d/a.txt").write_bytes(b"one")
    path = "d/a.txt"

    if op == "rename":
        resp = s.post("rename", {"path": s.path(path), "new_name": dest, "keep_both": True})
    else:
        resp = single(s, op, path, dest, keep_both=True)

    assert (resp.status_code, resp.json()["path"]) == (200, s.path("d/a.txt"))
    assert _tree(s) == {"d": "dir", "d/a.txt": b"one"}


@pytest.mark.parametrize("box", BOXES)
def test_candidates_2_to_5_taken_lands_as_6(env, box):
    s = side(env, box)
    s.disk("d").mkdir()
    s.disk("a.txt").write_bytes(b"new")
    taken = ["d/a.txt", *(f"d/a ({n}).txt" for n in range(2, 6))]
    for rel in taken:
        s.disk(rel).write_bytes(rel.encode())

    resp = single(s, "copy", "a.txt", "d", keep_both=True)

    assert (resp.status_code, resp.json()["path"]) == (200, s.path("d/a (6).txt"))
    assert s.disk("d/a (6).txt").read_bytes() == b"new"
    for rel in taken:
        assert s.disk(rel).read_bytes() == rel.encode()


@pytest.mark.parametrize("box", BOXES)
def test_a_name_that_is_itself_a_number_of_the_series_gets_its_own_series(env, box):
    s = side(env, box)
    s.disk("d").mkdir()
    s.disk("report (2).pdf").write_bytes(b"new")
    s.disk("d/report (2).pdf").write_bytes(b"old")

    resp = single(s, "move", "report (2).pdf", "d", keep_both=True)

    assert (resp.status_code, resp.json()["path"]) == (200, s.path("d/report (2) (2).pdf"))
    assert _tree(s) == {
        "d": "dir",
        "d/report (2).pdf": b"old",
        "d/report (2) (2).pdf": b"new",
    }


@pytest.mark.parametrize("box", BOXES)
def test_a_folder_moved_onto_a_name_taken_by_a_file_keeps_its_dots_whole(env, box):
    s = side(env, box)
    s.disk("src/p.v1").mkdir(parents=True)
    s.disk("src/p.v1/f.txt").write_bytes(b"f")
    s.disk("d").mkdir()
    s.disk("d/p.v1").write_bytes(b"a file")

    resp = single(s, "move", "src/p.v1", "d", keep_both=True)

    assert (resp.status_code, resp.json()["path"]) == (200, s.path("d/p.v1 (2)"))
    assert _tree(s) == {
        "src": "dir",
        "d": "dir",
        "d/p.v1": b"a file",
        "d/p.v1 (2)": "dir",
        "d/p.v1 (2)/f.txt": b"f",
    }


def upload_to(s: Side, directory: str, name: str, data: bytes = b"uploaded", **extra):
    return s.env.client.post(
        f"/api/v1/boxes/{s.box}/upload",
        data={"directory": s.path(directory), **extra},
        files={"file": (name, data, "application/octet-stream")},
        headers=HEADERS,
    )


@pytest.mark.parametrize("box", BOXES)
@pytest.mark.parametrize("taken", ["file", "folder", "dangling link"])
def test_an_upload_onto_a_taken_name_lands_beside_it(env, box, taken):
    s = side(env, box)
    _make_taken(s, "in/x.txt", taken)

    resp = upload_to(s, "in", "x.txt", keep_both="true")

    assert (resp.status_code, resp.json()["path"]) == (200, s.path("in/x (2).txt"))
    assert _tree(s) == {
        "in": "dir",
        **_taken_entries("in/x.txt", taken),
        "in/x (2).txt": b"uploaded",
    }


@pytest.mark.parametrize("box", BOXES)
def test_an_upload_onto_a_free_name_is_used_as_asked(env, box):
    s = side(env, box)
    s.disk("in").mkdir()

    resp = upload_to(s, "in", "x.txt", keep_both="true")

    assert (resp.status_code, resp.json()["path"]) == (200, s.path("in/x.txt"))


# --- two requests at once ---------------------------------------------------------------


def test_two_local_uploads_at_once_both_land_under_two_names(env, monkeypatch):
    """The second upload runs when the first has found ``x.txt`` taken and is about to
    take ``x (2).txt``: the second takes it first, and the first moves on to the next
    name. Both land, neither replaces."""
    s = side(env, "local")
    s.disk("in").mkdir()
    s.disk("in/x.txt").write_bytes(b"precious")
    real = local_files._LocalLanding.__init__
    inner: list[object] = []

    def first_lets_the_second_in(self, target, **kwargs):
        if target.endswith("x (2).txt") and not inner:
            inner.append(None)
            inner[0] = upload_to(s, "in", "x.txt", b"second", keep_both="true")
        real(self, target, **kwargs)

    monkeypatch.setattr(local_files._LocalLanding, "__init__", first_lets_the_second_in)

    first = upload_to(s, "in", "x.txt", b"first", keep_both="true")

    second = inner[0]
    assert (second.status_code, second.json()["path"]) == (200, s.path("in/x (2).txt"))
    assert (first.status_code, first.json()["path"]) == (200, s.path("in/x (3).txt"))
    assert _tree(s) == {
        "in": "dir",
        "in/x.txt": b"precious",
        "in/x (2).txt": b"second",
        "in/x (3).txt": b"first",
    }


# --- batch move and copy ------------------------------------------------------------------


@pytest.mark.parametrize("box", BOXES)
@pytest.mark.parametrize("op", ["move", "copy"])
def test_a_batch_lists_exactly_the_items_placed_under_another_name(env, box, op):
    s = side(env, box)
    s.disk("x").mkdir()
    s.disk("y").mkdir()
    s.disk("x/f.txt").write_bytes(b"x f")
    s.disk("y/f.txt").write_bytes(b"y f")
    s.disk("x/g.txt").write_bytes(b"x g")
    s.disk("x/t.txt").write_bytes(b"x t")
    s.disk("d").mkdir()
    s.disk("d/t.txt").write_bytes(b"old t")
    paths = [s.path("x/f.txt"), s.path("y/f.txt"), s.path("x/g.txt"), s.path("x/t.txt")]

    resp = s.post(f"batch/{op}", {"paths": paths, "destination": s.path("d"), "keep_both": True})

    assert resp.status_code == 200, resp.text
    assert resp.json() == {
        "status": "ok",
        "succeeded": paths,
        "failed": [],
        "placed": [
            {"path": s.path("y/f.txt"), "placed": s.path("d/f (2).txt")},
            {"path": s.path("x/t.txt"), "placed": s.path("d/t (2).txt")},
        ],
    }
    expected = {
        "d": "dir",
        "d/f.txt": b"x f",
        "d/f (2).txt": b"y f",
        "d/g.txt": b"x g",
        "d/t.txt": b"old t",
        "d/t (2).txt": b"x t",
        "x": "dir",
        "y": "dir",
    }
    if op == "copy":
        expected.update(
            {"x/f.txt": b"x f", "y/f.txt": b"y f", "x/g.txt": b"x g", "x/t.txt": b"x t"}
        )
    assert _tree(s) == expected


@pytest.mark.parametrize("box", BOXES)
def test_a_batch_with_keep_both_and_no_taken_name_has_an_empty_placed(env, box):
    s = side(env, box)
    s.disk("a.txt").write_bytes(b"a")
    s.disk("d").mkdir()

    resp = s.post(
        "batch/copy", {"paths": [s.path("a.txt")], "destination": s.path("d"), "keep_both": True}
    )

    assert resp.json() == {
        "status": "ok",
        "succeeded": [s.path("a.txt")],
        "failed": [],
        "placed": [],
    }


@pytest.mark.parametrize("box", BOXES)
def test_a_batch_without_keep_both_answers_as_before(env, box):
    s = side(env, box)
    s.disk("a.txt").write_bytes(b"a")
    s.disk("d").mkdir()

    resp = s.post("batch/copy", {"paths": [s.path("a.txt")], "destination": s.path("d")})

    assert resp.json() == {"status": "ok", "succeeded": [s.path("a.txt")], "failed": []}


# --- transfer ---------------------------------------------------------------------------------


@pytest.mark.parametrize("server", sorted(SERVERS))
@pytest.mark.parametrize(("src", "dest"), [("local", REMOTE), (REMOTE, "local")])
@pytest.mark.parametrize("mode", ["copy", "move"])
def test_a_transfer_lands_beside_a_taken_name_and_its_events_say_where(
    env, server, src, dest, mode
):
    a, b = side(env, src), side(env, dest)
    a.disk("x.txt").write_bytes(b"source x")
    a.disk("y.txt").write_bytes(b"source y")
    a.disk("e.txt").write_bytes(b"")
    b.disk("in").mkdir()
    b.disk("in/x.txt").write_bytes(b"precious")
    b.disk("in/e.txt").write_bytes(b"old empty")
    sent = [a.path("x.txt"), a.path("y.txt"), a.path("e.txt")]

    events = transfer(env, src, dest, sent, b.path("in"), mode=mode, keep_both=True)

    progress = [data for kind, data in events if kind == "progress"]
    assert progress == [
        {
            "file": a.path("x.txt"),
            "bytes_done": 8,
            "bytes_total": 8,
            "index": 0,
            "total": 3,
            "placed": b.path("in/x (2).txt"),
        },
        {"file": a.path("y.txt"), "bytes_done": 8, "bytes_total": 8, "index": 1, "total": 3},
        {
            "file": a.path("e.txt"),
            "bytes_done": 0,
            "bytes_total": 0,
            "index": 2,
            "total": 3,
            "placed": b.path("in/e (2).txt"),
        },
    ]
    assert events[-1] == ("done", {"succeeded": sent, "failed": []})
    assert files_under(b.disk("in")) == {
        "x.txt": b"precious",
        "x (2).txt": b"source x",
        "y.txt": b"source y",
        "e.txt": b"old empty",
        "e (2).txt": b"",
    }
    assert sorted(p.name for p in a.base.iterdir() if p.is_file()) == (
        ["e.txt", "x.txt", "y.txt"] if mode == "copy" else []
    )


@pytest.mark.parametrize("server", ["refusing-rename"])
def test_two_sources_of_one_name_both_land_in_a_transfer(env, server):
    a, b = side(env, "local"), side(env, REMOTE)
    a.disk("p").mkdir()
    a.disk("q").mkdir()
    a.disk("p/f.txt").write_bytes(b"p f")
    a.disk("q/f.txt").write_bytes(b"q f")
    b.disk("in").mkdir()

    events = transfer(
        env, "local", REMOTE, [a.path("p/f.txt"), a.path("q/f.txt")], b.path("in"), keep_both=True
    )

    placed = [data.get("placed") for kind, data in events if kind == "progress"]
    assert placed == [None, b.path("in/f (2).txt")]
    assert files_under(b.disk("in")) == {"f.txt": b"p f", "f (2).txt": b"q f"}


# --- overwrite and keep_both together, and keep_both that is not a boolean ---------------


def _every_route(s: Side, **extra):
    s.disk("a.txt").write_bytes(b"new")
    s.disk("d").mkdir()
    s.disk("d/a.txt").write_bytes(b"old")
    s.disk("x.txt").write_bytes(b"old x")
    return {
        "rename": lambda: s.post("rename", {"path": s.path("a.txt"), "new_name": "x.txt", **extra}),
        "move": lambda: single(s, "move", "a.txt", "d", **extra),
        "copy": lambda: single(s, "copy", "a.txt", "d", **extra),
        "batch move": lambda: s.post(
            "batch/move", {"paths": [s.path("a.txt")], "destination": s.path("d"), **extra}
        ),
        "batch copy": lambda: s.post(
            "batch/copy", {"paths": [s.path("a.txt")], "destination": s.path("d"), **extra}
        ),
    }


@pytest.mark.parametrize("box", BOXES)
@pytest.mark.parametrize("route", ["rename", "move", "copy", "batch move", "batch copy"])
def test_overwrite_with_keep_both_is_refused_and_changes_nothing(env, box, route):
    s = side(env, box)
    call = _every_route(s, overwrite=True, keep_both=True)[route]
    before = _tree(s)

    resp = call()

    assert (resp.status_code, resp.json()) == (400, INVALID_BOTH)
    assert _tree(s) == before


@pytest.mark.parametrize("box", BOXES)
def test_an_upload_with_overwrite_and_keep_both_is_refused_and_changes_nothing(env, box):
    s = side(env, box)
    s.disk("in").mkdir()
    s.disk("in/x.txt").write_bytes(b"precious")
    before = _tree(s)

    resp = upload_to(s, "in", "x.txt", overwrite="true", keep_both="true")

    assert (resp.status_code, resp.json()) == (400, INVALID_BOTH)
    assert _tree(s) == before


@pytest.mark.parametrize("server", ["refusing-rename"])
def test_a_transfer_with_overwrite_and_keep_both_is_refused_before_it_streams(env, server):
    a, b = side(env, "local"), side(env, REMOTE)
    a.disk("x.txt").write_bytes(b"source x")
    b.disk("in").mkdir()
    b.disk("in/x.txt").write_bytes(b"precious")

    resp = env.client.post(
        "/api/v1/transfer",
        json={
            "src_box": "local",
            "dest_box": REMOTE,
            "paths": [a.path("x.txt")],
            "destination": b.path("in"),
            "overwrite": True,
            "keep_both": True,
        },
        headers=HEADERS,
    )

    assert (resp.status_code, resp.json()) == (400, INVALID_BOTH)
    assert b.disk("in/x.txt").read_bytes() == b"precious"
    assert a.disk("x.txt").read_bytes() == b"source x"


@pytest.mark.parametrize("route", ["rename", "move", "copy", "batch move", "batch copy"])
def test_keep_both_as_a_string_is_refused(env, route):
    s = side(env, "local")
    call = _every_route(s, keep_both="true")[route]
    before = _tree(s)

    resp = call()

    assert resp.status_code == 422
    assert resp.json()["reason"] == "invalid"
    assert _tree(s) == before


def test_the_published_batch_answer_still_names_its_fields() -> None:
    """Leaving ``placed`` out of an answer must not empty the answer's published
    schema (``/openapi.json``): it names every field, ``placed`` as optional."""
    schema = APIBatchResult.model_json_schema(mode="serialization")

    assert sorted(schema["properties"]) == ["failed", "placed", "status", "succeeded"]
    assert sorted(schema["required"]) == ["failed", "status", "succeeded"]
