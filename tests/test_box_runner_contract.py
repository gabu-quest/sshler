"""A command on a box: one runner per kind of box, and the routes that use it.

The first part runs one table against the local runner and against the remote one on a
loopback asyncssh server. The second part sends the git routes, grep, the git badge and
a box's stats and status to a box of each kind and checks what each answers, how many
connections and commands a remote request costs, and the words a remote command line
carries.

The loopback server never hands a command line to a shell. It splits each line with
``shlex`` and reads it as ``[cd FOLDER [2>/dev/null] &&] WORDS 2>/dev/null [| head ...]``.
It answers the pool's health check and the home lookup; it emulates in Python the few
test programs of the table (``python -c SCRIPT``, by the exact script), ``true``, and
``cat`` of files under ``/proc`` (from canned text, ``Counts.proc``); and it runs
``git`` only when every word is plain (letters, digits and ``_./%:=()|,+-`` and the
log's field separator), never a word from a hostile input. A tmux line is answered from
canned text by ``_tmux_reply`` (no tmux runs), and the session-listing script of
``api/sessions.py`` by its exact text. Any other line is recorded and refused with
status 127.
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
from urllib.parse import quote

import asyncssh
import pytest
import pytest_asyncio
import yaml
from fastapi.testclient import TestClient

from sshler import ssh_pool, state
from sshler import tmux as tmux_module
from sshler.api import boxes as boxes_api
from sshler.api import dependencies as dependencies_api
from sshler.api import sessions as sessions_api
from sshler.api.box_runner import BoxRunner, LocalRunner, Ran, TmuxTimeoutError, over
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


# What the loopback server's /proc holds: each read of /proc/stat takes the next
# sample (the last one stays). The stats these give, by hand: CPU deltas total
# 1200 - 1000 = 200, active 300 - 200 = 100, so 50.0 %; MemTotal 2048000 kB is
# 2000.0 MB with MemAvailable 1024000 kB, so 1000.0 MB used, 50.0 %; up 3600 s.
PROC_STAT_FIRST = "cpu  100 0 100 800 0\ncpu0 100 0 100 800 0\nintr 7 1 2\nbtime 1700000000\n"
PROC_STAT_SECOND = "cpu  150 0 150 900 0\ncpu0 150 0 150 900 0\nintr 9 1 2\nbtime 1700000000\n"
PROC_MEMINFO = (
    "MemTotal:        2048000 kB\nMemFree:          512000 kB\n"
    "MemAvailable:    1024000 kB\nBuffers:           10000 kB\n"
)
PROC_UPTIME = "3600.50 100.00\n"


def _proc() -> dict[str, list[str]]:
    return {
        "/proc/stat": [PROC_STAT_FIRST, PROC_STAT_SECOND],
        "/proc/meminfo": [PROC_MEMINFO],
        "/proc/uptime": [PROC_UPTIME],
    }


# What a tmux stand-in answers, on both kinds: its words as JSON on its output; with a
# word "gone", tmux's refusal on its error output and status 1; with a word "latin", a
# line that is not UTF-8; with a word "stuck", nothing for 60 s. The local stand-in is
# this Python script first on PATH; the loopback server gives the same answers from
# ``_tmux_reply``.
TMUX_GONE = "can't find session: gone\n"
TMUX_NOT_INSTALLED = "sh: 1: tmux: not found\n"
TMUX_LATIN = b"caf\xe9\n"
FAKE_TMUX = f"""#!{PY}
import json, sys, time
args = sys.argv[1:]
if "stuck" in args:
    time.sleep(60)
if "gone" in args:
    sys.stderr.write({TMUX_GONE!r})
    sys.exit(1)
if "latin" in args:
    sys.stdout.buffer.write({TMUX_LATIN!r})
    sys.exit(0)
