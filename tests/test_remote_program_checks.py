"""The check that begins a remote command line, proved against a real shell.

A shell gives a pipeline the status of its last command, so a line that pipes a program
into ``head`` exits 0 when the program is not there. The lines of a route that need that
program begin by checking for it and exit 127 (the shell's status for a command it
cannot find). The fakes of the other tests answer from the words of a line; this file
runs exactly two lines with ``/bin/sh``, each with a ``PATH`` of one pytest folder: the
discovery line built from a folder of the test and the pattern ``*report*``, and the
fixed text ``REMOTE_LIST_SESSIONS`` (with a stand-in ``tmux`` and ``id``, and the
socket folder of the test).
"""

from __future__ import annotations

import shutil
import socket
import subprocess
from pathlib import Path

import pytest

from sshler.api import sessions as sessions_api
from sshler.api.search import remote_discovery_command
from sshler.api.sessions import REMOTE_LIST_SESSIONS

pytestmark = pytest.mark.posix_only("/bin/sh runs the command lines with a PATH of a temp folder")

PROGRAM_NOT_FOUND = 127


def _sh(
    line: str, programs: Path, *args: str, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["/bin/sh", "-c", line, "sh", *args],
        env={"PATH": str(programs), **(env or {})},
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )


def _link(programs: Path, name: str) -> None:
    found = shutil.which(name)
    assert found is not None, name
    (programs / name).symlink_to(found)


@pytest.fixture
def programs(tmp_path: Path) -> Path:
    folder = tmp_path / "programs"
    folder.mkdir()
    return folder


def test_a_discovery_line_on_a_box_without_find_exits_127(tmp_path: Path, programs: Path):
    (tmp_path / "tree" / "reports").mkdir(parents=True)
    line = remote_discovery_command(str(tmp_path / "tree"), "*report*", 4, 50, False)
    _link(programs, "head")  # head is there: it would give the pipeline status 0
    ran = _sh(line, programs)
    assert (ran.returncode, ran.stdout) == (PROGRAM_NOT_FOUND, "")


def test_a_discovery_line_on_a_box_with_find_gives_the_matching_folder(
    tmp_path: Path, programs: Path
):
    tree = tmp_path / "tree"
    (tree / "reports").mkdir(parents=True)
    (tree / "other").mkdir()
    line = remote_discovery_command(str(tree), "*report*", 4, 50, False)
    for name in ("find", "head"):
        _link(programs, name)
    ran = _sh(line, programs)
    assert (ran.returncode, ran.stdout) == (0, f"{tree}/reports\n")


def test_the_session_listing_on_a_box_without_tmux_exits_127(programs: Path):
    ran = _sh(REMOTE_LIST_SESSIONS, programs)
    assert (ran.returncode, ran.stdout) == (PROGRAM_NOT_FOUND, "")


def _program(programs: Path, name: str, text: str) -> None:
    path = programs / name
    path.write_text(text, encoding="utf-8")
    path.chmod(0o755)


def test_the_session_listing_on_a_box_with_no_sessions_exits_0_with_no_output(programs: Path):
    _program(programs, "tmux", "#!/bin/sh\necho 'no server running on /x/default' >&2\nexit 1\n")
    ran = _sh(REMOTE_LIST_SESSIONS, programs)
    assert (ran.returncode, ran.stdout) == (0, "")


# The stand-in tmux of a box with three ts servers: ts-fine has the session "fine",
# ts-stale is a socket whose server is gone, ts-slow does not answer; the default server
# is not running. ``id -u`` answers 4242, so the socket folder is the test's own.
BOX_TMUX = """#!/bin/sh
case "$2" in
  ts-fine) echo fine ;;
  ts-slow) exec sleep 30 ;;
  *) echo "no server running on /x/$2" >&2; exit 1 ;;
esac
"""


@pytest.fixture
def box(tmp_path: Path, programs: Path) -> dict[str, str]:
    """The box of ``BOX_TMUX``: the environment the listing runs in."""
    _program(programs, "tmux", BOX_TMUX)
    _program(programs, "id", "#!/bin/sh\necho 4242\n")
    for name in ("timeout", "sleep"):
        _link(programs, name)
    sockets = tmp_path / "tmux" / "tmux-4242"
    sockets.mkdir(parents=True)
    for server in ("ts-fine", "ts-stale", "ts-slow"):
        with socket.socket(socket.AF_UNIX) as bound:
            bound.bind(str(sockets / server))
    return {"TMUX_TMPDIR": str(tmp_path / "tmux")}


def test_the_session_listing_says_by_its_status_that_a_server_could_not_be_asked(
    programs: Path, box: dict[str, str]
):
    ran = _sh(REMOTE_LIST_SESSIONS, programs, env=box)
    assert (ran.returncode, ran.stdout) == (sessions_api.REMOTE_LISTING_INCOMPLETE, "fine\n")


def test_the_listing_of_one_session_asks_its_own_server_and_the_default_one(
    programs: Path, box: dict[str, str]
):
    ran = _sh(REMOTE_LIST_SESSIONS, programs, "fine", env=box)
    assert (ran.returncode, ran.stdout) == (0, "fine\n")


def test_the_listing_of_a_session_whose_server_does_not_answer_says_it_could_not_ask(
    programs: Path, box: dict[str, str]
):
    ran = _sh(REMOTE_LIST_SESSIONS, programs, "slow", env=box)
    assert (ran.returncode, ran.stdout) == (sessions_api.REMOTE_LISTING_INCOMPLETE, "")
