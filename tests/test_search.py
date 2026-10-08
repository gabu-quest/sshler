"""Tests for frecency-based directory search functionality."""

import math
import time
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from sshler import state
from sshler.webapp import ServerSettings, make_app

TEST_TOKEN = "search-test-token"


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


class TestDirectoryVisitTracking:
    """Tests for DirectoryVisit model and visit recording."""

    def test_record_new_visit(self, tmp_path):
        """First visit to a directory creates a new record with count=1."""
        config_dir = setup_config(tmp_path)
        state.initialize(config_dir)

        visit = state.record_directory_visit("testbox", "/home/user/projects")

        assert visit.box == "testbox"
        assert visit.path == "/home/user/projects"
        assert visit.visit_count == 1
        assert visit.last_visited > 0
        assert visit.first_visited > 0

        state.reset_state()

    def test_record_repeat_visit_increments_count(self, tmp_path):
        """Visiting same directory again increments visit_count."""
        config_dir = setup_config(tmp_path)
        state.initialize(config_dir)

        # First visit
        visit1 = state.record_directory_visit("testbox", "/home/user/projects")
        assert visit1.visit_count == 1

        # Second visit
        visit2 = state.record_directory_visit("testbox", "/home/user/projects")
        assert visit2.visit_count == 2

        # Third visit
        visit3 = state.record_directory_visit("testbox", "/home/user/projects")
        assert visit3.visit_count == 3

        state.reset_state()

    def test_separate_boxes_tracked_independently(self, tmp_path):
        """Visits are tracked separately per box."""
        config_dir = setup_config(tmp_path)
        state.initialize(config_dir)

        state.record_directory_visit("box1", "/shared/path")
        state.record_directory_visit("box1", "/shared/path")
        state.record_directory_visit("box2", "/shared/path")

        # Search should return different results per box
        box1_results = state.search_directories("box1", "shared", limit=10)
        box2_results = state.search_directories("box2", "shared", limit=10)

        assert len(box1_results) == 1
        assert len(box2_results) == 1
        assert box1_results[0][1] > box2_results[0][1]  # box1 has higher score (more visits)

        state.reset_state()


class TestFrecencyScoring:
    """Tests for frecency calculation and search ranking."""

    def test_frecency_score_formula(self, tmp_path):
        """Verify the frecency formula: score = visit_count * exp(-0.1 * days)."""
        config_dir = setup_config(tmp_path)
        state.initialize(config_dir)

        now = time.time()

        # Test with known values
        # 10 visits, 0 days ago
        score_fresh = state._calculate_frecency_score(10, now)
        assert abs(score_fresh - 10.0) < 0.01

        # 10 visits, 10 days ago
        ten_days_ago = now - (10 * 24 * 60 * 60)
        score_10_days = state._calculate_frecency_score(10, ten_days_ago)
        expected_10_days = 10 * math.exp(-0.1 * 10)
        assert abs(score_10_days - expected_10_days) < 0.01

        # Verify decay - older visits should score lower
        assert score_fresh > score_10_days

        state.reset_state()

    def test_search_returns_frecency_ranked_results(self, tmp_path):
        """Search results are ranked by frecency score."""
        config_dir = setup_config(tmp_path)
        state.initialize(config_dir)

        # Create visits with different counts
        for _ in range(5):
            state.record_directory_visit("testbox", "/projects/sshler")
        for _ in range(2):
            state.record_directory_visit("testbox", "/projects/other")
        state.record_directory_visit("testbox", "/projects/demo")

        results = state.search_directories("testbox", "projects", limit=10)

        assert len(results) == 3
        # Verify ordering by score (descending)
        assert results[0][0] == "/projects/sshler"  # 5 visits
        assert results[1][0] == "/projects/other"   # 2 visits
        assert results[2][0] == "/projects/demo"    # 1 visit

        # Verify scores are descending
        assert results[0][1] > results[1][1] > results[2][1]

        state.reset_state()

    def test_search_substring_matching(self, tmp_path):
        """Search matches substring in path."""
        config_dir = setup_config(tmp_path)
        state.initialize(config_dir)

        state.record_directory_visit("testbox", "/home/user/projects/sshler")
        state.record_directory_visit("testbox", "/var/log/sshler")
        state.record_directory_visit("testbox", "/home/user/other")

        results = state.search_directories("testbox", "sshler", limit=10)

        assert len(results) == 2
        paths = [r[0] for r in results]
        assert "/home/user/projects/sshler" in paths
        assert "/var/log/sshler" in paths
        assert "/home/user/other" not in paths

        state.reset_state()

    def test_search_case_insensitive(self, tmp_path):
        """Search is case-insensitive."""
        config_dir = setup_config(tmp_path)
        state.initialize(config_dir)

        state.record_directory_visit("testbox", "/Projects/SSHLER")
        state.record_directory_visit("testbox", "/projects/sshler")

        # Search with different case
        results = state.search_directories("testbox", "SSHLER", limit=10)
        assert len(results) == 2

        results = state.search_directories("testbox", "projects", limit=10)
        assert len(results) == 2

        state.reset_state()

    def test_search_respects_limit(self, tmp_path):
        """Search respects the limit parameter."""
        config_dir = setup_config(tmp_path)
        state.initialize(config_dir)

        for i in range(10):
            state.record_directory_visit("testbox", f"/path/dir{i}")

        results = state.search_directories("testbox", "path", limit=3)
        assert len(results) == 3

        state.reset_state()


