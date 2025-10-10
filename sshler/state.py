from __future__ import annotations

import asyncio
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


if TYPE_CHECKING:  # pragma: no cover - import for typing only
    from .config import StoredBox


def initialize(config_dir: Path) -> None:
    """Initialise the SQLite-backed state store using ``sqler``.

    The state database lives alongside ``boxes.yaml`` and holds favourites and
    future history records.
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
        Favorite.set_db(db)
        Favorite.ensure_index("box")
        Favorite.ensure_index("position")

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
