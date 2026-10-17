"""Fixed-window rate limiter on Redis (INCR + EXPIRE NX in one pipeline).

Keys never contain raw identifiers: callers pass a SHA-256 of the email. Redis failures raise
`ServiceUnavailableError` (fail closed: login is refused rather than left unthrottled).
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from redis import Redis
from redis.exceptions import RedisError

from app.core.errors import ServiceUnavailableError

KEY_PREFIX = "rl:"


@dataclass(frozen=True, slots=True)
class RateLimitResult:
    allowed: bool
    count: int
    limit: int
    retry_after: int


def rate_limit_key(scope: str, identifier: str) -> str:
    digest = hashlib.sha256(identifier.encode("utf-8")).hexdigest()[:32]
    return f"{KEY_PREFIX}{scope}:{digest}"


class FixedWindowRateLimiter:
    def __init__(self, redis_client: Redis) -> None:
        self._redis = redis_client

    def hit(
        self,
        key: str,
        *,
        limit: int,
        window_seconds: int,
        amount: int = 1,
        unavailable_message: str = "Sign-in is temporarily unavailable.",
    ) -> RateLimitResult:
        try:
            pipe = self._redis.pipeline(transaction=True)
            pipe.incrby(key, amount)
            pipe.expire(key, window_seconds, nx=True)
            pipe.ttl(key)
            count, _, ttl = pipe.execute()
        except RedisError as exc:
            raise ServiceUnavailableError(unavailable_message) from exc
        retry_after = int(ttl) if isinstance(ttl, int) and ttl > 0 else window_seconds
        return RateLimitResult(
            allowed=int(count) <= limit, count=int(count), limit=limit, retry_after=retry_after
        )
