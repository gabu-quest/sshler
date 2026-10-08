"""Artifact registration, discovery, and isolated static serving tests."""

from __future__ import annotations

import urllib.error
import urllib.request
from pathlib import Path

import pytest

from sshler import state
from sshler.artifacts import (
    ArtifactSidecar,
    ArtifactValidationError,
    clean_group_path,
    clean_mount_path,
    discover_pages,
    resolve_registration,
    resolve_served_file,
)


def _registration(
    tmp_path: Path,
    *,
    mode: str = "site",
    entrypoint: str | None = "index.html",
) -> state.ArtifactRegistration:
    state.initialize(tmp_path / "config")
    project, _ = state.get_or_create_artifact_project("Sample Project")
    return state.create_artifact_registration(
        project_id=project.id,
        group_path="experiments/layout",
        source_path=str(tmp_path / "site"),
        mode=mode,
        entrypoint=entrypoint,
        title_override=None,
    )


def test_resolve_registration_infers_file_site_and_collection(tmp_path: Path) -> None:
    page = tmp_path / "page.html"
    page.write_text("<title>Single</title>", encoding="utf-8")
    site = tmp_path / "site"
    site.mkdir()
    (site / "index.html").write_text("<title>Site</title>", encoding="utf-8")
    collection = tmp_path / "collection"
    collection.mkdir()
    (collection / "tool.html").write_text("<title>Tool</title>", encoding="utf-8")

    assert resolve_registration(str(page)) == (page.resolve(), "file", None)
    assert resolve_registration(str(site)) == (site.resolve(), "site", "index.html")
    assert resolve_registration(str(collection)) == (
        collection.resolve(),
        "collection",
        None,
    )


def test_file_mode_does_not_expose_siblings(tmp_path: Path) -> None:
    site = tmp_path / "site"
    site.mkdir()
    page = site / "page.html"
    page.write_text("<title>Page</title>", encoding="utf-8")
    (site / "sibling.txt").write_text("private sibling", encoding="utf-8")
    state.initialize(tmp_path / "config")
    project, _ = state.get_or_create_artifact_project("Sample")
    registration = state.create_artifact_registration(
        project_id=project.id,
        group_path="",
        source_path=str(page),
        mode="file",
        entrypoint=None,
        title_override=None,
    )

    assert resolve_served_file(registration, "") == page.resolve()
    with pytest.raises(ArtifactValidationError):
        resolve_served_file(registration, "sibling.txt")


def test_collection_discovery_builds_nested_groups_and_titles(tmp_path: Path) -> None:
    site = tmp_path / "site"
    nested = site / "tools" / "layout"
    nested.mkdir(parents=True)
    (site / "root.html").write_text("<title>Root Page</title>", encoding="utf-8")
    (nested / "grid.html").write_text("<title>\n Grid   Explorer \n</title>", encoding="utf-8")
    (nested / "ignore.txt").write_text("not html", encoding="utf-8")
    registration = _registration(tmp_path, mode="collection", entrypoint=None)

    pages = discover_pages(registration, refresh=True)

    assert [(page.relative_path, page.title, page.group_path) for page in pages] == [
        ("root.html", "Root Page", "experiments/layout"),
        (
            "tools/layout/grid.html",
            "Grid Explorer",
            "experiments/layout/tools/layout",
        ),
    ]


def test_collection_discovery_ignores_hidden_and_symlinked_directories(
    tmp_path: Path,
) -> None:
    site = tmp_path / "site"
    visible = site / "visible"
    hidden = site / ".hidden"
    outside = tmp_path / "outside"
    visible.mkdir(parents=True)
    hidden.mkdir()
    outside.mkdir()
    (visible / "ok.html").write_text("<title>OK</title>", encoding="utf-8")
    (hidden / "hidden.html").write_text("<title>Hidden</title>", encoding="utf-8")
    (outside / "outside.html").write_text("<title>Outside</title>", encoding="utf-8")
    (site / "linked").symlink_to(outside, target_is_directory=True)
    registration = _registration(tmp_path, mode="collection", entrypoint=None)

    pages = discover_pages(registration, refresh=True)

    assert [page.relative_path for page in pages] == ["visible/ok.html"]


