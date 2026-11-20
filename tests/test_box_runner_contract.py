"""A command on a box: one runner per kind of box, and the routes that use it.

The first part runs one table against the local runner and against the remote one on a
loopback asyncssh server. The second part sends the git routes, grep and the git badge
to a box of each kind and checks what each answers, how many connections and commands a
remote request costs, and the words a remote command line carries.

The loopback server never hands a command line to a shell. It splits each line with
``shlex`` and reads it as ``[cd FOLDER [2>/dev/null] &&] WORDS 2>/dev/null [| head ...]``.
It also reads the branch list's ``cd FOLDER && STEP && echo WORD && STEP``. It
answers the pool's health check and the home lookup; it emulates in Python the few
test programs of the table (``python -c SCRIPT``, by the exact script); and it runs
``git`` only when every word is plain (letters, digits and ``_./%:=()|,+-`` and the
log's field separator), never a word from a hostile input. Any other line is recorded
and refused with status 127.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shlex
import subprocess
import sys
import threading
import time
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass, field
from pathlib import Path

import asyncssh
import pytest
import pytest_asyncio
import yaml
from fastapi.testclient import TestClient

from sshler import ssh_pool, state
from sshler.api import boxes as boxes_api
from sshler.api import dependencies as dependencies_api
from sshler.api.box_runner import BoxRunner, LocalRunner, Ran, over
from sshler.ssh import connect
from sshler.ssh_pool import SSHConnectionPool
from sshler.webapp import ServerSettings, make_app

pytestmark = pytest.mark.posix_only("a loopback SSH server and git repositories in a temp folder")

TOKEN = "runner-token"
HEADERS = {"X-SSHLER-TOKEN": TOKEN}
HOME_CMD = 'printf %s "$HOME"'
HEALTH_CMD = "echo test"
PY = sys.executable

# The test programs of the table. The local runner runs them; the loopback server
# knows each by its exact text and gives the answer it would.
ECHO = "import json, os, sys; print(json.dumps([os.getcwd(), sys.argv[1:]]))"
FAIL = "import sys; print('half'); sys.exit(3)"
BIG_SIZE = 1_500_000
BIG = f"import sys; sys.stdout.write('x' * {BIG_SIZE})"
SLEEP = "import os, sys, time; open(sys.argv[1], 'w').write(str(os.getpid())); time.sleep(30)"

_PLAIN_WORD = re.compile(r"[A-Za-z0-9_./%:=()|,+\x1f-]*")


# --- the loopback server --------------------------------------------------------------


@dataclass
class Counts:
    commands: list[str] = field(default_factory=list)
    connections: int = 0


def _parse(line: str) -> tuple[str | None, list[str], int | None] | None:
    """The folder, the words and the byte cut of a line of the one shape the runner
    and the remote git badge send; None for any other line."""
    try:
        tokens = shlex.split(line)
    except ValueError:
        return None
    folder = None
    if tokens[:1] == ["cd"]:
        if tokens[2:3] == ["&&"]:
            folder, tokens = tokens[1], tokens[3:]
        elif tokens[2:4] == ["2>/dev/null", "&&"]:
            folder, tokens = tokens[1], tokens[4:]
        else:
            return None
    cut = None
    if len(tokens) > 4 and tokens[-4:-1] == ["|", "head", "-c"]:
        cut, tokens = int(tokens[-1]), tokens[:-4]
    elif len(tokens) > 3 and tokens[-3:] == ["|", "head", "-1"]:
        cut, tokens = -1, tokens[:-3]
    if not tokens or tokens[-1] != "2>/dev/null" or len(tokens) < 2:
        return None
    words = tokens[:-1]
    if any(word in {"&&", "|", ";", "2>/dev/null"} for word in words):
        return None
    return folder, words, cut


def _chain(line: str) -> tuple[str, list[list[str]]] | None:
    """The folder and the steps of the remote branch list: ``cd FOLDER && STEP && ...``,
    each step a plain command with its error output dropped, or ``echo WORD``."""
    try:
        tokens = shlex.split(line)
    except ValueError:
        return None
    if tokens[:1] != ["cd"] or tokens[2:3] != ["&&"]:
        return None
    steps: list[list[str]] = [[]]
    for token in tokens[3:]:
        if token == "&&":
            steps.append([])
        else:
            steps[-1].append(token)
    for step in steps:
        plain = len(step) >= 2 and step[-1] == "2>/dev/null"
        if not (plain or (len(step) == 2 and step[0] == "echo")):
            return None
    return tokens[1], [step[:-1] if step[0] != "echo" else step for step in steps]


async def _git(words: list[str], folder: str) -> tuple[int, str]:
    process = await asyncio.create_subprocess_exec(
        *words,
        cwd=folder,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    out, _ = await process.communicate()
    return process.returncode or 0, out.decode()


async def _answer(process: asyncssh.SSHServerProcess, counts: Counts, home: str) -> None:
    line = str(process.command)
    counts.commands.append(line)
    if line == HEALTH_CMD:
        process.stdout.write(b"test\n")
        process.exit(0)
        return
    if line == HOME_CMD:
        process.stdout.write(home.encode())
        process.exit(0)
        return
    parsed = _parse(line)
    chain = _chain(line) if parsed is None else None
    if chain is not None and os.path.isdir(chain[0]):
        out, status = "", 0
        for step in chain[1]:
            if step[0] == "echo":
                out += step[1] + "\n"
            elif step[0] == "git" and all(_PLAIN_WORD.fullmatch(word) for word in step):
                status, step_out = await _git(step, chain[0])
                out += step_out
            else:
                status = 127
            if status != 0:
                break
        process.stdout.write(out.encode())
        process.exit(status)
        return
    if parsed is None:
        process.exit(127)
        return
    folder, words, cut = parsed
    if folder is not None and not os.path.isdir(folder):
        process.exit(1)  # what `cd` gives
        return
    cwd = folder or home
    if folder is not None and folder.endswith("/stuck"):
        await asyncio.sleep(60)  # a box that stops answering
    if folder is not None and folder.endswith("/slow") and words[:2] == ["git", "status"]:
        await asyncio.sleep(60)  # a repository whose status never comes
    if words[0] == "git" and all(_PLAIN_WORD.fullmatch(word) for word in words):
        status, out = await _git(words, cwd)
    elif words[:2] == [PY, "-c"] and words[2] == ECHO:
        status, out = 0, json.dumps([cwd, words[3:]]) + "\n"
    elif words[:3] == [PY, "-c", FAIL]:
        status, out = 3, "half\n"
    elif words[:3] == [PY, "-c", BIG]:
        status, out = 0, "x" * BIG_SIZE
    elif words[:3] == [PY, "-c", SLEEP]:
        await asyncio.sleep(60)
        status, out = 0, ""
    else:
        process.exit(127)
        return
    data = out.encode()
    if cut == -1:
        data, status = b"".join(data.splitlines(keepends=True)[:1]), 0
    elif cut is not None:
        data, status = data[:cut], 0  # bytes, as head -c cuts: it may split a character
    process.stdout.write(data)
    process.exit(status)


class ServerThread:
    """An asyncssh server on its own event loop, so a sync test client can reach it."""

    def __init__(self, keys: Path, counts: Counts, home: str) -> None:
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self.loop.run_forever, daemon=True)
        self.thread.start()
        host_key = asyncssh.generate_private_key("ssh-ed25519")
        client_key = asyncssh.generate_private_key("ssh-ed25519")
        self.client_key = keys / "id_client"
        client_key.write_private_key(str(self.client_key))
        self.client_key.chmod(0o600)
        authorized = keys / "authorized_keys"
        client_key.write_public_key(str(authorized))
        self.connections: list[asyncssh.SSHServerConnection] = []
        connections = self.connections

        class Tracked(asyncssh.SSHServer):
            def connection_made(self, conn: asyncssh.SSHServerConnection) -> None:
                counts.connections += 1
                connections.append(conn)

        self.server = self._run(
            asyncssh.listen(
                "127.0.0.1",
                0,
                server_factory=Tracked,
                server_host_keys=[host_key],
                authorized_client_keys=str(authorized),
                process_factory=lambda process: _answer(process, counts, home),
                encoding=None,
            )
        )
        self.port = self.server.get_port()
        self.known_hosts = keys / "known_hosts"
        public = host_key.export_public_key("openssh").decode().strip()
        self.known_hosts.write_text(f"[127.0.0.1]:{self.port} {public}\n", encoding="utf-8")

    def _run(self, awaitable):
        async def _await():
            return await awaitable

        return asyncio.run_coroutine_threadsafe(_await(), self.loop).result(timeout=30)

    def close(self) -> None:
        async def _close() -> None:
            for conn in self.connections:
                conn.close()
            self.server.close()
            await self.server.wait_closed()

        self._run(_close())
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.thread.join(timeout=30)
        self.loop.close()


# --- one table, run against the runner of each kind of box ---------------------------

HOSTILE_WORDS = ["a b", "it's", 'a"b', "$HOME", "$(id)", "`id`", "a;b", "a\nb", "-rf", "--help"]


@dataclass
class Kind:
    """One kind of box: its runner, a folder of it, and where a program starts when no
    folder is given."""

    name: str
    runner: BoxRunner
    folder: str
    start: str
    home: str


@pytest_asyncio.fixture(params=["local", "remote"])
async def kind(request: pytest.FixtureRequest, tmp_path: Path) -> AsyncIterator[Kind]:
    folder = (tmp_path / "work dir").resolve()
    folder.mkdir()
    if request.param == "local":
        yield Kind("local", LocalRunner(), str(folder), os.getcwd(), str(Path.home().resolve()))
        return
    keys = tmp_path / "keys"
    keys.mkdir()
    home = (tmp_path / "remote-home").resolve()
    home.mkdir()
    server = ServerThread(keys, Counts(), str(home))
    connection = await connect(
        "127.0.0.1",
        "tester",
        port=server.port,
        keyfile=str(server.client_key),
        known_hosts=str(server.known_hosts),
        allow_alias=False,
    )
    try:
        yield Kind("remote", over(connection), str(folder), str(home), str(home))
    finally:
        connection.close()
        await asyncio.to_thread(server.close)


async def _ran(kind: Kind, words: list[str], **options) -> Ran:
    options.setdefault("folder", kind.folder)
    options.setdefault("timeout", 20)
    return await kind.runner.run(words, **options)


@pytest.mark.asyncio
async def test_a_command_that_succeeds_gives_status_0_and_its_output(kind):
    ran = await _ran(kind, [PY, "-c", ECHO, "a"])
    assert ran == Ran(0, json.dumps([kind.folder, ["a"]]) + "\n")


@pytest.mark.asyncio
async def test_a_command_that_fails_gives_its_status_and_its_output(kind):
    assert await _ran(kind, [PY, "-c", FAIL]) == Ran(3, "half\n")


@pytest.mark.asyncio
async def test_a_command_that_writes_more_than_a_megabyte_gives_all_of_it(kind):
    ran = await _ran(kind, [PY, "-c", BIG])
    assert (ran.status, len(ran.output), set(ran.output)) == (0, BIG_SIZE, {"x"})


@pytest.mark.asyncio
async def test_a_cut_output_gives_its_first_bytes_and_no_status(kind):
    assert await _ran(kind, [PY, "-c", BIG], output_limit=10) == Ran(None, "x" * 10)


@pytest.mark.asyncio
async def test_a_command_with_no_folder_runs_where_the_box_starts_it(kind):
    ran = await _ran(kind, [PY, "-c", ECHO], folder=None)
    assert ran == Ran(0, json.dumps([kind.start, []]) + "\n")


@pytest.mark.asyncio
async def test_a_folder_that_does_not_exist_runs_nothing(kind):
    ran = await _ran(kind, [PY, "-c", ECHO], folder=f"{kind.folder}/missing")
    assert (ran.ok, ran.output) == (False, "")


@pytest.mark.asyncio
async def test_every_word_reaches_the_program_as_that_one_word(kind):
    ran = await _ran(kind, [PY, "-c", ECHO, *HOSTILE_WORDS])
    assert ran == Ran(0, json.dumps([kind.folder, HOSTILE_WORDS]) + "\n")


@pytest.mark.asyncio
async def test_a_command_past_its_limit_raises_timeout_and_leaves_no_process(kind, tmp_path):
    pid_file = tmp_path / "sleeper.pid"
    with pytest.raises(TimeoutError):
        await _ran(kind, [PY, "-c", SLEEP, str(pid_file)], timeout=3)
    if kind.name == "local":
        with pytest.raises(ProcessLookupError):
            os.kill(int(pid_file.read_text()), 0)


@pytest.mark.asyncio
async def test_a_tilde_folder_is_under_the_home_of_the_box(kind):
    assert await kind.runner.folder("~/sub") == f"{kind.home}/sub"


# --- repositories ---------------------------------------------------------------------


def _git_setup(folder: Path, *words: str) -> str:
    """Run git in a test fixture (a list of words, no shell); its output."""
    done = subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", *words],
        cwd=folder,
        check=True,
        capture_output=True,
        text=True,
    )
    return done.stdout.strip()


@dataclass
class Repos:
    """Each folder the git badge is asked about, and the short commit of the ones that
    have one (read with git itself when the fixture is made)."""

    root: Path
    commits: dict[str, str]

    def at(self, name: str) -> str:
        return str(self.root / name)


@pytest.fixture
def repos(tmp_path: Path) -> Repos:
    root = (tmp_path / "repos").resolve()
    root.mkdir()
    commits = {}
    for name in ("clean", "detached", "changed", "slow"):
        folder = root / name
        folder.mkdir()
        _git_setup(folder, "init", "-q", "-b", "main")
        (folder / "a.txt").write_text("one\n", encoding="utf-8")
        _git_setup(folder, "add", "a.txt")
        _git_setup(folder, "commit", "-q", "-m", "first")
        commits[name] = _git_setup(folder, "rev-parse", "--short", "HEAD")
    _git_setup(root / "detached", "checkout", "-q", "--detach")
    (root / "changed" / "a.txt").write_text("two\n", encoding="utf-8")
    (root / "empty").mkdir()
    _git_setup(root / "empty", "init", "-q", "-b", "main")
    (root / "plain").mkdir()
    (root / "stuck").mkdir()
    return Repos(root=root, commits=commits)


# --- an app with the local box and a remote box on the loopback server ----------------


@dataclass
class Served:
    client: TestClient
    counts: Counts
    home: Path

    def get(self, box: str, route: str, **params: str):
        return self.client.get(f"/api/v1/boxes/{box}/{route}", params=params, headers=HEADERS)

    def remote_commands(self) -> list[str]:
        """The command lines of the requests so far, without the pool's health check."""
        return [line for line in self.counts.commands if line != HEALTH_CMD]