print(json.dumps(args))
"""
# The sessions the loopback box's listing script names.
LIVE_SESSIONS = "alpha\nwork\n"


@dataclass
class Counts:
    commands: list[str] = field(default_factory=list)
    connections: int = 0
    proc: dict[str, list[str]] = field(default_factory=_proc)
    tmux_installed: bool = True

    def read_proc(self, name: str) -> str | None:
        texts = self.proc.get(name)
        if not texts:
            return None
        return texts.pop(0) if len(texts) > 1 else texts[0]


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


async def _git(words: list[str], folder: str) -> tuple[int, bytes]:
    process = await asyncio.create_subprocess_exec(
        *words,
        cwd=folder,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    out, _ = await process.communicate()
    return process.returncode or 0, out  # bytes: a file git shows need not be UTF-8


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
    try:
        tokens = shlex.split(line)
    except ValueError:
        tokens = []
    if tokens[:1] == ["tmux"]:
        await _tmux_reply(process, counts, tokens[1:])
        return
    parsed = _parse(line)
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
    elif words == ["true"]:
        status, out = 0, ""
    elif words == ["sh", "-c", sessions_api.REMOTE_LIST_SESSIONS]:
        status, out = 0, LIVE_SESSIONS
    elif words[0] == "cat" and len(words) > 1 and all(w.startswith("/proc/") for w in words[1:]):
        texts = [counts.read_proc(name) for name in words[1:]]
        status = 0 if all(text is not None for text in texts) else 1  # what cat gives
        out = "".join(text for text in texts if text is not None)
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
    data = out if isinstance(out, bytes) else out.encode()
    if cut == -1:
        data, status = b"".join(data.splitlines(keepends=True)[:1]), 0
    elif cut is not None:
        data, status = data[:cut], 0  # bytes, as head -c cuts: it may split a character
    process.stdout.write(data)
    process.exit(status)


async def _tmux_reply(process: asyncssh.SSHServerProcess, counts: Counts, args: list[str]) -> None:
    """The answer of ``FAKE_TMUX`` for ``args``, or the shell's when tmux is not installed."""
    if not counts.tmux_installed:
        process.stderr.write(TMUX_NOT_INSTALLED.encode())
        process.exit(127)
        return
    if "stuck" in args:
        await asyncio.sleep(60)
    if "gone" in args:
        process.stderr.write(TMUX_GONE.encode())
        process.exit(1)
        return
    if "latin" in args:
        process.stdout.write(TMUX_LATIN)
        process.exit(0)
        return
    process.stdout.write((json.dumps(args) + "\n").encode())
    process.exit(0)


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
    nested = root / "nested"
    (nested / "sub" / "deeper").mkdir(parents=True)
    _git_setup(nested, "init", "-q", "-b", "main")
    (nested / "top.txt").write_text("top\n", encoding="utf-8")
    (nested / "sub" / "b.txt").write_text("bee\n", encoding="utf-8")
    (nested / "sub" / "deeper" / "c.txt").write_text("sea\n", encoding="utf-8")
    _git_setup(nested, "add", ".")
    _git_setup(nested, "commit", "-q", "-m", "first")
    (nested / "untracked.txt").write_text("new\n", encoding="utf-8")
    (root / "link").symlink_to(nested, target_is_directory=True)
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
        ("git/log", {"directory": "empty"}, 2),
        ("git/log", {"directory": "plain"}, 2),
        ("git/show", {"directory": "clean", "path": "a.txt"}, 2),
        ("git/show", {"directory": "clean", "path": "missing.txt"}, 3),
        ("git/blame", {"directory": "clean", "path": "a.txt"}, 2),
        ("git/branches", {"directory": "clean"}, 2),
        ("git/branches", {"directory": "plain"}, 1),
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
    at its failed branch command; it now takes the badge's three, as a repository does.

    Show, blame and branches sent one compound line, which ran python3 on the box for
    show and blame; each now asks git for the root first and works the path out here,
    so each takes two (show three for a file not at the ref, to tell it from an empty
    file). A log that fails asks for the root once more, on both kinds, to tell a
    repository with no commit from a folder outside any repository."""
    asked = {**params, "directory": repos.at(params["directory"])}
    resp = served.get("lb", route, **asked)
    assert resp.status_code == (404 if params.get("path") == "missing.txt" else 200)
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
    ("route", "params", "folder_word", "commands"),
    [
        ("grep", {"pattern": "x"}, 7, 2),  # grep -rn -i -m 100 -- x <folder>
        # cd <folder> && git log ...; the folder is no repository, so the root is asked
        ("git/log", {}, 1, 3),
    ],
)
def test_a_remote_folder_above_the_home_is_the_same_folder_for_grep_and_git(
    served, route, params, folder_word, commands
):
    """`~` is the home before `..` is applied, so `~/..` is the folder above the home on
    every route (`remote.resolve_tilde` owns the rule)."""
    served.get("lb", route, directory="~/..", **params)
    lines = [shlex.split(line) for line in served.remote_commands()]
    assert (len(lines), lines[0], lines[1][folder_word]) == (
        commands,
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


# --- show and blame: the root asked of the runner, the path worked out here -------------


def test_a_remote_show_needs_no_python_on_the_box(served, repos):
    """The loopback box runs git and nothing else, as a box with no python3 does."""
    resp = served.get("lb", "git/show", directory=repos.at("clean"), path="a.txt")
    assert (resp.status_code, resp.json()) == (
        200,
        {"content": "one", "ref": "HEAD", "path": "a.txt"},
    )


def test_a_remote_blame_needs_no_python_on_the_box(served, repos):
    resp = served.get("lb", "git/blame", directory=repos.at("clean"), path="a.txt")
    body = resp.json()
    assert (resp.status_code, body["error"], len(body["lines"])) == (200, None, 1)
    assert (body["lines"][0]["content"], body["lines"][0]["author"]) == ("one", "t")


SHOW_ANSWERS = [
    # (directory, path, ref, status, detail); a directory or path in braces is under
    # the repos fixture's root.
    ("plain", "a.txt", "HEAD", 404, "Not a git repository"),
    ("clean", "{plain}/a.txt", "HEAD", 400, "Path is outside the git repository"),
    ("clean", "../plain/a.txt", "HEAD", 400, "Path is outside the git repository"),
    ("clean", "{clean}", "HEAD", 400, "Path must point to a file inside the repository"),
    ("clean", ".", "HEAD", 400, "Path must point to a file inside the repository"),
    ("clean", "missing.txt", "HEAD", 404, "File not found at ref HEAD"),
    ("clean", "a.txt", "nope", 404, "File not found at ref nope"),
    ("clean", "   ", "HEAD", 400, "Path is required"),
]


def _under(repos: Repos, text: str) -> str:
    return text.replace("{plain}", repos.at("plain")).replace("{clean}", repos.at("clean"))


@pytest.mark.parametrize("box", ["local", "lb"])
@pytest.mark.parametrize(("directory", "path", "ref", "status", "detail"), SHOW_ANSWERS)
def test_a_git_show_gives_one_answer_on_both_kinds(
    served, repos, box, directory, path, ref, status, detail
):
    resp = served.get(
        box, "git/show", directory=repos.at(directory), path=_under(repos, path), ref=ref
    )
    assert (resp.status_code, resp.json()) == (status, {"detail": detail})


BLAME_ANSWERS = [
    ("plain", "a.txt", 404, {"detail": "Not a git repository"}),
    ("clean", "{plain}/a.txt", 400, {"detail": "Path is outside the git repository"}),
    ("clean", "{clean}", 400, {"detail": "Path must point to a file inside the repository"}),
    (
        "nested",
        "untracked.txt",
        200,
        {
            "box": "{box}",
            "path": "untracked.txt",
            "lines": [],
            "truncated": False,
            "error": "Not a git repository or file not tracked",
        },
    ),
]


@pytest.mark.parametrize("box", ["local", "lb"])
@pytest.mark.parametrize(("directory", "path", "status", "body"), BLAME_ANSWERS)
def test_a_git_blame_gives_one_answer_on_both_kinds(
    served, repos, box, directory, path, status, body
):
    resp = served.get(box, "git/blame", directory=repos.at(directory), path=_under(repos, path))
    expected = {k: (box if v == "{box}" else v) for k, v in body.items()}
    assert (resp.status_code, resp.json()) == (status, expected)


@pytest.mark.parametrize("box", ["local", "lb"])
@pytest.mark.parametrize(
    ("directory", "path", "content"),
    [
        ("nested", "top.txt", "top"),
        ("nested", "sub/b.txt", "bee"),
        ("nested", "{nested}/sub/b.txt", "bee"),
        ("nested/sub", "b.txt", "bee"),
        ("nested/sub", "{nested}/top.txt", "top"),
        ("nested/sub", "../top.txt", "top"),
        ("nested/sub/deeper", "../../top.txt", "top"),
        ("nested/sub", "deeper/../b.txt", "bee"),
    ],
)
def test_a_git_show_takes_a_path_absolute_or_relative_from_any_folder_on_both_kinds(
    served, repos, box, directory, path, content
):
    asked = path.replace("{nested}", repos.at("nested"))
    resp = served.get(box, "git/show", directory=repos.at(directory), path=asked)
    assert (resp.status_code, resp.json()) == (
        200,
        {"content": content, "ref": "HEAD", "path": asked},
    )


@pytest.mark.parametrize("box", ["local", "lb"])
@pytest.mark.parametrize(
    ("path", "status", "body"),
    [
        ("b.txt", 200, {"content": "bee", "ref": "HEAD", "path": "b.txt"}),
        ("{link}/sub/b.txt", 200, {"content": "bee", "ref": "HEAD", "path": "{link}/sub/b.txt"}),
        ("../top.txt", 200, {"content": "top", "ref": "HEAD", "path": "../top.txt"}),
        ("..", 400, {"detail": "Path must point to a file inside the repository"}),
        ("../../plain/a", 400, {"detail": "Path is outside the git repository"}),
    ],
)
def test_a_git_show_in_a_folder_reached_through_a_link_names_the_file_on_both_kinds(
    served, repos, box, path, status, body
):
    """git names the root by its real path, so the text of a path through a link is not
    under it; the file is named by where git says the folder is in the repository."""
    link = repos.at("link")
    asked = path.replace("{link}", link)
    expected = {key: value.replace("{link}", link) for key, value in body.items()}
    resp = served.get(box, "git/show", directory=f"{link}/sub", path=asked)
    assert (resp.status_code, resp.json()) == (status, expected)


@pytest.mark.parametrize("box", ["local", "lb"])
def test_a_git_show_of_a_file_that_is_not_utf8_gives_its_text_with_the_bad_byte_marked(
    served, repos, box
):
    folder = repos.root / "nested"
    (folder / "latin.txt").write_bytes(b"caf\xe9 au lait\n")
    _git_setup(folder, "add", "latin.txt")
    _git_setup(folder, "commit", "-q", "-m", "latin")
    resp = served.get(box, "git/show", directory=str(folder), path="latin.txt")
    assert (resp.status_code, resp.json()["content"]) == (200, "caf\ufffd au lait")


def test_a_git_show_of_an_empty_file_is_the_empty_content_on_both_kinds(served, repos):
    folder = repos.root / "nested"
    (folder / "empty.txt").write_text("", encoding="utf-8")
    _git_setup(folder, "add", "empty.txt")
    _git_setup(folder, "commit", "-q", "-m", "empty")
    answers = [
        served.get(box, "git/show", directory=str(folder), path="empty.txt")
        for box in ("local", "lb")
    ]
    assert [(r.status_code, r.json()) for r in answers] == [
        (200, {"content": "", "ref": "HEAD", "path": "empty.txt"})
    ] * 2


def test_a_remote_git_show_cuts_its_output_on_the_box(served, repos):
    served.get("lb", "git/show", directory=repos.at("clean"), path="a.txt")
    lines = served.remote_commands()
    assert [_parse(line) for line in lines] == [
        (repos.at("clean"), ["git", "rev-parse", "--show-toplevel"], None),
        (repos.at("clean"), ["git", "show", "HEAD:a.txt"], 1_048_576),
    ]


def _home_repo(served: Served) -> None:
    """The remote home is a repository with `h.txt` at its root and a folder `x`."""
    (served.home / "x").mkdir()
    _git_setup(served.home, "init", "-q", "-b", "main")
    (served.home / "h.txt").write_text("home\n", encoding="utf-8")
    _git_setup(served.home, "add", "h.txt")
    _git_setup(served.home, "commit", "-q", "-m", "first")


@pytest.mark.parametrize(
    ("directory", "path", "status", "body", "home_lookups"),
    [
        ("~", "h.txt", 200, {"content": "home", "ref": "HEAD", "path": "h.txt"}, 1),
        ("~/x", "../h.txt", 200, {"content": "home", "ref": "HEAD", "path": "../h.txt"}, 1),
        ("~", "~/h.txt", 200, {"content": "home", "ref": "HEAD", "path": "~/h.txt"}, 1),
        ("~", "~//h.txt", 200, {"content": "home", "ref": "HEAD", "path": "~//h.txt"}, 1),
        ("~", "~/../h.txt", 400, {"detail": "Path is outside the git repository"}, 1),
        ("~", "~root/h.txt", 404, {"detail": "File not found at ref HEAD"}, 1),
        ("~/..", "h.txt", 404, {"detail": "Not a git repository"}, 1),
        ("/", "~/h.txt", 404, {"detail": "Not a git repository"}, 0),
    ],
)
def test_a_remote_git_show_reads_a_tilde_as_the_home_of_the_box(
    served, directory, path, status, body, home_lookups
):
    _home_repo(served)
    resp = served.get("lb", "git/show", directory=directory, path=path)
    assert (resp.status_code, resp.json()) == (status, body)
    assert served.remote_commands().count(HOME_CMD) == home_lookups


# --- a hostile name reaches git as one word on both kinds -----------------------------

NAMES = [
    "$(touch x)",
    "`touch x`",
    "a b",
    "a'b",
    'a"b',
    "-rf",
    "--help",
    "a;b",
    "a\nb",
    "~",
    "~root",
    "   x",
    "n" * 200,
]


def _hostile_repo(tmp_path: Path, name: str) -> Path:
    """A repository in a folder named ``name`` holding one file named ``name``; a test
    gives the file's path absolute, so ``~`` is a name and not the home."""
    folder = (tmp_path / "hostile" / name).resolve()
    folder.mkdir(parents=True)
    _git_setup(folder, "init", "-q", "-b", "main")
    (folder / name).write_text("said\n", encoding="utf-8")
    _git_setup(folder, "add", "--", name)
    _git_setup(folder, "commit", "-q", "-m", "first")
    return folder