def test_group_validation_rejects_dot_segments() -> None:
    assert clean_group_path("design/layout") == "design/layout"
    for invalid in ("../layout", "design/./layout", "design//layout"):
        with pytest.raises(ArtifactValidationError):
            clean_group_path(invalid)


def test_mount_validation_accepts_url_paths_and_rejects_reserved_or_unsafe_paths() -> None:
    assert clean_mount_path("/published/Demo-Tools") == "published/Demo-Tools"
    for invalid in ("a/example", "r/example", "published/../demo", "published/a b"):
        with pytest.raises(ArtifactValidationError):
            clean_mount_path(invalid)


def test_project_and_registration_deletion_never_removes_source(tmp_path: Path) -> None:
    site = tmp_path / "site"
    site.mkdir()
    source = site / "index.html"
    original = b"<title>Keep Me</title>"
    source.write_bytes(original)
    registration = _registration(tmp_path)

    with pytest.raises(ValueError):
        state.delete_artifact_project(registration.project_id)
    removed, count = state.delete_artifact_project(registration.project_id, cascade=True)

    assert (removed, count) == (True, 1)
    assert source.read_bytes() == original


def test_sidecar_serves_inline_html_and_relative_asset_without_cors(
    tmp_path: Path,
) -> None:
    site = tmp_path / "site"
    site.mkdir()
    (site / "index.html").write_text(
        "<title>Demo</title><script>document.body.dataset.ready='yes'</script>",
        encoding="utf-8",
    )
    (site / "data.json").write_text('{"ready":true}', encoding="utf-8")
    registration = _registration(tmp_path)
    sidecar = ArtifactSidecar()
    port = sidecar.start()
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/a/{registration.id}/") as response:
            assert response.status == 200
            assert b"dataset.ready" in response.read()
            assert "no-store" in response.headers["Cache-Control"]
            assert response.headers["X-Content-Type-Options"] == "nosniff"
            assert response.headers["Cross-Origin-Opener-Policy"] == "same-origin"
            assert response.headers.get("Access-Control-Allow-Origin") is None
        with urllib.request.urlopen(
            f"http://127.0.0.1:{port}/a/{registration.id}/data.json"
        ) as response:
            assert response.read() == b'{"ready":true}'
            assert response.headers["Content-Type"].startswith("application/json")
    finally:
        sidecar.stop()


def test_sidecar_serves_stable_alias_and_root_mount(tmp_path: Path) -> None:
    site = tmp_path / "site"
    site.mkdir()
    (site / "index.html").write_text("<title>Linked Demo</title>", encoding="utf-8")
    (site / "details.html").write_text("<title>Details</title>", encoding="utf-8")
    state.initialize(tmp_path / "config")
    project, _ = state.get_or_create_artifact_project("Demo Tools")
    registration = state.create_artifact_registration(
        project_id=project.id,
        group_path="examples",
        source_path=str(site),
        mode="site",
        entrypoint="index.html",
        title_override=None,
        slug="linked-demo",
        mount_path="published/demo",
    )
    sidecar = ArtifactSidecar()
    port = sidecar.start()
    try:
        for path, expected in (
            (f"/r/{project.slug}/{registration.slug}/", b"Linked Demo"),
            ("/published/demo/details.html", b"Details"),
        ):
            with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}") as response:
                assert expected in response.read()
    finally:
        sidecar.stop()


