"""Tests for grep (file content search) API.

Local content search has two implementations: GNU grep on a POSIX local box and
the built-in walker (``_grep_builtin``) on a Windows local box, which has no
grep. The ``local_impl`` fixture runs every local test against both on Linux
(the platform flag is switched, the tree is the same), so the two cannot drift;
on Windows only the built-in one exists.
"""

import os
import sys
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from sshler.webapp import ServerSettings, make_app

TEST_TOKEN = "grep-test-token"


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


def write(path: Path, text: str) -> None:
    """Write exact bytes: text mode on Windows would turn every \\n into \\r\\n."""
    path.write_bytes(text.encode("utf-8"))


def client_path(path: Path) -> str:
    """The form the local routes return a path in: resolved, ``/`` separators."""
    return path.resolve().as_posix()


@pytest.fixture(
    params=[
        pytest.param(
            "gnu",
            marks=pytest.mark.skipif(
                sys.platform == "win32",
                reason=(
                    "GNU grep path runs only on a POSIX local box; "
                    "Windows uses the built-in search"
                ),
            ),
        ),
        "builtin",
    ]
)
def local_impl(request, monkeypatch) -> str:
    """Select the local search implementation the endpoint dispatches to."""
    monkeypatch.setattr("sshler.api.helpers.LOCAL_IS_WINDOWS", request.param == "builtin")
    return request.param