class TestSearchAPIEndpoint:
    """Tests for the /api/v1/boxes/{name}/search endpoint."""

    def test_search_requires_minimum_query_length(self, tmp_path, monkeypatch):
        """Search endpoint rejects queries shorter than 2 characters."""
        config_dir = setup_config(tmp_path)
        client = build_client(config_dir, monkeypatch)

        try:
            # Single character should fail
            resp = client.get(
                "/api/v1/boxes/local/search",
                params={"q": "a"},
                headers=auth_headers(),
            )
            assert resp.status_code == 422  # Validation error

            # Two characters should succeed
            resp = client.get(
                "/api/v1/boxes/local/search",
                params={"q": "ab"},
                headers=auth_headers(),
            )
            assert resp.status_code == 200
        finally:
            client.close()
            state.reset_state()

    def test_search_returns_valid_response_structure(self, tmp_path, monkeypatch):
        """Search endpoint returns properly structured response."""
        config_dir = setup_config(tmp_path)
        client = build_client(config_dir, monkeypatch)

        try:
            resp = client.get(
                "/api/v1/boxes/local/search",
                params={"q": "projects"},
                headers=auth_headers(),
            )
            assert resp.status_code == 200

            data = resp.json()
            assert "box" in data
            assert "query" in data
            assert "results" in data
            assert data["box"] == "local"
            assert data["query"] == "projects"
            assert isinstance(data["results"], list)
        finally:
            client.close()
            state.reset_state()

    def test_search_result_structure(self, tmp_path, monkeypatch):
        """Each source is labelled with the stage that produced it, with exact scores.

        Mutations killed: the frecency stage labelled "discovery" (or vice versa),
        a wrong score on either stage, a missing stage, a swapped rank order.
        """
        config_dir = setup_config(tmp_path)
        root = tmp_path / "tree"
        (root / "testdisc").mkdir(parents=True)

        async def fake_zoxide(pattern: str) -> list[tuple[str, float]]:
            assert pattern == "test"
            return [("/frecent/testthing", 5.0)]

        monkeypatch.setattr("sshler.api.search._query_zoxide", fake_zoxide)

        client = build_client(config_dir, monkeypatch)
        try:
            resp = client.get(
                "/api/v1/boxes/local/search",
                params={"q": "test", "root": str(root)},
                headers=auth_headers(),
            )
            assert resp.status_code == 200

            assert resp.json()["results"] == [
                {
                    "path": "/frecent/testthing",
                    "score": 5.0,
                    "source": "frecency",
                    "is_directory": True,
                },
                {
                    "path": str(root / "testdisc"),
                    "score": 0.1,
                    "source": "discovery",
                    "is_directory": True,
                },
            ]
        finally:
            client.close()
            state.reset_state()

    def test_search_requires_auth(self, tmp_path, monkeypatch):
        """Search endpoint requires authentication."""
        config_dir = setup_config(tmp_path)
        client = build_client(config_dir, monkeypatch)

        try:
            resp = client.get(
                "/api/v1/boxes/local/search",
                params={"q": "test"},
                # No auth headers
            )
            assert resp.status_code == 403
        finally:
            client.close()
            state.reset_state()

    def test_search_box_not_found(self, tmp_path, monkeypatch):
        """Search for non-existent box returns 404."""
        config_dir = setup_config(tmp_path)
        client = build_client(config_dir, monkeypatch)

        try:
            resp = client.get(
                "/api/v1/boxes/nonexistent/search",
                params={"q": "test"},
                headers=auth_headers(),
            )
            assert resp.status_code == 404
        finally:
            client.close()
            state.reset_state()


