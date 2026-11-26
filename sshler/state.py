from __future__ import annotations

import asyncio
import json
import logging
import math
import re
import secrets
import threading
import time
import unicodedata
from collections.abc import Callable, Coroutine, Iterable, Sequence
from functools import wraps
from pathlib import Path
from typing import TYPE_CHECKING, NamedTuple
from uuid import uuid4

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


def _in_thread[**P, R](function: Callable[P, R]) -> Callable[P, Coroutine[None, None, R]]:
    """Return the async form of ``function``: the same call, run in a worker thread.

    Positional and keyword arguments pass through unchanged, and the async function
    carries the sync function's signature and the name ``<name>_async``.
    """

    @wraps(function)
    async def run(*args: P.args, **kwargs: P.kwargs) -> R:
        return await asyncio.to_thread(function, *args, **kwargs)

    run.__name__ = f"{function.__name__}_async"
    run.__qualname__ = run.__name__
    return run


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


class ProgressBar(SQLerModel):
    """A named progress bar pushed by external scripts.

    `name` is the natural primary key — upserts target it. Global by design;
    any client holding the sshler token may push or read any bar.
    """

    __tablename__ = "progress_bars"

    name: str
    current: float = 0.0
    total: float = 1.0
    color: str | None = None
    label: str | None = None
    status: str = "running"
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)
    metadata_json: str = Field(default="{}")  # JSON bag pushed by external scripts
    metadata_error: str | None = None  # last-push metadata validation error, if any

    @property
    def metadata(self) -> dict:
        """Parse metadata JSON. Falls back to {} on corruption."""
        try:
            result = json.loads(self.metadata_json)
            return result if isinstance(result, dict) else {}
        except (json.JSONDecodeError, TypeError):
            return {}

    @metadata.setter
    def metadata(self, value: dict) -> None:
        """Set metadata as JSON."""
        self.metadata_json = json.dumps(value)


class DiffNotebook(SQLerModel):
    """A saved Diff Notebook (server-side, addressable by short id).

    The envelope is stored as an opaque JSON string. The frontend owns its shape;
    the server only validates that it parses as JSON and that ``v == 1`` and
    ``cells`` is a list. Notebooks are immutable — every "save" creates a new id.
    """

    __tablename__ = "diff_notebooks"

    id: str = Field(default_factory=lambda: secrets.token_urlsafe(8))
    label: str = ""
    envelope_json: str = "{}"
    cell_count: int = 0
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)


class ArtifactProject(SQLerModel):
    """Top-level project used to organize locally served artifacts."""

    __tablename__ = "artifact_projects"

    id: str = Field(default_factory=lambda: secrets.token_urlsafe(12))
    name: str
    normalized_name: str
    slug: str = ""
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)


class ArtifactRegistration(SQLerModel):
    """A local file or directory registered with the artifact catalog."""

    __tablename__ = "artifact_registrations"

    id: str = Field(default_factory=lambda: secrets.token_urlsafe(16))
    project_id: str
    group_path: str = ""
    source_path: str
    mode: str
    entrypoint: str | None = None
    title_override: str | None = None
    slug: str = ""
    mount_path: str = ""
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)


class Layout(SQLerModel):
    """Saved multi-terminal layout presets."""

    __tablename__ = "layouts"

    id: str = Field(default_factory=lambda: str(uuid4()))
    name: str
    terminals_json: str = "[]"
    created_at: float = Field(default_factory=time.time)

    @property
    def terminals(self) -> list[dict]:
        """Parse layout terminal configuration JSON."""
        try:
            result = json.loads(self.terminals_json)
            return result if isinstance(result, list) else []
        except (json.JSONDecodeError, TypeError):
            return []

    @terminals.setter
    def terminals(self, value: list[dict]) -> None:
        """Persist terminal configuration as JSON."""
        self.terminals_json = json.dumps(value)


class _Table(NamedTuple):
    """A model, its single-field indexes and its composite indexes (raw SQL)."""

    model: type[SQLerModel]
    fields: tuple[str, ...]
    composite: tuple[str, ...] = ()


