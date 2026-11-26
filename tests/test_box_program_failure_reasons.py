"""The reason of every failure of a route that runs a program on a box, the same on both
kinds of box.

One table of rows (route, the failure made to happen, the expected status, reason and
text), run against the local box and a remote box, and beside each failure the ordinary
answer it used to be confused with (a folder that is not a repository, a repository with
no commit, no matches, no sessions, a reachable box).

Nothing runs. The local box's programs are a fake ``run_bounded``, a fake
``run_local_tmux`` and a fake ``shutil.which``; the remote box is a fake connection whose
command lines are read with ``shlex.split`` only, and answered from the same programs
(``Scene.answer``). A program the box does not have is no ``which`` result on the local
box and the shell's status 127 on the remote one; a program past its limit raises
``TimeoutError`` on the local box (what ``run_bounded`` raises) and never answers on the
remote one, so the route's own limit ends it.
"""

from __future__ import annotations

import asyncio
import json
import os
import posixpath
import shlex
from collections.abc import Callable, Iterator
from dataclasses import dataclass, replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import asyncssh
import pytest
import yaml
from fastapi.testclient import TestClient

from sshler import ssh_pool, state
from sshler import tmux as tmux_module
from sshler.api import box_runner as box_runner_api
from sshler.api import boxes as boxes_api
from sshler.api import dependencies as dependencies_api
from sshler.api import git as git_api
from sshler.api import grep as grep_api
from sshler.api import search as search_api
from sshler.api import sessions as sessions_api
from sshler.api import stats_stream
from sshler.api.dependencies import APIDependencies
from sshler.api.models import APIBoxStats
from sshler.config import AppConfig
from sshler.config import Box as ConfigBox
from sshler.ssh import SSHError
from sshler.ssh_pool import SSHConnectionPool
from sshler.tmux import LiveSessions
from sshler.webapp import ServerSettings, make_app

pytestmark = pytest.mark.posix_only("POSIX paths for the fake programs")

TOKEN = "program-failure-token"
HEADERS = {"X-SSHLER-TOKEN": TOKEN}
REMOTE = "lb"
HEALTH = "echo test"
HOME = 'printf %s "$HOME"'
# Every limit a route sets itself; the bound of a polled request is longer, so the
# route's own limit is the one that ends a program that never answers.
LIMIT = 0.1
PROBE_LIMIT = 0.6
GREP_REFUSED = "grep: Unmatched [, [^, [:, [., or [="
TMUX_REFUSED = "can't find session: work"
PROC_FIRST = "cpu  100 0 100 800 0 0 0 0 0 0\n"
PROC_SECOND = (
    "3600.00 7000.00\n"
    "MemTotal:        2048000 kB\nMemAvailable:    1024000 kB\n"
    "cpu  200 0 200 1600 0 0 0 0 0 0\n"
)


class StuckError(Exception):
    """The program never answers (the fake box raises or waits, by kind)."""


