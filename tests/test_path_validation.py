"""Tests for path validation and symlink security."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from sshler.api.helpers import _normalize_local_path


class TestNormalizeLocalPathSymlinkSecurity:
    """Test that _normalize_local_path prevents symlink directory escape attacks."""

    def test_normal_path_no_restriction(self, tmp_path: Path):
        """Test that normal paths work without restrictions."""
        test_dir = tmp_path / "normal"
        test_dir.mkdir()

        normalized = _normalize_local_path(str(test_dir))
        assert Path(normalized) == test_dir.resolve()

    def test_symlink_within_allowed_base(self, tmp_path: Path):
        """Test that symlinks within allowed base are permitted."""
        # Create directory structure
        allowed = tmp_path / "allowed"
        allowed.mkdir()

        target = allowed / "target.txt"
        target.write_text("content")

        link = allowed / "link"
        link.symlink_to(target)

        # Should work - symlink points to file within allowed base
        normalized = _normalize_local_path(str(link), allowed_base=allowed)
        assert Path(normalized) == target.resolve()

    def test_symlink_escape_to_parent(self, tmp_path: Path):
        """Test that symlinks escaping to parent directory are blocked."""
        # Create directory structure:
        # tmp_path/
        #   allowed/
        #     evil_link -> ../secret.txt
        #   secret.txt
        allowed = tmp_path / "allowed"
        allowed.mkdir()

        secret = tmp_path / "secret.txt"
        secret.write_text("secret data")

        evil_link = allowed / "evil_link"
        evil_link.symlink_to(secret)

        # Should raise ValueError - symlink escapes allowed base
        with pytest.raises(ValueError, match="Path escape detected"):
            _normalize_local_path(str(evil_link), allowed_base=allowed)

    def test_symlink_escape_to_system(self, tmp_path: Path):
        """Test that symlinks to system files are blocked."""
        allowed = tmp_path / "allowed"
        allowed.mkdir()

        # Create symlink pointing to /etc/passwd
        evil_link = allowed / "evil_link"
        evil_link.symlink_to("/etc/passwd")

        # Should raise ValueError - symlink escapes allowed base
        with pytest.raises(ValueError, match="Path escape detected"):
            _normalize_local_path(str(evil_link), allowed_base=allowed)

    def test_symlink_escape_via_relative_path(self, tmp_path: Path):
        """Test that relative path symlinks escaping are blocked."""
        # Create directory structure:
        # tmp_path/
        #   allowed/
        #     subdir/
        #       evil_link -> ../../secret.txt
        #   secret.txt
        allowed = tmp_path / "allowed"
        allowed.mkdir()

        subdir = allowed / "subdir"
        subdir.mkdir()

        secret = tmp_path / "secret.txt"
        secret.write_text("secret data")

        evil_link = subdir / "evil_link"
        evil_link.symlink_to("../../secret.txt")

        # Should raise ValueError - symlink escapes allowed base
        with pytest.raises(ValueError, match="Path escape detected"):
            _normalize_local_path(str(evil_link), allowed_base=allowed)

    def test_directory_symlink_escape(self, tmp_path: Path):
        """Test that directory symlinks escaping are blocked."""
        allowed = tmp_path / "allowed"
        allowed.mkdir()

        outside = tmp_path / "outside"
        outside.mkdir()
        (outside / "file.txt").write_text("outside data")

        # Create symlink to directory outside allowed base
        evil_dir_link = allowed / "evil_dir"
        evil_dir_link.symlink_to(outside)

        # Should raise ValueError - symlink escapes allowed base
        with pytest.raises(ValueError, match="Path escape detected"):
            _normalize_local_path(str(evil_dir_link), allowed_base=allowed)

    def test_chained_symlinks_escape(self, tmp_path: Path):
        """Test that chained symlinks escaping are blocked."""
        # Create directory structure:
        # tmp_path/
        #   allowed/
        #     link1 -> link2
        #     link2 -> ../secret.txt
        #   secret.txt
        allowed = tmp_path / "allowed"
        allowed.mkdir()

        secret = tmp_path / "secret.txt"
        secret.write_text("secret data")

        link2 = allowed / "link2"
        link2.symlink_to("../secret.txt")

        link1 = allowed / "link1"
        link1.symlink_to(link2)

        # Should raise ValueError - chained symlinks escape allowed base
        with pytest.raises(ValueError, match="Path escape detected"):
            _normalize_local_path(str(link1), allowed_base=allowed)

    def test_symlink_to_allowed_subdirectory(self, tmp_path: Path):
        """Test that symlinks to subdirectories within allowed base work."""
        allowed = tmp_path / "allowed"
        allowed.mkdir()

        subdir = allowed / "subdir"
        subdir.mkdir()
        (subdir / "file.txt").write_text("content")

        link = allowed / "link_to_subdir"
        link.symlink_to(subdir)

        # Should work - symlink points to directory within allowed base
        normalized = _normalize_local_path(str(link), allowed_base=allowed)
        assert Path(normalized) == subdir.resolve()

    def test_no_restriction_allows_escape(self, tmp_path: Path):
        """Test that without allowed_base restriction, symlinks work freely."""
        allowed = tmp_path / "allowed"
        allowed.mkdir()

        outside = tmp_path / "outside.txt"
        outside.write_text("outside data")

        link = allowed / "link"
        link.symlink_to(outside)

        # Should work without restriction
        normalized = _normalize_local_path(str(link), allowed_base=None)
        assert Path(normalized) == outside.resolve()

    def test_home_directory_restriction(self):
        """Test that restricting to home directory works."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)

            # Create symlink in temp dir pointing to /etc/passwd
            evil_link = tmp_path / "evil"
            evil_link.symlink_to("/etc/passwd")

            # Should raise ValueError when restricted to home directory
            with pytest.raises(ValueError, match="Path escape detected"):
                _normalize_local_path(str(evil_link), allowed_base=Path.home())

    def test_expanduser_in_allowed_base(self, tmp_path: Path):
        """Test that ~ expansion works in allowed_base."""
        # This test verifies that allowed_base="~" works correctly
        # Create a symlink in temp trying to escape
        evil_link = tmp_path / "evil"
        evil_link.symlink_to("/etc/passwd")

        # Should raise - symlink outside home directory
        with pytest.raises(ValueError, match="Path escape detected"):
            _normalize_local_path(str(evil_link), allowed_base="~")

    def test_nonexistent_path_with_restriction(self, tmp_path: Path):
        """Test that non-existent paths work with strict=False."""
        allowed = tmp_path / "allowed"
        allowed.mkdir()

        # Path that doesn't exist yet
        nonexistent = allowed / "subdir" / "file.txt"

        # Should work - strict=False allows non-existent paths
        normalized = _normalize_local_path(str(nonexistent), allowed_base=allowed)
        assert "subdir" in normalized
        assert "file.txt" in normalized
