"""The shared platform skips skip exactly when they should (wave 6, WS-AK).

Windows CI must pass without hiding tests that should hold there: a POSIX-only test
skips on Windows only, with a reason that names its mechanism, and the symlink and
file-name helpers skip only on the one Windows error that means "the OS refused".
Each test names the mutation it kills.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from platform_support import (
    WINERROR_INVALID_NAME,
    WINERROR_PRIVILEGE_NOT_HELD,
    make_symlink,
    mkdir_or_skip_unholdable_name,
    posix_only_skip,
)

PTY = pytest.mark.posix_only("a PTY running the sh fake tmux").mark


def _no_skip(call) -> None:
    """Run *call*; a skip it raises fails the test (a skip would hide the re-raise)."""
    try:
        call()
    except pytest.skip.Exception as skipped:
        pytest.fail(f"skipped instead of re-raising: {skipped}")


def _winerror(code: int) -> OSError:
    """An OSError carrying *code* in ``winerror`` on any host (Linux has no such field)."""

    class _WinError(OSError):
        winerror = code

    return _WinError(22, "refused")


def test_posix_only_skips_on_windows_with_the_mechanism_in_the_reason() -> None:
    """Mutation: return None for every platform; the test would run (and fail) on Windows."""
    skip = posix_only_skip(PTY, "win32")
    assert skip is not None
    assert (skip.mark.name, skip.mark.kwargs) == (
        "skip",
        {
            "reason": "POSIX only: a PTY running the sh fake tmux. On Windows sshler runs "
            "tmux through WSL, which this test does not model."
        },
    )


@pytest.mark.parametrize("platform", ["linux", "darwin"])
def test_posix_only_runs_on_posix(platform: str) -> None:
    """Mutation: skip on every platform; the POSIX suite would silently lose the test."""
    assert posix_only_skip(PTY, platform) is None


def test_an_unmarked_test_is_never_skipped() -> None:
    """Mutation: skip whenever the platform is Windows, marked or not."""
    assert posix_only_skip(None, "win32") is None


@pytest.mark.parametrize("args", [(), ("",), ("a", "b"), (3,)])
def test_posix_only_without_one_mechanism_fails_collection_everywhere(args: tuple) -> None:
    """Mutation: accept a bare marker; a skip reason would no longer name its mechanism."""
    with pytest.raises(pytest.UsageError, match="posix_only needs exactly one"):
        posix_only_skip(pytest.mark.posix_only(*args).mark, "linux")


def test_make_symlink_skips_when_windows_refuses_the_privilege(monkeypatch, tmp_path) -> None:
    """Mutation: re-raise every OSError; a laptop without symlink privilege errors."""

    def refuse(*_args, **_kwargs):
        raise _winerror(WINERROR_PRIVILEGE_NOT_HELD)

    monkeypatch.setattr(os, "symlink", refuse)
    with pytest.raises(pytest.skip.Exception, match="WinError 1314"):
        make_symlink(tmp_path / "link", tmp_path / "target")


def test_make_symlink_reraises_any_other_failure(monkeypatch, tmp_path) -> None:
    """Mutation: skip on any OSError; a real failure (here, a bad name) would be hidden."""

    def fail(*_args, **_kwargs):
        raise _winerror(WINERROR_INVALID_NAME)

    monkeypatch.setattr(os, "symlink", fail)
    with pytest.raises(OSError, match="refused"):
        _no_skip(lambda: make_symlink(tmp_path / "link", tmp_path / "target"))


def test_make_symlink_creates_the_link_with_its_directory_flag(monkeypatch, tmp_path) -> None:
    """Mutation: drop ``target_is_directory``; Windows would make a file link to a folder."""
    calls: list[tuple[object, object, bool]] = []

    def record(target, link, target_is_directory=False):
        calls.append((target, link, target_is_directory))

    monkeypatch.setattr(os, "symlink", record)
    make_symlink(tmp_path / "link", tmp_path / "dir", target_is_directory=True)
    assert calls == [(tmp_path / "dir", tmp_path / "link", True)]


def test_unholdable_name_skips_only_on_invalid_name(monkeypatch, tmp_path) -> None:
    """Mutation: re-raise WinError 123; the `"` case errors on Windows instead of skipping."""

    def invalid(self, *_args, **_kwargs):
        raise _winerror(WINERROR_INVALID_NAME)

    monkeypatch.setattr(Path, "mkdir", invalid)
    with pytest.raises(pytest.skip.Exception, match="cannot hold the name"):
        mkdir_or_skip_unholdable_name(tmp_path / 'a"b')


def test_unholdable_name_reraises_any_other_failure(tmp_path) -> None:
    """Mutation: skip on any OSError; a missing parent would be hidden as a skip."""
    with pytest.raises(FileNotFoundError):
        _no_skip(lambda: mkdir_or_skip_unholdable_name(tmp_path / "missing" / "child"))