# Every model the store keeps. ``initialize()`` creates each table, index and composite
# index from this list, so a new model is one class and one row here.
#
# sqler stores a row as a JSON document, so a composite index is over ``json_extract``
# expressions. The statements are raw SQL, not ``create_index``, because sqlite keeps the
# statement text and a database made today must hold the same text as one made before.
_TABLES: tuple[_Table, ...] = (
    _Table(
        Favorite,
        ("box", "position"),
        (
            # Favorite lookups in toggle_favorite(): WHERE box = ? AND path = ?
            "CREATE INDEX IF NOT EXISTS idx_favorites_box_path\n"
            "        ON favorites(json_extract(data, '$.box'), json_extract(data, '$.path'))\n"
            "        ",
        ),
    ),
    _Table(
        Session,
        ("box", "last_accessed_at", "active", "session_name"),
        (
            # Session lookups: WHERE box = ? AND session_name = ?
            "CREATE INDEX IF NOT EXISTS idx_sessions_box_name\n"
            "        ON sessions(json_extract(data, '$.box'), "
            "json_extract(data, '$.session_name'))\n"
            "        ",
            # Session cleanup: WHERE active = 0 AND last_accessed_at < ?
            "CREATE INDEX IF NOT EXISTS idx_sessions_active_accessed\n"
            "        ON sessions(json_extract(data, '$.active'), "
            "json_extract(data, '$.last_accessed_at'))\n"
            "        ",
        ),
    ),
    _Table(
        DirectoryVisit,
        ("box", "path", "last_visited"),
        (
            # Directory visit lookups: WHERE box = ? AND path = ?
            "CREATE INDEX IF NOT EXISTS idx_directory_visits_box_path\n"
            "        ON directory_visits(json_extract(data, '$.box'), "
            "json_extract(data, '$.path'))\n"
            "        ",
        ),
    ),
    _Table(Snippet, ("box", "sort_order")),
    _Table(Layout, ("name", "created_at")),
    _Table(ProgressBar, ("name", "updated_at")),
    _Table(DiffNotebook, ("id", "created_at")),
    _Table(ArtifactProject, ("id", "normalized_name", "slug")),
    _Table(
        ArtifactRegistration,
        ("id", "project_id", "source_path", "slug", "mount_path"),
    ),
)


def artifact_slug(value: str, fallback: str = "artifact") -> str:
    """Convert a human label into a URL-safe artifact alias."""
    ascii_value = (
        unicodedata.normalize("NFKD", value)
        .encode("ascii", errors="ignore")
        .decode("ascii")
        .lower()
    )
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_value).strip("-")
    return (slug or fallback)[:80].rstrip("-")


def _unique_artifact_slug(base: str, used: set[str], stable_id: str) -> str:
    if base not in used:
        return base
    suffix = artifact_slug(stable_id, "item")[:8]
    candidate = f"{base[: max(1, 79 - len(suffix))]}-{suffix}"
    counter = 2
    while candidate in used:
        tail = f"{suffix}-{counter}"
        candidate = f"{base[: max(1, 79 - len(tail))]}-{tail}"
        counter += 1
    return candidate


def _ensure_artifact_aliases(db: SQLerDB) -> None:
    """Backfill stable slugs for rows created before alias routing existed."""
    used_projects: set[str] = set()
    projects = list(ArtifactProject.using(db).order_by("created_at").all())
    for project in projects:
        base = artifact_slug(project.slug or project.name, "project")
        chosen = _unique_artifact_slug(base, used_projects, project.id)
        used_projects.add(chosen)
        if project.slug != chosen:
            project.slug = chosen
            project.save()

    used_by_project: dict[str, set[str]] = {}
    registrations = list(ArtifactRegistration.using(db).order_by("created_at").all())
    for registration in registrations:
        used = used_by_project.setdefault(registration.project_id, set())
        source_name = Path(registration.source_path).stem or Path(registration.source_path).name
        base = artifact_slug(
            registration.slug or registration.title_override or source_name,
            "artifact",
        )
        chosen = _unique_artifact_slug(base, used, registration.id)
        used.add(chosen)
        if registration.slug != chosen:
            registration.slug = chosen
            registration.save()


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

        for table in _TABLES:
            for field in table.fields:
                table.model.ensure_index(field, db=db)
        _ensure_artifact_aliases(db)

        for table in _TABLES:
            for statement in table.composite:
                db.adapter.execute(statement)

        _DB = db
        _DB_PATH = target_path
        _INITIALISED = True

    seed_default_snippets()


def seed_default_snippets() -> None:
    """Seed starter snippets for new installations.

    Only inserts when zero __global__ snippets exist, so users who
    deleted the defaults won't see them reappear on restart.
    """
    db = _require_db()
    with _DB_LOCK:
        existing = Snippet.using(db).filter(F("box") == "__global__").first()
        if existing:
            return

        defaults = [
            ("System Info", "uname -a", "System"),
            ("Who Am I", "whoami && hostname", "System"),
            ("Disk Usage", "df -h", "System"),
            ("Top Processes", "ps aux --sort=-%mem | head -20", "System"),
        ]
        for order, (label, command, category) in enumerate(defaults):
            Snippet(
                box="__global__",
                label=label,
                command=command,
                category=category,
                sort_order=order,
            ).save(db=db)

    logger.info("Seeded %d default global snippets", len(defaults))


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

    db = _require_db()
    with _DB_LOCK:
        rows = Favorite.using(db).filter(F("box") == box_name).order_by("position").all()
        return [row.path for row in rows]