@pytest.mark.parametrize("name", NAMES, ids=lambda n: repr(n)[:24])
def test_a_local_show_and_blame_of_a_hostile_name_reach_git_as_one_word(served, tmp_path, name):
    folder = _hostile_repo(tmp_path, name)
    path = f"{folder}/{name}"
    show = served.get("local", "git/show", directory=str(folder), path=path)
    blame = served.get("local", "git/blame", directory=str(folder), path=path)
    assert (show.status_code, show.json()) == (
        200,
        {"content": "said", "ref": "HEAD", "path": path},
    )
    assert (blame.status_code, [line["content"] for line in blame.json()["lines"]]) == (
        200,
        ["said"],
    )


@pytest.mark.parametrize("name", NAMES, ids=lambda n: repr(n)[:24])
def test_a_remote_show_and_blame_of_a_hostile_name_reach_git_as_one_word(served, tmp_path, name):
    """The loopback box runs git only for plain words, so a hostile one is refused there;
    what is proved is the words it received."""
    folder = str(_hostile_repo(tmp_path, name))
    served.get("lb", "git/show", directory=folder, path=f"{folder}/{name}")
    served.get("lb", "git/blame", directory=folder, path=f"{folder}/{name}")
    shown = f"HEAD:{name}"
    expected = [
        (folder, ["git", "rev-parse", "--show-toplevel"], None),
        (folder, ["git", "show", shown], 1_048_576),
    ]
    if not _PLAIN_WORD.fullmatch(shown):
        expected.append((folder, ["git", "cat-file", "-e", shown], None))
    expected += [
        (folder, ["git", "rev-parse", "--show-toplevel"], None),
        (folder, ["git", "blame", "--porcelain", "--", name], None),
    ]
    assert [_parse(line) for line in served.remote_commands()] == expected