@pytest.fixture
def served(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Served]:
    home = (tmp_path / "remote-home").resolve()
    home.mkdir()
    keys = tmp_path / "keys"
    keys.mkdir()
    counts = Counts()
    server = ServerThread(keys, counts, str(home))
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    box = {
        "name": "lb",
        "host": "127.0.0.1",
        "user": "tester",
        "port": server.port,
        "keyfile": str(server.client_key),
        "known_hosts": str(server.known_hosts),
    }
    (config_dir / "boxes.yaml").write_text(yaml.safe_dump({"boxes": [box]}), encoding="utf-8")
    ssh_config = tmp_path / "ssh_config"
    ssh_config.write_text("", encoding="utf-8")
    monkeypatch.setenv("SSHLER_CONFIG_DIR", str(config_dir))
    monkeypatch.setenv("SSHLER_SSH_CONFIG", str(ssh_config))
    monkeypatch.setattr(ssh_pool, "_global_pool", SSHConnectionPool())
    state.initialize(config_dir)
    client = TestClient(make_app(ServerSettings(csrf_token=TOKEN)))
    try:
        yield Served(client=client, counts=counts, home=home)
    finally:
        client.close()
        state.reset_state()
        server.close()


# --- the git badge: one answer for one repository, on both kinds ----------------------