def test_sidecar_rejects_traversal_hidden_paths_and_post(tmp_path: Path) -> None:
    site = tmp_path / "site"
    site.mkdir()
    (site / "index.html").write_text("<title>Demo</title>", encoding="utf-8")
    (site / ".secret").write_text("hidden", encoding="utf-8")
    registration = _registration(tmp_path)
    sidecar = ArtifactSidecar()
    port = sidecar.start()
    try:
        paths = (
            f"/a/{registration.id}/../index.html",
            f"/a/{registration.id}/%2e%2e/index.html",
            f"/a/{registration.id}/.secret",
            f"/a/{registration.id}/missing.html",
        )
        for path in paths:
            with pytest.raises(urllib.error.HTTPError) as error:
                urllib.request.urlopen(f"http://127.0.0.1:{port}{path}")
            assert error.value.code == 404
            assert str(tmp_path).encode() not in error.value.read()

        for method in ("POST", "PUT", "PATCH", "DELETE", "OPTIONS"):
            request = urllib.request.Request(
                f"http://127.0.0.1:{port}/a/{registration.id}/", method=method
            )
            with pytest.raises(urllib.error.HTTPError) as error:
                urllib.request.urlopen(request)
            assert error.value.code == 405
    finally:
        sidecar.stop()


def test_sidecar_refuses_symlink_escaping_registered_root(tmp_path: Path) -> None:
    """Mutation killed: dropping the ``is_relative_to(allowed_root)`` check in
    ``resolve_served_file`` (e.g. ``if False and not ...``) serves the secret.
    """
    site = tmp_path / "site"
    site.mkdir()
    (site / "index.html").write_text("<title>Demo</title>", encoding="utf-8")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.html").write_text("TOP-SECRET-BODY", encoding="utf-8")
    (site / "leak.html").symlink_to(outside / "secret.html")
    # A symlinked directory pointing out of the root must not leak either.
    (site / "leakdir").symlink_to(outside, target_is_directory=True)
    registration = _registration(tmp_path)
    sidecar = ArtifactSidecar()
    port = sidecar.start()
    try:
        base = f"http://127.0.0.1:{port}/a/{registration.id}"
        for leak_path in ("/leak.html", "/leakdir/secret.html"):
            with pytest.raises(urllib.error.HTTPError) as error:
                urllib.request.urlopen(base + leak_path)
            assert error.value.code == 404
            assert error.value.read() == b"Artifact file not found"

        # Positive control: a real in-root file is served with its exact body.
        with urllib.request.urlopen(base + "/index.html") as response:
            assert response.status == 200
            assert response.read() == b"<title>Demo</title>"
    finally:
        sidecar.stop()


def test_sidecar_serves_existing_page_and_404s_missing_one(
    tmp_path: Path,
) -> None:
    """Controls the 404 test: an existing file answers 200 while a missing one
    answers 404, so a server returning 404 for everything would fail here."""
    site = tmp_path / "site"
    site.mkdir()
    (site / "index.html").write_text("<title>Demo</title>", encoding="utf-8")
    registration = _registration(tmp_path)
    sidecar = ArtifactSidecar()
    port = sidecar.start()
    try:
        base = f"http://127.0.0.1:{port}/a/{registration.id}"
        with urllib.request.urlopen(base + "/index.html") as response:
            assert response.read() == b"<title>Demo</title>"
        with pytest.raises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(base + "/missing.html")
        assert error.value.code == 404
    finally:
        sidecar.stop()


def test_sidecar_rejects_foreign_host_header(tmp_path: Path) -> None:
    # DNS rebinding: a hostile page resolves its own name to 127.0.0.1, so the
    # browser connects to the sidecar but sends the attacker's Host header.
    site = tmp_path / "site"
    site.mkdir()
    (site / "index.html").write_text("<title>Demo</title>", encoding="utf-8")
    registration = _registration(tmp_path)
    sidecar = ArtifactSidecar()
    port = sidecar.start()
    try:
        url = f"http://127.0.0.1:{port}/a/{registration.id}/"
        for host in ("evil.example", f"evil.example:{port}", "127.0.0.1", f"127.0.0.1:{port + 1}"):
            request = urllib.request.Request(url, headers={"Host": host})
            with pytest.raises(urllib.error.HTTPError) as error:
                urllib.request.urlopen(request)
            assert error.value.code == 421
            assert b"Demo" not in error.value.read()

        for host in (f"127.0.0.1:{port}", f"localhost:{port}", f"[::1]:{port}"):
            request = urllib.request.Request(url, headers={"Host": host})
            with urllib.request.urlopen(request) as response:
                assert response.status == 200
                assert b"<title>Demo</title>" in response.read()
    finally:
        sidecar.stop()
