"""The owners of a remote path, an SFTP session and a remote command line.

* ``sshler.api.remote``: ``~`` is resolved here, once per connection, and every
  command line is built here from quoted words.
* ``sshler.ssh.sftp_session``: the one place an SFTP session is opened and closed.

Every fake here records what it is asked and runs nothing. The hostile names are
data: a built command line is only ever parsed with ``shlex.split``.
"""

from __future__ import annotations

import asyncio
import contextlib
import shlex
from pathlib import Path, PurePosixPath
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from sshler.api import remote
from sshler.api.archive import archive_create_command, archive_extract_command
from sshler.api.batch import remove_tree_command
from sshler.api.boxes import remote_git_info_commands
from sshler.api.git import (
    _build_remote_repo_path_command,
    remote_git_blame_command,
    remote_git_branches_command,
    remote_git_diff_files_command,
    remote_git_log_command,
    remote_git_show_command,
)
from sshler.api.grep import remote_grep_command
from sshler.api.remote_files import directory_test_command, du_command, folder_zip_command
from sshler.api.search import DISCOVERY_SEPARATOR, remote_discovery_command, remote_find_command
from sshler.ssh import sftp_session

HOME_CMD = 'printf %s "$HOME"'


def _reply(stdout: str, status: int = 0) -> SimpleNamespace:
    return SimpleNamespace(stdout=stdout, exit_status=status, returncode=status, stderr="")


class HomeConnection:
    """Answers the home lookup with ``answers`` in turn and records every command."""

    def __init__(self, *answers: SimpleNamespace | BaseException) -> None:
        self.answers = list(answers)
        self.commands: list[str] = []

    async def run(self, command: str, check: bool = False):
        self.commands.append(command)
        answer = self.answers.pop(0)
        if isinstance(answer, BaseException):
            raise answer
        return answer


# --- the home folder ----------------------------------------------------------------


def test_the_home_is_asked_for_once_per_connection():
    conn = HomeConnection(_reply("/home/u"))

    async def three_lookups():
        return [await remote.resolve_tilde(conn, p) for p in ("~", "~/a", "~/b")]

    assert asyncio.run(three_lookups()) == ["/home/u", "/home/u/a", "/home/u/b"]
    assert conn.commands == [HOME_CMD]


def test_a_new_connection_asks_for_its_own_home():
    first, second = HomeConnection(_reply("/home/u")), HomeConnection(_reply("/root"))
    assert asyncio.run(remote.resolve_tilde(first, "~/x")) == "/home/u/x"
    assert asyncio.run(remote.resolve_tilde(second, "~/x")) == "/root/x"
    assert (first.commands, second.commands) == ([HOME_CMD], [HOME_CMD])


@pytest.mark.parametrize(
    "failure",
    [_reply("", 1), _reply(""), _reply("home/u"), _reply("~")],
    ids=["exit status", "nothing", "relative path", "tilde"],
)
def test_a_failed_lookup_is_a_502_and_the_next_call_asks_again(failure):
    conn = HomeConnection(failure, _reply("/home/u"))
    with pytest.raises(HTTPException) as caught:
        asyncio.run(remote.resolve_tilde(conn, "~/x"))
    assert (caught.value.status_code, caught.value.detail) == (
        502,
        "Could not resolve the remote home directory",
    )
    assert asyncio.run(remote.resolve_tilde(conn, "~/x")) == "/home/u/x"
    assert conn.commands == [HOME_CMD, HOME_CMD]


def test_a_lookup_that_times_out_is_not_remembered():
    conn = HomeConnection(TimeoutError(), _reply("/home/u"))
    with pytest.raises(TimeoutError):
        asyncio.run(remote.resolve_tilde(conn, "~"))
    assert asyncio.run(remote.resolve_tilde(conn, "~")) == "/home/u"
    assert conn.commands == [HOME_CMD, HOME_CMD]


@pytest.mark.parametrize(
    ("home", "path", "resolved"),
    [
        ("/home/u", "~", "/home/u"),
        ("/home/u", "~/", "/home/u"),
        ("/home/u", "~/x", "/home/u/x"),
        ("/home/u", "~//etc", "/home/u/etc"),
        ("/home/u", "~/../etc", "/home/etc"),
        ("/", "~", "/"),
        ("/", "~/x", "/x"),
        ("/", "~//etc", "/etc"),
    ],
)
def test_a_tilde_path_resolves_under_the_home(home, path, resolved):
    assert asyncio.run(remote.resolve_tilde(HomeConnection(_reply(home)), path)) == resolved


@pytest.mark.parametrize("path", ["~root", "~root/x", "/srv/~", "a~b", "/abs"])
def test_a_path_that_does_not_mean_the_home_is_left_alone_without_a_lookup(path):
    conn = HomeConnection()
    assert asyncio.run(remote.resolve_tilde(conn, path)) == path
    assert conn.commands == []


