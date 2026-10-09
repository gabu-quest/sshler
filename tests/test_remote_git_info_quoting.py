"""``_get_remote_git_info`` passes the directory to the remote shell as one quoted word.

Before the fix the directory was interpolated bare into ``cd {directory}``: a path with
a space split into two words, and ``/tmp/x; touch /tmp/pwned`` ran a second command on
the remote host. A fake connection records the exact command strings.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from sshler.api.boxes import _get_remote_git_info
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
    conn = RecordingConnection([_reply("main\n"), _reply("abc1234\n"), _reply(" M x\n")])
    info = asyncio.run(_get_remote_git_info(conn, directory))
    assert info == APIGitInfo(is_repo=True, branch="main", commit="abc1234", dirty=True)
    assert conn.commands == [
        f"cd {quoted} 2>/dev/null && git rev-parse --abbrev-ref HEAD 2>/dev/null",
        f"cd {quoted} && git rev-parse --short HEAD 2>/dev/null",
        f"cd {quoted} && git status --porcelain 2>/dev/null | head -1",
    ]


def test_plain_directory_is_unchanged():
    """Mutation: quote with double quotes or always wrap in quotes; a plain path that
    needs no quoting is sent as-is by `shlex.quote`."""
    conn = RecordingConnection([_reply("dev\n"), _reply("0f0f0f0\n"), _reply("")])
    info = asyncio.run(_get_remote_git_info(conn, "/srv/repo"))
    assert info == APIGitInfo(is_repo=True, branch="dev", commit="0f0f0f0", dirty=False)
    assert conn.commands[0] == (
        "cd /srv/repo 2>/dev/null && git rev-parse --abbrev-ref HEAD 2>/dev/null"
    )


@pytest.mark.parametrize(
    ("directory", "target"),
    [
        ("~", "~"),
        ("~/", "~"),
        ("~/proj", "~/proj"),
        ("~/a b; rm -rf x", "~/'a b; rm -rf x'"),
    ],
)
def test_leading_tilde_stays_expandable_and_the_rest_is_quoted(directory, target):
    """Mutation: quote the whole path (`'~/a b'`); the remote shell then never expands
    `~` and `cd` fails, so a repo under the home folder shows as not a repo."""
    conn = RecordingConnection([_reply("main\n"), _reply("abc1234\n"), _reply("")])
    asyncio.run(_get_remote_git_info(conn, directory))
    assert conn.commands[1] == f"cd {target} && git rev-parse --short HEAD 2>/dev/null"
