"""Redis-backed fixed-window limits for expensive public API operations."""

from __future__ import annotations

from dataclasses import dataclass


class RateLimitExceededError(RuntimeError):
    """Raised when an identifier has exhausted a configured request window."""

    def __init__(self, retry_after_seconds: int) -> None:
        self.retry_after_seconds = retry_after_seconds
        super().__init__("Request limit exceeded; retry later")


class RateLimitUnavailableError(RuntimeError):
    """Raised when rate-limit state cannot be read or updated."""


@dataclass(frozen=True)
class RateLimitPolicy:
    limit: int
    window_seconds: int


async def enforce_rate_limit(redis_client, key: str, policy: RateLimitPolicy) -> None:
    """Consume one fixed-window request slot or raise a safe, retryable error."""
    try:
        count = await redis_client.incr(key)
        ttl = await redis_client.ttl(key)
        if ttl < 0:
            await redis_client.expire(key, policy.window_seconds)
            ttl = policy.window_seconds
    except RateLimitExceededError:
        raise
    except Exception as exc:
        raise RateLimitUnavailableError("Rate-limit storage is unavailable") from exc

    if count > policy.limit:
        raise RateLimitExceededError(max(ttl, 1))
