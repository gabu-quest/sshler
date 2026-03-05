from __future__ import annotations

import asyncio
import json
import logging
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

logger = logging.getLogger(__name__)

STATE_FILENAME = "state.sqlite"

_DB_LOCK = threading.RLock()
_DB: SQLerDB | None = None
_DB_PATH: Path | None = None
_INITIALISED = False


def _ensure_composite_indexes(db: SQLerDB) -> None:
    """Create composite indexes for performance optimization.

    Composite indexes improve query performance for multi-column WHERE clauses.
    SQLite can use a composite index for queries filtering on the leftmost columns.

    Performance improvements:
    - Session lookups by (box, session_name): O(log n) instead of O(n)
    - Session cleanup by (active, last_accessed_at): O(log n) instead of O(n)
    - Favorite lookups by (box, path): O(log n) instead of O(n)

    Index strategy:
    - Put most selective column first for best performance
    - box is typically selective (10-100 boxes)
    - session_name is unique per box
    - active is binary (low selectivity) but combined with time is useful
    """

    adapter = db.adapter

    # Composite index for session lookups by box and name
    # Used by: get_session_by_name(), create_or_update_session()
    # Query: WHERE box = ? AND session_name = ?
    # Note: sqler uses JSON storage, so we must use json_extract()
    adapter.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_sessions_box_name
        ON sessions(json_extract(data, '$.box'), json_extract(data, '$.session_name'))
        """
    )

    # Composite index for session cleanup queries
    # Used by: cleanup_old_sessions()
    # Query: WHERE active = 0 AND last_accessed_at < ?
    adapter.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_sessions_active_accessed
        ON sessions(json_extract(data, '$.active'), json_extract(data, '$.last_accessed_at'))
        """
    )

    # Composite index for favorite lookups
    # Used by: toggle_favorite()
    # Query: WHERE box = ? AND path = ?
    adapter.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_favorites_box_path
        ON favorites(json_extract(data, '$.box'), json_extract(data, '$.path'))
        """
    )

    # Composite index for directory visit lookups
    # Used by: record_directory_visit(), search_directories()
    # Query: WHERE box = ? AND path = ?
    adapter.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_directory_visits_box_path
        ON directory_visits(json_extract(data, '$.box'), json_extract(data, '$.path'))
        """
    )


class Favorite(SQLerModel):
    """Persisted favourite directories per box."""

    __tablename__ = "favorites"

    box: str
    path: str
    position: int = 0
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)


class DirectoryVisit(SQLerModel):
    """Track directory visits for frecency-based search."""

    __tablename__ = "directory_visits"

    box: str
    path: str
    visit_count: int = 1
    last_visited: float = Field(default_factory=time.time)
    first_visited: float = Field(default_factory=time.time)


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
            result = json.loads(self.metadata_json)
            return result if isinstance(result, dict) else {}
        except (json.JSONDecodeError, TypeError):
            return {}

    @metadata.setter
    def metadata(self, value: dict) -> None:
        """Set metadata as JSON."""
        self.metadata_json = json.dumps(value)


class Snippet(SQLerModel):
    """Saved command snippets per box or global."""

    __tablename__ = "snippets"

    id: str = Field(default_factory=lambda: secrets.token_urlsafe(16))
    box: str  # box name or "__global__" for all boxes
    label: str
    command: str
    category: str = ""
    sort_order: int = 0
    created_at: float = Field(default_factory=time.time)


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

        # Initialize DirectoryVisit model
        DirectoryVisit.set_db(db)
        DirectoryVisit.ensure_index("box")
        DirectoryVisit.ensure_index("path")
        DirectoryVisit.ensure_index("last_visited")

        # Initialize Snippet model
        Snippet.set_db(db)
        Snippet.ensure_index("box")
        Snippet.ensure_index("sort_order")

        # Create composite indexes for performance optimization
        # Note: sqler only supports single-column indexes via ensure_index(),
        # so we create composite indexes using raw SQL for optimal query performance.
        _ensure_composite_indexes(db)

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
            except Exception as exc:  # pragma: no cover - best effort cleanup
                # Log but don't fail - this is cleanup during reset
                logger.debug(f"Error closing database during reset: {exc}")
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


def rename_session(session_id: str, new_name: str) -> bool:
    """Rename a session in the database."""
    _require_db()
    with _DB_LOCK:
        session = Session.query().filter(F("id") == session_id).first()
        if not session:
            return False
        session.session_name = new_name
        session.last_accessed_at = time.time()
        session.save()
        return True


async def rename_session_async(session_id: str, new_name: str) -> bool:
    return await asyncio.to_thread(rename_session, session_id, new_name)


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


# Directory Visit Tracking (Frecency)

import math


