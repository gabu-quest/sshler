"""Rate limiting dependencies for FastAPI endpoints."""

from __future__ import annotations

import logging
from typing import Callable

from fastapi import HTTPException, Request, status

from ..rate_limit import get_rate_limiter

logger = logging.getLogger(__name__)


def create_rate_limit_dependency(
    name: str,
    rate: int,
    per: int,
    capacity_multiplier: float = 1.5,
) -> Callable:
    """Create a rate limiting dependency for FastAPI endpoints.

    Args:
        name: Unique name for this rate limiter
        rate: Number of requests allowed
        per: Time period in seconds
        capacity_multiplier: Bucket capacity multiplier (allows bursts)

    Returns:
        FastAPI dependency function that raises 429 if rate limit exceeded

    Example:
        ```python
        rate_limit_upload = create_rate_limit_dependency(
            name="upload",
            rate=10,  # 10 requests
            per=60,   # per 60 seconds (1 minute)
        )

        @router.post("/upload")
        async def upload_file(
            request: Request,
            _: None = Depends(rate_limit_upload),
        ):
            ...
        ```
    """

    async def rate_limit_dependency(request: Request) -> None:
        """Rate limit check dependency.

        Args:
            request: FastAPI request object

        Raises:
            HTTPException: 429 Too Many Requests if rate limit exceeded
        """
        # Get client IP for rate limiting
        client_ip = request.client.host if request.client else "unknown"

        # Get or create rate limiter
        limiter = get_rate_limiter(name, rate, per, capacity_multiplier)

        # Check rate limit
        if not limiter.check(client_ip):
            logger.warning(
                f"[Security] Rate limit exceeded for {name} endpoint from {client_ip}"
            )
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"Rate limit exceeded. Maximum {rate} requests per {per} seconds allowed.",
                headers={"Retry-After": str(per)},
            )

    return rate_limit_dependency


# Pre-configured rate limiters for common endpoints
# These can be used as FastAPI dependencies: Depends(rate_limit_upload)

# File uploads: 10 per minute (prevents abuse)
rate_limit_upload = create_rate_limit_dependency(
    name="api_upload",
    rate=10,
    per=60,
)

# File delete: 20 per minute (less restrictive than upload, but still protected)
rate_limit_delete = create_rate_limit_dependency(
    name="api_delete",
    rate=20,
    per=60,
)

# File write/edit: 30 per minute
rate_limit_write = create_rate_limit_dependency(
    name="api_write",
    rate=30,
    per=60,
)

# Login endpoint: 5 per minute (strict for security)
rate_limit_login = create_rate_limit_dependency(
    name="api_login",
    rate=5,
    per=60,
)

# Terminal creation: 10 per minute
rate_limit_terminal = create_rate_limit_dependency(
    name="api_terminal",
    rate=10,
    per=60,
)

# File copy/move: 20 per minute
rate_limit_file_ops = create_rate_limit_dependency(
    name="api_file_ops",
    rate=20,
    per=60,
)
