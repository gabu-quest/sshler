"""Tests for the token-bucket rate limiter in sshler/rate_limit.py.

Every bucket and limiter here runs on a `FakeClock`, so no test sleeps and every token
count is exact. Time steps are binary-exact (0.25, 0.5, 1.0) so float sums are exact too.
Each test names the mutation it kills.
"""

from __future__ import annotations

import time as real_time

import pytest

from sshler import rate_limit
from sshler.rate_limit import RateLimiter, TokenBucket, get_rate_limiter

START = 1_000_000.0


class FakeClock:
    """A clock callable that only moves when `advance()` is called."""

    def __init__(self, start: float = START) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class FakeTimeModule:
    """Stands in for the `time` module inside sshler.rate_limit (default-clock tests)."""

    def __init__(self, clock: FakeClock) -> None:
        self._clock = clock

    def time(self) -> float:
        return self._clock()

    def __getattr__(self, name: str):
        return getattr(real_time, name)


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def module_clock(monkeypatch: pytest.MonkeyPatch) -> FakeClock:
    """Drives the default clock (`time.time` looked up in sshler.rate_limit)."""
    fake = FakeClock()
    monkeypatch.setattr(rate_limit, "time", FakeTimeModule(fake))
    return fake


class TestTokenBucket:
    def test_initial_state(self, clock: FakeClock):
        """Kills: a bucket that starts empty, and `last_refill` not read from the clock."""
        bucket = TokenBucket(capacity=10, refill_rate=1.0, clock=clock)
        assert bucket.tokens == 10.0
        assert bucket.last_refill == START

    def test_consume_subtracts_exact_tokens(self, clock: FakeClock):
        """Kills: `consume` ignoring its `tokens` argument (subtracting 1 each time)."""
        bucket = TokenBucket(capacity=10, refill_rate=1.0, clock=clock)
        assert bucket.consume(1) is True
        assert bucket.tokens == 9.0
        assert bucket.consume(5) is True
        assert bucket.tokens == 4.0

    def test_refused_consume_takes_nothing(self, clock: FakeClock):
        """Kills: `>=` turned into `>` (the exact-capacity request would be refused), and
        subtracting on a refused request (tokens would go negative)."""
        bucket = TokenBucket(capacity=5, refill_rate=1.0, clock=clock)
        assert bucket.consume(5) is True
        assert bucket.tokens == 0.0
        assert bucket.consume(1) is False
        assert bucket.tokens == 0.0

    def test_refill_adds_elapsed_times_rate(self, clock: FakeClock):
        """Kills: a refill that ignores elapsed time or the refill rate (2 tokens/s)."""
        bucket = TokenBucket(capacity=10, refill_rate=2.0, clock=clock)
        assert bucket.consume(10) is True

        clock.advance(1.0)
        bucket._refill()
        assert bucket.tokens == 2.0
        assert bucket.last_refill == START + 1.0
        assert bucket.consume(2) is True
        assert bucket.consume(1) is False

    def test_refill_is_not_counted_twice(self, clock: FakeClock):
        """Kills: `_refill` not moving `last_refill` forward (the same second would be
        credited again on the second call, giving 4 tokens)."""
        bucket = TokenBucket(capacity=10, refill_rate=2.0, clock=clock)
        bucket.consume(10)
        clock.advance(1.0)
        bucket._refill()
        bucket._refill()
        assert bucket.tokens == 2.0

    def test_partial_token_does_not_admit_a_request(self, clock: FakeClock):
        """Boundary: one tick before a whole token has refilled the request is refused;
        at the whole token it is allowed. Kills: rounding partial tokens up."""
        bucket = TokenBucket(capacity=10, refill_rate=2.0, clock=clock)
        bucket.consume(10)

        clock.advance(0.25)  # 0.5 tokens
        assert bucket.consume(1) is False
        assert bucket.tokens == 0.5

        clock.advance(0.25)  # 1.0 token
        assert bucket.consume(1) is True
        assert bucket.tokens == 0.0

    def test_refill_is_capped_at_capacity(self, clock: FakeClock):
        """Kills: dropping the `min(self.capacity, ...)` cap (2 s at 10/s would give 25)."""
        bucket = TokenBucket(capacity=5, refill_rate=10.0, clock=clock)
        clock.advance(2.0)
        bucket._refill()
        assert bucket.tokens == 5.0
        assert bucket.consume(6) is False
        assert bucket.tokens == 5.0

    def test_default_clock_reads_time_at_call_time(self, module_clock: FakeClock):
        """Production wiring keeps `time.time`, looked up when called, so patching
        `sshler.rate_limit.time` (as tests/test_rate_limit_middleware.py does) drives it.
        Kills: binding the default to `time.time` at import (`clock=time.time`)."""
        bucket = TokenBucket(capacity=4, refill_rate=1.0)
        assert bucket.last_refill == START
        bucket.consume(4)
        module_clock.advance(2.0)
        assert bucket.consume(2) is True
        assert bucket.consume(1) is False


