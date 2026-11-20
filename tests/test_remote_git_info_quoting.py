"""The remote git badge (``git_info`` on a remote runner) passes the directory to the
remote shell as one quoted word.

Before the fix the directory was interpolated bare into ``cd {directory}``: a path with
a space split into two words, and ``/tmp/x; touch /tmp/pwned`` ran a second command on
the remote host. A fake connection records the exact command strings.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from sshler.api.box_runner import over
from sshler.api.boxes import git_info
from sshler.api.models import APIGitInfo


class RecordingConnection:
    """Answers every command with ``replies[i]`` in order and records the command."""

    def __init__(self, replies: list[SimpleNamespace]) -> None:
        self.replies = replies
        self.commands: list[str] = []

    async def run(self, command: str, check: bool = False):
        self.commands.append(command)
        return self.replies[len(self.commands) - 1]


def _reply(stdout: str, status: int = 0) -> SimpleNamespace:
    return SimpleNamespace(returncode=status, exit_status=status, stdout=stdout)


@pytest.mark.parametrize(
    ("directory", "quoted"),
    [
        ("/tmp/a b", "'/tmp/a b'"),
        ("/tmp/x; touch /tmp/pwned", "'/tmp/x; touch /tmp/pwned'"),
        ("/srv/$(id)`id`", "'/srv/$(id)`id`'"),
        ("/srv/it's", "'/srv/it'\"'\"'s'"),
    ],
)
def test_directory_reaches_the_remote_shell_as_one_quoted_word(directory, quoted):
    """Mutation: drop `shlex.quote` (the pre-fix `cd {directory}`); every command
    string then differs from the quoted form below."""
    conn = RecordingConnection([_reply(".git\nmain\n"), _reply("abc1234\n"), _reply(" M x\n")])
    info = asyncio.run(git_info(over(conn), directory))
    assert info == APIGitInfo(is_repo=True, branch="main", commit="abc1234", dirty=True)
    assert conn.commands == [
        f"cd {quoted} && git rev-parse --git-dir --abbrev-ref HEAD 2>/dev/null",
        f"cd {quoted} && git rev-parse --short HEAD 2>/dev/null",
        f"cd {quoted} && git status --porcelain 2>/dev/null | head -c 1",
    ]


def test_plain_directory_is_unchanged():
    """Mutation: quote with double quotes or always wrap in quotes; a plain path that
    needs no quoting is sent as-is by `shlex.quote`."""
    conn = RecordingConnection([_reply(".git\ndev\n"), _reply("0f0f0f0\n"), _reply("")])
    info = asyncio.run(git_info(over(conn), "/srv/repo"))
    assert info == APIGitInfo(is_repo=True, branch="dev", commit="0f0f0f0", dirty=False)
    assert (
        conn.commands[0] == "cd /srv/repo && git rev-parse --git-dir --abbrev-ref HEAD 2>/dev/null"
    )


@pytest.mark.parametrize(
    ("directory", "target"),
    [
        ("~", "/home/u"),
        ("~/", "/home/u"),
        ("~/proj", "/home/u/proj"),
        ("~/a b; rm -rf x", "'/home/u/a b; rm -rf x'"),
    ],
)
def test_leading_tilde_is_the_resolved_home_and_the_path_is_quoted(directory, target):
    """Mutation: quote the whole path unresolved (`'~/a b'`); the remote shell then
    never expands `~` and `cd` fails, so a repo under the home folder shows as not a
    repo. The command line holds no unquoted `~` either."""
    conn = RecordingConnection(
        [_reply("/home/u"), _reply("main\n"), _reply("abc1234\n"), _reply("")]
    )
    asyncio.run(git_info(over(conn), directory))
    assert conn.commands[0] == 'printf %s "$HOME"'
    assert conn.commands[2] == f"cd {target} && git rev-parse --short HEAD 2>/dev/null"
