"""Session management for cookie-based authentication.

Provides a simple in-memory session store with expiration and cleanup.
"""

from __future__ import annotations

import secrets
import time
from dataclasses import dataclass, field
from typing import Dict


@dataclass
class Session:
    """Represents an authenticated user session."""

    session_id: str
    user_id: str
    username: str
    created_at: float = field(default_factory=time.time)
    last_accessed_at: float = field(default_factory=time.time)
    expires_at: float = field(default=0.0)

    def is_expired(self, idle_timeout: int = 0) -> bool:
        """Check if session is expired.

        Args:
            idle_timeout: Optional idle timeout in seconds (0 = disabled)

        Returns:
            True if session is expired
        """
        now = time.time()

        # Check absolute expiration
        if now >= self.expires_at:
            return True

        # Check idle timeout if enabled
        if idle_timeout > 0 and (now - self.last_accessed_at) >= idle_timeout:
            return True

        return False

    def touch(self) -> None:
        """Update last accessed time."""
        self.last_accessed_at = time.time()


class SessionStore:
    """In-memory session store.

    ⚠️  WARNING: NOT SUITABLE FOR MULTI-INSTANCE DEPLOYMENTS

    This implementation stores sessions in process memory and does not share
    session state between multiple instances. If you run multiple sshler processes
    behind a load balancer, users will experience:
    - Session loss when requests are routed to different instances
    - Random logouts as load balancer switches between instances
    - "Session expired or invalid" errors

    **Suitable for:**
    - Single-instance deployments (systemd service, Docker container)
    - Development and testing
    - Small internal tools with one backend process

    **For multi-instance deployments, implement a shared session backend:**
    - Redis (recommended) - use redis-py with session_id as key
    - PostgreSQL/MySQL - create a sessions table
    - Memcached - similar to Redis approach

    See the SessionStore interface below for methods to implement.
    """

    def __init__(self):
        """Initialize empty session store."""
        self._sessions: Dict[str, Session] = {}

    def create_session(
        self,
        username: str,
        user_id: str,
        ttl_seconds: int = 28800,
    ) -> Session:
        """Create a new session.

        Args:
            username: Username for the session
            user_id: User ID (can be same as username for simple auth)
            ttl_seconds: Time-to-live in seconds (default: 8 hours)

        Returns:
            Created session object
        """
        # Generate cryptographically secure session ID (128 bits = 32 hex chars)
        session_id = secrets.token_hex(16)

        now = time.time()
        session = Session(
            session_id=session_id,
            user_id=user_id,
            username=username,
            created_at=now,
            last_accessed_at=now,
            expires_at=now + ttl_seconds,
        )

        self._sessions[session_id] = session
        return session

    def get_session(self, session_id: str, idle_timeout: int = 0) -> Session | None:
        """Get session by ID.

        Args:
            session_id: Session ID to retrieve
            idle_timeout: Optional idle timeout in seconds (0 = disabled)

        Returns:
            Session if valid and not expired, None otherwise
        """
        session = self._sessions.get(session_id)

        if session is None:
            return None

        # Check if expired
        if session.is_expired(idle_timeout):
            # Clean up expired session
            self.delete_session(session_id)
            return None

        # Update last accessed time
        session.touch()
        return session

    def delete_session(self, session_id: str) -> bool:
        """Delete a session.

        Args:
            session_id: Session ID to delete

        Returns:
            True if session was deleted, False if not found
        """
        if session_id in self._sessions:
            del self._sessions[session_id]
            return True
        return False

    def cleanup_expired(self, idle_timeout: int = 0) -> int:
        """Remove all expired sessions.

        Args:
            idle_timeout: Optional idle timeout in seconds (0 = disabled)

        Returns:
            Number of sessions cleaned up
        """
        expired_ids = [
            sid
            for sid, session in self._sessions.items()
            if session.is_expired(idle_timeout)
        ]

        for sid in expired_ids:
            del self._sessions[sid]

        return len(expired_ids)

    def count(self) -> int:
        """Get total session count.

        Returns:
            Number of active sessions
        """
        return len(self._sessions)

    def clear(self) -> None:
        """Clear all sessions (useful for testing)."""
        self._sessions.clear()


# Global session store instance
_session_store: SessionStore | None = None


def get_session_store() -> SessionStore:
    """Get or create global session store instance."""
    global _session_store
    if _session_store is None:
        _session_store = SessionStore()
    return _session_store


def reset_session_store() -> None:
    """Reset session store (useful for testing)."""
    global _session_store
    if _session_store is not None:
        _session_store.clear()
    _session_store = None