class TestRateLimiter:
    def test_allows_exactly_capacity_then_blocks(self, clock: FakeClock):
        """Kills: a changed default `capacity_multiplier` (5 * 1.5 = 7 requests)."""
        limiter = RateLimiter(rate=5, per=60, clock=clock)
        assert limiter.capacity == 7
        assert [limiter.check("user1") for _ in range(8)] == [True] * 7 + [False]

    def test_no_burst_allows_exactly_rate(self, clock: FakeClock):
        """Kills: ignoring `capacity_multiplier` (1.0 must give exactly `rate`)."""
        limiter = RateLimiter(rate=3, per=60, capacity_multiplier=1.0, clock=clock)
        assert [limiter.check("user1") for _ in range(4)] == [True, True, True, False]

    def test_different_keys_independent(self, clock: FakeClock):
        """Kills: sharing one bucket across keys."""
        limiter = RateLimiter(rate=2, per=60, capacity_multiplier=1.0, clock=clock)
        assert [limiter.check("user1") for _ in range(3)] == [True, True, False]
        assert [limiter.check("user2") for _ in range(3)] == [True, True, False]

    def test_refill_rate_is_rate_over_per(self, clock: FakeClock):
        """10 per 1 s refills 2.5 tokens in 0.25 s: two requests pass, the third does not.
        Kills: `refill_rate = per / rate`, and buckets not getting the limiter's clock
        (they would refill from wall time, which has not moved by 0.25 s)."""
        limiter = RateLimiter(rate=10, per=1, clock=clock)
        assert [limiter.check("user1") for _ in range(16)] == [True] * 15 + [False]

        clock.advance(0.25)
        assert [limiter.check("user1") for _ in range(3)] == [True, True, False]

    def test_reset_clears_bucket(self, clock: FakeClock):
        """Kills: `reset` not deleting the key's bucket."""
        limiter = RateLimiter(rate=2, per=60, capacity_multiplier=1.0, clock=clock)
        assert [limiter.check("user1") for _ in range(3)] == [True, True, False]
        limiter.reset("user1")
        assert [limiter.check("user1") for _ in range(3)] == [True, True, False]


class TestRateLimitCleanup:
    def test_cleanup_removes_buckets_idle_past_600s(self, clock: FakeClock):
        """Boundary: a bucket idle for exactly 600 s stays; at 601 s it is removed.
        Kills: `<` turned into `<=` in the stale check, and dropping the cleanup."""
        limiter = RateLimiter(rate=10, per=60, clock=clock)
        limiter._cleanup_interval = 0  # run cleanup on every check
        limiter.check("user1")

        clock.advance(600)
        limiter.check("user2")
        assert set(limiter._buckets) == {"user1", "user2"}

        clock.advance(1)
        limiter.check("user2")
        assert set(limiter._buckets) == {"user2"}

    def test_cleanup_runs_only_every_300s(self, clock: FakeClock):
        """Boundary: 299 s after the last cleanup it does not run; at 300 s it does.
        Kills: `<` turned into `<=` in the interval check, and a cleanup that never
        updates `_last_cleanup` (it would run at 299 s)."""
        limiter = RateLimiter(rate=10, per=60, clock=clock)
        limiter.check("user1")  # last_refill = START

        clock.advance(400)
        limiter.check("user2")  # cleanup runs; user1 idle 400 s, kept
        assert set(limiter._buckets) == {"user1", "user2"}

        clock.advance(299)  # user1 idle 699 s, but the interval has not elapsed
        limiter.check("user2")
        assert set(limiter._buckets) == {"user1", "user2"}

        clock.advance(1)
        limiter.check("user2")
        assert set(limiter._buckets) == {"user2"}


class TestGetRateLimiter:
    def test_creates_new_limiter(self):
        """Kills: ignoring the `rate`/`per` arguments."""
        limiter = get_rate_limiter("test_new", rate=10, per=60)
        assert (limiter.rate, limiter.per, limiter.capacity) == (10, 60, 15)

    def test_returns_existing_limiter(self):
        """Kills: replacing the limiter on every call (first parameters must win)."""
        limiter1 = get_rate_limiter("test_existing", rate=5, per=60)
        limiter2 = get_rate_limiter("test_existing", rate=10, per=30)
        assert limiter1 is limiter2
        assert (limiter1.rate, limiter1.per) == (5, 60)

    def test_different_names_different_limiters(self, module_clock: FakeClock):
        """Kills: keying the registry on something other than the name."""
        limiter1 = get_rate_limiter("test_a", rate=10, per=60)
        limiter2 = get_rate_limiter("test_b", rate=10, per=60)
        assert limiter1 is not limiter2
        assert [limiter1.check("user1") for _ in range(16)] == [True] * 15 + [False]
        assert limiter2.check("user1") is True
