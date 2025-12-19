from __future__ import annotations

import asyncio
import contextlib
import platform
import posixpath
from pathlib import Path, PurePosixPath

import asyncssh

from ..ssh import sftp_read_file

DEFAULT_MAX_UPLOAD_BYTES = 50 * 1024 * 1024
MAX_IMAGE_PREVIEW_BYTES = 2 * 1024 * 1024
IMAGE_CONTENT_TYPES: dict[str, str] = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".svg": "image/svg+xml",
}
LOCAL_IS_WINDOWS = platform.system().lower().startswith("windows")


def _normalize_directory_path(directory: str | None) -> str:
    raw = (directory or "/").strip()
    if not raw:
        raw = "/"
    if not raw.startswith("/"):
        raw = "/" + raw.lstrip("/")

    normalized = posixpath.normpath(raw)
    if normalized == ".":
        return "/"
    if not normalized.startswith("/"):
        normalized = "/" + normalized.lstrip("/")
    return normalized or "/"


def _compose_remote_child_path(directory: str, filename: str) -> str:
    cleaned = (filename or "").strip()
    if not cleaned:
        raise ValueError("Filename is required")
    if cleaned in {".", ".."} or "/" in cleaned:
        raise ValueError("Filename cannot contain path separators")
    if "\x00" in cleaned:
        raise ValueError("Filename contains unsupported characters")
    parent = PurePosixPath(_normalize_directory_path(directory))
    return (parent / cleaned).as_posix()


def _normalize_local_path(directory: str | None, allowed_base: str | Path | None = None) -> str:
    """Normalize and validate local path, resolving symlinks to prevent directory escape.

    Args:
        directory: Path to normalize (defaults to user home)
        allowed_base: Base directory to restrict access to. If provided, validates that
                     the resolved path (after following symlinks) stays within this base.
                     If None, no restriction is applied (full filesystem access).

    Returns:
        Normalized absolute path string

    Raises:
        ValueError: If resolved path escapes the allowed base directory (when allowed_base is set)

    Security Notes:
        - This function resolves symlinks using Path.resolve(), preventing attacks where
          a symlink inside an allowed directory points to files outside it.
        - For maximum security, always pass an allowed_base parameter.
        - For "local" boxes, consider restricting to Path.home() or box.default_dir.
    """
    if directory:
        base = Path(directory).expanduser()
    else:
        base = Path.home()

    # Resolve symlinks to prevent directory traversal via symlinks
    try:
        resolved = base.resolve(strict=False)  # strict=False allows non-existent paths
    except Exception:
        resolved = base

    # Validate that resolved path is within allowed base (if specified)
    if allowed_base is not None:
        # Convert allowed_base to Path and resolve it
        if isinstance(allowed_base, str):
            allowed_base = Path(allowed_base)
        allowed_base_resolved = allowed_base.expanduser().resolve(strict=False)

        # Check if resolved path is within allowed base
        # Use try/except for Python 3.8 compatibility (is_relative_to added in 3.9)
        try:
            # Python 3.9+
            if not resolved.is_relative_to(allowed_base_resolved):
                raise ValueError(
                    f"Path escape detected: {resolved} is outside allowed directory {allowed_base_resolved}"
                )
        except AttributeError:
            # Python 3.8 fallback
            try:
                resolved.relative_to(allowed_base_resolved)
            except ValueError:
                raise ValueError(
                    f"Path escape detected: {resolved} is outside allowed directory {allowed_base_resolved}"
                )

    if LOCAL_IS_WINDOWS:
        return resolved.as_posix()
    return str(resolved)


def _compose_local_child_path(directory: str, filename: str) -> str:
    cleaned = (filename or "").strip()
    if not cleaned:
        raise ValueError("Filename is required")
    if cleaned in {".", ".."} or any(sep in cleaned for sep in ("/", "\\")):
        raise ValueError("Filename cannot contain path separators")
    parent = Path(directory or Path.home())
    target = parent / cleaned
    if LOCAL_IS_WINDOWS:
        return target.expanduser().as_posix()
    return str(target.expanduser())