def _badge(repos: Repos, name: str) -> dict:
    """The answer the local badge gave before for each folder."""
    if name in ("plain", "missing"):
        return {"is_repo": False, "branch": None, "commit": None, "dirty": False, "error": None}
    if name == "empty":
        return {"is_repo": True, "branch": None, "commit": None, "dirty": False, "error": None}
    branch = "HEAD" if name == "detached" else "main"
    return {
        "is_repo": True,
        "branch": branch,
        "commit": repos.commits[name],
        "dirty": name == "changed",
        "error": None,
    }


@pytest.mark.parametrize("box", ["local", "lb"])
@pytest.mark.parametrize("name", ["clean", "empty", "detached", "changed", "plain", "missing"])
def test_the_git_badge_gives_one_answer_for_one_folder_on_both_kinds(served, repos, box, name):
    """A repository with no commit was not one on a remote box: it read "is this a
    repository" off the branch command, which fails there."""
    resp = served.get(box, "git", directory=repos.at(name))
    assert (resp.status_code, resp.json()) == (200, _badge(repos, name))


def test_a_remote_git_badge_on_a_box_that_stops_answering_is_an_error_and_sends_nothing_more(
    served, repos, monkeypatch
):
    """The first command past its limit ends the badge with an error that says so; no
    later command is sent. The probe limit is set well above the command limit."""
    monkeypatch.setattr(boxes_api, "GIT_INFO_TIMEOUT", 0.3)
    monkeypatch.setattr(dependencies_api, "PROBE_TIMEOUT_SECONDS", 5.0)
    folder = repos.at("stuck")
    started = time.monotonic()
    resp = served.get("lb", "git", directory=folder)
    assert (resp.status_code, resp.json()) == (
        200,
        {
            "is_repo": False,
            "branch": None,
            "commit": None,
            "dirty": False,
            "error": f"git in {folder} timed out after 0.3 s",
        },
    )
    assert time.monotonic() - started < 4.0
    assert [_parse(line) for line in served.remote_commands()] == [
        (folder, ["git", "rev-parse", "--git-dir", "--abbrev-ref", "HEAD"], None)
    ]


