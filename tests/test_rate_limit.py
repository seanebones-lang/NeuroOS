"""Rate-limit behavior independent of a running Redis server."""

import pytest

from neuro_os.rate_limit import (
    RateLimitExceededError,
    RateLimitPolicy,
    RateLimitUnavailableError,
    enforce_rate_limit,
)

WINDOW_SECONDS = 60


class FakeRedis:
    def __init__(self) -> None:
        self.counts: dict[str, int] = {}
        self.expirations: dict[str, int] = {}

    async def incr(self, key: str) -> int:
        self.counts[key] = self.counts.get(key, 0) + 1
        return self.counts[key]

    async def ttl(self, key: str) -> int:
        return self.expirations.get(key, -1)

    async def expire(self, key: str, seconds: int) -> None:
        self.expirations[key] = seconds


class UnavailableRedis:
    async def incr(self, _key: str) -> int:
        raise ConnectionError("redis unavailable")


@pytest.mark.asyncio
async def test_rate_limit_sets_a_window_and_rejects_excess_requests():
    redis = FakeRedis()
    policy = RateLimitPolicy(limit=2, window_seconds=WINDOW_SECONDS)

    await enforce_rate_limit(redis, "rate-limit:test:subject", policy)
    await enforce_rate_limit(redis, "rate-limit:test:subject", policy)

    with pytest.raises(RateLimitExceededError) as error:
        await enforce_rate_limit(redis, "rate-limit:test:subject", policy)

    assert redis.expirations["rate-limit:test:subject"] == WINDOW_SECONDS
    assert error.value.retry_after_seconds == WINDOW_SECONDS


@pytest.mark.asyncio
async def test_rate_limit_fails_closed_when_redis_is_unavailable():
    with pytest.raises(RateLimitUnavailableError, match="unavailable"):
        await enforce_rate_limit(
            UnavailableRedis(),
            "rate-limit:test:subject",
            RateLimitPolicy(1, WINDOW_SECONDS),
        )