@pytest.mark.parametrize("box", ["local", "lb"])
@pytest.mark.parametrize("name", NAMES, ids=lambda n: repr(n)[:24])
def test_git_branches_in_a_folder_with_a_hostile_name_answers_on_both_kinds(
    served, tmp_path, box, name
):
    folder = _hostile_repo(tmp_path, name)
    resp = served.get(box, "git/branches", directory=str(folder))
    body = resp.json()
    assert (resp.status_code, body["is_repo"], body["root"]) == (200, True, str(folder))
    assert [(b["name"], b["is_current"]) for b in body["branches"]] == [("main", True)]


# --- a remote git that hangs ------------------------------------------------------------


@pytest.mark.parametrize(
    ("route", "params"),
    [
        ("git/show", {"path": "a.txt"}),
        ("git/blame", {"path": "a.txt"}),
        ("git/branches", {}),
    ],
)
def test_a_remote_git_that_hangs_is_the_504_and_its_connection_is_not_handed_on(
    served, repos, monkeypatch, route, params
):
    from sshler.api import git as git_api

    monkeypatch.setattr(git_api, "GIT_TIMEOUT", 0.3)
    monkeypatch.setattr(git_api, "BLAME_TIMEOUT", 0.3)
    with served.client:  # one event loop for every request, so the pool can reuse
        stuck = served.get("lb", route, directory=repos.at("stuck"), **params)
        again = served.get("lb", "git/log", directory=repos.at("clean"))
    assert (stuck.status_code, stuck.json(), again.status_code) == (
        504,
        {"detail": "Git command timed out"},
        200,
    )
    assert served.counts.connections == 2