def test_a_remote_git_badge_whose_status_never_comes_is_an_error_and_sends_nothing_more(
    served, repos, monkeypatch
):
    monkeypatch.setattr(boxes_api, "GIT_INFO_TIMEOUT", 0.3)
    monkeypatch.setattr(dependencies_api, "PROBE_TIMEOUT_SECONDS", 5.0)
    folder = repos.at("slow")
    started = time.monotonic()
    resp = served.get("lb", "git", directory=folder)
    assert (resp.status_code, resp.json()) == (
        200,
        {
            "is_repo": False,
            "branch": None,
            "commit": None,
            "dirty": False,
            "error": f"git in {folder} timed out after 0.3 s",
        },
    )
    assert time.monotonic() - started < 4.0
    assert [_parse(line)[1] for line in served.remote_commands()] == [
        ["git", "rev-parse", "--git-dir", "--abbrev-ref", "HEAD"],
        ["git", "rev-parse", "--short", "HEAD"],
        ["git", "status", "--porcelain"],
    ]


def test_a_remote_badge_command_past_its_limit_does_not_hand_its_connection_on(
    served, repos, monkeypatch
):
    """A command that ran past its limit may still run on its connection, so the next
    request opens a new one."""
    monkeypatch.setattr(boxes_api, "GIT_INFO_TIMEOUT", 0.3)
    with served.client:  # one event loop for every request, so the pool can reuse
        stuck = served.get("lb", "git", directory=repos.at("stuck"))
        clean = served.get("lb", "git", directory=repos.at("clean"))
        again = served.get("lb", "git", directory=repos.at("clean"))
    assert (stuck.json()["error"] is None, clean.json()["is_repo"], again.json()["is_repo"]) == (
        False,
        True,
        True,
    )
    # The stuck request's connection is closed; the next two share one.
    assert served.counts.connections == 2


