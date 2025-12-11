from __future__ import annotations

import asyncio
import json
import secrets
import threading
import time
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import Field
from sqler import SQLerDB, SQLerModel
from sqler.adapter import SQLiteAdapter
from sqler.query import SQLerField as F

STATE_FILENAME = "state.sqlite"

_DB_LOCK = threading.RLock()
_DB: SQLerDB | None = None
_DB_PATH: Path | None = None
_INITIALISED = False


class Favorite(SQLerModel):
    """Persisted favourite directories per box."""

    __tablename__ = "favorites"

    box: str
    path: str
    position: int = 0
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)


class Session(SQLerModel):
    """Persisted tmux session metadata per box."""

    __tablename__ = "sessions"

    id: str = Field(default_factory=lambda: secrets.token_urlsafe(16))
    box: str
    session_name: str
    working_directory: str
    created_at: float = Field(default_factory=time.time)
    last_accessed_at: float = Field(default_factory=time.time)
    active: bool = True
    window_count: int = 1
    metadata_json: str = Field(default="{}")  # JSON string for terminal size, colors, etc.

    @property
    def metadata(self) -> dict:
        """Parse metadata JSON."""
        try:
            return json.loads(self.metadata_json)
        except (json.JSONDecodeError, TypeError):
            return {}

    @metadata.setter
    def metadata(self, value: dict) -> None:
        """Set metadata as JSON."""
        self.metadata_json = json.dumps(value)


if TYPE_CHECKING:  # pragma: no cover - import for typing only
    from .config import StoredBox


def initialize(config_dir: Path) -> None:
    """Initialise the SQLite-backed state store using ``sqler``.

    The state database lives alongside ``boxes.yaml`` and holds favourites and
    session tracking records.
    """

    global _DB, _DB_PATH, _INITIALISED

    config_dir = config_dir.expanduser()
    config_dir.mkdir(parents=True, exist_ok=True)
    target_path = config_dir / STATE_FILENAME

    with _DB_LOCK:
        if _INITIALISED and _DB_PATH == target_path:
            return

        if _DB is not None and _DB_PATH != target_path:
            _DB.close()

        adapter = SQLiteAdapter(path=str(target_path))
        db = SQLerDB(adapter)

        # Initialize Favorite model
        Favorite.set_db(db)
        Favorite.ensure_index("box")
        Favorite.ensure_index("position")

        # Initialize Session model
        Session.set_db(db)
        Session.ensure_index("box")
        Session.ensure_index("last_accessed_at")
        Session.ensure_index("active")
        Session.ensure_index("session_name")

        _DB = db
        _DB_PATH = target_path
        _INITIALISED = True


def reset_state() -> None:
    """Reset the in-memory cache (used by tests)."""

    global _DB, _DB_PATH, _INITIALISED
    with _DB_LOCK:
        if _DB is not None:
            try:
                _DB.close()
            except Exception:  # pragma: no cover - best effort cleanup
                pass
        _DB = None
        _DB_PATH = None
        _INITIALISED = False


def _require_db() -> SQLerDB:
    if not _INITIALISED or _DB is None:
        raise RuntimeError("State store not initialised")
    return _DB


def migrate_legacy_favorites(stored: dict[str, StoredBox]) -> bool:
    """Move favourites persisted in YAML into the sqler-backed store."""

    if not stored:
        return False

    _require_db()
    migrated = False
    with _DB_LOCK:
        for item in stored.values():
            if not item.favorites:
                continue
            replace_favorites(item.name, item.favorites)
            item.favorites.clear()
            migrated = True
    return migrated


def list_favorites(box_name: str) -> list[str]:
    """Return the ordered favourites for ``box_name``."""

    _require_db()
    with _DB_LOCK:
        rows = (
            Favorite.query()
            .filter(F("box") == box_name)
            .order_by("position")
            .all()
        )
        return [row.path for row in rows]


async def list_favorites_async(box_name: str) -> list[str]:
    return await asyncio.to_thread(list_favorites, box_name)


