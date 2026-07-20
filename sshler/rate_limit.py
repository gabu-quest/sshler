"""Simple rate limiting middleware using token bucket algorithm."""

from __future__ import annotations

import time
from collections import defaultdict
from dataclasses import dataclass, field


@dataclass
class TokenBucket:
    """Token bucket for rate limiting."""

    capacity: int
    refill_rate: float  # tokens per second
    tokens: float = field(init=False)
    last_refill: float = field(init=False)

    def __post_init__(self):
        self.tokens = float(self.capacity)
        self.last_refill = time.time()

    def consume(self, tokens: int = 1) -> bool:
        """Try to consume tokens. Returns True if allowed, False if rate limited."""
        self._refill()

        if self.tokens >= tokens:
            self.tokens -= tokens
            return True
        return False

    def _refill(self):
        """Refill tokens based on time passed."""
        now = time.time()
        elapsed = now - self.last_refill

        # Add tokens based on time elapsed
        self.tokens = min(
            self.capacity,
            self.tokens + (elapsed * self.refill_rate)
        )
        self.last_refill = now


class RateLimiter:
    """Simple rate limiter using token bucket algorithm."""

    def __init__(
        self,
        rate: int,  # requests allowed
        per: int,  # per this many seconds
        capacity_multiplier: float = 1.5,
    ):
        """Initialize rate limiter.

        Args:
            rate: Number of requests allowed
            per: Time period in seconds
            capacity_multiplier: Bucket capacity = rate * multiplier (allows bursts)
        """
        self.rate = rate
        self.per = per
        self.refill_rate = rate / per
        self.capacity = int(rate * capacity_multiplier)
        self._buckets: dict[str, TokenBucket] = {}
        self._cleanup_interval = 300  # cleanup every 5 minutes
        self._last_cleanup = time.time()

    def check(self, key: str) -> bool:
        """Check if request is allowed for the given key.

        Args:
            key: Unique identifier (e.g., IP address, user ID)

        Returns:
            True if request is allowed, False if rate limited
        """
        # Periodic cleanup of old buckets
        self._maybe_cleanup()

        # Get or create bucket for this key
        if key not in self._buckets:
            self._buckets[key] = TokenBucket(
                capacity=self.capacity,
                refill_rate=self.refill_rate,
            )

        return self._buckets[key].consume()

    def _maybe_cleanup(self):
        """Remove stale buckets to prevent memory growth."""
        now = time.time()
        if now - self._last_cleanup < self._cleanup_interval:
            return

        # Remove buckets that haven't been used in 10 minutes
        cutoff = now - 600
        stale_keys = [
            key for key, bucket in self._buckets.items()
            if bucket.last_refill < cutoff
        ]

        for key in stale_keys:
            del self._buckets[key]

        self._last_cleanup = now

    def reset(self, key: str):
        """Reset rate limit for a specific key."""
        if key in self._buckets:
            del self._buckets[key]


# Global rate limiters for different endpoints
_rate_limiters: dict[str, RateLimiter] = {}


def get_rate_limiter(
    name: str,
    rate: int,
    per: int,
    capacity_multiplier: float = 1.5,
) -> RateLimiter:
    """Get or create a rate limiter.

    Args:
        name: Unique name for this rate limiter
        rate: Requests allowed
        per: Time period in seconds
        capacity_multiplier: Bucket capacity multiplier

    Returns:
        RateLimiter instance
    """
    if name not in _rate_limiters:
        _rate_limiters[name] = RateLimiter(rate, per, capacity_multiplier)
    return _rate_limiters[name]