# --- round trips of a remote request ---------------------------------------------------


@pytest.mark.parametrize(
    ("route", "params", "commands"),
    [
        ("git", {"directory": "clean"}, 3),
        ("git", {"directory": "empty"}, 3),
        ("git", {"directory": "plain"}, 1),
        ("git/log", {"directory": "clean"}, 1),
        ("git/show", {"directory": "clean", "path": "a.txt"}, 1),
        ("git/blame", {"directory": "clean", "path": "a.txt"}, 1),
        ("git/branches", {"directory": "clean"}, 1),
        ("git/diff-files", {"directory": "clean", "ref_a": "HEAD", "ref_b": "HEAD"}, 1),
        ("grep", {"directory": "clean", "pattern": "one"}, 1),
    ],
)
def test_a_remote_request_takes_one_connection_and_no_more_commands_than_before(
    served, repos, route, params, commands
):
    """Counted on the code before the runner: one connection for each request; three
    commands for the badge of a repository and one outside a repository; one for each
    git route and for grep. A repository with no commit took one, as the badge stopped
    at its failed branch command; it now takes the badge's three, as a repository does."""
    asked = {**params, "directory": repos.at(params["directory"])}
    resp = served.get("lb", route, **asked)
    assert resp.status_code in {200, 404}
    assert (served.counts.connections, len(served.remote_commands())) == (1, commands)


