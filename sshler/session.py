"""Session management for cookie-based authentication.

Provides a simple in-memory session store with expiration and cleanup, and the
record of which sockets were let in on which session: a session's end, however it
comes, ends each of them.
"""

from __future__ import annotations

import asyncio
import contextlib
import secrets
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from .settings import get_settings

Clock = Callable[[], float]


def _system_clock() -> float:
    """Default clock: `time.time()`, looked up at call time so the module stays patchable."""
    return time.time()


@dataclass
class Session:
    """Represents an authenticated user session."""

    session_id: str
    user_id: str
    username: str
    created_at: float = field(default_factory=_system_clock)
    last_accessed_at: float = field(default_factory=_system_clock)
    expires_at: float = field(default=0.0)
    clock: Clock = field(default=_system_clock, repr=False, compare=False)

    def deadline(self, idle_timeout: int = 0) -> float:
        """When the session ends unless it is used again: its absolute expiry, or one
        idle timeout after its last use when that comes first (0 = no idle timeout)."""
        if idle_timeout > 0:
            return min(self.expires_at, self.last_accessed_at + idle_timeout)
        return self.expires_at

    def is_expired(self, idle_timeout: int = 0) -> bool:
        """Check if session is expired.

        Args:
            idle_timeout: Optional idle timeout in seconds (0 = disabled)

        Returns:
            True if session is expired
        """
        return self.clock() >= self.deadline(idle_timeout)

    def touch(self) -> None:
        """Update last accessed time."""
        self.last_accessed_at = self.clock()


SocketEnd = Callable[[], None]

# The longest a held socket's timer waits before it reads the store's clock again. The
# event loop's clock stands still while the machine sleeps and the store's does not: a
# timer set once for the whole time left would let a socket outlive its session by as
# long as the machine slept.
RECHECK_SECONDS = 30.0


class SocketHold:
    """One socket's place in the record of its session.

    Made by ``SessionStore.hold`` on the socket's event loop. When the session ends,
    ``end`` runs once, on that loop; ``release`` takes the socket out of the record
    when it closes for any other reason. While held, one timer waits for the
    session's deadline, so its time running out is noticed without a request.
    """

    def __init__(
        self,
        store: SessionStore,
        session_id: str,
        end: SocketEnd,
        idle_timeout: int,
        loop: asyncio.AbstractEventLoop,
    ) -> None:
        self._store = store
        self.session_id = session_id
        self._end = end
        self._idle_timeout = idle_timeout
        self._loop = loop
        self._timer: asyncio.TimerHandle | None = None
        self._ended = False

    def release(self) -> None:
        """Leave the record: the socket closed for a reason of its own. Idempotent."""
        self._store._release(self)
        self._cancel_timer()

    def _arm(self, deadline: float) -> None:
        """Wake at *deadline* (on the store's clock), or after ``RECHECK_SECONDS`` if that
        is sooner, to ask the store about the session."""
        self._cancel_timer()
        delay = min(max(0.0, deadline - self._store._clock()), RECHECK_SECONDS)
        self._timer = self._loop.call_later(delay, self._store._deadline_reached, self)

    def _cancel_timer(self) -> None:
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None

    def _session_ended(self) -> None:
        """Run ``end`` on the socket's loop, once, whichever thread ended the session."""
        with contextlib.suppress(RuntimeError):  # the socket's loop has already closed
            self._loop.call_soon_threadsafe(self._run_end)

    def _run_end(self) -> None:
        self._cancel_timer()
        if not self._ended:
            self._ended = True
            self._end()


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

    def __init__(self, clock: Clock = _system_clock):
        """Initialize empty session store.

        Args:
            clock: Returns the current time in seconds; shared with every session
        """
        self._clock = clock
        self._sessions: dict[str, Session] = {}
        # The sockets let in on each live session. The lock makes "is the session live"
        # and "record this socket" one step against an end from any thread.
        self._held: dict[str, set[SocketHold]] = {}
        self._lock = threading.Lock()

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

        now = self._clock()
        session = Session(
            session_id=session_id,
            user_id=user_id,
            username=username,
            created_at=now,
            last_accessed_at=now,
            expires_at=now + ttl_seconds,
            clock=self._clock,
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
        with self._lock:
            existed = self._sessions.pop(session_id, None) is not None
            held = self._held.pop(session_id, set())
        for hold in held:
            hold._session_ended()
        return existed

    def hold(self, session_id: str, end: SocketEnd, idle_timeout: int = 0) -> SocketHold | None:
        """Record a socket on its session, so the session's end runs *end* for it.

        Call it on the socket's event loop. The session is judged live and the socket
        recorded in one step, so a session that ended after the socket was let in (and
        before this call) gives None and the caller refuses the socket; one that ends
        later runs *end* (on the socket's loop, once). A session that is past its
        deadline is ended here.
        """
        loop = asyncio.get_running_loop()
        with self._lock:
            session = self._sessions.get(session_id)
            live = session is not None and not session.is_expired(idle_timeout)
            if live:
                hold = SocketHold(self, session_id, end, idle_timeout, loop)
                self._held.setdefault(session_id, set()).add(hold)
        if session is None:
            return None
        if not live:
            self.delete_session(session_id)
            return None
        hold._arm(session.deadline(idle_timeout))
        return hold

    def sockets_held(self, session_id: str) -> int:
        """How many sockets the record holds for *session_id*."""
        with self._lock:
            return len(self._held.get(session_id, ()))

    def _release(self, hold: SocketHold) -> None:
        with self._lock:
            held = self._held.get(hold.session_id)
            if held is None:
                return
            held.discard(hold)
            if not held:
                del self._held[hold.session_id]

    def _deadline_reached(self, hold: SocketHold) -> None:
        """A held socket's timer: end the session if its time is up, else wait again.

        The deadline moves later when the session is used (the idle timeout), never
        past the absolute expiry."""
        with self._lock:
            session = self._sessions.get(hold.session_id)
            if session is None or hold not in self._held.get(hold.session_id, ()):
                return
            expired = session.is_expired(hold._idle_timeout)
        if expired:
            self.delete_session(hold.session_id)
        else:
            hold._arm(session.deadline(hold._idle_timeout))

    def cleanup_expired(self, idle_timeout: int = 0) -> int:
        """Remove all expired sessions.

        Args:
            idle_timeout: Optional idle timeout in seconds (0 = disabled)

        Returns:
            Number of sessions cleaned up
        """
        expired_ids = [
            sid for sid, session in self._sessions.items() if session.is_expired(idle_timeout)
        ]

        for sid in expired_ids:
            self.delete_session(sid)

        return len(expired_ids)

    def count(self) -> int:
        """Get total session count.

        Returns:
            Number of active sessions
        """
        return len(self._sessions)

    def clear(self) -> None:
        """Clear all sessions (useful for testing); every held socket's session ends."""
        for session_id in list(self._sessions):
            self.delete_session(session_id)


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


def live_session(session_id: str) -> Session | None:
    """The live session for this id, or None.

    The one check of "this session is live": the server's door, the sockets and the
    auth routes all ask here, so the configured idle timeout applies to each alike.
    """
    return get_session_store().get_session(
        session_id, idle_timeout=get_settings().session_idle_timeout_seconds
    )


def hold_on_session(session_id: str, end: SocketEnd) -> SocketHold | None:
    """Record a socket on its live session, with the configured idle timeout, as
    ``live_session`` judges it; None when that session has ended."""
    return get_session_store().hold(
        session_id, end, idle_timeout=get_settings().session_idle_timeout_seconds
    )