list_favorites_async = _in_thread(list_favorites)


def favorites_map(box_names: Sequence[str] | None = None) -> dict[str, list[str]]:
    """Return favourites for the supplied ``box_names``."""

    db = _require_db()
    with _DB_LOCK:
        if box_names is not None:
            return {name: list_favorites(name) for name in box_names}

        rows = Favorite.using(db).order_by("box").order_by("position").all()
        mapping: dict[str, list[str]] = {}
        for row in rows:
            mapping.setdefault(row.box, []).append(row.path)
        return mapping


def toggle_favorite(box_name: str, path: str) -> bool:
    """Add or remove ``path`` from favourites. Returns ``True`` if added."""

    if not path:
        return False

    db = _require_db()
    now = time.time()
    with _DB_LOCK:
        query = Favorite.using(db).filter((F("box") == box_name) & (F("path") == path))
        existing = query.first()
        if existing:
            existing.delete()
            return False

        position = _next_position(box_name, db)
        Favorite(box=box_name, path=path, position=position, created_at=now, updated_at=now).save(
            db=db
        )
        return True


toggle_favorite_async = _in_thread(toggle_favorite)


def replace_favorites(box_name: str, paths: Iterable[str]) -> None:
    """Replace all favourites for ``box_name`` with ``paths`` preserving order."""

    db = _require_db()
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
        existing = {fav.path: fav for fav in Favorite.using(db).filter(F("box") == box_name).all()}

        for position, path in enumerate(deduped):
            favourite = existing.pop(path, None)
            if favourite is None:
                Favorite(
                    box=box_name,
                    path=path,
                    position=position,
                    created_at=now,
                    updated_at=now,
                ).save(db=db)
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

    db = _require_db()
    with _DB_LOCK:
        rows = Favorite.using(db).filter(F("box") == box_name).all()
        for row in rows:
            row.delete()


def _next_position(box_name: str, db: SQLerDB) -> int:
    existing = (
        Favorite.using(db).filter(F("box") == box_name).order_by("position", desc=True).first()
    )
    if not existing:
        return 0
    return existing.position + 1


# Session Management Functions

# The metadata key of the time the running server saw a session end (written through
# ``session_activity.match_tmux`` only, dropped whenever the row is active again). The
# start's lost list leaves such a session out (``snapshot.reconcile_on_startup``).
END_SEEN_AT = "end_seen_at"

# How long the last layout of an ended session is kept: after that its row is purged
# (``purge_stale_snapshots``), and with it the layout "Restore layout" recreates.
SNAPSHOT_KEEP_DAYS = 7


def _set_active(session: Session, active: bool, *, end_seen: bool = False) -> None:
    """The one write of a row's ``active`` flag. An active row holds no record that
    its end was seen; an inactive one gets the record with *end_seen*, and otherwise
    keeps what it had."""
    session.active = active
    meta = session.metadata
    if active:
        if meta.pop(END_SEEN_AT, None) is None:
            return
    elif end_seen:
        meta[END_SEEN_AT] = time.time()
    else:
        return
    session.metadata = meta


def create_or_update_session(
    box_name: str,
    session_name: str,
    working_directory: str,
    metadata: dict | None = None,
    keep_existing: bool = False,
) -> Session:
    """Create or update a session record (one per box and name). With
    ``keep_existing`` a record that already exists is answered as it is."""
    db = _require_db()
    now = time.time()

    with _DB_LOCK:
        # Check if session already exists
        existing = (
            Session.using(db)
            .filter((F("box") == box_name) & (F("session_name") == session_name))
            .first()
        )

        if existing and keep_existing:
            return existing
        if existing:
            # Update existing session
            existing.last_accessed_at = now
            _set_active(existing, True)
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
        session.save(db=db)
        return session


create_or_update_session_async = _in_thread(create_or_update_session)


def list_sessions(
    box_name: str,
    active_only: bool = False,
    limit: int | None = 50,
) -> list[Session]:
    """List sessions for a box, ordered by last accessed (most recent first): at most
    ``limit`` of them, every one with ``limit=None``."""
    db = _require_db()

    with _DB_LOCK:
        query = Session.using(db).filter(F("box") == box_name)

        if active_only:
            query = query.filter(F("active") == True)  # noqa: E712

        query = query.order_by("last_accessed_at", desc=True)
        if limit is not None:
            query = query.limit(limit)
        return list(query.all())