# --- the log of a repository with no commit --------------------------------------------


@pytest.mark.parametrize("box", ["local", "lb"])
def test_a_git_log_of_a_repository_with_no_commit_is_a_repository_on_both_kinds(served, repos, box):
    resp = served.get(box, "git/log", directory=repos.at("empty"))
    assert (resp.status_code, resp.json()["is_repo"], resp.json()["commits"]) == (200, True, [])


# --- a box's stats and status ----------------------------------------------------------

STATS = {
    "name": "lb",
    "cpu_percent": 50.0,
    "memory_used_mb": 1000.0,
    "memory_total_mb": 2000.0,
    "memory_percent": 50.0,
    "uptime_seconds": 3600,
    "error": None,
}


def test_a_remote_box_s_stats_are_read_from_proc_in_two_commands(served):
    resp = served.get("lb", "stats")
    assert (resp.status_code, resp.json()) == (200, STATS)
    assert served.counts.connections == 1
    assert [_parse(line) for line in served.remote_commands()] == [
        (None, ["cat", "/proc/stat"], 16_384),
        (None, ["cat", "/proc/uptime", "/proc/meminfo", "/proc/stat"], 16_384),
    ]


@pytest.mark.parametrize(
    ("proc", "changed"),
    [
        (
            {"/proc/stat": [PROC_STAT_FIRST, "intr 9 1 2\n"]},
            {"cpu_percent": None},
        ),
        (
            {"/proc/meminfo": ["MemTotal:        2048000 kB\nMemFree:          512000 kB\n"]},
            {"memory_used_mb": None, "memory_percent": None},
        ),
        (
            {"/proc/meminfo": ["MemFree:          512000 kB\n"]},
            {"memory_used_mb": None, "memory_total_mb": None, "memory_percent": None},
        ),
        ({"/proc/uptime": []}, {"uptime_seconds": None}),
    ],
    ids=["no-cpu-line", "no-available", "no-memory-lines", "no-uptime"],
)
def test_a_remote_box_s_stats_with_a_line_missing_give_the_rest(served, proc, changed):
    served.counts.proc.update(proc)
    resp = served.get("lb", "stats")
    assert (resp.status_code, resp.json()) == (200, {**STATS, **changed})


