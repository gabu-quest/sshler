"""Input validation and sanitization utilities for security."""

from __future__ import annotations

import posixpath
import re


class ValidationError(Exception):
    """Raised when input validation fails."""

    pass


class PathValidator:
    """Validate and sanitize filesystem paths to prevent traversal attacks."""

    @staticmethod
    def validate_remote_path(path: str, allow_absolute: bool = True) -> str:
        """Validate remote path and prevent traversal attacks.

        Args:
            path: Path to validate
            allow_absolute: Whether to allow absolute paths

        Returns:
            Normalized path

        Raises:
            ValidationError: If path contains traversal attempts or is invalid
        """
        if not path:
            raise ValidationError("Path cannot be empty")

        # Normalize path using posixpath (for remote paths)
        normalized = posixpath.normpath(path)

        # Check for null bytes
        if "\0" in normalized:
            raise ValidationError("Path cannot contain null bytes")

        # Check for traversal attempts after normalization
        # After normpath, '..' should only appear at the start if it's a relative path going up
        if not allow_absolute and normalized.startswith("/"):
            raise ValidationError("Absolute paths not allowed")

        # Check for suspicious patterns that survived normalization
        if "/../" in normalized or normalized.endswith("/.."):
            raise ValidationError("Path traversal detected")

        # For absolute paths, ensure they don't try to go above root
        if normalized.startswith("/") and "/.." in normalized:
            raise ValidationError("Path traversal above root detected")

        return normalized

    @staticmethod
    def validate_local_path(path: str, allow_absolute: bool = True) -> str:
        """Validate local path and prevent traversal attacks.

        Args:
            path: Path to validate
            allow_absolute: Whether to allow absolute paths

        Returns:
            Normalized path

        Raises:
            ValidationError: If path contains traversal attempts or is invalid
        """
        if not path:
            raise ValidationError("Path cannot be empty")

        # Check for null bytes
        if "\0" in path:
            raise ValidationError("Path cannot contain null bytes")

        # Use posixpath for consistency (works on Windows paths too)
        normalized = posixpath.normpath(path)

        if not allow_absolute and (normalized.startswith("/") or ":" in normalized):
            raise ValidationError("Absolute paths not allowed")

        # Check for traversal attempts
        if "/../" in normalized or normalized.endswith("/..") or normalized == "..":
            raise ValidationError("Path traversal detected")

        return normalized

    @staticmethod
    def validate_filename(filename: str) -> str:
        """Validate filename component (no slashes, no dangerous characters).

        Args:
            filename: Filename to validate

        Returns:
            Validated filename

        Raises:
            ValidationError: If filename is invalid
        """
        if not filename:
            raise ValidationError("Filename cannot be empty")

        if filename in {".", ".."}:
            raise ValidationError("Filename cannot be '.' or '..'")

        # Check for path separators
        if "/" in filename or "\\" in filename:
            raise ValidationError("Filename cannot contain path separators")

        # Check for null bytes
        if "\0" in filename:
            raise ValidationError("Filename cannot contain null bytes")

        # Check for dangerous characters (optional - uncomment if needed)
        # dangerous_chars = '<>:"|?*'
        # if any(c in filename for c in dangerous_chars):
        #     raise ValidationError(f"Filename cannot contain: {dangerous_chars}")

        return filename

    @staticmethod
    def sanitize_session_name(session_name: str) -> str:
        """Sanitize tmux session name to only allow safe characters.

        Args:
            session_name: Session name to sanitize

        Returns:
            Sanitized session name with only alphanumeric, dash, underscore, dot

        Raises:
            ValidationError: If session name is empty after sanitization
        """
        # Allow only alphanumeric, dash, underscore, dot
        sanitized = "".join(
            ch if ch.isalnum() or ch in "-_." else "_"
            for ch in session_name
        )

        if not sanitized:
            raise ValidationError("Session name must contain at least one valid character")

        return sanitized


class InputValidator:
    """Validate various user inputs."""

    @staticmethod
    def validate_box_name(box_name: str) -> str:
        """Validate box name format.

        Args:
            box_name: Box name to validate

        Returns:
            Validated box name

        Raises:
            ValidationError: If box name is invalid
        """
        if not box_name:
            raise ValidationError("Box name cannot be empty")

        # Box names should be reasonable length
        if len(box_name) > 100:
            raise ValidationError("Box name too long (max 100 characters)")

        # Check for null bytes
        if "\0" in box_name:
            raise ValidationError("Box name cannot contain null bytes")

        # Allow alphanumeric, dash, underscore, dot
        if not re.match(r"^[a-zA-Z0-9_.-]+$", box_name):
            raise ValidationError(
                "Box name can only contain alphanumeric characters, dash, underscore, and dot"
            )

        return box_name

    @staticmethod
    def validate_port(port: int | str) -> int:
        """Validate port number.

        Args:
            port: Port number to validate

        Returns:
            Validated port number

        Raises:
            ValidationError: If port is invalid
        """
        try:
            port_int = int(port)
        except (ValueError, TypeError):
            raise ValidationError("Port must be a valid integer")

        if not (1 <= port_int <= 65535):
            raise ValidationError("Port must be between 1 and 65535")

        return port_int

    @staticmethod
    def validate_upload_size(size_bytes: int, max_size: int) -> None:
        """Validate file upload size.

        Args:
            size_bytes: Size of upload in bytes
            max_size: Maximum allowed size in bytes

        Raises:
            ValidationError: If file is too large
        """
        if size_bytes > max_size:
            max_mb = max_size / (1024 * 1024)
            raise ValidationError(f"File too large (max {max_mb:.1f}MB)")

    @staticmethod
    def validate_limit(limit: int, max_limit: int = 1000) -> int:
        """Validate pagination limit parameter.

        Args:
            limit: Requested limit
            max_limit: Maximum allowed limit

        Returns:
            Validated and clamped limit

        Raises:
            ValidationError: If limit is invalid
        """
        try:
            limit_int = int(limit)
        except (ValueError, TypeError):
            raise ValidationError("Limit must be a valid integer")

        if limit_int < 1:
            raise ValidationError("Limit must be at least 1")

        return min(limit_int, max_limit)
