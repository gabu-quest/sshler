"""Tests for frecency-based directory search functionality."""

import asyncio
import math
import os
import sys
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


@pytest.fixture(
    params=[
        pytest.param(
            "gnu",
            marks=pytest.mark.skipif(
                sys.platform == "win32",
                reason=(
                    "GNU find path runs only on a POSIX local box; Windows uses the built-in walk"
                ),
            ),
        ),
        "builtin",
    ]
)
def local_impl(request, monkeypatch) -> str:
    """Select the local discovery implementation (GNU find or the built-in walk).

    On Linux every test using it runs against both on the same tree, so the
    built-in walk Windows depends on cannot drift from find's results.
    """
    monkeypatch.setattr("sshler.api.helpers.LOCAL_IS_WINDOWS", request.param == "builtin")
    return request.param


def discover(root: Path, pattern: str, **kwargs) -> list[tuple[str, bool]]:
    from sshler.api.search import _discover_local

    return asyncio.run(_discover_local(str(root), pattern, **kwargs))


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
        assert results[1][0] == "/projects/other"  # 2 visits
        assert results[2][0] == "/projects/demo"  # 1 visit

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

    def test_search_result_structure(self, tmp_path, monkeypatch, local_impl):
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
                    "path": (root / "testdisc").as_posix(),
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

    def test_finds_nested_directories(self, tmp_path, local_impl):
        root = make_tree(tmp_path)
        results = discover(root, "*captur*")

        assert sorted(results) == [
            ((root / "gadgets" / "captures").as_posix(), True),
            ((root / "gadgets" / "captures" / "capture_x").as_posix(), True),
        ]

    def test_dirs_only_by_default(self, tmp_path, local_impl):
        root = make_tree(tmp_path)
        assert discover(root, "*blue_vs*") == []

    def test_finds_files_when_enabled_and_prunes_node_modules(self, tmp_path, local_impl):
        root = make_tree(tmp_path)
        results = discover(root, "*blue*", include_files=True)

        expected_file = (
            root / "gadgets" / "captures" / "capture_x" / "exports" / "blue_vs_green_frame473.png"
        ).as_posix()
        expected_dir = (root / "docs" / "blue-notes").as_posix()
        assert sorted(results) == sorted([(expected_file, False), (expected_dir, True)])

    def test_respects_limit(self, tmp_path, local_impl):
        root = tmp_path / "many"
        root.mkdir()
        for i in range(10):
            (root / f"bluedir_{i}").mkdir()
        results = discover(root, "*blue*", limit=3)
        assert len(results) == 3
        every = {((root / f"bluedir_{i}").as_posix(), True) for i in range(10)}
        assert set(results) <= every
        assert len(set(results)) == 3

    def test_depth_limit(self, tmp_path, local_impl):
        """find -maxdepth 6: an entry 6 levels below root is listed, 7 is not.

        Mutation killed: an off-by-one depth (5 or 7) in the built-in walk.
        """
        root = tmp_path / "deep"
        level = root
        for depth in range(1, 8):
            level = level / f"lvl{depth}"
        level.mkdir(parents=True)
        listed = sorted(path for path, _ in discover(root, "lvl*"))
        assert listed == [
            (root / "/".join(f"lvl{d}" for d in range(1, n + 1))).as_posix() for n in range(1, 7)
        ]

    def test_prune_names_are_exact_and_case_sensitive(self, tmp_path, local_impl):
        """find -name is case-sensitive: .Git and Node_Modules are searched, the
        PRUNE_DIRS names are neither listed nor entered, whatever they are.

        Mutations killed: case-folded prune names, pruning only directories
        (a file named .cache listed), descending into a pruned directory.
        """
        # separate parents: on a case-insensitive file system (Windows) the
        # differently cased names would otherwise be one directory
        root = tmp_path / "prune"
        (root / "a" / ".Git" / "blue_a").mkdir(parents=True)
        (root / "b" / "Node_Modules" / "blue_b").mkdir(parents=True)
        (root / "c" / "node_modules" / "blue_c").mkdir(parents=True)
        (root / "c" / "__pycache__" / "blue_d").mkdir(parents=True)
        (root / "c" / ".cache").write_text("x")
        results = discover(root, "*", include_files=True)
        assert sorted(results) == [
            ((root / "a").as_posix(), True),
            ((root / "a" / ".Git").as_posix(), True),
            ((root / "a" / ".Git" / "blue_a").as_posix(), True),
            ((root / "b").as_posix(), True),
            ((root / "b" / "Node_Modules").as_posix(), True),
            ((root / "b" / "Node_Modules" / "blue_b").as_posix(), True),
            ((root / "c").as_posix(), True),
        ]

    def test_glob_is_case_insensitive_and_root_is_never_listed(self, tmp_path, local_impl):
        """Mutations killed: a case-sensitive match, `?` matching zero or two
        characters, the root itself listed when its name matches."""
        root = tmp_path / "blue_root"
        (root / "BLUE_upper").mkdir(parents=True)
        (root / "frame473").mkdir()
        (root / "frame4730").mkdir()
        (root / "frame47").mkdir()
        assert discover(root, "*blue*") == [((root / "BLUE_upper").as_posix(), True)]
        assert discover(root, "frame?73") == [((root / "frame473").as_posix(), True)]

    def test_symlinks_are_neither_listed_nor_entered(self, tmp_path, local_impl):
        """find -P: a symlink is type l, so -type d/-type f never list it and the
        walk never enters it. Mutation killed: following symlinks."""
        outside = tmp_path / "outside"
        (outside / "blue_inside_target").mkdir(parents=True)
        (outside / "blue_file.txt").write_text("x")
        root = tmp_path / "root"
        (root / "blue_real").mkdir(parents=True)
        try:
            os.symlink(outside, root / "blue_link", target_is_directory=True)
            os.symlink(outside / "blue_file.txt", root / "blue_file_link.txt")
        except OSError as exc:
            pytest.skip(f"this OS refused to create a symlink: {exc}")
        results = discover(root, "*blue*", include_files=True)
        assert results == [((root / "blue_real").as_posix(), True)]