def test_a_remote_box_with_no_proc_says_its_stats_could_not_be_read(served):
    served.counts.proc.clear()
    resp = served.get("lb", "stats")
    assert (resp.status_code, resp.json()) == (
        200,
        {
            "name": "lb",
            "cpu_percent": None,
            "memory_used_mb": None,
            "memory_total_mb": None,
            "memory_percent": None,
            "uptime_seconds": None,
            "error": "could not read /proc on lb",
        },
    )


def test_a_remote_box_s_status_runs_true_once(served):
    resp = served.get("lb", "status")
    body = resp.json()
    assert (resp.status_code, body["name"], body["status"]) == (200, "lb", "online")
    assert 0 < body["latency_ms"] < 15_000
    assert served.counts.connections == 1
    assert [_parse(line) for line in served.remote_commands()] == [(None, ["true"], None)]


# --- tmux on a session's own server, on both kinds -------------------------------------


@pytest.fixture
def fake_tmux_on_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """``FAKE_TMUX`` first on PATH as ``tmux``, so the local box never runs a real tmux."""
    bin_dir = tmp_path / "tmux-bin"
    bin_dir.mkdir()
    script = bin_dir / "tmux"
    script.write_text(FAKE_TMUX, encoding="utf-8")
    script.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")


@pytest.mark.asyncio
async def test_tmux_runs_on_the_session_server_with_every_word_as_one_word(kind, fake_tmux_on_path):
    ran = await kind.runner.tmux("work", ["display-message", "-p", *HOSTILE_WORDS])
    expected = ["-L", "ts-work", "display-message", "-p", *HOSTILE_WORDS]
    assert ran == Ran(0, json.dumps(expected) + "\n", "")


@pytest.mark.asyncio
async def test_tmux_that_says_no_gives_its_status_and_its_error_output(kind, fake_tmux_on_path):
    ran = await kind.runner.tmux("gone", ["capture-pane", "-p", "-t", "gone"])
    assert ran == Ran(1, "", TMUX_GONE)


@pytest.mark.asyncio
async def test_tmux_past_its_limit_raises_the_tmux_timeout(kind, fake_tmux_on_path):
    started = time.monotonic()
    with pytest.raises(TmuxTimeoutError):
        await kind.runner.tmux("stuck", ["kill-session", "-t", "stuck"], timeout=0.5)
    assert time.monotonic() - started < 4.0


@pytest.mark.asyncio
async def test_tmux_with_no_limit_has_the_tmux_command_limit(kind, fake_tmux_on_path, monkeypatch):
    monkeypatch.setattr(tmux_module, "TMUX_COMMAND_TIMEOUT", 0.5)
    started = time.monotonic()
    with pytest.raises(TmuxTimeoutError):
        await kind.runner.tmux("stuck", ["kill-session", "-t", "stuck"])
    assert time.monotonic() - started < 4.0


# --- the session routes run tmux through the runner ------------------------------------


def _new_session(served: Served, box: str, name: str) -> str:
    created = served.client.post(
        f"/api/v1/boxes/{box}/sessions",
        json={"session_name": name, "working_directory": "/srv"},
        headers=HEADERS,
    )
    assert created.status_code == 200, created.text
    return created.json()["id"]


def _capture_words(target: str) -> list[str]:
    session = target.split(":")[0]
    return ["-L", f"ts-{session}", "capture-pane", "-p", "-J", "-S", "-", "-t", target]


@pytest.mark.parametrize("box", ["local", "lb"])
def test_a_capture_gives_one_answer_on_both_kinds(served, fake_tmux_on_path, box):
    resp = served.get(box, "sessions/work/capture", window="1", pane="0")
    text = json.dumps(_capture_words("work:1.0"))
    assert (resp.status_code, resp.json()) == (
        200,
        {"session": "work", "target": "work:1.0", "text": text, "lines": 1, "chars": len(text)},
    )