def make_tree(tmp_path: Path) -> Path:
    """Deterministic tree:

    tree/
      gadgets/captures/capture_x/exports/blue_vs_green_frame473.png
      node_modules/junk/blue_thing.txt   (pruned — must never appear)
      docs/blue-notes/                   (directory matching 'blue')
    """
    root = tmp_path / "tree"
    exports = root / "gadgets" / "captures" / "capture_x" / "exports"
    exports.mkdir(parents=True)
    (exports / "blue_vs_green_frame473.png").write_bytes(b"png")
    junk = root / "node_modules" / "junk"
    junk.mkdir(parents=True)
    (junk / "blue_thing.txt").write_text("x", encoding="utf-8")
    (root / "docs" / "blue-notes").mkdir(parents=True)
    return root


class TestFindPattern:
    def test_plain_query_becomes_substring(self):
        from sshler.api.search import _find_pattern

        assert _find_pattern("blue") == "*blue*"

    def test_wildcard_query_passes_through(self):
        from sshler.api.search import _find_pattern

        assert _find_pattern("blue*png") == "blue*png"
        assert _find_pattern("frame?73*") == "frame?73*"


class TestLocalDiscovery:
    """Tests for filesystem discovery on the local box (the find fallback)."""

    def test_finds_nested_directories(self, tmp_path):
        import asyncio

        from sshler.api.search import _discover_local

        root = make_tree(tmp_path)
        results = asyncio.run(_discover_local(str(root), "*captur*"))

        paths = sorted(path for path, _ in results)
        assert paths == [
            str(root / "gadgets" / "captures"),
            str(root / "gadgets" / "captures" / "capture_x"),
        ]
        assert all(is_dir for _, is_dir in results)

    def test_dirs_only_by_default(self, tmp_path):
        import asyncio

        from sshler.api.search import _discover_local

        root = make_tree(tmp_path)
        results = asyncio.run(_discover_local(str(root), "*blue_vs*"))
        assert results == []

    def test_finds_files_when_enabled_and_prunes_node_modules(self, tmp_path):
        import asyncio

        from sshler.api.search import _discover_local

        root = make_tree(tmp_path)
        results = asyncio.run(_discover_local(str(root), "*blue*", include_files=True))

        expected_file = str(
            root
            / "gadgets"
            / "captures"
            / "capture_x"
            / "exports"
            / "blue_vs_green_frame473.png"
        )
        expected_dir = str(root / "docs" / "blue-notes")
        assert sorted(results) == sorted(
            [(expected_file, False), (expected_dir, True)]
        )

    def test_respects_limit(self, tmp_path):
        import asyncio

        from sshler.api.search import _discover_local

        root = tmp_path / "many"
        root.mkdir()
        for i in range(10):
            (root / f"bluedir_{i}").mkdir()
        results = asyncio.run(_discover_local(str(root), "*blue*", limit=3))
        assert len(results) == 3