class TestBuiltinDiscoveryTimeout:
    def test_past_the_deadline_returns_nothing(self, tmp_path):
        """As the GNU path does when find is killed. Mutation killed: the
        built-in walk ignoring its timeout."""
        from sshler.api.search import _discover_local_builtin

        root = make_tree(tmp_path)
        assert _discover_local_builtin(str(root), "*", 6, 50, True, -1) == []
        everything = _discover_local_builtin(str(root), "*", 6, 50, True, 60)
        # gadgets, captures, capture_x, exports, the png, docs, blue-notes
        assert len(everything) == 7


class TestSearchEndpointDiscovery:
    """Endpoint-level tests for root-scoped discovery + file results."""

    def test_local_search_finds_file_under_root(self, tmp_path, monkeypatch, local_impl):
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

            discovery = [r for r in resp.json()["results"] if r["source"] == "discovery"]
            assert len(discovery) == 1
            assert (
                discovery[0]["path"]
                == (
                    root
                    / "gadgets"
                    / "captures"
                    / "capture_x"
                    / "exports"
                    / "blue_vs_green_frame473.png"
                ).as_posix()
            )
            assert discovery[0]["is_directory"] is False
        finally:
            client.close()
            state.reset_state()

    def test_local_search_wildcard_query(self, tmp_path, monkeypatch, local_impl):
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
            discovery = [r for r in resp.json()["results"] if r["source"] == "discovery"]
            assert len(discovery) == 1
            assert (
                discovery[0]["path"]
                == (
                    root
                    / "gadgets"
                    / "captures"
                    / "capture_x"
                    / "exports"
                    / "blue_vs_green_frame473.png"
                ).as_posix()
            )
        finally:
            client.close()
            state.reset_state()

    def test_local_search_dirs_only_without_files_flag(self, tmp_path, monkeypatch, local_impl):
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
            discovery = [r for r in resp.json()["results"] if r["source"] == "discovery"]
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