@pytest.mark.parametrize(
    ("route", "params"),
    [
        ("git", {}),
        ("git/log", {}),
        ("git/branches", {}),
        ("git/diff-files", {"ref_a": "HEAD", "ref_b": "HEAD"}),
        ("grep", {"pattern": "one"}),
    ],
)
def test_a_remote_request_at_the_home_asks_for_the_home_once_more(served, route, params):
    served.get("lb", route, directory="~", **params)
    assert served.counts.connections == 1
    assert served.remote_commands()[0] == HOME_CMD
    assert served.remote_commands().count(HOME_CMD) == 1


def test_a_remote_badge_runs_the_same_words_in_the_folder_as_the_local_one(served, repos):
    served.get("lb", "git", directory=repos.at("clean"))
    lines = served.remote_commands()
    assert [_parse(line) for line in lines] == [
        (repos.at("clean"), ["git", "rev-parse", "--git-dir", "--abbrev-ref", "HEAD"], None),
        (repos.at("clean"), ["git", "rev-parse", "--short", "HEAD"], None),
        (repos.at("clean"), ["git", "status", "--porcelain"], boxes_api.GIT_STATUS_BYTES),
    ]


# --- the remote git routes and grep answer as before -----------------------------------


def test_a_remote_git_log_lists_the_commits_of_the_repository(served, repos):
    resp = served.get("lb", "git/log", directory=repos.at("clean"))
    body = resp.json()
    assert (resp.status_code, body["is_repo"], len(body["commits"])) == (200, True, 1)
    assert (body["commits"][0]["short_hash"], body["commits"][0]["message"]) == (
        repos.commits["clean"],
        "first",
    )


@pytest.mark.parametrize(("box", "is_repo"), [("local", True), ("lb", False)])
def test_a_git_log_of_a_repository_with_no_commit_answers_as_each_kind_did(
    served, repos, box, is_repo
):
    """Kept as it was: the local box finds the repository first, the remote box reads
    it off the log alone, in one command."""
    resp = served.get(box, "git/log", directory=repos.at("empty"))
    assert (resp.status_code, resp.json()["is_repo"], resp.json()["commits"]) == (
        200,
        is_repo,
        [],
    )


@pytest.mark.parametrize("box", ["local", "lb"])
def test_a_git_log_outside_a_repository_is_no_repository_on_both_kinds(served, repos, box):
    resp = served.get(box, "git/log", directory=repos.at("plain"))
    assert (resp.status_code, resp.json()["is_repo"], resp.json()["commits"]) == (200, False, [])


@pytest.mark.parametrize("box", ["local", "lb"])
def test_git_diff_files_lists_a_changed_file_on_both_kinds(served, repos, box):
    folder = repos.root / "clean"
    (folder / "b.txt").write_text("b\n", encoding="utf-8")
    _git_setup(folder, "add", "b.txt")
    _git_setup(folder, "commit", "-q", "-m", "second")
    resp = served.get(
        box, "git/diff-files", directory=str(folder), ref_a=repos.commits["clean"], ref_b="HEAD"
    )
    assert (resp.status_code, resp.json()["files"]) == (200, [{"path": "b.txt", "status": "A"}])