@dataclass(frozen=True)
class Scene:
    """What the box does: a program it does not have, a program that never answers, a
    program that says no, a folder that is not a repository, a repository with no
    commit; on a remote box, a connect that fails or a connection that drops while a
    command runs. ``fails`` names a git subcommand that fails in a repository with a
    commit, ``unknown`` a ref that names nothing there, ``unreadable`` a grep that
    found a match and could not read another file."""

    missing: str | None = None
    stuck: str | None = None
    refuses: bool = False
    repo: bool = True
    commits: bool = True
    unreachable: bool = False
    dropped: bool = False
    fails: str | None = None
    unknown: str | None = None
    unreadable: bool = False
    # The error output of a program that says no, as bytes, in place of its words.
    raw_errors: bytes | None = None

    def errors(self, status: int, err: str) -> bytes:
        if status != 0 and self.raw_errors is not None:
            return self.raw_errors
        return err.encode()

    def answer(self, words: list[str], root: str) -> tuple[int, str, str]:
        """The exit status, output and error output of ``words`` on this box."""
        name = posixpath.basename(words[0])
        if name == self.stuck:
            raise StuckError(name)
        args = words[1:]
        if name == "git":
            return self._git(args, root)
        if name == "grep":
            return self._grep(words)
        if name == "tmux":
            return (1, "", TMUX_REFUSED + "\n") if self.refuses else (0, "line\n", "")
        if name == "sh":  # the session listing: no sessions
            return (1, "", "") if self.refuses else (0, "", "")
        if name == "true":
            return 0, "", ""
        if name == "test":  # asked after a grep that could not search
            there = os.path.exists(args[1]) if args[0] == "-e" else os.access(args[1], os.R_OK)
            return (0 if there else 1), "", ""
        if name == "cat":
            if self.refuses:
                return 1, "", ""
            return 0, PROC_FIRST if args == ["/proc/stat"] else PROC_SECOND, ""
        if name == "find":
            if "-printf" in args:  # the local walk prints the type and the path
                return 0, f"d\t{root}/xfound\n", ""
            return 0, f"{root}/xfound\n", ""
        raise AssertionError(f"unexpected program: {words!r}")

    def _grep(self, words: list[str]) -> tuple[int, str, str]:
        """grep names itself by the word it was started with, as the real one does."""
        folder = words[-1]
        if self.refuses:
            return 2, "", GREP_REFUSED + "\n"
        if folder == "/dev/null":  # the pattern check: the pattern is fine
            return 1, "", ""
        if self.unreadable:
            found = "a.txt:1:one\n" if words[-2] == "one" else ""
            return 2, found, f"{words[0]}: {folder}/locked: Permission denied\n"
        if not os.path.isdir(folder):
            return 2, "", f"{words[0]}: {folder}: No such file or directory\n"
        return 1, "", ""

    def _git(self, args: list[str], root: str) -> tuple[int, str, str]:
        if not self.repo:
            return 128, "", "fatal: not a git repository\n"
        if self.commits and args[:1] == [self.fails]:
            return 128, "", f"fatal: {self.fails} failed\n"
        if self.commits and self.unknown in args:
            if args[:2] == ["rev-parse", "--verify"]:
                return 1, "", ""
            if args[:1] == ["diff"]:
                return 128, "", f"fatal: bad revision '{self.unknown}'\n"
        if args[:2] == ["rev-parse", "--show-toplevel"]:
            return 0, root + "\n", ""
        if args[:2] == ["rev-parse", "--git-dir"]:
            return 0, ".git\nmain\n", ""
        if args[:2] == ["rev-parse", "--short"]:
            return (0, "abc1234\n", "") if self.commits else (128, "", "")
        if args[:2] == ["rev-parse", "--verify"]:
            return (0, "abc1234\n", "") if self.commits else (1, "", "")
        if args[:1] == ["log"]:
            return (0, "", "") if self.commits else (128, "", "")
        if args[:1] == ["blame"]:
            return (128, "", "fatal: no such path\n") if self.refuses else (0, "", "")
        if args[:1] == ["show"]:
            return 0, "one\n", ""
        if args[:1] == ["branch"]:
            return 0, "main|*|abc1234\n", ""
        if args[:1] == ["diff"]:
            return (0, "", "") if self.commits else (128, "", "fatal: bad revision 'HEAD'\n")
        if args[:1] in (["status"], ["cat-file"]):
            return 0, "", ""
        raise AssertionError(f"unexpected git: {args!r}")


def _words(line: str) -> list[str]:
    """The program and its words of a remote command line, read with ``shlex.split``:
    a leading ``cd <folder> &&``, the error output's redirection and anything after a
    pipe or a second command left out."""
    tokens = shlex.split(line)
    if tokens[:1] == ["cd"]:
        tokens = tokens[tokens.index("&&") + 1 :]
    words: list[str] = []
    for token in tokens:
        if token in {"|", ";", "&&"}:
            break
        if token != "2>/dev/null":
            words.append(token)
    return words


class FakeConnection:
    """A remote box: answers each command line from ``Scene.answer``."""

    def __init__(self, box: Box) -> None:
        self.box = box
        self.closed = False

    async def run(self, command: str, check: bool = False, encoding: str | None = "utf-8"):
        if command == HEALTH:
            return SimpleNamespace(exit_status=0, stdout="test\n", stderr="")
        if command == HOME:
            return SimpleNamespace(exit_status=0, stdout="/home/t", stderr="")
        scene = self.box.scene
        if scene.dropped:
            raise asyncssh.ConnectionLost("connection dropped")
        # A line that begins by checking for a program exits 127 when it is not there.
        if command.startswith("command -v "):
            first, _, command = command.partition("; ")
            if shlex.split(first)[2] == scene.missing:
                return SimpleNamespace(exit_status=127, stdout="", stderr="")
        words = _words(command)
        if posixpath.basename(words[0]) == scene.missing or (
            words[0] == "sh" and scene.missing == "tmux"
        ):
            # A shell gives a pipeline the status of its last command: only a line that
            # is one command has the 127 of the command it could not find.
            piped = "|" in shlex.split(command)
            return SimpleNamespace(exit_status=0 if piped else 127, stdout="", stderr="")
        try:
            status, out, err = scene.answer(words, self.box.root)
        except StuckError:
            await asyncio.Event().wait()  # never answers: the route's limit ends it
            raise
        return SimpleNamespace(exit_status=status, stdout=out, stderr=scene.errors(status, err))

    def close(self) -> None:
        self.closed = True


