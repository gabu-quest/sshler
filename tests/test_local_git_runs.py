"""A local git child is run with a time limit and is gone once the limit has passed."""

from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path

import pytest
from fastapi import HTTPException

from sshler.api import boxes, local_files
from sshler.api.git import _run_local_git
from sshler.api.models import APIGitInfo


@pytest.mark.posix_only("a #!/bin/sh stand-in for git first on PATH")
def test_a_timed_out_git_child_is_gone_after_the_504(tmp_path, stalled_program):
    """Mutation killed: wait_for(proc.communicate()) with no kill, which left git running."""
    gone = stalled_program("git")

    with pytest.raises(HTTPException) as caught:
        asyncio.run(_run_local_git(["git", "status"], cwd=str(tmp_path), timeout=0.5))

    assert (caught.value.status_code, caught.value.detail) == (504, "Git command timed out")
    gone()


@pytest.mark.posix_only("a #!/bin/sh stand-in for git first on PATH")
def test_a_stuck_gitignore_check_gives_up_and_marks_nothing(tmp_path, monkeypatch, stalled_program):
    """Mutation killed: communicate() with no limit, which held the directory listing for as
    long as git ran."""
    gone = stalled_program("git")
    monkeypatch.setattr(local_files, "GITIGNORE_TIMEOUT", 0.5)

    started = time.monotonic()
    ignored = asyncio.run(local_files._get_gitignored_names(str(tmp_path), ["a.txt", "b.txt"]))

    assert ignored == set()
    assert time.monotonic() - started < 10
    gone()


@pytest.mark.posix_only("a #!/bin/sh stand-in for git first on PATH")
def test_a_stuck_git_gives_the_git_info_of_no_repository(tmp_path, monkeypatch, stalled_program):
    """Mutation killed: communicate() with no limit in the git info of a local folder."""
    gone = stalled_program("git")
    monkeypatch.setattr(boxes, "GIT_INFO_TIMEOUT", 0.5)

    started = time.monotonic()
    info = asyncio.run(boxes._get_local_git_info(str(tmp_path)))

    assert info == APIGitInfo(is_repo=False)
    assert time.monotonic() - started < 10
    gone()


@pytest.mark.posix_only("a #!/bin/sh stand-in for git first on PATH")
def test_the_gitignore_check_gives_git_the_names_on_its_input(tmp_path, monkeypatch):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    stub = bin_dir / "git"
    stub.write_text("#!/bin/sh\ngrep -v keep\n")
    stub.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")

    names = ["keep.txt", "out.log", "tmp"]
    ignored = asyncio.run(local_files._get_gitignored_names(str(tmp_path), names))

    assert ignored == {"out.log", "tmp"}


@pytest.mark.posix_only("a #!/bin/sh stand-in for git first on PATH")
def test_git_runs_in_the_given_folder_and_returns_its_output(tmp_path, monkeypatch):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    stub = bin_dir / "git"
    stub.write_text("#!/bin/sh\npwd\n")
    stub.chmod(0o755)
    work = Path(tmp_path / "work")
    work.mkdir()
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")

    ok, out = asyncio.run(_run_local_git(["git", "x"], cwd=str(work)))

    assert (ok, out) == (True, str(work.resolve()))