@pytest.mark.parametrize("box", ["local", "lb"])
def test_git_branches_names_the_branch_and_the_root_on_both_kinds(served, repos, box):
    resp = served.get(box, "git/branches", directory=repos.at("clean"))
    body = resp.json()
    assert (resp.status_code, body["is_repo"], body["root"]) == (200, True, repos.at("clean"))
    assert [(b["name"], b["is_current"]) for b in body["branches"]] == [("main", True)]


def test_a_remote_git_route_on_a_box_that_stops_answering_is_the_504(served, repos, monkeypatch):
    from sshler.api import git as git_api

    monkeypatch.setattr(git_api, "GIT_TIMEOUT", 0.3)
    resp = served.get("lb", "git/log", directory=repos.at("stuck"))
    assert (resp.status_code, resp.json()) == (504, {"detail": "Git command timed out"})


def test_a_remote_git_route_on_a_folder_that_fails_its_check_is_a_400(served):
    resp = served.get("lb", "git/log", directory="a\x00b")
    assert (resp.status_code, resp.json()) == (400, {"detail": "Path cannot contain null bytes"})
    assert served.counts.connections == 0


HOSTILE = ["-v", "a b", "it's", 'a"b', "`id`", "$(id)", "a;b", "a\nb", "--help"]


@pytest.mark.parametrize("pattern", HOSTILE, ids=repr)
def test_a_remote_grep_pattern_and_folder_reach_grep_as_one_word_each(served, pattern):
    folder = f"{served.home}/x {pattern}y"
    resp = served.get("lb", "grep", directory=f"~/x {pattern}y", pattern=pattern)
    assert (resp.status_code, resp.json()["matches"]) == (200, [])
    lines = served.remote_commands()
    assert len(lines) == 2
    assert shlex.split(lines[1]) == [
        "grep",
        "-rn",
        "-i",
        "-m",
        "100",
        "--",
        pattern,
        folder,
        "2>/dev/null",
    ]


@pytest.mark.parametrize("pattern", ["-v", "it's", "$(id)", "a;b"], ids=repr)
def test_a_local_grep_pattern_reaches_grep_as_one_word(served, tmp_path, pattern):
    folder = (tmp_path / "searched").resolve()
    folder.mkdir()
    (folder / "f.txt").write_bytes(f"x\nsays {pattern}\n".encode())
    resp = served.get(
        "local", "grep", directory=str(folder), pattern=pattern, case_sensitive="true"
    )
    assert (resp.status_code, resp.json()["matches"]) == (
        200,
        [{"file": f"{folder}/f.txt", "line_number": 2, "line": f"says {pattern}"}],
    )


def test_a_remote_grep_with_no_grep_program_is_no_match_and_a_local_one_is_a_500(
    served, tmp_path, monkeypatch
):
    """Kept as it was: the local box asks for the program before it runs it; on a
    remote box the shell's 127 is an exit status grep's route does not read."""
    empty_bin = tmp_path / "no-bin"
    empty_bin.mkdir()
    remote = served.get("lb", "grep", directory=str(served.home), pattern="x")
    monkeypatch.setenv("PATH", str(empty_bin))
    local = served.get("local", "grep", directory=str(tmp_path), pattern="x")
    assert (remote.status_code, remote.json()["matches"]) == (200, [])
    assert (local.status_code, local.json()) == (500, {"detail": "grep not available"})


# --- the two refs of diff-files are read as revisions ----------------------------------


def _folders_named_like_refs(root: Path) -> Path:
    """A repository whose subfolder `src` holds two folders named like its two
    branches, `main` and `test`; `test` has one more changed file."""
    repo = root / "named"
    (repo / "src" / "main").mkdir(parents=True)
    (repo / "src" / "test").mkdir()
    repo.mkdir(exist_ok=True)
    _git_setup(repo, "init", "-q", "-b", "main")
    (repo / "src" / "main" / "k.txt").write_text("k\n", encoding="utf-8")
    (repo / "src" / "test" / "k.txt").write_text("k\n", encoding="utf-8")
    (repo / "f.txt").write_text("one\n", encoding="utf-8")
    _git_setup(repo, "add", ".")
    _git_setup(repo, "commit", "-q", "-m", "first")
    _git_setup(repo, "checkout", "-q", "-b", "test")
    (repo / "f.txt").write_text("two\n", encoding="utf-8")
    _git_setup(repo, "commit", "-q", "-a", "-m", "second")
    _git_setup(repo, "checkout", "-q", "main")
    return repo


