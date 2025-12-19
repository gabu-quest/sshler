"""Authentication endpoints for session-based auth.

Provides /auth/login, /auth/me, and /auth/logout endpoints.
"""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel

from ..auth import AuthManager
from ..session import Session, get_session_store
from ..settings import get_settings
from .rate_limiting import rate_limit_login

logger = logging.getLogger(__name__)


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
    failure_tracker: object,
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
        - IP-based lockout: 5 failed attempts = 5 minute lockout (failure tracker)
        - Additional reverse proxy rate limiting recommended for production
        """
        # Check if auth is required
        if not settings.require_auth or auth_manager is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Authentication is not enabled",
            )

        # Get client IP for rate limiting
        client_ip = request.client.host if request.client else "unknown"

        # Check if IP is locked out
        if failure_tracker.is_locked_out(client_ip):
            remaining = failure_tracker.get_lockout_remaining(client_ip)
            logger.warning(f"[Security] Login attempt from locked out IP: {client_ip}")
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"Too many failed login attempts. Try again in {remaining} seconds.",
                headers={"Retry-After": str(remaining)},
            )

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
        from ..session import Session
        import time

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