@pytest.mark.parametrize("box", ["local", "lb"])
def test_a_capture_that_is_not_utf8_gives_its_text_with_the_bad_byte_marked_on_both_kinds(
    served, fake_tmux_on_path, box
):
    """A remote capture read the output strictly as UTF-8: the read failed, and the
    route answered 400 "tmux capture-pane failed: " with no text after it."""
    resp = served.get(box, "sessions/latin/capture")
    assert (resp.status_code, resp.json()["text"]) == (200, "caf\ufffd")


@pytest.mark.parametrize("box", ["local", "lb"])
def test_a_capture_tmux_refuses_is_the_400_with_its_error_output_on_both_kinds(
    served, fake_tmux_on_path, box
):
    resp = served.get(box, "sessions/gone/capture")
    assert (resp.status_code, resp.json()) == (
        400,
        {"detail": "tmux capture-pane failed: can't find session: gone"},
    )


@pytest.mark.parametrize("box", ["local", "lb"])
def test_a_capture_past_its_limit_is_the_504_on_both_kinds(
    served, fake_tmux_on_path, monkeypatch, box
):
    monkeypatch.setattr(tmux_module, "TMUX_CAPTURE_TIMEOUT", 0.5)
    resp = served.get(box, "sessions/stuck/capture")
    assert (resp.status_code, resp.json()) == (
        504,
        {"detail": "tmux capture-pane timed out after 0.5 s"},
    )


@pytest.mark.parametrize("box", ["local", "lb"])
def test_a_rename_tmux_refuses_is_the_400_and_keeps_the_name_on_both_kinds(
    served, fake_tmux_on_path, box
):
    session_id = _new_session(served, box, "gone")
    resp = served.client.patch(
        f"/api/v1/boxes/{box}/sessions/{session_id}",
        json={"session_name": "fresh"},
        headers=HEADERS,
    )
    listed = served.client.get(f"/api/v1/boxes/{box}/sessions", headers=HEADERS)
    assert (resp.status_code, resp.json()) == (400, {"detail": "tmux rename failed"})
    assert [item["session_name"] for item in listed.json()] == ["gone"]


@pytest.mark.parametrize(
    ("method", "route", "body", "status"),
    [
        ("PATCH", "sessions/{id}", {"session_name": "fresh"}, 200),
        ("DELETE", "sessions/{id}?kill_tmux=true", None, 200),
        ("GET", "sessions/work/capture", None, 200),
        ("POST", "sessions/sync", None, 200),
    ],
    ids=["rename", "kill", "capture", "sync"],
)
def test_a_remote_session_route_takes_one_connection_and_one_command(
    served, method, route, body, status
):
    """Counted on the code before the runner: one pooled connection and one command
    for each (the pool's health check on release aside)."""
    session_id = _new_session(served, "lb", "work")
    resp = served.client.request(
        method,
        f"/api/v1/boxes/lb/{route.format(id=session_id)}",
        json=body,
        headers=HEADERS,
    )
    assert resp.status_code == status, resp.text
    assert (served.counts.connections, len(served.remote_commands())) == (1, 1)


def test_a_remote_rename_kill_and_capture_send_the_tmux_words_of_the_session(served):
    session_id = _new_session(served, "lb", "work")
    served.client.patch(
        f"/api/v1/boxes/lb/sessions/{session_id}", json={"session_name": "fresh"}, headers=HEADERS
    )
    served.client.delete(f"/api/v1/boxes/lb/sessions/{session_id}?kill_tmux=true", headers=HEADERS)
    served.get("lb", "sessions/work/capture", lines="50")
    assert [shlex.split(line) for line in served.remote_commands()] == [
        ["tmux", "-L", "ts-work", "rename-session", "-t", "work", "fresh"],
        ["tmux", "-L", "ts-fresh", "kill-session", "-t", "fresh"],
        ["tmux", "-L", "ts-work", "capture-pane", "-p", "-J", "-S", "-50", "-t", "work"],
    ]


def test_a_remote_sync_runs_the_listing_script_and_marks_the_rest_inactive(served):
    _new_session(served, "lb", "work")
    _new_session(served, "lb", "beta")
    resp = served.client.post("/api/v1/boxes/lb/sessions/sync", headers=HEADERS)
    assert resp.status_code == 200
    assert sorted((item["session_name"], item["active"]) for item in resp.json()) == [
        ("beta", False),
        ("work", True),
    ]
    assert [_parse(line) for line in served.remote_commands()] == [
        (None, ["sh", "-c", sessions_api.REMOTE_LIST_SESSIONS], None)
    ]