@pytest.mark.parametrize("box", ["local", "lb"])
def test_git_diff_files_reads_its_refs_as_revisions_in_a_folder_named_like_them(
    served, tmp_path, box
):
    repo = _folders_named_like_refs(tmp_path)
    resp = served.get(
        box, "git/diff-files", directory=str(repo / "src"), ref_a="main", ref_b="test"
    )
    assert (resp.status_code, resp.json()["files"]) == (200, [{"path": "f.txt", "status": "M"}])


@pytest.mark.parametrize("box", ["local", "lb"])
def test_git_diff_files_outside_a_repository_lists_nothing_for_two_files_named_as_refs(
    served, tmp_path, box
):
    plain = tmp_path / "not-a-repo"
    plain.mkdir()
    (plain / "a").write_text("one\n", encoding="utf-8")
    (plain / "b").write_text("two\n", encoding="utf-8")
    resp = served.get(box, "git/diff-files", directory=str(plain), ref_a="a", ref_b="b")
    assert (resp.status_code, resp.json()["files"]) == (200, [])


# --- a remote folder is the one the path owner gives ------------------------------------


@pytest.mark.parametrize(
    ("route", "params", "folder_word"),
    [
        ("grep", {"pattern": "x"}, 7),  # grep -rn -i -m 100 -- x <folder>
        ("git/log", {}, 1),  # cd <folder> && git log ...
    ],
)
def test_a_remote_folder_above_the_home_is_the_same_folder_for_grep_and_git(
    served, route, params, folder_word
):
    """`~` is the home before `..` is applied, so `~/..` is the folder above the home on
    every route (`remote.resolve_tilde` owns the rule)."""
    served.get("lb", route, directory="~/..", **params)
    lines = [shlex.split(line) for line in served.remote_commands()]
    assert (len(lines), lines[0], lines[1][folder_word]) == (
        2,
        shlex.split(HOME_CMD),
        str(served.home.parent),
    )


# --- the badge reads one byte of the status --------------------------------------------


@pytest.mark.parametrize("box", ["local", "lb"])
def test_a_badge_of_a_tree_whose_only_change_is_a_modified_file_is_dirty(served, repos, box):
    """The first byte of the status is a space (` M a.txt`): it is a change all the same."""
    resp = served.get(box, "git", directory=repos.at("changed"))
    assert (resp.status_code, resp.json()["dirty"], resp.json()["error"]) == (200, True, None)


# --- a local badge command past its limit ----------------------------------------------


def _stub_git(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, status: str) -> None:
    """A stand-in `git` first on PATH that answers the badge's two rev-parse commands
    and runs `status` for the third."""
    bin_dir = tmp_path / "stub-bin"
    bin_dir.mkdir()
    script = bin_dir / "git"
    script.write_text(
        "#!/bin/sh\n"
        'case "$1" in\n'
        '  rev-parse) if [ "$2" = "--short" ]; then echo abc1234; '
        "else echo .git; echo main; fi ;;\n"
        f"  status) {status} ;;\n"
        "esac\n",
        encoding="utf-8",
    )
    script.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")


def test_a_local_badge_whose_status_never_comes_is_an_error(tmp_path, monkeypatch):
    _stub_git(tmp_path, monkeypatch, "exec sleep 30")
    monkeypatch.setattr(boxes_api, "GIT_INFO_TIMEOUT", 0.5)
    started = time.monotonic()
    info = asyncio.run(boxes_api.git_info(LocalRunner(), str(tmp_path)))
    assert info.model_dump() == {
        "is_repo": False,
        "branch": None,
        "commit": None,
        "dirty": False,
        "error": f"git in {tmp_path} timed out after 0.5 s",
    }
    assert time.monotonic() - started < 4.0