class TestSearchEndpointDiscovery:
    """Endpoint-level tests for root-scoped discovery + file results."""

    def test_local_search_finds_file_under_root(self, tmp_path, monkeypatch):
        root = make_tree(tmp_path)
        config_dir = setup_config(tmp_path)
        client = build_client(config_dir, monkeypatch)

        try:
            resp = client.get(
                "/api/v1/boxes/local/search",
                params={"q": "blue_vs_green", "files": "true", "root": str(root)},
                headers=auth_headers(),
            )
            assert resp.status_code == 200

            discovery = [
                r for r in resp.json()["results"] if r["source"] == "discovery"
            ]
            assert len(discovery) == 1
            assert discovery[0]["path"] == str(
                root
                / "gadgets"
                / "captures"
                / "capture_x"
                / "exports"
                / "blue_vs_green_frame473.png"
            )
            assert discovery[0]["is_directory"] is False
        finally:
            client.close()
            state.reset_state()

    def test_local_search_wildcard_query(self, tmp_path, monkeypatch):
        root = make_tree(tmp_path)
        config_dir = setup_config(tmp_path)
        client = build_client(config_dir, monkeypatch)

        try:
            resp = client.get(
                "/api/v1/boxes/local/search",
                params={"q": "blue*png", "files": "true", "root": str(root)},
                headers=auth_headers(),
            )
            assert resp.status_code == 200
            discovery = [
                r for r in resp.json()["results"] if r["source"] == "discovery"
            ]
            assert len(discovery) == 1
            assert discovery[0]["path"].endswith("blue_vs_green_frame473.png")
        finally:
            client.close()
            state.reset_state()

    def test_local_search_dirs_only_without_files_flag(self, tmp_path, monkeypatch):
        root = make_tree(tmp_path)
        config_dir = setup_config(tmp_path)
        client = build_client(config_dir, monkeypatch)

        try:
            resp = client.get(
                "/api/v1/boxes/local/search",
                params={"q": "blue_vs_green", "root": str(root)},
                headers=auth_headers(),
            )
            assert resp.status_code == 200
            discovery = [
                r for r in resp.json()["results"] if r["source"] == "discovery"
            ]
            assert discovery == []
        finally:
            client.close()
            state.reset_state()

    def test_invalid_root_rejected(self, tmp_path, monkeypatch):
        config_dir = setup_config(tmp_path)
        client = build_client(config_dir, monkeypatch)

        try:
            resp = client.get(
                "/api/v1/boxes/local/search",
                params={"q": "blue", "root": "bad\0path"},
                headers=auth_headers(),
            )
            assert resp.status_code == 400
        finally:
            client.close()
            state.reset_state()


class TestVisitTrackingIntegration:
    """Tests for visit tracking integration with file listing."""

    def test_listing_remote_directory_records_visit(self, tmp_path):
        """Listing a remote directory should record a visit."""
        # Note: This would require mocking SSH, so we test the state module directly
        config_dir = setup_config(tmp_path)
        state.initialize(config_dir)

        # Simulate what the API endpoint does
        state.record_directory_visit("remote-box", "/home/user/projects")

        results = state.search_directories("remote-box", "projects", limit=10)
        assert len(results) == 1
        assert results[0][0] == "/home/user/projects"

        state.reset_state()

    def test_local_box_does_not_record_visits(self, tmp_path, monkeypatch):
        """Local box uses zoxide, so we don't record visits in our DB."""
        config_dir = setup_config(tmp_path)
        workdir = tmp_path / "work"
        workdir.mkdir()
        client = build_client(config_dir, monkeypatch)

        try:
            # List a local directory
            resp = client.get(
                "/api/v1/boxes/local/ls",
                params={"directory": str(workdir)},
                headers=auth_headers(),
            )
            assert resp.status_code == 200

            # Check that no visit was recorded for local box
            results = state.search_directories("local", str(workdir), limit=10)
            assert len(results) == 0
        finally:
            client.close()
            state.reset_state()