def grep(client: TestClient, directory: Path, pattern: str, **params) -> dict:
    resp = client.get(
        "/api/v1/boxes/local/grep",
        params={"pattern": pattern, "directory": str(directory), **params},
        headers=auth_headers(),
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def hits(data: dict) -> set[tuple[str, int, str]]:
    return {(m["file"], m["line_number"], m["line"]) for m in data["matches"]}


@pytest.fixture
def grep_client(tmp_path, monkeypatch, local_impl):
    client = build_client(setup_config(tmp_path), monkeypatch)
    try:
        yield client
    finally:
        client.close()


class TestGrepSearch:
    def test_grep_finds_content(self, tmp_path, grep_client):
        """Mutations killed: a `C:\\` directory or file path, a 0-based line number,
        the line returned with its newline, the wrong file."""
        workdir = tmp_path / "work"
        workdir.mkdir()
        write(workdir / "greet.py", "def greet():\n    print('Hello World')\n")
        write(workdir / "other.txt", "nothing here\n")

        data = grep(grep_client, workdir, "Hello World")
        assert data["box"] == "local"
        assert data["pattern"] == "Hello World"
        assert data["directory"] == client_path(workdir)
        assert data["matches"] == [
            {
                "file": client_path(workdir / "greet.py"),
                "line_number": 2,
                "line": "    print('Hello World')",
            }
        ]
        assert data["truncated"] is False

    def test_grep_case_insensitive(self, tmp_path, grep_client):
        """Mutation killed: case_sensitive=false still matching case-sensitively."""
        workdir = tmp_path / "work"
        workdir.mkdir()
        write(workdir / "test.txt", "FOO\nfoo\nFoO\nbar\n")

        data = grep(grep_client, workdir, "foo", case_sensitive="false")
        assert sorted(m["line_number"] for m in data["matches"]) == [1, 2, 3]

    def test_grep_case_sensitive(self, tmp_path, grep_client):
        """Mutation killed: case_sensitive=true ignoring case."""
        workdir = tmp_path / "work"
        workdir.mkdir()
        write(workdir / "test.txt", "FOO\nfoo\nFoO\n")

        data = grep(grep_client, workdir, "foo", case_sensitive="true")
        assert hits(data) == {(client_path(workdir / "test.txt"), 2, "foo")}

    def test_grep_no_results(self, tmp_path, grep_client):
        workdir = tmp_path / "work"
        workdir.mkdir()
        write(workdir / "test.txt", "nothing relevant\n")

        data = grep(grep_client, workdir, "xyznonexistent")
        assert data["matches"] == []
        assert data["truncated"] is False

    def test_grep_multiple_files(self, tmp_path, grep_client):
        """Mutation killed: the walk stopping after the first matching file."""
        workdir = tmp_path / "work"
        workdir.mkdir()
        write(workdir / "a.txt", "target line 1\n")
        write(workdir / "b.txt", "target line 2\n")
        write(workdir / "c.txt", "no match\n")

        data = grep(grep_client, workdir, "target")
        assert hits(data) == {
            (client_path(workdir / "a.txt"), 1, "target line 1"),
            (client_path(workdir / "b.txt"), 1, "target line 2"),
        }

    def test_grep_pattern_too_long(self, tmp_path, monkeypatch):
        config_dir = setup_config(tmp_path)
        client = build_client(config_dir, monkeypatch)
        try:
            resp = client.get(
                "/api/v1/boxes/local/grep",
                params={"pattern": "x" * 501, "directory": "/tmp"},
                headers=auth_headers(),
            )
            assert resp.status_code == 400
            assert "too long" in resp.json()["detail"].lower()
        finally:
            client.close()

    def test_grep_null_bytes_rejected(self, tmp_path, monkeypatch):
        config_dir = setup_config(tmp_path)
        client = build_client(config_dir, monkeypatch)
        try:
            resp = client.get(
                "/api/v1/boxes/local/grep",
                params={"pattern": "test\x00evil", "directory": "/tmp"},
                headers=auth_headers(),
            )
            assert resp.status_code == 400
            assert "null" in resp.json()["detail"].lower()
        finally:
            client.close()


@pytest.fixture
def tree(tmp_path) -> Path:
    """Deterministic content tree (every line listed, LF endings):

    tree/
      src/app.py         1 "def main():"  2 "    total = 100 + 23"  3 "    return (total)"
                         4 "# end"
      src/nested/deep/util.txt   1 "Foo bar"  2 "foobar"  3 "x+y {1}"  4 "a|b"  5 "a*b"
      node_modules/pkg/index.js  1 "foo in node_modules"
      .git/HEAD                  1 "foo in git"
      blob.bin                   b"foo\\0bar\\n" (binary: never reported)
      crlf.txt                   1 "foo crlf\\r"
    """
    root = tmp_path / "tree"
    (root / "src" / "nested" / "deep").mkdir(parents=True)
    (root / "src" / "app.py").write_bytes(
        b"def main():\n    total = 100 + 23\n    return (total)\n# end\n"
    )
    (root / "src" / "nested" / "deep" / "util.txt").write_bytes(
        b"Foo bar\nfoobar\nx+y {1}\na|b\na*b\n"
    )
    (root / "node_modules" / "pkg").mkdir(parents=True)
    (root / "node_modules" / "pkg" / "index.js").write_bytes(b"foo in node_modules\n")
    (root / ".git").mkdir()
    (root / ".git" / "HEAD").write_bytes(b"foo in git\n")
    (root / "blob.bin").write_bytes(b"foo\0bar\n")
    (root / "crlf.txt").write_bytes(b"foo crlf\r\n")
    return root


APP = "src/app.py"
UTIL = "src/nested/deep/util.txt"


class TestGrepSemantics:
    """Results both implementations must agree on, each a hand-known exact set.

    The built-in walker must reproduce GNU grep's basic-regex reading of the
    pattern, its unpruned recursion, its binary-file rule and its limits.
    """

    @pytest.mark.parametrize(
        ("pattern", "case_sensitive", "expected"),
        [
            # plain substring, case folded: no pruning of node_modules or .git,
            # binary file skipped, CR kept on a CRLF line
            (
                "foo",
                "false",
                {
                    (UTIL, 1, "Foo bar"),
                    (UTIL, 2, "foobar"),
                    ("node_modules/pkg/index.js", 1, "foo in node_modules"),
                    (".git/HEAD", 1, "foo in git"),
                    ("crlf.txt", 1, "foo crlf\r"),
                },
            ),
            ("^def", "true", {(APP, 1, "def main():")}),
            ("23$", "true", {(APP, 2, "    total = 100 + 23")}),
            ("^ *return", "true", {(APP, 3, "    return (total)")}),
            # bare + ( ) { } | are literals in a basic regex
            ("x+y", "true", {(UTIL, 3, "x+y {1}")}),
            ("(total)", "true", {(APP, 3, "    return (total)")}),
            ("{1}", "true", {(UTIL, 3, "x+y {1}")}),
            ("a|b", "true", {(UTIL, 4, "a|b")}),
            # GNU escapes are operators
            ("main\\|end", "true", {(APP, 1, "def main():"), (APP, 4, "# end")}),
            ("[[:digit:]]\\{3\\}", "true", {(APP, 2, "    total = 100 + 23")}),
            ("\\(o\\)\\1b", "true", {(UTIL, 2, "foobar")}),
            ("\\<bar\\>", "true", {(UTIL, 1, "Foo bar")}),
            ("o\\+b", "true", {(UTIL, 2, "foobar")}),
            # * at the start is a literal star; . is any character
            ("*", "true", {(UTIL, 5, "a*b")}),
            # after a character * is "zero or more": a*b is any line with a b
            (
                "a*b",
                "true",
                {(UTIL, 1, "Foo bar"), (UTIL, 2, "foobar"), (UTIL, 4, "a|b"), (UTIL, 5, "a*b")},
            ),
            ("f.ob", "true", {(UTIL, 2, "foobar")}),
            ("[^a-z ]oo", "true", {(UTIL, 1, "Foo bar")}),
            # an invalid pattern is no matches (GNU grep exits 2), not an error
            ("[abc", "true", set()),
            ("foo\\", "true", set()),
        ],
    )
    def test_pattern_semantics(self, tree, grep_client, pattern, case_sensitive, expected):
        """Mutations killed: the pattern compiled as a Python regex untranslated
        (`x+y`, `(total)`, `a|b`, `main\\|end` change), escaped as a literal string
        (`^def`, `main\\|end`, `\\<bar\\>` change), node_modules/.git pruned, binary
        files read, CR stripped, an invalid pattern raising a 500."""
        data = grep(grep_client, tree, pattern, case_sensitive=case_sensitive)
        base = client_path(tree)
        assert hits(data) == {(f"{base}/{rel}", n, line) for rel, n, line in expected}
        assert data["truncated"] is False

    def test_limit_caps_matches_and_sets_truncated(self, tmp_path, grep_client):
        """Mutations killed: no overall cap (6 matches), the cap applied per file
        only, truncated left False when the limit is reached, lines out of order."""
        workdir = tmp_path / "work"
        workdir.mkdir()
        write(workdir / "one.txt", "hit\n" * 3)
        write(workdir / "two.txt", "hit\n" * 3)

        data = grep(grep_client, workdir, "hit", limit="4")
        per_file: dict[str, list[int]] = {}
        for match in data["matches"]:
            per_file.setdefault(match["file"], []).append(match["line_number"])
        # the first file read gives all 3 lines, the second only its first line;
        # which file is read first is directory order, so compare sorted
        assert sorted(per_file.values()) == [[1], [1, 2, 3]]
        assert data["truncated"] is True

    def test_limit_not_reached_is_not_truncated(self, tmp_path, grep_client):
        workdir = tmp_path / "work"
        workdir.mkdir()
        write(workdir / "one.txt", "hit\n" * 3)

        data = grep(grep_client, workdir, "hit", limit="4")
        assert [m["line_number"] for m in data["matches"]] == [1, 2, 3]
        assert data["truncated"] is False

    def test_symlinks_below_the_directory_are_not_followed(self, tmp_path, grep_client):
        """Mutation killed: the walk following a symlinked file or directory
        (GNU `grep -r` follows only command-line symlinks)."""
        outside = tmp_path / "outside"
        outside.mkdir()
        write(outside / "secret.txt", "needle outside\n")
        workdir = tmp_path / "work"
        workdir.mkdir()
        write(workdir / "real.txt", "needle inside\n")
        try:
            os.symlink(outside / "secret.txt", workdir / "link.txt")
            os.symlink(outside, workdir / "linkdir", target_is_directory=True)
        except OSError as exc:
            pytest.skip(f"this OS refused to create a symlink: {exc}")

        data = grep(grep_client, workdir, "needle")
        assert hits(data) == {(client_path(workdir / "real.txt"), 1, "needle inside")}

    def test_directory_that_does_not_exist(self, tmp_path, grep_client):
        data = grep(grep_client, tmp_path / "missing", "x")
        assert data["matches"] == []
        assert data["truncated"] is False


class TestGrepBuiltinTimeout:
    def test_past_the_deadline_is_504(self, tree, monkeypatch):
        """Mutation killed: the built-in walk ignoring GREP_TIMEOUT."""
        monkeypatch.setattr("sshler.api.helpers.LOCAL_IS_WINDOWS", True)
        monkeypatch.setattr("sshler.api.grep.GREP_TIMEOUT", -1)
        client = build_client(setup_config(tree.parent), monkeypatch)
        try:
            resp = client.get(
                "/api/v1/boxes/local/grep",
                params={"pattern": "foo", "directory": str(tree)},
                headers=auth_headers(),
            )
            assert resp.status_code == 504
            assert resp.json()["detail"] == "Search timed out"
        finally:
            client.close()


class TestGrepInjectionPrevention:
    """A pattern is data: it is matched as text and never run as a command.

    Each pattern would create ``marker`` if a shell ran it. Mutation killed:
    the GNU path joining its argv into a string for
    `asyncio.create_subprocess_shell` (the marker appears and the literal line
    stops matching).
    """

    @pytest.mark.parametrize(
        "template",
        ["; touch {marker}", "$(touch {marker})", "`touch {marker}`", "| touch {marker}"],
    )
    def test_shell_syntax_is_matched_literally(self, tmp_path, grep_client, template):
        workdir = tmp_path / "work"
        workdir.mkdir()
        marker = (tmp_path / "pwned").as_posix()
        pattern = template.format(marker=marker)
        write(workdir / "safe.txt", f"before\nsays {pattern} here\n")

        data = grep(grep_client, workdir, pattern, case_sensitive="true")
        assert hits(data) == {
            (client_path(workdir / "safe.txt"), 2, f"says {pattern} here")
        }
        assert not Path(marker).exists()
        assert (workdir / "safe.txt").read_bytes() == f"before\nsays {pattern} here\n".encode()
