"""Remote paths and remote command lines, each with one owner.

Paths: a remote route takes every path it hands to SFTP or to a command from here.
``~`` and ``~/x`` mean the box's home folder; the home is asked for once per
connection and remembered for that connection's life (a pooled connection is
reused, a new connection asks again, a failed lookup is not remembered).

Commands: every dynamic word of a command line that runs in the remote shell is
quoted by :func:`command` (``shlex.join``). The compound shapes the routes need
(``cd X && ...``, ``... | head``, ``2>/dev/null``) are composed here from built
parts, so no route writes a path into a command line by hand. A path given to a
tool as an argument goes through :func:`path_word`, so no tool reads it as an option.
"""

from __future__ import annotations

import asyncio
import posixpath
import shlex
import weakref
from pathlib import PurePosixPath

from fastapi import HTTPException

from ..validation import PathValidator, ValidationError

HOME_COMMAND = 'printf %s "$HOME"'
HOME_LOOKUP_TIMEOUT = 10

# The home folder of each live connection. Weak keys: an entry goes with its
# connection, so a new connection (even one at a reused address) asks again.
_homes: weakref.WeakKeyDictionary[object, str] = weakref.WeakKeyDictionary()


def _means_home(path: str) -> bool:
    return path == "~" or path.startswith("~/")


async def remote_home(connection) -> str:
    """The home folder of the box behind ``connection``, an absolute path.

    Asked for once per connection. A lookup that fails or answers anything but an
    absolute path is a 502 and is not remembered; a timeout raises ``TimeoutError``.
    """
    try:
        return _homes[connection]
    except (KeyError, TypeError):
        pass
    result = await asyncio.wait_for(connection.run(HOME_COMMAND), timeout=HOME_LOOKUP_TIMEOUT)
    home = (result.stdout or "").strip()
    if result.exit_status != 0 or not home.startswith("/"):
        raise HTTPException(status_code=502, detail="Could not resolve the remote home directory")
    try:
        _homes[connection] = home
    except TypeError:
        pass  # a connection that cannot be weakly referenced is asked every time
    return home


async def resolve_tilde(connection, path: str) -> str:
    """``path`` with a leading ``~`` or ``~/`` replaced by the remote home, normalised.

    Any other path comes back as it is (``~other`` is a name, not a home)."""
    if not _means_home(path):
        return path
    home = await remote_home(connection)
    if path == "~":
        return home
    # Joined by hand: posixpath.join would drop the home when the rest starts with
    # "/" (as in `~//etc`), and a home of "/" must not give "//x".
    return posixpath.normpath(f"{home.rstrip('/')}/{path[2:].lstrip('/')}")


async def remote_path(connection, path: str) -> str:
    """``path`` with ``~`` resolved to the remote home, then validated by
    ``PathValidator`` (raises ``ValidationError``). SFTP does not expand ``~``."""
    return PathValidator.validate_remote_path(await resolve_tilde(connection, path))


async def remote_directory(connection, directory: str | None) -> str:
    """A remote folder (``/`` when none is given) with ``~`` resolved, normalised."""
    resolved = await resolve_tilde(connection, (directory or "/").strip())
    return normalize_remote_directory(resolved)


def normalize_remote_directory(directory: str | None) -> str:
    """An absolute, normalised remote folder. A path that still means the home
    (``~`` or ``~/x``) is refused with ``ValueError``: resolve it first with
    :func:`remote_directory`, or it would silently become a folder under ``/``."""
    raw = (directory or "/").strip()
    if _means_home(raw):
        raise ValueError(f"Remote folder {raw!r} has an unresolved '~'")
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


def compose_remote_child_path(directory: str, filename: str) -> str:
    """``directory/filename`` for a single name (no separators); raises ``ValueError``."""
    cleaned = (filename or "").strip()
    if not cleaned:
        raise ValueError("Filename is required")
    if cleaned in {".", ".."} or "/" in cleaned:
        raise ValueError("Filename cannot contain path separators")
    if "\x00" in cleaned:
        raise ValueError("Filename contains unsupported characters")
    parent = PurePosixPath(normalize_remote_directory(directory))
    return (parent / cleaned).as_posix()


def compose_remote_child(directory: str, filename: str) -> str:
    """``directory/filename``, validated; a bad result is a 400."""
    try:
        return PathValidator.validate_remote_path(compose_remote_child_path(directory, filename))
    except (ValidationError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


# --- command lines ---------------------------------------------------------------


def path_word(path: str) -> str:
    """``path`` as a tool argument that cannot be read as an option: a path that
    starts with ``-`` gets ``./`` in front (the same file, for every tool)."""
    return f"./{path}" if path.startswith("-") else path


def command(*words: str) -> str:
    """One simple command; every word is quoted for the remote shell."""
    return shlex.join(words)


def assign(name: str, value: str) -> str:
    """A shell variable assignment, ``name=value``, with ``value`` quoted."""
    if not name.isidentifier():
        raise ValueError(f"not a shell variable name: {name!r}")
    return f"{name}={shlex.quote(value)}"


def and_then(*commands: str) -> str:
    """Each command runs only when the one before it succeeded."""
    return " && ".join(commands)


def then(*commands: str) -> str:
    """Each command runs after the one before it, whatever its status."""
    return "; ".join(commands)


def pipe(*commands: str) -> str:
    return " | ".join(commands)


def quiet(cmd: str) -> str:
    """``cmd`` with its error output dropped."""
    return f"{cmd} 2>/dev/null"


def in_directory(directory: str, cmd: str) -> str:
    """``cmd`` run in ``directory`` (not at all when ``cd`` fails)."""
    return and_then(command("cd", path_word(directory)), cmd)
