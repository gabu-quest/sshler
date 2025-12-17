"""Tests for session-based authentication."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from sshler.session import Session, SessionStore, get_session_store, reset_session_store
from sshler.settings import SshlerSettings, reset_settings


class TestSessionStore:
    """Tests for SessionStore class."""

    def setup_method(self):
        """Reset session store before each test."""
        reset_session_store()

    def test_create_session(self):
        """Test session creation."""
        store = get_session_store()

        session = store.create_session(
            username="testuser",
            user_id="testuser",
            ttl_seconds=3600,
        )

        assert session.session_id is not None
        assert len(session.session_id) == 32  # 128 bits = 32 hex chars
        assert session.username == "testuser"
        assert session.user_id == "testuser"
        assert session.expires_at > session.created_at

    def test_get_valid_session(self):
        """Test retrieving a valid session."""
        store = get_session_store()

        created = store.create_session("testuser", "testuser", ttl_seconds=3600)
        retrieved = store.get_session(created.session_id)

        assert retrieved is not None
        assert retrieved.session_id == created.session_id
        assert retrieved.username == "testuser"

    def test_get_invalid_session(self):
        """Test retrieving non-existent session."""
        store = get_session_store()

        retrieved = store.get_session("nonexistent")
        assert retrieved is None

    def test_delete_session(self):
        """Test session deletion."""
        store = get_session_store()

        session = store.create_session("testuser", "testuser", ttl_seconds=3600)
        assert store.delete_session(session.session_id) is True

        # Verify session is gone
        retrieved = store.get_session(session.session_id)
        assert retrieved is None

    def test_delete_nonexistent_session(self):
        """Test deleting non-existent session."""
        store = get_session_store()
        assert store.delete_session("nonexistent") is False

    def test_session_expiration(self):
        """Test that expired sessions are cleaned up."""
        store = get_session_store()

        # Create session with 0 second TTL (immediately expired)
        session = store.create_session("testuser", "testuser", ttl_seconds=0)

        # Try to retrieve it (should be None since it's expired)
        retrieved = store.get_session(session.session_id)
        assert retrieved is None

    def test_session_touch(self):
        """Test session last_accessed update."""
        import time

        session = Session(
            session_id="test",
            user_id="user",
            username="user",
            created_at=time.time(),
            last_accessed_at=time.time(),
            expires_at=time.time() + 3600,
        )

        initial_accessed = session.last_accessed_at
        time.sleep(0.01)  # Small delay
        session.touch()

        assert session.last_accessed_at > initial_accessed

    def test_cleanup_expired(self):
        """Test cleanup of expired sessions."""
        store = get_session_store()

        # Create two sessions: one valid, one expired
        valid = store.create_session("valid", "valid", ttl_seconds=3600)
        expired = store.create_session("expired", "expired", ttl_seconds=0)

        # Run cleanup
        cleaned = store.cleanup_expired()

        # At least 1 session should be cleaned (the expired one)
        assert cleaned >= 1

        # Valid session should still exist
        assert store.get_session(valid.session_id) is not None


class TestSettings:
    """Tests for SshlerSettings."""

    def setup_method(self):
        """Reset settings before each test."""
        reset_settings()

    def test_default_settings(self):
        """Test default settings values."""
        settings = SshlerSettings()

        assert settings.host == "127.0.0.1"
        assert settings.port == 8822
        assert settings.cookie_secure is True
        assert settings.cookie_samesite == "lax"
        assert settings.session_ttl_seconds == 28800  # 8 hours
        assert settings.require_auth is True

    def test_cors_disabled_by_default(self):
        """Test that CORS is disabled when no origins configured."""
        settings = SshlerSettings()

        assert settings.cors_enabled is False
        assert settings.allowed_origins_list == []

    def test_cors_enabled_with_origins(self):
        """Test CORS enabled when origins are configured."""
        settings = SshlerSettings(allowed_origins="http://localhost:3000,http://example.com")

        assert settings.cors_enabled is True
        assert len(settings.allowed_origins_list) == 2
        assert "http://localhost:3000" in settings.allowed_origins_list
        assert "http://example.com" in settings.allowed_origins_list


# Integration tests with FastAPI
@pytest.mark.skip(reason="Requires full app setup - placeholder for future implementation")
class TestAuthEndpoints:
    """Integration tests for auth endpoints."""

    def test_login_success(self):
        """Test successful login."""
        # TODO: Implement when app test fixture is ready
        pass

    def test_login_invalid_credentials(self):
        """Test login with invalid credentials."""
        # TODO: Implement when app test fixture is ready
        pass

    def test_get_me_authenticated(self):
        """Test /auth/me with valid session."""
        # TODO: Implement when app test fixture is ready
        pass

    def test_get_me_unauthenticated(self):
        """Test /auth/me without session."""
        # TODO: Implement when app test fixture is ready
        pass

    def test_logout(self):
        """Test logout endpoint."""
        # TODO: Implement when app test fixture is ready
        pass