list_sessions_async = _in_thread(list_sessions)


def get_session_by_id(session_id: str) -> Session | None:
    """Get a session by its ID."""
    db = _require_db()

    with _DB_LOCK:
        return Session.using(db).filter(F("id") == session_id).first()


get_session_by_id_async = _in_thread(get_session_by_id)


def get_session_by_name(box_name: str, session_name: str) -> Session | None:
    """Get a session by box and session name."""
    db = _require_db()

    with _DB_LOCK:
        return (
            Session.using(db)
            .filter((F("box") == box_name) & (F("session_name") == session_name))
            .first()
        )


get_session_by_name_async = _in_thread(get_session_by_name)


def update_session_activity(
    session_id: str,
    active: bool = True,
    window_count: int | None = None,
    *,
    end_seen: bool = False,
) -> bool:
    """Update session activity status and optionally window count; with *end_seen*
    an inactive row records that the running server saw its session end (``END_SEEN_AT``,
    for ``session_activity.match_tmux`` only)."""
    db = _require_db()

    with _DB_LOCK:
        session = Session.using(db).filter(F("id") == session_id).first()
        if not session:
            return False

        _set_active(session, active, end_seen=end_seen)
        session.last_accessed_at = time.time()
        if window_count is not None:
            session.window_count = window_count
        session.save()
        return True


update_session_activity_async = _in_thread(update_session_activity)


def delete_session(session_id: str) -> bool:
    """Delete a session record."""
    db = _require_db()

    with _DB_LOCK:
        session = Session.using(db).filter(F("id") == session_id).first()
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

    db = _require_db()
    with _DB_LOCK:
        record = Session(
            box=box_name,
            session_name=session_name,
            working_directory=working_directory,
            metadata_json=json.dumps(metadata or {}),
        )
        record.save(db=db)
        return record


create_session_async = _in_thread(create_session)


delete_session_async = _in_thread(delete_session)


def update_session_metadata(
    session_id: str, metadata: dict | None = None, window_count: int | None = None
) -> bool:
    """Update metadata and optionally window count."""

    db = _require_db()
    with _DB_LOCK:
        session = Session.using(db).filter(F("id") == session_id).first()
        if not session:
            return False
        if metadata is not None:
            session.metadata = metadata
        if window_count is not None:
            session.window_count = window_count
        session.last_accessed_at = time.time()
        session.save()
        return True


update_session_metadata_async = _in_thread(update_session_metadata)


def rename_session(session_id: str, new_name: str) -> bool:
    """Rename a session in the database."""
    db = _require_db()
    with _DB_LOCK:
        session = Session.using(db).filter(F("id") == session_id).first()
        if not session:
            return False
        session.session_name = new_name
        session.last_accessed_at = time.time()
        session.save()
        return True


rename_session_async = _in_thread(rename_session)


def cleanup_old_sessions(max_age_days: int = 30) -> int:
    """Delete sessions older than max_age_days that are inactive."""
    db = _require_db()

    cutoff = time.time() - (max_age_days * 24 * 60 * 60)
    with _DB_LOCK:
        sessions = (
            Session.using(db)
            .filter((F("active") == False) & (F("last_accessed_at") < cutoff))  # noqa: E712
            .all()
        )

        count = 0
        for session in sessions:
            session.delete()
            count += 1

        return count


cleanup_old_sessions_async = _in_thread(cleanup_old_sessions)


def list_all_active_sessions() -> list[Session]:
    """List all active sessions across all boxes."""
    db = _require_db()
    with _DB_LOCK:
        return list(
            Session.using(db)
            .filter(F("active") == True)  # noqa: E712
            .all()
        )


list_all_active_sessions_async = _in_thread(list_all_active_sessions)


def list_all_sessions() -> list[Session]:
    """List every session row across all boxes, active or not."""
    db = _require_db()
    with _DB_LOCK:
        return list(Session.using(db).all())


list_all_sessions_async = _in_thread(list_all_sessions)


def update_session_snapshot(session_id: str, windows: list[dict]) -> bool:
    """Atomically update session metadata with window snapshot."""
    db = _require_db()
    with _DB_LOCK:
        session = Session.using(db).filter(F("id") == session_id).first()
        if not session:
            return False
        meta = session.metadata
        meta["windows"] = windows
        meta["last_snapshot_at"] = time.time()
        session.metadata = meta
        session.save()
        return True