@dataclass
class Box:
    """The box under test: its name, the scene it plays and the repository folder."""

    name: str
    root: str
    scene: Scene
    client: TestClient

    @property
    def remote(self) -> bool:
        return self.name == REMOTE


@pytest.fixture
def box(request, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Box]:
    root = (tmp_path / "repo").resolve()
    root.mkdir()
    (root / "a.txt").write_text("one\n", encoding="utf-8")
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "boxes.yaml").write_text(yaml.safe_dump({"boxes": []}), encoding="utf-8")
    ssh_config = tmp_path / "ssh_config"
    ssh_config.write_text(f"Host {REMOTE}\n  HostName example.invalid\n", encoding="utf-8")
    monkeypatch.setenv("SSHLER_CONFIG_DIR", str(config_dir))
    monkeypatch.setenv("SSHLER_SSH_CONFIG", str(ssh_config))
    state.initialize(config_dir)
    client = TestClient(make_app(ServerSettings(csrf_token=TOKEN)))
    under_test = Box(name=request.param, root=str(root), scene=Scene(), client=client)
    _fake_programs(monkeypatch, under_test)
    try:
        yield under_test
    finally:
        client.close()
        state.reset_state()


def _fake_programs(monkeypatch: pytest.MonkeyPatch, box: Box) -> None:
    def which(name: str) -> str | None:
        return None if name == box.scene.missing else f"/usr/bin/{name}"

    async def run_bounded(argv, timeout, cwd=None, **_: Any):
        try:
            status, out, err = box.scene.answer(list(argv), box.root)
        except StuckError:
            raise TimeoutError from None  # what run_bounded raises past its limit
        return status, out.encode(), box.scene.errors(status, err)

    async def run_local_tmux(session, args, timeout=None):
        if box.scene.missing == "tmux":
            raise FileNotFoundError("tmux")
        try:
            status, out, err = box.scene.answer(["tmux", *args], box.root)
        except StuckError:
            raise TimeoutError from None
        return status, out.encode(), box.scene.errors(status, err)

    async def discover_local_sessions() -> LiveSessions:
        return LiveSessions()

    async def connect(self, target, application_config):
        if box.scene.unreachable:
            raise SSHError(f"{target.name}: connection refused")
        return FakeConnection(box)

    fake_shutil = SimpleNamespace(which=which)
    monkeypatch.setattr(box_runner_api, "shutil", fake_shutil)
    monkeypatch.setattr(sessions_api, "shutil", fake_shutil, raising=False)
    monkeypatch.setattr(search_api, "shutil", fake_shutil)
    monkeypatch.setattr(box_runner_api, "run_bounded", run_bounded)
    monkeypatch.setattr(tmux_module, "run_local_tmux", run_local_tmux)
    monkeypatch.setattr(sessions_api, "discover_local_sessions", discover_local_sessions)
    monkeypatch.setattr(APIDependencies, "connect_for_box", connect)
    monkeypatch.setattr(ssh_pool, "_global_pool", SSHConnectionPool())
    monkeypatch.setattr(dependencies_api, "PROBE_TIMEOUT_SECONDS", PROBE_LIMIT)
    monkeypatch.setattr(git_api, "GIT_TIMEOUT", LIMIT)
    monkeypatch.setattr(git_api, "BLAME_TIMEOUT", LIMIT)
    monkeypatch.setattr(boxes_api, "GIT_INFO_TIMEOUT", LIMIT)
    monkeypatch.setattr(grep_api, "GREP_TIMEOUT", LIMIT)
    monkeypatch.setattr(tmux_module, "TMUX_COMMAND_TIMEOUT", LIMIT)
    monkeypatch.setattr(tmux_module, "TMUX_CAPTURE_TIMEOUT", LIMIT)
    monkeypatch.setattr(search_api, "LOCAL_DISCOVERY_TIMEOUT", LIMIT)
    monkeypatch.setattr(search_api, "REMOTE_DISCOVERY_TIMEOUT", LIMIT, raising=False)