def _calculate_frecency_score(visit_count: int, last_visited: float) -> float:
    """Calculate frecency score using exponential decay.

    Formula: score = visit_count * exp(-0.1 * days_since_last_visit)

    This weighs both frequency (visit_count) and recency (exponential decay).
    A directory visited 10 times yesterday scores higher than one visited
    100 times 30 days ago.
    """
    days_since = (time.time() - last_visited) / (24 * 60 * 60)
    return visit_count * math.exp(-0.1 * days_since)


def record_directory_visit(box_name: str, path: str) -> DirectoryVisit:
    """Record or update a directory visit for frecency tracking."""
    _require_db()
    now = time.time()

    with _DB_LOCK:
        existing = (
            DirectoryVisit.query()
            .filter((F("box") == box_name) & (F("path") == path))
            .first()
        )

        if existing:
            existing.visit_count += 1
            existing.last_visited = now
            existing.save()
            return existing

        visit = DirectoryVisit(
            box=box_name,
            path=path,
            visit_count=1,
            last_visited=now,
            first_visited=now,
        )
        visit.save()
        return visit


async def record_directory_visit_async(box_name: str, path: str) -> DirectoryVisit:
    return await asyncio.to_thread(record_directory_visit, box_name, path)


def search_directories(
    box_name: str,
    query: str,
    limit: int = 20,
) -> list[tuple[str, float]]:
    """Search directories by substring match, ranked by frecency.

    Returns list of (path, score) tuples sorted by score descending.
    """
    _require_db()
    query_lower = query.lower()

    with _DB_LOCK:
        all_visits = DirectoryVisit.query().filter(F("box") == box_name).all()

        results: list[tuple[str, float]] = []
        for visit in all_visits:
            if query_lower in visit.path.lower():
                score = _calculate_frecency_score(visit.visit_count, visit.last_visited)
                results.append((visit.path, score))

        results.sort(key=lambda x: x[1], reverse=True)
        return results[:limit]


async def search_directories_async(
    box_name: str,
    query: str,
    limit: int = 20,
) -> list[tuple[str, float]]:
    return await asyncio.to_thread(search_directories, box_name, query, limit)


# Snippet CRUD Functions


def list_snippets(box_name: str) -> list[Snippet]:
    """Return snippets for a box plus global snippets, ordered by sort_order."""
    _require_db()
    with _DB_LOCK:
        rows = (
            Snippet.query()
            .filter((F("box") == box_name) | (F("box") == "__global__"))
            .order_by("sort_order")
            .all()
        )
        return list(rows)


async def list_snippets_async(box_name: str) -> list[Snippet]:
    return await asyncio.to_thread(list_snippets, box_name)


def create_snippet(
    box: str,
    label: str,
    command: str,
    category: str = "",
) -> Snippet:
    """Create a new snippet."""
    _require_db()
    with _DB_LOCK:
        # Determine next sort_order
        existing = (
            Snippet.query()
            .filter(F("box") == box)
            .order_by("sort_order", desc=True)
            .first()
        )
        next_order = (existing.sort_order + 1) if existing else 0

        snippet = Snippet(
            box=box,
            label=label,
            command=command,
            category=category,
            sort_order=next_order,
        )
        snippet.save()
        return snippet


async def create_snippet_async(
    box: str, label: str, command: str, category: str = ""
) -> Snippet:
    return await asyncio.to_thread(create_snippet, box, label, command, category)


def get_snippet_by_id(snippet_id: str) -> Snippet | None:
    """Get a snippet by ID."""
    _require_db()
    with _DB_LOCK:
        return Snippet.query().filter(F("id") == snippet_id).first()


def update_snippet(
    snippet_id: str,
    label: str | None = None,
    command: str | None = None,
    category: str | None = None,
    sort_order: int | None = None,
) -> Snippet | None:
    """Update a snippet. Returns updated snippet or None if not found."""
    _require_db()
    with _DB_LOCK:
        snippet = Snippet.query().filter(F("id") == snippet_id).first()
        if not snippet:
            return None
        if label is not None:
            snippet.label = label
        if command is not None:
            snippet.command = command
        if category is not None:
            snippet.category = category
        if sort_order is not None:
            snippet.sort_order = sort_order
        snippet.save()
        return snippet


async def update_snippet_async(
    snippet_id: str,
    label: str | None = None,
    command: str | None = None,
    category: str | None = None,
    sort_order: int | None = None,
) -> Snippet | None:
    return await asyncio.to_thread(update_snippet, snippet_id, label, command, category, sort_order)


def delete_snippet(snippet_id: str) -> bool:
    """Delete a snippet. Returns True if deleted."""
    _require_db()
    with _DB_LOCK:
        snippet = Snippet.query().filter(F("id") == snippet_id).first()
        if not snippet:
            return False
        snippet.delete()
        return True


async def delete_snippet_async(snippet_id: str) -> bool:
    return await asyncio.to_thread(delete_snippet, snippet_id)
