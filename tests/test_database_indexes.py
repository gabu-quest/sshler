"""Tests for database composite indexes."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from sshler.state import (
    Session,
    Favorite,
    create_or_update_session,
    cleanup_old_sessions,
    get_session_by_name,
    initialize,
    toggle_favorite,
    reset_state,
)


@pytest.fixture
def temp_state_db():
    """Create a temporary state database for testing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        config_dir = Path(tmpdir)
        initialize(config_dir)
        yield config_dir
        reset_state()


class TestCompositeIndexes:
    """Test that composite indexes are created and improve query performance."""

    def test_indexes_are_created(self, temp_state_db):
        """Test that composite indexes are created during initialization."""
        # Get the database path
        db_path = temp_state_db / "state.sqlite"
        assert db_path.exists()

        # Query SQLite to verify indexes exist
        import sqlite3
        conn = sqlite3.connect(str(db_path))
        cursor = conn.cursor()

        # Get all indexes
        cursor.execute("SELECT name, tbl_name, sql FROM sqlite_master WHERE type='index'")
        indexes = cursor.fetchall()
        conn.close()

        index_names = [idx[0] for idx in indexes]

        # Verify composite indexes exist
        assert "idx_sessions_box_name" in index_names, "Missing composite index for (box, session_name)"
        assert "idx_sessions_active_accessed" in index_names, "Missing composite index for (active, last_accessed_at)"
        assert "idx_favorites_box_path" in index_names, "Missing composite index for (box, path)"

    def test_composite_index_structure(self, temp_state_db):
        """Test that composite indexes have the correct column order."""
        db_path = temp_state_db / "state.sqlite"

        import sqlite3
        conn = sqlite3.connect(str(db_path))
        cursor = conn.cursor()

        # Get index details
        cursor.execute("SELECT name, sql FROM sqlite_master WHERE type='index' AND name LIKE 'idx_%'")
        indexes = {name: sql for name, sql in cursor.fetchall()}
        conn.close()

        # Verify sessions composite index structure (sqler uses JSON storage)
        assert "idx_sessions_box_name" in indexes
        assert "json_extract(data, '$.box')" in indexes["idx_sessions_box_name"]
        assert "json_extract(data, '$.session_name')" in indexes["idx_sessions_box_name"]

        assert "idx_sessions_active_accessed" in indexes
        assert "json_extract(data, '$.active')" in indexes["idx_sessions_active_accessed"]
        assert "json_extract(data, '$.last_accessed_at')" in indexes["idx_sessions_active_accessed"]

        assert "idx_favorites_box_path" in indexes
        assert "json_extract(data, '$.box')" in indexes["idx_favorites_box_path"]
        assert "json_extract(data, '$.path')" in indexes["idx_favorites_box_path"]

    def test_session_lookup_uses_index(self, temp_state_db):
        """Test that session lookups by (box, session_name) work correctly."""
        # Create a session
        session = create_or_update_session(
            box_name="test-box",
            session_name="test-session",
            working_directory="/tmp",
        )

        assert session.box == "test-box"
        assert session.session_name == "test-session"

        # Lookup the session
        found = get_session_by_name("test-box", "test-session")
        assert found is not None
        assert found.id == session.id
        assert found.box == "test-box"
        assert found.session_name == "test-session"

        # Lookup non-existent session
        not_found = get_session_by_name("test-box", "nonexistent")
        assert not_found is None

    def test_session_cleanup_uses_index(self, temp_state_db):
        """Test that session cleanup queries work correctly."""
        import time

        # Create some sessions
        # Active session (should not be cleaned up)
        active_session = create_or_update_session(
            box_name="test-box",
            session_name="active",
            working_directory="/tmp",
        )

        # Inactive old session (should be cleaned up)
        old_session = create_or_update_session(
            box_name="test-box",
            session_name="old",
            working_directory="/tmp",
        )
        # Manually mark as inactive and old
        old_session.active = False
        old_session.last_accessed_at = time.time() - (31 * 24 * 60 * 60)  # 31 days ago
        old_session.save()

        # Inactive recent session (should not be cleaned up)
        recent_session = create_or_update_session(
            box_name="test-box",
            session_name="recent",
            working_directory="/tmp",
        )
        recent_session.active = False
        recent_session.save()

        # Run cleanup
        deleted = cleanup_old_sessions(max_age_days=30)

        # Verify only old inactive session was deleted
        assert deleted == 1

        # Verify sessions
        assert get_session_by_name("test-box", "active") is not None  # Active - kept
        assert get_session_by_name("test-box", "old") is None  # Inactive + old - deleted
        assert get_session_by_name("test-box", "recent") is not None  # Inactive but recent - kept

    def test_favorite_lookup_uses_index(self, temp_state_db):
        """Test that favorite lookups by (box, path) work correctly."""
        # Add a favorite
        added = toggle_favorite("test-box", "/home/user/project")
        assert added is True

        # Toggle again (should remove)
        removed = toggle_favorite("test-box", "/home/user/project")
        assert removed is False

        # Toggle back (should add)
        added_again = toggle_favorite("test-box", "/home/user/project")
        assert added_again is True

    def test_index_performance_with_many_sessions(self, temp_state_db):
        """Test that indexes improve performance with many sessions."""
        # Create many sessions across different boxes
        for box_num in range(10):
            box_name = f"box-{box_num}"
            for session_num in range(10):
                session_name = f"session-{session_num}"
                create_or_update_session(
                    box_name=box_name,
                    session_name=session_name,
                    working_directory=f"/tmp/{box_num}/{session_num}",
                )

        # With 100 sessions, lookups should still be fast with indexes
        # Test a lookup (should use idx_sessions_box_name)
        found = get_session_by_name("box-5", "session-7")
        assert found is not None
        assert found.box == "box-5"
        assert found.session_name == "session-7"

        # Test update (should use idx_sessions_box_name for lookup)
        updated = create_or_update_session(
            box_name="box-5",
            session_name="session-7",
            working_directory="/new/path",
        )
        assert updated.working_directory == "/new/path"

    def test_indexes_are_idempotent(self, temp_state_db):
        """Test that reinitializing doesn't fail due to existing indexes."""
        # Initialize again (should not fail)
        initialize(temp_state_db)

        # Indexes should still exist
        db_path = temp_state_db / "state.sqlite"
        import sqlite3
        conn = sqlite3.connect(str(db_path))
        cursor = conn.cursor()

        cursor.execute("SELECT name FROM sqlite_master WHERE type='index' AND name LIKE 'idx_%'")
        indexes = [row[0] for row in cursor.fetchall()]
        conn.close()

        assert "idx_sessions_box_name" in indexes
        assert "idx_sessions_active_accessed" in indexes
        assert "idx_favorites_box_path" in indexes

    def test_query_plan_uses_composite_index(self, temp_state_db):
        """Test that SQLite query planner uses composite indexes."""
        # Create some test data
        create_or_update_session("test-box", "test-session", "/tmp")

        db_path = temp_state_db / "state.sqlite"
        import sqlite3
        conn = sqlite3.connect(str(db_path))
        cursor = conn.cursor()

        # Check query plan for session lookup (sqler uses JSON storage)
        # Query must use json_extract() to match how sqler queries
        cursor.execute(
            """EXPLAIN QUERY PLAN
            SELECT * FROM sessions
            WHERE json_extract(data, '$.box') = ?
            AND json_extract(data, '$.session_name') = ?""",
            ("test-box", "test-session")
        )
        query_plan = cursor.fetchall()
        conn.close()

        # Query plan should mention using the index
        query_plan_str = " ".join(str(row) for row in query_plan)
        # SQLite should use idx_sessions_box_name for this query
        assert "idx_sessions_box_name" in query_plan_str or "USING INDEX" in query_plan_str.upper()
