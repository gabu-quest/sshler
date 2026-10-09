"""Platform skips shared by the suite.

One marker, ``posix_only(mechanism)``, skips a test on Windows when it drives a
POSIX-only mechanism (tmux on a PTY, ``fork``, ``/proc``, ``sh`` stand-ins on
``PATH``); ``tests/conftest.py`` applies it. On Windows sshler runs tmux through
WSL, which those tests do not model.

The two helpers below skip only when the OS actually refuses the operation, so a
machine that can create symlinks (CI, Developer Mode) runs the test.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

# Windows error codes, as raised on ``OSError.winerror``.
WINERROR_INVALID_NAME = 123  # ERROR_INVALID_NAME: the name has a character NTFS cannot hold
WINERROR_PRIVILEGE_NOT_HELD = 1314  # ERROR_PRIVILEGE_NOT_HELD: no symlink privilege

POSIX_ONLY_REASON = (
    "POSIX only: {mechanism}. On Windows sshler runs tmux through WSL, "
    "which this test does not model."
)


def posix_only_skip(marker: pytest.Mark | None, platform: str) -> pytest.MarkDecorator | None:
    """The skip to add for a ``posix_only`` marker on *platform*, or None.

    The marker must name its mechanism in one non-empty string, on every platform,
    so a bare ``@pytest.mark.posix_only`` fails collection on Linux too.
    """
    if marker is None:
        return None
    if len(marker.args) != 1 or not isinstance(marker.args[0], str) or not marker.args[0]:
        raise pytest.UsageError(
            f"posix_only needs exactly one non-empty mechanism string, got {marker.args!r}"
        )
    if platform != "win32":
        return None
    return pytest.mark.skip(reason=POSIX_ONLY_REASON.format(mechanism=marker.args[0]))


def make_symlink(link: Path, target: Path | str, *, target_is_directory: bool = False) -> None:
    """Create *link* -> *target*; skip the test if the OS refuses for lack of privilege."""
    try:
        os.symlink(target, link, target_is_directory=target_is_directory)
    except OSError as exc:
        if getattr(exc, "winerror", None) != WINERROR_PRIVILEGE_NOT_HELD:
            raise
        pytest.skip(
            "the OS refused to create a symlink (WinError 1314, privilege not held); "
            "enable Developer Mode or run elevated to run this test"
        )


def mkdir_or_skip_unholdable_name(path: Path) -> None:
    """``path.mkdir()``; skip the test if the file system cannot hold the name."""
    try:
        path.mkdir()
    except OSError as exc:
        if getattr(exc, "winerror", None) != WINERROR_INVALID_NAME:
            raise
        pytest.skip(f"this file system cannot hold the name {path.name!r} (WinError 123)")
