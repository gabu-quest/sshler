"""Authentication endpoints for session-based auth.

Provides /auth/login, /auth/me, and /auth/logout endpoints.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel

from ..auth import AuthManager
from ..session import Session, get_session_store
from ..settings import get_settings
from .rate_limiting import rate_limit_login

if TYPE_CHECKING:
    from ..webapp import AuthFailureTracker

logger = logging.getLogger(__name__)


def get_client_ip(request: Request) -> str:
    """Get the client address that rate limits, login failures and the lockout key on.

    X-Real-IP is trusted only when ``SSHLER_TRUST_PROXY_HEADERS`` says a reverse proxy
    (Caddy, nginx) on this host overwrites it, and only on connections from that proxy
    (127.0.0.1). Otherwise any local process could send a new X-Real-IP per request to
    get a fresh bucket, or name a victim's address to lock the victim out, so the
    socket peer is used.
    """
    if (
        get_settings().trust_proxy_headers
        and request.client
        and request.client.host == "127.0.0.1"
    ):
        forwarded_ip = request.headers.get("x-real-ip")
        if forwarded_ip:
            return forwarded_ip
    return request.client.host if request.client else "unknown"


class LoginRequest(BaseModel):
    """Login request payload."""

    username: str
    password: str


class UserInfo(BaseModel):
    """User information response."""

    username: str
    user_id: str
    authenticated: bool = True


class LoginResponse(BaseModel):
    """Login response payload."""

    success: bool
    message: str = "Login successful"


class LogoutResponse(BaseModel):
    """Logout response payload."""

    success: bool
    message: str = "Logged out successfully"


def create_auth_router(
    auth_manager: AuthManager | None,
    failure_tracker: AuthFailureTracker,
) -> APIRouter:
    """Create authentication router.

    Args:
        auth_manager: Authentication manager instance (can be None if auth disabled)
        failure_tracker: Auth failure tracker instance

    Returns:
        Configured APIRouter
    """
    router = APIRouter(prefix="/auth", tags=["authentication"])
    settings = get_settings()
    session_store = get_session_store()

    @router.post("/login", response_model=LoginResponse, status_code=200)
    async def login(
        request: Request,
        response: Response,
        credentials: LoginRequest,
        _rate_limit: None = Depends(rate_limit_login),
    ) -> LoginResponse:
        """Authenticate user and create session.

        Sets httpOnly session cookie on successful authentication.

        Rate limiting:
        - IP-based rate limiting: 5 requests per minute (prevents brute force)
        - Failures are recorded in the shared failure tracker; the IP lockout itself
          is enforced by ``_security_middleware`` in webapp.py before this route runs
        - Additional reverse proxy rate limiting recommended for production
        """
        # Check if auth is required
        if not settings.require_auth or auth_manager is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Authentication is not enabled",
            )

        # Get client IP for rate limiting
        client_ip = get_client_ip(request)

        # Authenticate credentials
        if not auth_manager.authenticate(credentials.username, credentials.password):
            failure_tracker.record_failure(client_ip)
            failure_count = failure_tracker.get_failure_count(client_ip)
            logger.warning(
                f"[Security] Failed login attempt for user '{credentials.username}' "
                f"from {client_ip} (failure count: {failure_count})"
            )
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid username or password",
            )

        # Reset failure tracker on successful auth
        failure_tracker.reset_failures(client_ip)

        # Create session
        session = session_store.create_session(
            username=credentials.username,
            user_id=credentials.username,  # Use username as user_id for simple auth
            ttl_seconds=settings.session_ttl_seconds,
        )

        # Set session cookie
        response.set_cookie(
            key=settings.cookie_name,
            value=session.session_id,
            max_age=settings.session_ttl_seconds,
            httponly=True,
            secure=settings.cookie_secure,
            samesite=settings.cookie_samesite,
            path="/",
        )

        logger.info(f"[Security] User '{credentials.username}' logged in from {client_ip}")

        return LoginResponse(success=True, message="Login successful")

    @router.get("/me", response_model=UserInfo)
    async def get_current_user(
        request: Request,
    ) -> UserInfo:
        """Get current authenticated user info.

        Returns user information if session is valid, 401 otherwise.
        """
        if not settings.require_auth:
            # If auth is disabled, return a default user
            return UserInfo(username="anonymous", user_id="anonymous")

        # Get session cookie
        session_id = request.cookies.get(settings.cookie_name)
        if not session_id:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Not authenticated",
            )

        # Validate session
        session = session_store.get_session(
            session_id,
            idle_timeout=settings.session_idle_timeout_seconds,
        )

        if session is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Session expired or invalid",
            )

        return UserInfo(
            username=session.username,
            user_id=session.user_id,
        )

    @router.post("/logout", response_model=LogoutResponse)
    async def logout(
        request: Request,
        response: Response,
    ) -> LogoutResponse:
        """Logout current user and destroy session."""
        # Get session cookie
        session_id = request.cookies.get(settings.cookie_name)

        if session_id:
            # Delete session from store
            session_store.delete_session(session_id)

        # Clear cookie
        response.delete_cookie(
            key=settings.cookie_name,
            path="/",
            httponly=True,
            secure=settings.cookie_secure,
            samesite=settings.cookie_samesite,
        )

        return LogoutResponse(success=True, message="Logged out successfully")

    return router


async def get_current_session(request: Request) -> Session:
    """Dependency to get current session.

    Validates session cookie and returns Session object.
    Raises 401 if not authenticated.

    Args:
        request: FastAPI request object

    Returns:
        Valid Session object

    Raises:
        HTTPException: If not authenticated or session invalid
    """
    settings = get_settings()

    # If auth is disabled, create a dummy session
    if not settings.require_auth:
        import time

        from ..session import Session

        return Session(
            session_id="anonymous",
            user_id="anonymous",
            username="anonymous",
            created_at=time.time(),
            last_accessed_at=time.time(),
            expires_at=time.time() + 86400,
        )

    # Get session cookie
    session_id = request.cookies.get(settings.cookie_name)
    if not session_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated - session cookie missing",
        )

    # Validate session
    session_store = get_session_store()
    session = session_store.get_session(
        session_id,
        idle_timeout=settings.session_idle_timeout_seconds,
    )

    if session is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Session expired or invalid",
        )

    return session


# Type alias for dependency injection
CurrentSession = Annotated[Session, Depends(get_current_session)]