# --- the requests -----------------------------------------------------------------------


def _get(box: Box, route: str, **params: str):
    return box.client.get(f"/api/v1/boxes/{box.name}/{route}", params=params, headers=HEADERS)


def _new_session(box: Box) -> str:
    created = box.client.post(
        f"/api/v1/boxes/{box.name}/sessions",
        json={"session_name": "work", "working_directory": "/srv"},
        headers=HEADERS,
    )
    assert created.status_code == 200, created.text
    return created.json()["id"]


def _sessions(box: Box) -> list[tuple[str, bool]]:
    """Each stored session of the box: its name and whether it is marked active."""
    listed = box.client.get(f"/api/v1/boxes/{box.name}/sessions", headers=HEADERS)
    return [(item["session_name"], item["active"]) for item in listed.json()]


def _sync(box: Box):
    box.session_id = _new_session(box)  # type: ignore[attr-defined]
    return box.client.post(f"/api/v1/boxes/{box.name}/sessions/sync", headers=HEADERS)


def _rename(box: Box):
    session_id = _new_session(box)
    box.session_id = session_id  # type: ignore[attr-defined]
    return box.client.patch(
        f"/api/v1/boxes/{box.name}/sessions/{session_id}",
        json={"session_name": "fresh"},
        headers=HEADERS,
    )


NUL = "a\x00b"
ROUTES: dict[str, Callable[..., Any]] = {
    "log": lambda b, d=None: _get(b, "git/log", directory=d or b.root),
    "show": lambda b, ref="HEAD": _get(b, "git/show", directory=b.root, path="a.txt", ref=ref),
    "blame": lambda b: _get(b, "git/blame", directory=b.root, path="a.txt"),
    "branches": lambda b, d=None: _get(b, "git/branches", directory=d or b.root),
    "diff-files": lambda b, ref="HEAD": _get(
        b, "git/diff-files", directory=b.root, ref_a=ref, ref_b="HEAD"
    ),
    "badge": lambda b, d=None: _get(b, "git", directory=d or b.root),
    "grep": lambda b, pattern="nothing", below="": _get(
        b, "grep", directory=b.root + below, pattern=pattern
    ),
    "sync": _sync,
    "rename": _rename,
    "capture": lambda b, window="1": _get(b, "sessions/work/capture", window=window),
    "status": lambda b: _get(b, "status"),
    "stats": lambda b: _get(b, "stats"),
    "search": lambda b, root=None: _get(b, "search", q="xv", root=root or b.root),
}

# --- the table --------------------------------------------------------------------------

UNREACHABLE = Scene(unreachable=True)
DROPPED = Scene(dropped=True)
BOTH = ("local", REMOTE)
ONLY_REMOTE = (REMOTE,)
ONLY_LOCAL = ("local",)


@dataclass(frozen=True)
class Row:
    """A request (a route of ``ROUTES`` and its arguments) on a box playing ``scene``,
    and the answer: the status and the exact body (``{box}`` and ``{root}`` filled
    in), or, for an ordinary answer, the exact value of each field named."""

    route: str
    scene: Scene
    kinds: tuple[str, ...]
    status: int
    body: dict[str, Any]
    args: tuple = ()
    whole: bool = True  # the body is the whole answer, not the fields named


def _failure(reason: str, detail: str) -> dict[str, str]:
    return {"detail": detail, "reason": reason}


def _unreachable(route: str) -> list[Row]:
    return [
        Row(
            route,
            UNREACHABLE,
            ONLY_REMOTE,
            502,
            _failure("unreachable", "lb: connection refused"),
        ),
        Row(route, DROPPED, ONLY_REMOTE, 502, _failure("unreachable", "connection dropped")),
    ]


def _missing(route: str, program: str) -> Row:
    return Row(
        route,
        Scene(missing=program),
        BOTH,
        501,
        _failure("program_missing", f"{program} is not installed on {{box}}"),
    )


def _git_rows(route: str, timeout_text: str) -> list[Row]:
    return [
        *_unreachable(route),
        _missing(route, "git"),
        Row(route, Scene(stuck="git"), BOTH, 504, _failure("timeout", timeout_text)),
    ]


