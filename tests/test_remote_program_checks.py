"""The check that begins a remote command line, proved against a real shell.

A shell gives a pipeline the status of its last command, so a line that pipes a program
into ``head`` exits 0 when the program is not there. The lines of a route that need that
program begin by checking for it and exit 127 (the shell's status for a command it
cannot find). The fakes of the other tests answer from the words of a line; this file
runs exactly two lines with ``/bin/sh``, each with a ``PATH`` of one pytest folder: the
discovery line built from a folder of the test and the pattern ``*report*``, and the
fixed text ``REMOTE_LIST_SESSIONS``.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from sshler.api.search import remote_discovery_command
from sshler.api.sessions import REMOTE_LIST_SESSIONS

pytestmark = pytest.mark.posix_only("/bin/sh runs the command lines with a PATH of a temp folder")

PROGRAM_NOT_FOUND = 127


def _sh(line: str, programs: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["/bin/sh", "-c", line],
        env={"PATH": str(programs)},
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


def test_the_session_listing_on_a_box_with_no_sessions_exits_0_with_no_output(programs: Path):
    tmux = programs / "tmux"
    tmux.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
    tmux.chmod(0o755)
    ran = _sh(REMOTE_LIST_SESSIONS, programs)
    assert (ran.returncode, ran.stdout) == (0, "")