update_session_snapshot_async = _in_thread(update_session_snapshot)


def clear_session_snapshot(session_id: str) -> bool:
    """Remove snapshot data from a session so it won't be re-detected as lost."""
    db = _require_db()
    with _DB_LOCK:
        session = Session.using(db).filter(F("id") == session_id).first()
        if not session:
            return False
        meta = session.metadata
        meta.pop("last_snapshot_at", None)
        meta.pop("windows", None)
        session.metadata = meta
        session.save()
        return True


clear_session_snapshot_async = _in_thread(clear_session_snapshot)


def purge_stale_snapshots(max_age_days: int = SNAPSHOT_KEEP_DAYS) -> int:
    """Delete inactive sessions with snapshots older than max_age_days.

    Returns the number of sessions purged.
    """
    db = _require_db()
    cutoff = time.time() - (max_age_days * 86400)
    purged = 0
    with _DB_LOCK:
        inactive = list(
            Session.using(db)
            .filter(F("active") == False)  # noqa: E712
            .all()
        )
        for session in inactive:
            snap_at = session.metadata.get("last_snapshot_at")
            if snap_at and snap_at < cutoff:
                session.delete()
                purged += 1
    return purged


purge_stale_snapshots_async = _in_thread(purge_stale_snapshots)


# Directory Visit Tracking (Frecency)


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
    db = _require_db()
    now = time.time()

    with _DB_LOCK:
        existing = (
            DirectoryVisit.using(db).filter((F("box") == box_name) & (F("path") == path)).first()
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
        visit.save(db=db)
        return visit


record_directory_visit_async = _in_thread(record_directory_visit)


def search_directories(
    box_name: str,
    query: str,
    limit: int = 20,
) -> list[tuple[str, float]]:
    """Search directories by substring match, ranked by frecency.

    Returns list of (path, score) tuples sorted by score descending.
    """
    db = _require_db()
    query_lower = query.lower()

    with _DB_LOCK:
        all_visits = DirectoryVisit.using(db).filter(F("box") == box_name).all()

        results: list[tuple[str, float]] = []
        for visit in all_visits:
            if query_lower in visit.path.lower():
                score = _calculate_frecency_score(visit.visit_count, visit.last_visited)
                results.append((visit.path, score))

        results.sort(key=lambda x: x[1], reverse=True)
        return results[:limit]


search_directories_async = _in_thread(search_directories)


# Layout CRUD Functions


def list_layouts() -> list[Layout]:
    """List saved multi-terminal layouts."""
    db = _require_db()
    with _DB_LOCK:
        return list(Layout.using(db).all())


list_layouts_async = _in_thread(list_layouts)


def save_layout(name: str, terminals: list[dict]) -> Layout:
    """Create and persist a named layout preset."""
    db = _require_db()
    with _DB_LOCK:
        layout = Layout(name=name, terminals_json=json.dumps(terminals))
        layout.save(db=db)
        return layout


save_layout_async = _in_thread(save_layout)


def delete_layout(layout_id: str) -> bool:
    """Delete a saved layout preset."""
    db = _require_db()
    with _DB_LOCK:
        layout = Layout.using(db).filter(F("id") == layout_id).first()
        if not layout:
            return False
        layout.delete()
        return True


delete_layout_async = _in_thread(delete_layout)


# Snippet CRUD Functions


def list_snippets(box_name: str) -> list[Snippet]:
    """Return snippets for a box plus global snippets, ordered by sort_order."""
    db = _require_db()
    with _DB_LOCK:
        rows = (
            Snippet.using(db)
            .filter((F("box") == box_name) | (F("box") == "__global__"))
            .order_by("sort_order")
            .all()
        )
        return list(rows)


list_snippets_async = _in_thread(list_snippets)


def create_snippet(
    box: str,
    label: str,
    command: str,
    category: str = "",
) -> Snippet:
    """Create a new snippet."""
    db = _require_db()
    with _DB_LOCK:
        # Determine next sort_order
        existing = (
            Snippet.using(db).filter(F("box") == box).order_by("sort_order", desc=True).first()
        )
        next_order = (existing.sort_order + 1) if existing else 0

        snippet = Snippet(
            box=box,
            label=label,
            command=command,
            category=category,
            sort_order=next_order,
        )
        snippet.save(db=db)
        return snippet


create_snippet_async = _in_thread(create_snippet)


def get_snippet_by_id(snippet_id: str) -> Snippet | None:
    """Get a snippet by ID."""
    db = _require_db()
    with _DB_LOCK:
        return Snippet.using(db).filter(F("id") == snippet_id).first()


def update_snippet(
    snippet_id: str,
    label: str | None = None,
    command: str | None = None,
    category: str | None = None,
    sort_order: int | None = None,
) -> Snippet | None:
    """Update a snippet. Returns updated snippet or None if not found."""
    db = _require_db()
    with _DB_LOCK:
        snippet = Snippet.using(db).filter(F("id") == snippet_id).first()
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


update_snippet_async = _in_thread(update_snippet)


def delete_snippet(snippet_id: str) -> bool:
    """Delete a snippet. Returns True if deleted."""
    db = _require_db()
    with _DB_LOCK:
        snippet = Snippet.using(db).filter(F("id") == snippet_id).first()
        if not snippet:
            return False
        snippet.delete()
        return True


delete_snippet_async = _in_thread(delete_snippet)


# Progress Bar CRUD Functions


def upsert_progress(
    name: str,
    current: float,
    total: float,
    color: str | None = None,
    label: str | None = None,
    status: str = "running",
    *,
    apply_metadata: bool = False,
    metadata: dict | None = None,
    metadata_error: str | None = None,
    merge_metadata: bool = False,
) -> ProgressBar:
    """Insert or update a progress bar by name. Returns the persisted record.

    Metadata semantics (only when ``apply_metadata`` is True, i.e. the push
    actually carried a metadata field):
      - ``metadata_error`` set  → keep the prior bag, record the error.
      - else ``merge_metadata`` → shallow-merge into the prior bag.
      - else                    → replace the bag (the default).
    When ``apply_metadata`` is False the bag and error are left untouched, so a
    long loop that only sends current/total never wipes earlier metadata.
    """
    db = _require_db()
    now = time.time()
    with _DB_LOCK:
        existing = ProgressBar.using(db).filter(F("name") == name).first()
        if existing:
            existing.current = current
            existing.total = total
            existing.color = color
            existing.label = label
            existing.status = status
            existing.updated_at = now
            if apply_metadata:
                if metadata_error is not None:
                    existing.metadata_error = metadata_error
                else:
                    existing.metadata = (
                        {**existing.metadata, **(metadata or {})}
                        if merge_metadata
                        else (metadata or {})
                    )
                    existing.metadata_error = None
            existing.save()
            return existing
        bar = ProgressBar(
            name=name,
            current=current,
            total=total,
            color=color,
            label=label,
            status=status,
            created_at=now,
            updated_at=now,
        )
        if apply_metadata and metadata_error is None:
            bar.metadata = metadata or {}
        bar.metadata_error = metadata_error if apply_metadata else None
        bar.save(db=db)
        return bar


upsert_progress_async = _in_thread(upsert_progress)


def list_progress() -> list[ProgressBar]:
    """Return all progress bars ordered by most-recently updated first."""
    db = _require_db()
    with _DB_LOCK:
        return list(ProgressBar.using(db).order_by("updated_at", desc=True).all())


list_progress_async = _in_thread(list_progress)


def get_progress(name: str) -> ProgressBar | None:
    """Return the progress bar named ``name`` or None."""
    db = _require_db()
    with _DB_LOCK:
        return ProgressBar.using(db).filter(F("name") == name).first()


get_progress_async = _in_thread(get_progress)


def delete_progress(name: str) -> bool:
    """Delete a progress bar by name. Returns True if a row was removed."""
    db = _require_db()
    with _DB_LOCK:
        bar = ProgressBar.using(db).filter(F("name") == name).first()
        if not bar:
            return False
        bar.delete()
        return True


delete_progress_async = _in_thread(delete_progress)


# --- Diff notebooks ---


def save_diff_notebook(label: str, envelope_json: str, cell_count: int) -> DiffNotebook:
    """Persist a new diff notebook. Always creates a new row (no upsert)."""
    db = _require_db()
    now = time.time()
    with _DB_LOCK:
        nb = DiffNotebook(
            label=label or "",
            envelope_json=envelope_json,
            cell_count=cell_count,
            created_at=now,
            updated_at=now,
        )
        nb.save(db=db)
        return nb


save_diff_notebook_async = _in_thread(save_diff_notebook)


def get_diff_notebook(notebook_id: str) -> DiffNotebook | None:
    db = _require_db()
    with _DB_LOCK:
        return DiffNotebook.using(db).filter(F("id") == notebook_id).first()


get_diff_notebook_async = _in_thread(get_diff_notebook)


def list_diff_notebooks() -> list[DiffNotebook]:
    """Return all saved diff notebooks ordered by newest first."""
    db = _require_db()
    with _DB_LOCK:
        return list(DiffNotebook.using(db).order_by("created_at", desc=True).all())


list_diff_notebooks_async = _in_thread(list_diff_notebooks)


def delete_diff_notebook(notebook_id: str) -> bool:
    """Delete a diff notebook by id. Returns True if a row was removed."""
    db = _require_db()
    with _DB_LOCK:
        nb = DiffNotebook.using(db).filter(F("id") == notebook_id).first()
        if not nb:
            return False
        nb.delete()
        return True


delete_diff_notebook_async = _in_thread(delete_diff_notebook)


# --- Artifact projects and registrations ---


def _normalize_project_name(name: str) -> str:
    return " ".join(name.split()).casefold()


def get_or_create_artifact_project(name: str) -> tuple[ArtifactProject, bool]:
    """Return a case-insensitively named project and whether it was created."""
    db = _require_db()
    clean_name = " ".join(name.split())
    normalized = _normalize_project_name(clean_name)
    now = time.time()
    with _DB_LOCK:
        existing = ArtifactProject.using(db).filter(F("normalized_name") == normalized).first()
        if existing:
            return existing, False
        used = {row.slug for row in ArtifactProject.using(db).all()}
        project_id = secrets.token_urlsafe(12)
        slug = _unique_artifact_slug(artifact_slug(clean_name, "project"), used, project_id)
        project = ArtifactProject(
            id=project_id,
            name=clean_name,
            normalized_name=normalized,
            slug=slug,
            created_at=now,
            updated_at=now,
        )
        project.save(db=db)
        return project, True


get_or_create_artifact_project_async = _in_thread(get_or_create_artifact_project)


def get_artifact_project(project_id: str) -> ArtifactProject | None:
    db = _require_db()
    with _DB_LOCK:
        return ArtifactProject.using(db).filter(F("id") == project_id).first()


get_artifact_project_async = _in_thread(get_artifact_project)


def list_artifact_projects() -> list[ArtifactProject]:
    db = _require_db()
    with _DB_LOCK:
        return list(ArtifactProject.using(db).order_by("name").all())


list_artifact_projects_async = _in_thread(list_artifact_projects)


def rename_artifact_project(project_id: str, name: str) -> ArtifactProject | None:
    db = _require_db()
    clean_name = " ".join(name.split())
    normalized = _normalize_project_name(clean_name)
    now = time.time()
    with _DB_LOCK:
        project = ArtifactProject.using(db).filter(F("id") == project_id).first()
        if not project:
            return None
        collision = ArtifactProject.using(db).filter(F("normalized_name") == normalized).first()
        if collision and collision.id != project_id:
            raise ValueError("A project with that name already exists")
        project.name = clean_name
        project.normalized_name = normalized
        project.updated_at = now
        project.save()
        return project


rename_artifact_project_async = _in_thread(rename_artifact_project)


def list_artifact_registrations(
    project_id: str | None = None,
) -> list[ArtifactRegistration]:
    db = _require_db()
    with _DB_LOCK:
        query = ArtifactRegistration.using(db)
        if project_id is not None:
            query = query.filter(F("project_id") == project_id)
        return list(query.order_by("created_at", desc=True).all())


list_artifact_registrations_async = _in_thread(list_artifact_registrations)


def get_artifact_registration(registration_id: str) -> ArtifactRegistration | None:
    db = _require_db()
    with _DB_LOCK:
        return ArtifactRegistration.using(db).filter(F("id") == registration_id).first()


get_artifact_registration_async = _in_thread(get_artifact_registration)


def find_artifact_registration(
    source_path: str, mode: str, entrypoint: str | None
) -> ArtifactRegistration | None:
    db = _require_db()
    with _DB_LOCK:
        rows = ArtifactRegistration.using(db).filter(F("source_path") == source_path).all()
        return next(
            (row for row in rows if row.mode == mode and row.entrypoint == entrypoint),
            None,
        )


find_artifact_registration_async = _in_thread(find_artifact_registration)


def get_artifact_project_by_slug(slug: str) -> ArtifactProject | None:
    db = _require_db()
    with _DB_LOCK:
        return ArtifactProject.using(db).filter(F("slug") == slug).first()


def get_artifact_registration_by_alias(
    project_slug: str, registration_slug: str
) -> ArtifactRegistration | None:
    db = _require_db()
    with _DB_LOCK:
        project = ArtifactProject.using(db).filter(F("slug") == project_slug).first()
        if project is None:
            return None
        rows = ArtifactRegistration.using(db).filter(F("project_id") == project.id).all()
        return next((row for row in rows if row.slug == registration_slug), None)


def find_artifact_registration_by_mount(
    request_path: str,
) -> tuple[ArtifactRegistration, str] | None:
    """Resolve the longest registered root mount and return its remaining path."""
    db = _require_db()
    with _DB_LOCK:
        rows = [
            row
            for row in ArtifactRegistration.using(db).all()
            if row.mount_path
            and (request_path == row.mount_path or request_path.startswith(f"{row.mount_path}/"))
        ]
        if not rows:
            return None
        registration = max(rows, key=lambda row: len(row.mount_path))
        relative = request_path[len(registration.mount_path) :].lstrip("/")
        return registration, relative


def create_artifact_registration(
    *,
    project_id: str,
    group_path: str,
    source_path: str,
    mode: str,
    entrypoint: str | None,
    title_override: str | None,
    slug: str | None = None,
    mount_path: str = "",
) -> ArtifactRegistration:
    db = _require_db()
    now = time.time()
    with _DB_LOCK:
        project_rows = ArtifactRegistration.using(db).filter(F("project_id") == project_id).all()
        used = {row.slug for row in project_rows}
        source_name = Path(source_path).stem or Path(source_path).name
        slug_base = artifact_slug(slug or title_override or source_name, "artifact")
        if slug is not None and slug_base in used:
            raise ValueError("An artifact with that alias already exists in the project")
        chosen_slug = _unique_artifact_slug(slug_base, used, secrets.token_urlsafe(16))
        if mount_path and any(
            row.mount_path == mount_path for row in ArtifactRegistration.using(db).all()
        ):
            raise ValueError("That artifact mount path is already registered")
        registration = ArtifactRegistration(
            project_id=project_id,
            group_path=group_path,
            source_path=source_path,
            mode=mode,
            entrypoint=entrypoint,
            title_override=title_override,
            slug=chosen_slug,
            mount_path=mount_path,
            created_at=now,
            updated_at=now,
        )
        registration.save(db=db)
        return registration


create_artifact_registration_async = _in_thread(create_artifact_registration)


def update_artifact_registration(
    registration_id: str,
    **changes: object,
) -> ArtifactRegistration | None:
    db = _require_db()
    allowed = {
        "project_id",
        "group_path",
        "source_path",
        "mode",
        "entrypoint",
        "title_override",
        "slug",
        "mount_path",
    }
    with _DB_LOCK:
        registration = ArtifactRegistration.using(db).filter(F("id") == registration_id).first()
        if not registration:
            return None
        next_project_id = str(changes.get("project_id", registration.project_id))
        next_slug = str(changes.get("slug", registration.slug))
        slug_collision = next(
            (
                row
                for row in ArtifactRegistration.using(db)
                .filter(F("project_id") == next_project_id)
                .all()
                if row.id != registration_id and row.slug == next_slug
            ),
            None,
        )
        if slug_collision:
            raise ValueError("An artifact with that alias already exists in the project")
        next_mount = str(changes.get("mount_path", registration.mount_path))
        if next_mount:
            mount_collision = next(
                (
                    row
                    for row in ArtifactRegistration.using(db).all()
                    if row.id != registration_id and row.mount_path == next_mount
                ),
                None,
            )
            if mount_collision:
                raise ValueError("That artifact mount path is already registered")
        for key, value in changes.items():
            if key in allowed:
                setattr(registration, key, value)
        registration.updated_at = time.time()
        registration.save()
        return registration


update_artifact_registration_async = _in_thread(update_artifact_registration)


def delete_artifact_registration(registration_id: str) -> bool:
    db = _require_db()
    with _DB_LOCK:
        registration = ArtifactRegistration.using(db).filter(F("id") == registration_id).first()
        if not registration:
            return False
        registration.delete()
        return True


delete_artifact_registration_async = _in_thread(delete_artifact_registration)


def delete_artifact_project(project_id: str, *, cascade: bool = False) -> tuple[bool, int]:
    """Delete a project, optionally unregistering its artifacts first."""
    db = _require_db()
    with _DB_LOCK:
        project = ArtifactProject.using(db).filter(F("id") == project_id).first()
        if not project:
            return False, 0
        registrations = list(
            ArtifactRegistration.using(db).filter(F("project_id") == project_id).all()
        )
        if registrations and not cascade:
            raise ValueError("Project still contains artifact registrations")
        for registration in registrations:
            registration.delete()
        project.delete()
        return True, len(registrations)


delete_artifact_project_async = _in_thread(delete_artifact_project)