@pytest.mark.parametrize("directory", ["~", "~/", "~/x", " ~/x "])
def test_normalizing_a_folder_that_still_means_the_home_refuses_it(directory):
    """Mutation: map `~` to `/` (the old normaliser); a forgotten resolve then lists or
    writes under `/` without a word."""
    with pytest.raises(ValueError, match="unresolved '~'"):
        remote.normalize_remote_directory(directory)


def test_composing_a_child_of_an_unresolved_home_is_a_400():
    with pytest.raises(HTTPException) as caught:
        remote.compose_remote_child("~", "a.txt")
    assert caught.value.status_code == 400


# --- the SFTP session ---------------------------------------------------------------


class SftpCounter:
    """A connection that counts the SFTP sessions it opened and the ones closed."""

    def __init__(self, exit_error: Exception | None = None) -> None:
        self.opened = 0
        self.closed = 0
        self.exit_error = exit_error

    async def start_sftp_client(self):
        self.opened += 1
        counter = self

        class Client:
            def exit(self) -> None:
                counter.closed += 1
                if counter.exit_error is not None:
                    raise counter.exit_error

        return Client()

    @property
    def open_now(self) -> int:
        return self.opened - self.closed


def test_a_session_that_returns_is_closed_once():
    conn = SftpCounter()

    async def body():
        async with sftp_session(conn):
            return "done"

    assert asyncio.run(body()) == "done"
    assert (conn.opened, conn.closed) == (1, 1)


def test_a_session_whose_body_raises_is_closed_once_and_the_error_is_kept():
    conn = SftpCounter()

    async def body():
        async with sftp_session(conn):
            raise FileNotFoundError("gone")

    with pytest.raises(FileNotFoundError, match="gone"):
        asyncio.run(body())
    assert (conn.opened, conn.closed) == (1, 1)


def test_a_session_cancelled_mid_read_is_closed_once():
    conn = SftpCounter()

    async def body():
        started = asyncio.Event()

        async def read():
            async with sftp_session(conn):
                started.set()
                await asyncio.Event().wait()

        task = asyncio.create_task(read())
        await started.wait()
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    asyncio.run(body())
    assert (conn.opened, conn.closed, conn.open_now) == (1, 1, 0)


def test_a_session_on_a_dropped_connection_raises_only_the_bodys_error():
    conn = SftpCounter(exit_error=ConnectionError("connection lost"))

    async def body():
        async with sftp_session(conn):
            raise OSError("read failed")

    with pytest.raises(OSError, match="read failed"):
        asyncio.run(body())
    assert (conn.opened, conn.closed) == (1, 1)


def test_a_destination_session_that_fails_to_open_closes_the_source_session():
    """The transfer route enters both sessions on one exit stack."""
    source = SftpCounter()

    class Refusing:
        async def start_sftp_client(self):
            raise ConnectionError("subsystem request failed")

    async def body():
        async with contextlib.AsyncExitStack() as stack:
            await stack.enter_async_context(sftp_session(source))
            await stack.enter_async_context(sftp_session(Refusing()))

    with pytest.raises(ConnectionError, match="subsystem request failed"):
        asyncio.run(body())
    assert (source.opened, source.closed) == (1, 1)


def test_the_package_opens_sftp_sessions_only_in_the_owner():
    """Mutation: open a session anywhere else (a route calling `start_sftp_client`)."""
    package = Path(remote.__file__).resolve().parents[1]
    sites = [
        path.relative_to(package).as_posix()
        for path in sorted(package.rglob("*.py"))
        for line in path.read_text(encoding="utf-8").splitlines()
        if "start_sftp_client" in line
    ]
    assert sites == ["ssh.py"]


# --- command lines ------------------------------------------------------------------

HOSTILE = [
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
    "~/../etc",
    "~//etc",
    "   ",
    "n" * 4000,
]


def _as_path(name: str) -> str:
    """How a path argument reaches its tool: one starting with a dash gets `./`."""
    return f"./{name}" if name.startswith("-") else name