async def _local_list_directory(path: str) -> list[dict[str, object]]:
    def _worker() -> list[dict[str, object]]:
        entries: list[dict[str, object]] = []
        base_path = Path(path)
        for child in base_path.iterdir():
            try:
                stats = child.stat()
            except Exception:
                continue
            entries.append(
                {
                    "name": child.name,
                    "is_directory": child.is_dir(),
                    "size": stats.st_size if child.is_file() else None,
                    "modified": stats.st_mtime,
                }
            )
        entries.sort(key=lambda entry: (not entry["is_directory"], str(entry["name"]).lower()))
        return entries

    return await asyncio.to_thread(_worker)


async def _local_is_directory(path: str) -> bool:
    return await asyncio.to_thread(lambda: Path(path).is_dir())


async def _local_read_text(path: str, max_bytes: int) -> str:
    def _worker() -> str:
        with open(Path(path), "rb") as handle:
            data = handle.read(max_bytes)
        return data.decode("utf-8", errors="replace")

    return await asyncio.to_thread(_worker)


async def _local_write_text(path: str, content: str) -> None:
    def _worker() -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with open(Path(path), "w", encoding="utf-8") as handle:
            handle.write(content)

    await asyncio.to_thread(_worker)


async def _local_read_bytes(path: str, limit: int) -> tuple[bytes, bool]:
    def _worker() -> tuple[bytes, bool]:
        with open(Path(path), "rb") as handle:
            data = handle.read(limit + 1)
        return (data[:limit], len(data) > limit)

    return await asyncio.to_thread(_worker)


async def _local_write_bytes(path: str, data: bytes) -> None:
    def _worker() -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with open(Path(path), "wb") as handle:
            handle.write(data)

    await asyncio.to_thread(_worker)


async def _local_create_file(path: str) -> None:
    def _worker() -> None:
        file_path = Path(path)
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.touch(exist_ok=False)

    await asyncio.to_thread(_worker)


async def _local_delete_file(path: str) -> None:
    def _worker() -> None:
        Path(path).unlink()

    await asyncio.to_thread(_worker)


async def _read_file_bytes(
    connection: asyncssh.SSHClientConnection, path: str, limit: int
) -> tuple[bytes, bool]:
    sftp_client = await connection.start_sftp_client()
    try:
        async with await sftp_client.open(path, "rb") as remote_file:
            data_read = await remote_file.read(limit + 1)
            # Opened with 'rb' so data should be bytes
            data = data_read if isinstance(data_read, bytes) else data_read.encode()
    finally:
        with contextlib.suppress(Exception):
            await sftp_client.exit()  # type: ignore[func-returns-value]
    too_large = len(data) > limit
    if too_large:
        return b"", True
    return data, False


async def _read_remote_text(
    connection: asyncssh.SSHClientConnection, path: str, limit: int
) -> str:
    """Retrieve UTF-8 text from an SFTP connection with graceful fallback."""

    try:
        return await sftp_read_file(connection, path, max_bytes=limit)
    except TypeError as exc:
        if "max_bytes" not in str(exc):
            raise
        return await sftp_read_file(connection, path)


def _syntax_from_filename(path: str) -> str:
    suffix = Path(path).suffix.lower()
    mapping = {
        ".py": "python",
        ".js": "javascript",
        ".ts": "typescript",
        ".json": "json",
        ".yaml": "yaml",
        ".yml": "yaml",
        ".md": "markdown",
        ".sh": "bash",
        ".bash": "bash",
        ".html": "markup",
        ".css": "css",
        ".toml": "toml",
        ".ini": "ini",
    }
    return mapping.get(suffix, "").strip()


def _is_markdown_file(path: str) -> bool:
    """Check if a file is a markdown file based on its extension."""
    suffix = Path(path).suffix.lower()
    return suffix in [".md", ".markdown"]