def favorites_map(box_names: Sequence[str] | None = None) -> dict[str, list[str]]:
    """Return favourites for the supplied ``box_names``."""

    _require_db()
    with _DB_LOCK:
        if box_names is not None:
            return {name: list_favorites(name) for name in box_names}

        rows = Favorite.query().order_by("box").order_by("position").all()
        mapping: dict[str, list[str]] = {}
        for row in rows:
            mapping.setdefault(row.box, []).append(row.path)
        return mapping


def toggle_favorite(box_name: str, path: str) -> bool:
    """Add or remove ``path`` from favourites. Returns ``True`` if added."""

    if not path:
        return False

    _require_db()
    now = time.time()
    with _DB_LOCK:
        query = Favorite.query().filter((F("box") == box_name) & (F("path") == path))
        existing = query.first()
        if existing:
            existing.delete()
            return False

        position = _next_position(box_name)
        Favorite(box=box_name, path=path, position=position, created_at=now, updated_at=now).save()
        return True


async def toggle_favorite_async(box_name: str, path: str) -> bool:
    return await asyncio.to_thread(toggle_favorite, box_name, path)


def replace_favorites(box_name: str, paths: Iterable[str]) -> None:
    """Replace all favourites for ``box_name`` with ``paths`` preserving order."""

    _require_db()
    deduped: list[str] = []
    seen: set[str] = set()
    for raw in paths:
        cleaned = raw.strip()
        if not cleaned or cleaned in seen:
            continue
        deduped.append(cleaned)
        seen.add(cleaned)

    now = time.time()
    with _DB_LOCK:
        existing = {
            fav.path: fav
            for fav in Favorite.query().filter(F("box") == box_name).all()
        }

        for position, path in enumerate(deduped):
            favourite = existing.pop(path, None)
            if favourite is None:
                Favorite(
                    box=box_name,
                    path=path,
                    position=position,
                    created_at=now,
                    updated_at=now,
                ).save()
                continue

            if favourite.position != position:
                favourite.position = position
                favourite.updated_at = now
                favourite.save()

        for leftover in existing.values():
            leftover.delete()


async def replace_favorites_async(box_name: str, paths: Iterable[str]) -> None:
    await asyncio.to_thread(replace_favorites, box_name, list(paths))


def remove_box(box_name: str) -> None:
    """Delete all persisted state for ``box_name``."""

    _require_db()
    with _DB_LOCK:
        rows = Favorite.query().filter(F("box") == box_name).all()
        for row in rows:
            row.delete()


def _next_position(box_name: str) -> int:
    existing = (
        Favorite.query()
        .filter(F("box") == box_name)
        .order_by("position", desc=True)
        .first()
    )
    if not existing:
        return 0
    return existing.position + 1


# Session Management Functions


def create_or_update_session(
    box_name: str,
    session_name: str,
    working_directory: str,
    metadata: dict | None = None,
) -> Session:
    """Create or update a session record."""
    _require_db()
    now = time.time()

    with _DB_LOCK:
        # Check if session already exists
        existing = (
            Session.query()
            .filter((F("box") == box_name) & (F("session_name") == session_name))
            .first()
        )

        if existing:
            # Update existing session
            existing.last_accessed_at = now
            existing.active = True
            existing.working_directory = working_directory
            if metadata:
                existing.metadata = metadata
            existing.save()
            return existing

        # Create new session
        session = Session(
            box=box_name,
            session_name=session_name,
            working_directory=working_directory,
            created_at=now,
            last_accessed_at=now,
            active=True,
            window_count=1,
        )
        if metadata:
            session.metadata = metadata
        session.save()
        return session


async def create_or_update_session_async(
    box_name: str,
    session_name: str,
    working_directory: str,
    metadata: dict | None = None,
) -> Session:
    return await asyncio.to_thread(
        create_or_update_session, box_name, session_name, working_directory, metadata
    )


def list_sessions(
    box_name: str,
    active_only: bool = False,
    limit: int = 50,
) -> list[Session]:
    """List sessions for a box, ordered by last accessed (most recent first)."""
    _require_db()

    with _DB_LOCK:
        query = Session.query().filter(F("box") == box_name)

        if active_only:
            query = query.filter(F("active") == True)  # noqa: E712

        rows = query.order_by("last_accessed_at", desc=True).limit(limit).all()
        return list(rows)