NOT_A_REPO = Scene(repo=False)
NO_COMMIT = Scene(commits=False)
REFUSED_FOLDER = _failure("invalid", "Path cannot contain null bytes")

ROWS: list[Row] = [
    # git log
    *_git_rows("log", "Git command timed out"),
    Row("log", Scene(), BOTH, 400, REFUSED_FOLDER, (NUL,)),
    Row("log", NOT_A_REPO, BOTH, 200, {"is_repo": False, "commits": []}, whole=False),
    Row("log", NO_COMMIT, BOTH, 200, {"is_repo": True, "commits": []}, whole=False),
    Row("log", Scene(fails="log"), BOTH, 500, _failure("failed", "git log failed")),
    # git show
    *_git_rows("show", "Git command timed out"),
    Row("show", Scene(), BOTH, 400, _failure("invalid", "Invalid git ref"), ("-x",)),
    Row("show", NOT_A_REPO, BOTH, 404, _failure("not_found", "Not a git repository")),
    Row("show", Scene(), BOTH, 200, {"content": "one", "ref": "HEAD", "path": "a.txt"}),
    # git blame
    *_git_rows("blame", "Git command timed out"),
    Row(
        "blame",
        Scene(refuses=True),
        BOTH,
        404,
        _failure("not_found", "File not tracked by git: a.txt"),
    ),
    Row("blame", Scene(), BOTH, 200, {"lines": [], "truncated": False}, whole=False),
    # git branches
    *_git_rows("branches", "Git command timed out"),
    Row("branches", Scene(), BOTH, 400, REFUSED_FOLDER, (NUL,)),
    Row("branches", NOT_A_REPO, BOTH, 200, {"is_repo": False, "branches": []}, whole=False),
    Row(
        "branches",
        Scene(),
        BOTH,
        200,
        {"is_repo": True, "root": "{root}", "branches": [{"name": "main", "is_current": True}]},
        whole=False,
    ),
    Row("branches", Scene(fails="branch"), BOTH, 500, _failure("failed", "git branch failed")),
    # git diff-files
    *_git_rows("diff-files", "Git command timed out"),
    Row("diff-files", Scene(), BOTH, 400, _failure("invalid", "Invalid git ref"), ("-x",)),
    Row("diff-files", NOT_A_REPO, BOTH, 200, {"files": []}, whole=False),
    Row("diff-files", NO_COMMIT, BOTH, 200, {"files": []}, whole=False),
    Row("diff-files", Scene(), BOTH, 200, {"files": []}, whole=False),
    Row(
        "diff-files",
        Scene(unknown="nope"),
        BOTH,
        404,
        _failure("not_found", "Ref not found: nope"),
        ("nope",),
    ),
    Row(
        "diff-files",
        Scene(fails="diff"),
        BOTH,
        500,
        _failure("failed", "git diff failed"),
    ),
    # the git badge
    *_git_rows("badge", f"git in {{root}} timed out after {LIMIT:g} s"),
    Row("badge", Scene(), BOTH, 400, REFUSED_FOLDER, (NUL,)),
    Row(
        "badge",
        NOT_A_REPO,
        BOTH,
        200,
        {"branch": None, "commit": None, "is_repo": False, "dirty": False},
    ),
    Row(
        "badge",
        NO_COMMIT,
        BOTH,
        200,
        {"branch": "main", "commit": None, "is_repo": True, "dirty": False},
    ),
    Row(
        "badge",
        Scene(),
        BOTH,
        200,
        {"branch": "main", "commit": "abc1234", "is_repo": True, "dirty": False},
    ),
    # grep
    *_unreachable("grep"),
    _missing("grep", "grep"),
    Row("grep", Scene(stuck="grep"), BOTH, 504, _failure("timeout", "Search timed out")),
    Row("grep", Scene(refuses=True), BOTH, 400, _failure("invalid", GREP_REFUSED), ("[abc",)),
    Row("grep", Scene(), BOTH, 200, {"matches": [], "truncated": False}, whole=False),
    Row(
        "grep",
        Scene(),
        BOTH,
        404,
        _failure("not_found", "Folder not found: {root}/missing"),
        ("nothing", "/missing"),
    ),
    Row(
        "grep",
        Scene(unreadable=True),
        BOTH,
        200,
        {"matches": [{"file": "a.txt", "line_number": 1, "line": "one"}], "truncated": False},
        ("one",),
        whole=False,
    ),
    # grep could not read one file and found nothing in the rest: no matches
    Row(
        "grep", Scene(unreadable=True), BOTH, 200, {"matches": [], "truncated": False}, whole=False
    ),
    # a session sync (after each, the stored session is checked: see the test)
    *_unreachable("sync"),
    _missing("sync", "tmux"),
    Row("sync", Scene(stuck="sh"), ONLY_REMOTE, 504, {"reason": "timeout"}, whole=False),
    Row(
        "sync",
        Scene(refuses=True),
        ONLY_REMOTE,
        500,
        _failure("failed", "listing the tmux sessions of lb exited with status 1"),
    ),
    # rename (after each, the stored name is checked: see the test)
    *_unreachable("rename"),
    _missing("rename", "tmux"),
    Row("rename", Scene(stuck="tmux"), BOTH, 504, _failure("timeout", "tmux on work timed out")),
    Row(
        "rename",
        Scene(refuses=True),
        BOTH,
        500,
        _failure("failed", f"tmux rename-session failed: {TMUX_REFUSED}"),
    ),
    Row("rename", Scene(), BOTH, 200, {"session_name": "fresh"}, whole=False),
    # capture
    *_unreachable("capture"),
    _missing("capture", "tmux"),
    Row(
        "capture",
        Scene(stuck="tmux"),
        BOTH,
        504,
        _failure("timeout", f"tmux capture-pane timed out after {LIMIT:g} s"),
    ),
    Row(
        "capture",
        Scene(refuses=True),
        BOTH,
        500,
        _failure("failed", f"tmux capture-pane failed: {TMUX_REFUSED}"),
    ),
    Row("capture", Scene(), BOTH, 400, _failure("invalid", "Invalid window"), ("a;b",)),
    Row("capture", Scene(), BOTH, 200, {"session": "work", "text": "line"}, whole=False),
    # a box's status: "offline" is this probe's ordinary answer and says why
    Row(
        "status",
        UNREACHABLE,
        ONLY_REMOTE,
        200,
        {
            "name": "lb",
            "status": "offline",
            "latency_ms": None,
            "error": "lb: connection refused",
            "reason": "unreachable",
        },
    ),
    Row(
        "status",
        DROPPED,
        ONLY_REMOTE,
        200,
        {
            "name": "lb",
            "status": "offline",
            "latency_ms": None,
            "error": "connection dropped",
            "reason": "unreachable",
        },
    ),
    Row(
        "status",
        Scene(stuck="true"),
        ONLY_REMOTE,
        200,
        {"status": "offline", "latency_ms": None, "reason": "timeout"},
        whole=False,
    ),
    Row("status", Scene(), ONLY_LOCAL, 200, {"name": "local", "status": "online", "latency_ms": 0}),
    Row("status", Scene(), ONLY_REMOTE, 200, {"name": "lb", "status": "online"}, whole=False),
    # a box's stats (its /proc is read with a cut output, so no exit status is seen)
    *_unreachable("stats"),
    Row("stats", Scene(stuck="cat"), ONLY_REMOTE, 504, {"reason": "timeout"}, whole=False),
    Row(
        "stats",
        Scene(refuses=True),
        ONLY_REMOTE,
        500,
        _failure("failed", "could not read /proc on lb"),
    ),
    Row("stats", Scene(), BOTH, 200, {"name": "{box}", "error": None}, whole=False),
]