@pytest.mark.posix_only("#!/bin/sh stand-ins for find and zoxide first on PATH")
class TestLocalChildrenKilledOnTimeout:
    def test_find_is_gone_after_a_discovery_timeout(self, tmp_path, monkeypatch, stalled_program):
        """Mutation killed: proc.kill() without a wait, or no kill at all."""
        from sshler.api import search

        gone = stalled_program("find")
        monkeypatch.setattr("sshler.api.helpers.LOCAL_IS_WINDOWS", False)
        monkeypatch.setattr(search, "LOCAL_DISCOVERY_TIMEOUT", 0.5)
        assert asyncio.run(search._discover_local(str(tmp_path), "*x*")) == []
        gone()

    def test_zoxide_is_gone_after_its_timeout(self, monkeypatch, stalled_program):
        """Mutation killed: wait_for(proc.communicate()) with no kill."""
        from sshler.api import search

        gone = stalled_program("zoxide")
        monkeypatch.setattr(search, "ZOXIDE_TIMEOUT", 0.5)
        assert asyncio.run(search._query_zoxide("abc")) == []
        gone()


class _Home:
    """Answers the home lookup, records every other command."""

    def __init__(self, home_stdout="/home/u", status=0):
        self.home_stdout = home_stdout
        self.status = status
        self.commands: list[str] = []

    async def run(self, command, check=False):
        from types import SimpleNamespace

        if command == 'printf %s "$HOME"':
            return SimpleNamespace(stdout=self.home_stdout, exit_status=self.status)
        self.commands.append(command)
        return SimpleNamespace(stdout="", exit_status=0)


def _remote_find(root, **kw):
    from sshler.api.search import _discover_remote_directories

    conn = _Home(**kw)
    result = asyncio.run(_discover_remote_directories(conn, "*x*", root=root))
    return result, conn.commands


class TestRemoteRootUnderHome:
    @pytest.mark.parametrize(
        ("root", "sent"),
        [
            ("~/projects", "/home/u/projects"),
            ("~/a b", "'/home/u/a b'"),
            ("~/$(touch x)", "'/home/u/$(touch x)'"),
            ("~/a;b", "'/home/u/a;b'"),
            ("~//etc", "/home/u/etc"),
            ("~", "/home/u"),
        ],
    )
    def test_the_command_names_the_absolute_root(self, root, sent):
        """Mutation killed: shlex.quote on '~/projects', which the remote shell takes literally."""
        _, commands = _remote_find(root)
        assert commands == [
            f"find {sent} -maxdepth 4 -type d -iname '*x*' 2>/dev/null | head -n 50"
        ]

    @pytest.mark.parametrize("root", [None, ""])
    def test_no_root_still_means_the_remote_home(self, root):
        _, commands = _remote_find(root)
        assert commands == [
            "find /home/u -maxdepth 4 -type d -iname '*x*' 2>/dev/null | head -n 50"
        ]

    def test_another_users_home_is_quoted_whole(self):
        _, commands = _remote_find("~other/x")
        assert commands == [
            "find '~other/x' -maxdepth 4 -type d -iname '*x*' 2>/dev/null | head -n 50"
        ]

    @pytest.mark.parametrize("kw", [{"home_stdout": ""}, {"status": 1}])
    def test_a_failed_home_lookup_finds_nothing_and_sends_no_find(self, kw):
        result, commands = _remote_find("~/p", **kw)
        assert (result, commands) == ([], [])

    def test_an_invalid_root_finds_nothing(self):
        result, commands = _remote_find("~/a\x00b")
        assert (result, commands) == ([], [])


class TestRemoteRootNamedLikeAnOption:
    def test_a_relative_root_starting_with_a_dash_is_sent_as_a_path(self):
        """Mutation: send the root as it came; find reads `-delete` as an expression."""
        _, commands = _remote_find("-delete")
        assert commands == [
            "find ./-delete -maxdepth 4 -type d -iname '*x*' 2>/dev/null | head -n 50"
        ]


