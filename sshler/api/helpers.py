from __future__ import annotations

import asyncio
import logging
import os
import platform
import re
import shutil
import subprocess
from pathlib import Path, PureWindowsPath
from urllib.parse import quote

logger = logging.getLogger(__name__)

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


def _wsl_distros() -> list[str]:
    """Return installed WSL distro names, or [] if WSL/no distro is present.

    ``wsl.exe -l -q`` emits UTF-16-LE with one distro per line. A bare WSL stub
    with no distro installed prints an install notice on stderr and returns a
    non-zero code, so we treat any failure as "no distros".
    """
    if not LOCAL_IS_WINDOWS or shutil.which("wsl") is None:
        return []
    try:
        result = subprocess.run(
            ["wsl.exe", "-l", "-q"],
            capture_output=True,
            timeout=3,
            check=False,
        )
    except Exception as exc:  # pragma: no cover - environment specific
        logger.debug("WSL distro probe failed: %s", exc)
        return []
    if result.returncode != 0:
        return []
    text = result.stdout.decode("utf-16-le", errors="ignore")
    return [line.strip() for line in text.splitlines() if line.strip()]


# Ordered shell definitions: id -> (label, which-target). cmd resolves via ComSpec.
_WINDOWS_SHELL_DEFS: tuple[tuple[str, str, str | None], ...] = (
    ("pwsh", "PowerShell 7", "pwsh"),
    ("powershell", "Windows PowerShell", "powershell"),
    ("cmd", "Command Prompt", "cmd"),
)


def available_windows_shells() -> list[dict[str, object]]:
    """Enumerate selectable shells for a local terminal on Windows.

    Each entry: ``{"id", "label", "argv": [...], "available": bool}``. Native
    shells (pwsh/powershell/cmd) are listed only when their executable resolves.
    WSL is always listed when ``wsl.exe`` exists so the UI can offer it; its
    ``available`` flag is ``False`` when no distro is installed, letting the
    frontend surface a toast instead of hanging.
    """
    shells: list[dict[str, object]] = []
    for shell_id, label, which_target in _WINDOWS_SHELL_DEFS:
        if shell_id == "cmd":
            exe = os.environ.get("ComSpec") or shutil.which("cmd")
        else:
            exe = shutil.which(which_target) if which_target else None
        if exe:
            argv = [exe]
            if shell_id in ("pwsh", "powershell"):
                argv.append("-NoLogo")
            shells.append({"id": shell_id, "label": label, "argv": argv, "available": True})

    wsl_exe = shutil.which("wsl")
    if wsl_exe:
        distros = _wsl_distros()
        label = f"WSL ({distros[0]})" if distros else "WSL"
        shells.append(
            {
                "id": "wsl",
                "label": label,
                "argv": [wsl_exe],
                "available": bool(distros),
            }
        )
    return shells


def default_windows_shell() -> str | None:
    """Preferred default shell id: first available of pwsh, powershell, cmd."""
    available = {str(s["id"]) for s in available_windows_shells() if s.get("available")}
    for shell_id in ("pwsh", "powershell", "cmd"):
        if shell_id in available:
            return shell_id
    return None


def local_path_form(path: str | os.PathLike[str]) -> str:
    """*path* in the one form the local box answers a path in: POSIX form on a Windows
    server (``C:/x``, whichever separators *path* holds), as the system spells it
    elsewhere (where a backslash is a character of a name)."""
    if LOCAL_IS_WINDOWS:
        return PureWindowsPath(path).as_posix()
    return os.fspath(path)


_BARE_DRIVE = re.compile(r"[A-Za-z]:")


def _bare_drive_as_root(directory: str) -> str:
    """On a Windows server a bare drive (``C:``) is the drive's root (``C:/``).

    Windows reads ``Path("C:")`` as the process's current folder on that drive, a
    place no page ever means; a page that asks for ``C:`` wants the drive."""
    if LOCAL_IS_WINDOWS and _BARE_DRIVE.fullmatch(directory):
        return f"{directory}/"
    return directory


def _normalize_local_path(directory: str | None, allowed_base: str | Path | None = None) -> str:
    """Normalize and validate local path, resolving symlinks to prevent directory escape.

    Args:
        directory: Path to normalize (defaults to user home)
        allowed_base: Base directory to restrict access to. If provided, validates that
                     the resolved path (after following symlinks) stays within this base.
                     If None, no restriction is applied (full filesystem access).

    Returns:
        Normalized absolute path string, in ``local_path_form``

    Raises:
        ValueError: If resolved path escapes the allowed base directory (when allowed_base is set)

    Security Notes:
        - This function resolves symlinks using Path.resolve(), preventing attacks where
          a symlink inside an allowed directory points to files outside it.
        - For maximum security, always pass an allowed_base parameter.
        - For "local" boxes, consider restricting to Path.home() or box.default_dir.
    """
    if directory:
        base = Path(_bare_drive_as_root(directory)).expanduser()
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
                    f"Path escape detected: {resolved} is outside allowed directory "
                    f"{allowed_base_resolved}"
                )
        except AttributeError:
            # Python 3.8 fallback
            try:
                resolved.relative_to(allowed_base_resolved)
            except ValueError as exc:
                raise ValueError(
                    f"Path escape detected: {resolved} is outside allowed directory "
                    f"{allowed_base_resolved}"
                ) from exc

    return local_path_form(resolved)


async def _local_is_directory(path: str) -> bool:
    return await asyncio.to_thread(lambda: Path(path).is_dir())


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


def _attachment_header(filename: str) -> str:
    """``Content-Disposition`` for a download, safe for any file name.

    ``filename*`` carries the exact name percent-encoded as UTF-8 (RFC 5987/6266);
    ``filename`` is an ASCII fallback with ``"``, ``\\``, ``%`` and non-ASCII or
    control characters replaced, so a Japanese or quoted name cannot break the header.
    """
    fallback = "".join(
        ch if 0x20 <= ord(ch) < 0x7F and ch not in '"\\%' else "_" for ch in filename
    )
    return f"attachment; filename=\"{fallback}\"; filename*=UTF-8''{quote(filename, safe='')}"