# Directory search answers 200 with the folders visited before, and says why the walk of
# the box did not run.
VISITED = "/srv/xvisited"
SEARCH_ROWS: list[tuple[Scene, tuple[str, ...], dict[str, Any]]] = [
    (UNREACHABLE, ONLY_REMOTE, {"error": "lb: connection refused", "reason": "unreachable"}),
    (DROPPED, ONLY_REMOTE, {"error": "connection dropped", "reason": "unreachable"}),
    (
        Scene(missing="find"),
        BOTH,
        {"error": "find is not installed on {box}", "reason": "program_missing"},
    ),
    (
        Scene(stuck="find"),
        BOTH,
        {"error": f"directory search timed out after {LIMIT:g} s", "reason": "timeout"},
    ),
]


def _filled(value: Any, box: Box) -> Any:
    if isinstance(value, str):
        return (
            value.replace("{box}", box.name)
            .replace("{root}", box.root)
            .replace("{grep}", "grep" if box.remote else "/usr/bin/grep")
        )
    if isinstance(value, dict):
        return {key: _filled(item, box) for key, item in value.items()}
    if isinstance(value, list):
        return [_filled(item, box) for item in value]
    return value


def _picked(answer: Any, wanted: Any) -> Any:
    """``answer`` cut down to the fields ``wanted`` names, at every depth."""
    if isinstance(wanted, dict):
        return {key: _picked(answer[key], item) for key, item in wanted.items()}
    if isinstance(wanted, list):
        return [_picked(item, want) for item, want in zip(answer, wanted, strict=True)]
    return answer