class _FindBox:
    """A remote box whose connection answers the pool's health check and the home
    lookup, records every other command line and answers it with ``stdout``; nothing
    is run."""

    def __init__(self, stdout: str = "") -> None:
        self.stdout = stdout
        self.commands: list[str] = []
        self.refuse = False

    async def connect(self, box, application_config):
        from sshler.ssh import SSHError

        if self.refuse:
            raise SSHError("unreachable")
        return self

    async def run(self, command, check=False, encoding="utf-8"):
        from types import SimpleNamespace

        if command == "echo test":
            return SimpleNamespace(stdout="test\n", exit_status=0)
        if command == 'printf %s "$HOME"':
            return SimpleNamespace(stdout="/home/u", exit_status=0)
        self.commands.append(command)
        return SimpleNamespace(stdout=self.stdout, exit_status=0)

    def close(self) -> None:
        pass


@pytest.fixture
def remote_search(tmp_path, monkeypatch):
    from sshler import ssh_pool
    from sshler.api.dependencies import APIDependencies
    from sshler.ssh_pool import SSHConnectionPool

    config_dir = setup_config(tmp_path)
    ssh_config = tmp_path / "ssh_config"
    ssh_config.write_text("Host rbox\n  HostName example.invalid\n  User u\n", encoding="utf-8")
    monkeypatch.setenv("SSHLER_SSH_CONFIG", str(ssh_config))
    state.initialize(config_dir)
    state.record_directory_visit("rbox", "/srv/proj-a")
    box = _FindBox(stdout="/home/u/proj-b\n/srv/proj-a\n")

    async def connect_for_box(deps_self, target, application_config):
        return await box.connect(target, application_config)

    monkeypatch.setattr(APIDependencies, "connect_for_box", connect_for_box)
    monkeypatch.setattr(ssh_pool, "_global_pool", SSHConnectionPool())
    client = build_client(config_dir, monkeypatch)
    try:
        yield box, client
    finally:
        client.close()
        state.reset_state()


def _search_rbox(client, **params):
    return client.get("/api/v1/boxes/rbox/search", params=params, headers=auth_headers())


class TestRemoteSearchRoute:
    def test_frecency_first_then_discovery_without_the_path_seen_twice(self, remote_search):
        box, client = remote_search
        resp = _search_rbox(client, q="proj")
        assert resp.status_code == 200
        results = [(r["path"], r["source"], r["is_directory"]) for r in resp.json()["results"]]
        assert results == [
            ("/srv/proj-a", "frecency", True),
            ("/home/u/proj-b", "discovery", True),
        ]
        assert resp.json()["results"][1]["score"] == 0.1
        assert box.commands == [
            "find /home/u -maxdepth 4 -type d -iname '*proj*' 2>/dev/null | head -n 20"
        ]

    def test_a_box_that_cannot_be_reached_gives_the_frecency_results(self, remote_search):
        box, client = remote_search
        box.refuse = True
        resp = _search_rbox(client, q="proj")
        assert resp.status_code == 200
        assert [r["path"] for r in resp.json()["results"]] == ["/srv/proj-a"]
        assert box.commands == []

    def test_a_bad_root_is_400_before_the_box_is_reached(self, remote_search):
        box, client = remote_search
        resp = _search_rbox(client, q="proj", root="/srv/a\x00b")
        assert (resp.status_code, resp.json()) == (
            400,
            {"detail": "Path cannot contain null bytes", "reason": "invalid"},
        )
        assert box.commands == []

    @pytest.mark.parametrize(
        ("q", "root"),
        [
            ("*a?b*", "/srv/a b"),
            ("it's", "/srv/it's"),
            ("$HOME", "/srv/$(x)"),
            ("-delete", "-delete"),
            ("a`b`;c", "/srv/a;b`c`"),
        ],
    )
    def test_a_hostile_query_and_root_each_reach_find_as_one_word(self, remote_search, q, root):
        import shlex

        box, client = remote_search
        assert _search_rbox(client, q=q, root=root).status_code == 200
        pattern = q if ("*" in q or "?" in q) else f"*{q}*"
        sent_root = f"./{root}" if root.startswith("-") else root
        assert len(box.commands) == 1
        words = shlex.split(box.commands[0])
        assert words == [
            "find",
            sent_root,
            "-maxdepth",
            "4",
            "-type",
            "d",
            "-iname",
            pattern,
            "2>/dev/null",
            "|",
            "head",
            "-n",
            "20",
        ]
