"""Tests for rate limiting functionality."""

from __future__ import annotations

import time

import pytest

from sshler.rate_limit import RateLimiter, TokenBucket, get_rate_limiter


class TestTokenBucket:
    """Test TokenBucket implementation."""

    def test_initial_state(self):
        """Test that bucket starts with full capacity."""
        bucket = TokenBucket(capacity=10, refill_rate=1.0)
        assert bucket.tokens == 10.0
        assert bucket.capacity == 10
        assert bucket.refill_rate == 1.0

    def test_consume_tokens_allowed(self):
        """Test that consuming tokens when available succeeds."""
        bucket = TokenBucket(capacity=10, refill_rate=1.0)

        # Should allow consuming tokens
        assert bucket.consume(1) is True
        # Allow small refill from timing variance
        assert 8.9 <= bucket.tokens <= 9.1

        assert bucket.consume(5) is True
        # Allow small refill from timing variance
        assert 3.9 <= bucket.tokens <= 4.1

    def test_consume_tokens_rate_limited(self):
        """Test that consuming more tokens than available fails."""
        bucket = TokenBucket(capacity=5, refill_rate=1.0)

        # Consume all tokens
        assert bucket.consume(5) is True
        # Allow small refill from timing variance
        assert bucket.tokens < 0.1

        # Next request should be rate limited
        assert bucket.consume(1) is False
        # Tokens should still be near zero (minor refill possible)
        assert bucket.tokens < 0.1

    def test_token_refill(self):
        """Test that tokens refill over time."""
        bucket = TokenBucket(capacity=10, refill_rate=2.0)  # 2 tokens per second

        # Consume all tokens
        bucket.consume(10)
        assert bucket.tokens == 0.0

        # Wait 1 second - should refill 2 tokens
        time.sleep(1.1)
        bucket._refill()

        # Should have approximately 2 tokens (allow some timing variance)
        assert 1.8 <= bucket.tokens <= 2.5

        # Should allow consuming 2 tokens
        assert bucket.consume(2) is True

    def test_refill_respects_capacity(self):
        """Test that refilling doesn't exceed capacity."""
        bucket = TokenBucket(capacity=5, refill_rate=10.0)  # Fast refill

        # Wait 2 seconds - would refill 20 tokens if uncapped
        time.sleep(2.1)
        bucket._refill()

        # Should be capped at capacity
        assert bucket.tokens == 5.0

        # Should not allow consuming more than capacity
        assert bucket.consume(6) is False


class TestRateLimiter:
    """Test RateLimiter implementation."""

    def test_allows_requests_within_limit(self):
        """Test that requests within limit are allowed."""
        limiter = RateLimiter(rate=5, per=60)  # 5 requests per minute

        # Should allow 5 requests
        for i in range(5):
            assert limiter.check("user1") is True, f"Request {i+1} should be allowed"

    def test_blocks_requests_exceeding_limit(self):
        """Test that requests exceeding limit are blocked."""
        # rate=3, capacity=3*1.5=4.5, so can burst 4 requests initially
        limiter = RateLimiter(rate=3, per=60, capacity_multiplier=1.0)  # No burst

        # Allow 3 requests (the rate limit)
        for _ in range(3):
            assert limiter.check("user1") is True

        # 4th request should be rate limited
        assert limiter.check("user1") is False

    def test_different_keys_independent(self):
        """Test that different keys have independent rate limits."""
        # rate=2, capacity=2*1.0=2 (no burst)
        limiter = RateLimiter(rate=2, per=60, capacity_multiplier=1.0)

        # User1 uses their limit (2 requests)
        assert limiter.check("user1") is True
        assert limiter.check("user1") is True
        assert limiter.check("user1") is False  # Rate limited

        # User2 should still be allowed (independent limit)
        assert limiter.check("user2") is True
        assert limiter.check("user2") is True
        assert limiter.check("user2") is False  # Rate limited

    def test_capacity_multiplier_allows_bursts(self):
        """Test that capacity_multiplier allows burst requests."""
        # rate=2, capacity=2*1.5=3, so can burst 3 requests initially
        limiter = RateLimiter(rate=2, per=60, capacity_multiplier=1.5)

        # Should allow burst of 3 requests (capacity=3)
        assert limiter.check("user1") is True
        assert limiter.check("user1") is True
        assert limiter.check("user1") is True

        # 4th request should be blocked
        assert limiter.check("user1") is False

    def test_refill_allows_more_requests(self):
        """Test that tokens refill over time allowing more requests."""
        limiter = RateLimiter(rate=10, per=1)  # 10 requests per second

        # Use all tokens
        for _ in range(15):  # capacity = 10*1.5 = 15
            limiter.check("user1")

        # Should be rate limited now
        assert limiter.check("user1") is False

        # Wait for refill (0.2 seconds should refill ~2 tokens)
        time.sleep(0.25)

        # Should allow some requests now
        assert limiter.check("user1") is True

    def test_reset_clears_bucket(self):
        """Test that reset clears rate limit for a key."""
        # rate=2, capacity=2*1.0=2 (no burst)
        limiter = RateLimiter(rate=2, per=60, capacity_multiplier=1.0)

        # Use up limit
        limiter.check("user1")
        limiter.check("user1")
        assert limiter.check("user1") is False  # Rate limited

        # Reset
        limiter.reset("user1")

        # Should be allowed again
        assert limiter.check("user1") is True


class TestGetRateLimiter:
    """Test get_rate_limiter function."""

    def test_creates_new_limiter(self):
        """Test that get_rate_limiter creates new limiter."""
        limiter = get_rate_limiter("test_new", rate=10, per=60)

        assert isinstance(limiter, RateLimiter)
        assert limiter.rate == 10
        assert limiter.per == 60

    def test_returns_existing_limiter(self):
        """Test that get_rate_limiter returns existing limiter."""
        limiter1 = get_rate_limiter("test_existing", rate=5, per=60)
        limiter2 = get_rate_limiter("test_existing", rate=10, per=30)  # Different params

        # Should return same instance (original params)
        assert limiter1 is limiter2
        assert limiter1.rate == 5  # Original params preserved
        assert limiter1.per == 60

    def test_different_names_different_limiters(self):
        """Test that different names create different limiters."""
        limiter1 = get_rate_limiter("test_a", rate=10, per=60)
        limiter2 = get_rate_limiter("test_b", rate=10, per=60)

        assert limiter1 is not limiter2

        # Should be independent
        for _ in range(15):  # capacity = 10*1.5 = 15
            limiter1.check("user1")

        # limiter1 should be exhausted
        assert limiter1.check("user1") is False

        # limiter2 should still work
        assert limiter2.check("user1") is True


class TestRateLimitCleanup:
    """Test rate limiter cleanup functionality."""

    def test_cleanup_removes_stale_buckets(self):
        """Test that cleanup removes old buckets."""
        limiter = RateLimiter(rate=10, per=60)
        limiter._cleanup_interval = 0  # Force cleanup on every check

        # Create bucket for user1
        limiter.check("user1")
        assert "user1" in limiter._buckets

        # Manually set last_refill to 11 minutes ago
        limiter._buckets["user1"].last_refill = time.time() - 660

        # Trigger cleanup by checking different user
        limiter.check("user2")

        # user1 bucket should be cleaned up
        assert "user1" not in limiter._buckets
        assert "user2" in limiter._buckets