def _row_id(row: Row) -> str:
    scene = row.scene
    if scene.unreachable:
        case = "unreachable"
    elif scene.dropped:
        case = "dropped"
    elif scene.missing:
        case = f"no-{scene.missing}"
    elif scene.stuck:
        case = f"stuck-{scene.stuck}"
    elif scene.fails:
        case = f"{scene.fails}-fails"
    elif scene.unknown:
        case = "unknown-ref"
    elif scene.unreadable:
        case = "unreadable-match" if row.args else "unreadable-no-match"
    elif row.route == "grep" and len(row.args) == 2:
        case = "missing-folder"
    elif row.args:
        case = "refused-input"
    elif scene.refuses:
        case = "refused"
    elif not scene.repo:
        case = "not-a-repo"
    elif not scene.commits:
        case = "no-commit"
    else:
        case = "ordinary"
    return f"{row.route}-{case}"


CASES = [pytest.param(kind, row, id=f"{kind}-{_row_id(row)}") for row in ROWS for kind in row.kinds]


@pytest.mark.parametrize(("box", "row"), CASES, indirect=["box"])
def test_a_route_that_runs_a_program_answers_each_failure_by_its_reason(box: Box, row: Row):
    box.scene = replace(row.scene)
    resp = ROUTES[row.route](box, *row.args)
    body = resp.json()
    expected = _filled(row.body, box)
    if row.whole:
        assert (resp.status_code, body) == (row.status, expected)
    else:
        assert (resp.status_code, _picked(body, expected)) == (row.status, expected)
    if row.route == "sync":
        # A failed listing changes no session; no sessions marks the stored one inactive.
        assert _sessions(box) == [("work", True)]
    if row.route == "rename":
        assert _sessions(box) == [("fresh" if row.status == 200 else "work", True)]


@pytest.mark.parametrize("box", BOTH, indirect=True)
def test_a_sync_on_a_box_with_no_sessions_marks_the_stored_one_inactive(box: Box):
    resp = ROUTES["sync"](box)
    assert (resp.status_code, [(s["session_name"], s["active"]) for s in resp.json()]) == (
        200,
        [("work", False)],
    )
    assert _sessions(box) == [("work", False)]


SEARCH_CASES = [
    pytest.param(kind, scene, said, id=f"{kind}-{case}")
    for (scene, kinds, said), case in zip(
        SEARCH_ROWS, ["unreachable", "dropped", "no-find", "stuck-find"], strict=True
    )
    for kind in kinds
]


@pytest.mark.parametrize(("box", "scene", "said"), SEARCH_CASES, indirect=["box"])
def test_a_search_whose_walk_did_not_run_gives_the_visited_folders_and_says_why(
    box: Box, scene: Scene, said: dict[str, Any]
):
    state.record_directory_visit(box.name, VISITED)
    box.scene = scene
    resp = ROUTES["search"](box)
    body = resp.json()
    # zoxide is not on the fake local box: it has no folders visited before.
    visited = [(VISITED, "frecency")] if box.remote else []
    assert (resp.status_code, [(r["path"], r["source"]) for r in body["results"]]) == (
        200,
        visited,
    )
    assert {key: body.get(key) for key in ("error", "reason")} == _filled(said, box)


@pytest.mark.parametrize("box", BOTH, indirect=True)
def test_a_search_whose_walk_ran_gives_what_it_found_and_no_failure(box: Box):
    state.record_directory_visit(box.name, VISITED)
    resp = ROUTES["search"](box)
    body = resp.json()
    found = [(f"{box.root}/xfound", "discovery")]
    visited = [(VISITED, "frecency")] if box.remote else []
    assert (resp.status_code, [(r["path"], r["source"]) for r in body["results"]]) == (
        200,
        visited + found,
    )
    assert sorted(body) == ["box", "query", "results"]


