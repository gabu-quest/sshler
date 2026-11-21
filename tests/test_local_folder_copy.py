"""A local folder copy makes each file the way a single-file copy does: no setuid, setgid
or sticky bit, the file's bits on it before its first byte, a link kept as a link, and a
copy that fails removes what it made and reports the first error."""

from __future__ import annotations

import os
import shutil
import stat
import threading
from pathlib import Path

import pytest
from platform_support import make_symlink

from sshler.api import local_files as local_files_api
from sshler.api.failure import Failure

pytestmark = pytest.mark.posix_only("permission bits, setuid and a link in a folder")


@pytest.fixture
def source(tmp_path: Path) -> Path:
    folder = tmp_path / "src"
    (folder / "sub").mkdir(parents=True)
    (folder / "plain.txt").write_bytes(b"plain")
    (folder / "plain.txt").chmod(0o644)
    (folder / "sub" / "suid.bin").write_bytes(b"suid")
    (folder / "sub" / "suid.bin").chmod(0o6755)
    (folder / "sticky.bin").write_bytes(b"sticky")
    (folder / "sticky.bin").chmod(0o1644)
    make_symlink(folder / "link", Path("plain.txt"))
    return folder


def _modes(folder: Path) -> dict[str, int]:
    return {
        str(path.relative_to(folder)): stat.S_IMODE(path.lstat().st_mode)
        for path in sorted(folder.rglob("*"))
        if path.is_file() and not path.is_symlink()
    }


def test_a_copied_file_never_carries_setuid_setgid_or_sticky(source, tmp_path):
    target = tmp_path / "dst"

    local_files_api._local_copy_folder_new(str(source), str(target), symlinks=True)

    assert _modes(target) == {
        "plain.txt": 0o644,
        "sticky.bin": 0o644,
        "sub/suid.bin": 0o755,
    }


def test_a_copy_gives_each_file_its_bits_before_its_first_byte(source, tmp_path, monkeypatch):
    seen: list[int] = []
    real = shutil.copyfileobj

    def copyfileobj(src, dst, length=0):
        seen.append(stat.S_IMODE(os.fstat(dst.fileno()).st_mode))
        real(src, dst, length)

    monkeypatch.setattr(local_files_api.shutil, "copyfileobj", copyfileobj)
    (source / "plain.txt").chmod(0o600)

    local_files_api._local_copy_folder_new(str(source), str(tmp_path / "dst"), symlinks=True)

    assert sorted(seen) == [0o600, 0o644, 0o755]


def test_a_link_in_the_folder_is_copied_as_a_link(source, tmp_path):
    target = tmp_path / "dst"

    local_files_api._local_copy_folder_new(str(source), str(target), symlinks=True)

    assert os.readlink(target / "link") == "plain.txt"


@pytest.mark.skipif(os.geteuid() == 0, reason="root reads mode 0")
def test_a_file_that_cannot_be_read_fails_the_copy_with_its_error_and_leaves_nothing(
    source, tmp_path
):
    (source / "sub" / "suid.bin").chmod(0o000)
    target = tmp_path / "dst"
    try:
        with pytest.raises(Failure) as caught:
            local_files_api._local_copy_folder_new(str(source), str(target), symlinks=True)
    finally:
        (source / "sub" / "suid.bin").chmod(0o644)

    assert (caught.value.reason, os.path.lexists(target)) == ("denied", False)


@pytest.mark.parametrize("symlinks", [True, False])
def test_a_named_pipe_in_the_folder_fails_the_copy_without_opening_it(source, tmp_path, symlinks):
    pipe = source / "sub" / "pipe"
    os.mkfifo(pipe)
    target = tmp_path / "dst"
    answers: list[tuple[str, str]] = []

    def copy() -> None:
        try:
            local_files_api._local_copy_folder_new(str(source), str(target), symlinks=symlinks)
        except Failure as failure:
            answers.append((failure.reason, str(failure.detail)))

    worker = threading.Thread(target=copy, daemon=True)
    worker.start()
    worker.join(10)
    if worker.is_alive():
        with open(pipe, "wb"):  # a writer that comes and goes ends the wait
            pass
        worker.join()
        pytest.fail("the copy waited for a writer on the named pipe")

    assert answers == [("invalid", f"Could not copy {source} to {target}: Not a regular file")]
    assert os.path.lexists(target) is False