FMT = "%H\x1f%s"
BUILDERS = {
    "du": (lambda n: du_command("-sb", n), lambda n, p: ["du", "-sb", p]),
    "test -d": (directory_test_command, lambda n, p: ["test", "-d", p]),
    "folder zip": (
        lambda n: folder_zip_command(f"/srv/{n}")[1],
        lambda n, p: [
            "cd",
            PurePosixPath(f"/srv/{n}").parent.as_posix(),
            "&&",
            "zip",
            "-r",
            "-q",
            "-",
            _as_path(PurePosixPath(f"/srv/{n}").name),
        ],
    ),
    "tar create source": (
        lambda n: archive_create_command("tar.gz", "/srv/o.tgz", ["/a", n]),
        lambda n, p: ["tar", "czf", "/srv/o.tgz", "-C", "/", "/a", p],
    ),
    "tar create target": (
        lambda n: archive_create_command("tgz", n, ["/a"]),
        lambda n, p: ["tar", "czf", p, "-C", "/", "/a"],
    ),
    "zip create": (
        lambda n: archive_create_command("zip", n, [n]),
        lambda n, p: ["cd", "/", "&&", "zip", "-r", p, p],
    ),
    "tar extract": (
        lambda n: archive_extract_command(f"/srv/{n}.tgz", n),
        lambda n, p: ["tar", "xzf", f"/srv/{n}.tgz", "-C", p],
    ),
    "unzip": (
        lambda n: archive_extract_command(f"{n}.zip", n),
        lambda n, p: ["unzip", "-o", f"{p}.zip", "-d", p],
    ),
    "rm -rf": (remove_tree_command, lambda n, p: ["rm", "-rf", p]),
    "grep": (
        lambda n: remote_grep_command(n, n, False, 100),
        lambda n, p: ["grep", "-rn", "-i", "-m", "100", "--", n, n, "2>/dev/null"],
    ),
    "find": (
        lambda n: remote_find_command(n, n, "d", 4, 50),
        lambda n, p: [
            "find",
            p,
            "-maxdepth",
            "4",
            "-type",
            "d",
            "-iname",
            n,
            "2>/dev/null",
            "|",
            "head",
            "-n",
            "50",
        ],
    ),
    "find folders and files": (
        lambda n: remote_discovery_command(n, n, 4, 50, True),
        lambda n, p: [
            "find",
            p,
            "-maxdepth",
            "4",
            "-type",
            "d",
            "-iname",
            n,
            "2>/dev/null",
            "|",
            "head",
            "-n",
            "50;",
            "printf",
            "%s\\n",
            f"{DISCOVERY_SEPARATOR};",
            "find",
            p,
            "-maxdepth",
            "4",
            "-type",
            "f",
            "-iname",
            n,
            "2>/dev/null",
            "|",
            "head",
            "-n",
            "50",
        ],
    ),
    "git badge branch": (
        lambda n: remote_git_info_commands(n)[0],
        lambda n, p: [
            "cd",
            p,
            "2>/dev/null",
            "&&",
            "git",
            "rev-parse",
            "--abbrev-ref",
            "HEAD",
            "2>/dev/null",
        ],
    ),
    "git badge commit": (
        lambda n: remote_git_info_commands(n)[1],
        lambda n, p: ["cd", p, "&&", "git", "rev-parse", "--short", "HEAD", "2>/dev/null"],
    ),
    "git badge status": (
        lambda n: remote_git_info_commands(n)[2],
        lambda n, p: [
            "cd",
            p,
            "&&",
            "git",
            "status",
            "--porcelain",
            "2>/dev/null",
            "|",
            "head",
            "-1",
        ],
    ),
    "git log": (
        lambda n: remote_git_log_command(n, FMT, 5),
        lambda n, p: [
            "cd",
            p,
            "&&",
            "git",
            "log",
            f"--pretty=format:{FMT}",
            "-n",
            "5",
            "2>/dev/null",
        ],
    ),
    "git branches": (
        lambda n: remote_git_branches_command(n, "%(refname:short)"),
        lambda n, p: [
            "cd",
            p,
            "&&",
            "git",
            "branch",
            "--format=%(refname:short)",
            "2>/dev/null",
            "&&",
            "echo",
            "---ROOT---",
            "&&",
            "git",
            "rev-parse",
            "--show-toplevel",
            "2>/dev/null",
        ],
    ),
    "git diff files": (
        lambda n: remote_git_diff_files_command(n, "main", "dev"),
        lambda n, p: ["cd", p, "&&", "git", "diff", "--name-status", "main", "dev", "2>/dev/null"],
    ),
}


@pytest.mark.parametrize("name", HOSTILE, ids=lambda n: repr(n)[:24])
@pytest.mark.parametrize("builder", sorted(BUILDERS))
def test_every_dynamic_word_comes_back_as_one_word(builder, name):
    """Mutation: write a word into the line without quoting it (or give a tool a
    path starting with a dash as it came); the parsed words differ."""
    build, expected = BUILDERS[builder]
    assert shlex.split(build(name)) == expected(name, _as_path(name))


@pytest.mark.parametrize("name", HOSTILE, ids=lambda n: repr(n)[:24])
def test_the_repo_path_commands_carry_folder_path_and_ref_as_one_word_each(name):
    """``git show`` and ``git blame`` pass the folder, the path and the ref through
    shell variables; each value is one quoted word of its assignment."""
    for line in (
        _build_remote_repo_path_command(name, name),
        remote_git_blame_command(name, name),
        remote_git_show_command(name, name, "main", 1024),
    ):
        words = shlex.split(line)
        assert words[:3] == ["cd", _as_path(name), "&&"]
        assert words.count(f"DIR={name}") == 1
        assert words.count(f"TARGET={name}") == 1
    show = shlex.split(remote_git_show_command("/r", name, name, 1024))
    assert show.count(f"REF={name}") == 1
    assert show[-3:] == ["head", "-c", "1024"]


def test_a_folder_zip_of_root_zips_its_contents_as_root():
    assert folder_zip_command("/") == ("root", "cd / && zip -r -q - .")