@pytest.mark.parametrize(
    ("method", "route", "body", "status", "detail"),
    [
        ("PATCH", "sessions/{id}", {"session_name": "fresh"}, 500, "Failed to rename session"),
        ("DELETE", "sessions/{id}?kill_tmux=true", None, 200, "deleted"),
        (
            "GET",
            "sessions/stuck/capture",
            None,
            504,
            "tmux capture-pane timed out after 0.5 s",
        ),
    ],
    ids=["rename", "kill", "capture"],
)
def test_a_remote_tmux_that_hangs_does_not_hand_its_connection_on(
    served, monkeypatch, method, route, body, status, detail
):
    """tmux may still run on the connection, so the next request opens a new one. A
    rename or a kill has tmux's command limit on a remote box too; before, only the
    15 s bound of the request stopped it."""
    monkeypatch.setattr(tmux_module, "TMUX_COMMAND_TIMEOUT", 0.5)
    monkeypatch.setattr(tmux_module, "TMUX_CAPTURE_TIMEOUT", 0.5)
    with served.client:  # one event loop for every request, so the pool can reuse
        session_id = _new_session(served, "lb", "stuck")
        started = time.monotonic()
        stuck = served.client.request(
            method,
            f"/api/v1/boxes/lb/{route.format(id=session_id)}",
            json=body,
            headers=HEADERS,
        )
        elapsed = time.monotonic() - started
        again = served.get("lb", "sessions/work/capture")
    assert elapsed < 4.0
    answer = stuck.json().get("detail", stuck.json().get("message"))
    assert (stuck.status_code, answer, again.status_code) == (status, detail, 200)
    assert served.counts.connections == 2


@pytest.mark.parametrize(
    ("method", "route", "body", "status", "detail"),
    [
        ("PATCH", "sessions/{id}", {"session_name": "fresh"}, 400, "tmux rename failed"),
        (
            "GET",
            "sessions/work/capture",
            None,
            400,
            "tmux capture-pane failed: sh: 1: tmux: not found",
        ),
    ],
    ids=["rename", "capture"],
)
def test_a_remote_box_with_no_tmux_is_the_400_of_a_refusal(
    served, method, route, body, status, detail
):
    """As before: the remote shell answers 127 and its text, which the route reads as
    tmux saying no. (The local box without tmux raises before anything runs, a 500.)"""
    served.counts.tmux_installed = False
    session_id = _new_session(served, "lb", "work")
    resp = served.client.request(
        method,
        f"/api/v1/boxes/lb/{route.format(id=session_id)}",
        json=body,
        headers=HEADERS,
    )
    assert (resp.status_code, resp.json()) == (status, {"detail": detail})


SESSION_HOSTILE = ["a b", "it's", 'a"b', "$HOME", "`id`", "a;b", "a\nb"]


@pytest.mark.parametrize("name", SESSION_HOSTILE, ids=repr)
@pytest.mark.parametrize(
    ("field", "detail"),
    [("session", "Invalid session name"), ("window", "Invalid window"), ("pane", "Invalid pane")],
)
def test_a_remote_capture_refuses_a_hostile_word_before_it_connects(served, name, field, detail):
    session = quote(name, safe="") if field == "session" else "work"
    params = {"window": name if field == "window" else "1"}
    if field == "pane":
        params["pane"] = name
    resp = served.get("lb", f"sessions/{session}/capture", **params)
    assert (resp.status_code, resp.json()) == (400, {"detail": detail})
    assert served.counts.connections == 0


@pytest.mark.parametrize("name", SESSION_HOSTILE, ids=repr)
def test_a_remote_rename_refuses_a_hostile_new_name_before_it_connects(served, name):
    """The body's own pattern refuses the name (422), before the route's check."""
    session_id = _new_session(served, "lb", "work")
    resp = served.client.patch(
        f"/api/v1/boxes/lb/sessions/{session_id}", json={"session_name": name}, headers=HEADERS
    )
    assert (resp.status_code, resp.json()["detail"][0]["loc"]) == (422, ["body", "session_name"])
    assert served.counts.connections == 0


def test_a_remote_capture_of_a_name_with_a_leading_dash_reaches_tmux_as_one_word(served):
    """The name check lets a leading ``-`` through, as before; it reaches tmux as the
    value of ``-t`` and as part of the server's name, one word each."""
    resp = served.get("lb", "sessions/-x/capture")
    assert (resp.status_code, resp.json()["text"]) == (200, json.dumps(_capture_words("-x")))
    assert [shlex.split(line) for line in served.remote_commands()] == [
        ["tmux", *_capture_words("-x")]
    ]