@pytest.mark.parametrize("box", BOTH, indirect=True)
def test_a_search_under_a_root_the_route_refuses_is_invalid(box: Box):
    resp = ROUTES["search"](box, NUL)
    assert (resp.status_code, resp.json()) == (400, REFUSED_FOLDER)


def test_a_failed_box_in_the_stats_stream_says_why_in_its_event():
    """An answer about several boxes: the failed one's event carries the failure's text
    and reason, the stats fields empty."""

    async def failed() -> None:
        raise SSHError("lb: connection refused")

    async def event() -> str:
        task = asyncio.ensure_future(failed())
        await asyncio.gather(task, return_exceptions=True)
        return stats_stream._stats_event("lb", task)

    assert asyncio.run(event()) == (
        "event: stats\ndata: "
        '{"name": "lb", "cpu_percent": null, "memory_used_mb": null, '
        '"memory_total_mb": null, "memory_percent": null, "uptime_seconds": null, '
        '"error": "lb: connection refused", "reason": "unreachable"}\n\n'
    )


def test_the_stats_stream_gives_each_box_its_own_event_when_one_fails(monkeypatch):
    """Through the stream's own generator: the box that fails says why, the box that
    answers gives its numbers, and neither event touches the other."""

    async def stats_of(deps, box, application_config):
        if box.name == "lb":
            raise SSHError("lb: connection refused")
        return APIBoxStats(name=box.name, cpu_percent=12.5, memory_used_mb=512.0, uptime_seconds=60)

    class Connected:
        async def is_disconnected(self) -> bool:
            return False

    async def events() -> dict[str, dict[str, Any]]:
        deps = SimpleNamespace(get_application_config=lambda: None)
        route = next(r for r in stats_stream.get_router(deps).routes if r.path.endswith("/stream"))
        config = AppConfig(
            boxes=[ConfigBox(name=n, connect_host=n, display_host=n, user="u") for n in BOTH]
        )
        response = await route.endpoint(Connected(), boxes="local,lb", application_config=config)
        stream = response.body_iterator
        got: dict[str, dict[str, Any]] = {}
        try:
            async for chunk in stream:
                data = json.loads(chunk.removeprefix("event: stats\ndata: "))
                got[data["name"]] = data
                if len(got) == len(BOTH):
                    break
        finally:
            await stream.aclose()
        return got

    monkeypatch.setattr(stats_stream, "box_stats", stats_of)
    got = asyncio.run(events())
    assert got == {
        "lb": {
            "name": "lb",
            "cpu_percent": None,
            "memory_used_mb": None,
            "memory_total_mb": None,
            "memory_percent": None,
            "uptime_seconds": None,
            "error": "lb: connection refused",
            "reason": "unreachable",
        },
        "local": {
            "name": "local",
            "cpu_percent": 12.5,
            "memory_used_mb": 512.0,
            "memory_total_mb": None,
            "memory_percent": None,
            "uptime_seconds": 60,
            "error": None,
            "reason": None,
        },
    }


@pytest.mark.parametrize("box", BOTH, indirect=True)
def test_error_output_that_is_not_utf8_comes_back_as_text_with_the_bad_byte_marked(box: Box):
    box.scene = Scene(refuses=True, raw_errors=b"grep: caf\xe9 [\n")
    refused = ROUTES["grep"](box, "[abc")
    box.scene = Scene(refuses=True, raw_errors=b"caf\xe9\n")
    captured = ROUTES["capture"](box)
    assert (refused.status_code, refused.json()) == (
        400,
        _failure("invalid", "grep: caf\ufffd ["),
    )
    assert (captured.status_code, captured.json()) == (
        500,
        _failure("failed", "tmux capture-pane failed: caf\ufffd"),
    )


@pytest.mark.parametrize("box", BOTH, indirect=True)
def test_a_failure_text_holding_the_client_s_path_gives_it_back_unchanged(box: Box):
    folder = Path(box.root) / 'it\'s "q"\nx'
    folder.mkdir()
    box.scene = Scene(stuck="git")
    resp = ROUTES["badge"](box, str(folder))
    assert (resp.status_code, resp.json()) == (
        504,
        _failure("timeout", f"git in {folder} timed out after {LIMIT:g} s"),
    )