async def list_sessions_async(
    box_name: str,
    active_only: bool = False,
    limit: int = 50,
) -> list[Session]:
    return await asyncio.to_thread(list_sessions, box_name, active_only, limit)


def get_session_by_id(session_id: str) -> Session | None:
    """Get a session by its ID."""
    _require_db()

    with _DB_LOCK:
        return Session.query().filter(F("id") == session_id).first()


async def get_session_by_id_async(session_id: str) -> Session | None:
    return await asyncio.to_thread(get_session_by_id, session_id)


def get_session_by_name(box_name: str, session_name: str) -> Session | None:
    """Get a session by box and session name."""
    _require_db()

    with _DB_LOCK:
        return (
            Session.query()
            .filter((F("box") == box_name) & (F("session_name") == session_name))
            .first()
        )


async def get_session_by_name_async(box_name: str, session_name: str) -> Session | None:
    return await asyncio.to_thread(get_session_by_name, box_name, session_name)


def update_session_activity(session_id: str, active: bool = True, window_count: int | None = None) -> bool:
    """Update session activity status and optionally window count."""
    _require_db()

    with _DB_LOCK:
        session = Session.query().filter(F("id") == session_id).first()
        if not session:
            return False

        session.active = active
        session.last_accessed_at = time.time()
        if window_count is not None:
            session.window_count = window_count
        session.save()
        return True


async def update_session_activity_async(
    session_id: str, active: bool = True, window_count: int | None = None
) -> bool:
    return await asyncio.to_thread(update_session_activity, session_id, active, window_count)


def delete_session(session_id: str) -> bool:
    """Delete a session record."""
    _require_db()

    with _DB_LOCK:
        session = Session.query().filter(F("id") == session_id).first()
        if not session:
            return False

        session.delete()
        return True


def create_session(
    box_name: str,
    session_name: str,
    working_directory: str,
    metadata: dict | None = None,
) -> Session:
    """Create and persist a session record."""

    _require_db()
    with _DB_LOCK:
        record = Session(
            box=box_name,
            session_name=session_name,
            working_directory=working_directory,
            metadata_json=json.dumps(metadata or {}),
        )
        record.save()
        return record


async def create_session_async(
    box_name: str,
    session_name: str,
    working_directory: str,
    metadata: dict | None = None,
) -> Session:
    return await asyncio.to_thread(create_session, box_name, session_name, working_directory, metadata)


async def delete_session_async(session_id: str) -> bool:
    return await asyncio.to_thread(delete_session, session_id)


def update_session_metadata(
    session_id: str, metadata: dict | None = None, window_count: int | None = None
) -> bool:
    """Update metadata and optionally window count."""

    _require_db()
    with _DB_LOCK:
        session = Session.query().filter(F("id") == session_id).first()
        if not session:
            return False
        if metadata is not None:
            session.metadata = metadata
        if window_count is not None:
            session.window_count = window_count
        session.last_accessed_at = time.time()
        session.save()
        return True


async def update_session_metadata_async(
    session_id: str, metadata: dict | None = None, window_count: int | None = None
) -> bool:
    return await asyncio.to_thread(update_session_metadata, session_id, metadata, window_count)


async def delete_session_async(session_id: str) -> bool:
    return await asyncio.to_thread(delete_session, session_id)


def cleanup_old_sessions(max_age_days: int = 30) -> int:
    """Delete sessions older than max_age_days that are inactive."""
    _require_db()

    cutoff = time.time() - (max_age_days * 24 * 60 * 60)
    with _DB_LOCK:
        sessions = (
            Session.query()
            .filter((F("active") == False) & (F("last_accessed_at") < cutoff))  # noqa: E712
            .all()
        )

        count = 0
        for session in sessions:
            session.delete()
            count += 1

        return count


async def cleanup_old_sessions_async(max_age_days: int = 30) -> int:
    return await asyncio.to_thread(cleanup_old_sessions, max_age_days)
