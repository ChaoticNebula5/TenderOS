"""B15: Redis-backed rate limiting.

Fixed-window counters (atomic INCR, TTL set only on the first increment in
a window) - not sliding-window or token-bucket, both a real, more precise
alternative available later at the cost of extra Redis round-trips per
request. Fixed-window's known imprecision (a burst of close to 2x the
limit is possible right at a window boundary - a client could send up to
`limit` requests in the last instant of one window and another `limit` in
the first instant of the next) is an accepted tradeoff for a first real
rate limiter, not silently assumed away.

Keyed on client IP (request.client.host) for both the global and login-
specific limits. X-Forwarded-For is deliberately NOT trusted - nothing in
this project's own docker-compose defines a reverse proxy in front of the
API in any configuration, so honoring that header would let any caller
trivially bypass IP-based limiting by setting it themselves. A real
deployment that puts a reverse proxy/load balancer in front of this API
needs to revisit this (trust X-Forwarded-For only from a known-trusted
proxy hop) - named here as a real gap, not silently assumed away.

See tenderos_api.config.Settings.rate_limit_enabled's own comment for why
the test suite disables this entirely rather than working around it
per-test, and see this file's own test module
(tests/route/test_rate_limit.py) for how the real limiting logic still
gets genuine, direct test coverage with the flag deliberately re-enabled.
"""

from redis.asyncio import Redis as AsyncRedis

GLOBAL_LIMIT = 100
GLOBAL_WINDOW_SECONDS = 60
LOGIN_LIMIT = 5
LOGIN_WINDOW_SECONDS = 60


async def check_rate_limit(
    redis_client: AsyncRedis, key: str, limit: int, window_seconds: int
) -> tuple[bool, int]:
    """Returns (allowed, retry_after_seconds). retry_after_seconds is only
    meaningful - and only computed, one extra Redis round-trip - when
    allowed is False, since that's the only time a caller needs it.
    """
    current = await redis_client.incr(key)
    if current == 1:
        await redis_client.expire(key, window_seconds)
    if current <= limit:
        return True, 0
    ttl = await redis_client.ttl(key)
    # A negative ttl (key expired between INCR and TTL, or has no
    # expiry - shouldn't happen given the expire() call above, but a
    # negative Retry-After would be a nonsensical value to hand a client)
    # falls back to the full window rather than a value that could
    # confuse a well-behaved client into retrying immediately.
    return False, ttl if ttl and ttl > 0 else window_seconds


def client_key(request_client_host: str | None) -> str:
    return request_client_host or "unknown"
