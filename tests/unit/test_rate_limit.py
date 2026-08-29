"""B15: check_rate_limit's own counting logic - real Redis, no HTTP/
middleware involved. See tests/route/test_rate_limit_middleware.py for the
real, end-to-end proof the middleware itself returns 429 when tripped."""

import uuid

import pytest
import pytest_asyncio
from redis.asyncio import Redis as AsyncRedis
from tenderos_api.rate_limit import check_rate_limit, client_key
from tenderos_backend.core.config import settings


@pytest_asyncio.fixture
async def redis_client():
    client = AsyncRedis.from_url(settings.redis_url)
    yield client
    await client.aclose()


def _key() -> str:
    return f"test:ratelimit:{uuid.uuid4()}"


@pytest.mark.asyncio
async def test_requests_within_the_limit_are_allowed(redis_client):
    key = _key()
    for _ in range(3):
        allowed, retry_after = await check_rate_limit(redis_client, key, limit=3, window_seconds=60)
        assert allowed is True
        assert retry_after == 0
    await redis_client.delete(key)


@pytest.mark.asyncio
async def test_the_request_that_exceeds_the_limit_is_rejected(redis_client):
    key = _key()
    for _ in range(3):
        allowed, _ = await check_rate_limit(redis_client, key, limit=3, window_seconds=60)
        assert allowed is True

    allowed, retry_after = await check_rate_limit(redis_client, key, limit=3, window_seconds=60)

    assert allowed is False
    assert 0 < retry_after <= 60
    await redis_client.delete(key)


@pytest.mark.asyncio
async def test_a_new_window_resets_the_count(redis_client):
    key = _key()
    for _ in range(2):
        allowed, _ = await check_rate_limit(redis_client, key, limit=2, window_seconds=1)
        assert allowed is True
    blocked, _ = await check_rate_limit(redis_client, key, limit=2, window_seconds=1)
    assert blocked is False

    # Real TTL expiry, not simulated - the key this project's own fixed-
    # window design relies on for "a new window" is Redis's own EXPIRE,
    # not application-level bookkeeping.
    await redis_client.expire(key, 0)

    allowed, _ = await check_rate_limit(redis_client, key, limit=2, window_seconds=1)
    assert allowed is True
    await redis_client.delete(key)


@pytest.mark.asyncio
async def test_separate_keys_do_not_share_a_counter(redis_client):
    key_a, key_b = _key(), _key()
    for _ in range(3):
        allowed, _ = await check_rate_limit(redis_client, key_a, limit=3, window_seconds=60)
        assert allowed is True

    # key_a is now exhausted - key_b, a genuinely different identity
    # (a different client IP in the real middleware), must be unaffected.
    allowed, _ = await check_rate_limit(redis_client, key_b, limit=3, window_seconds=60)

    assert allowed is True
    await redis_client.delete(key_a)
    await redis_client.delete(key_b)


@pytest.mark.asyncio
async def test_the_first_increment_atomically_sets_a_ttl(redis_client):
    """Regression: INCR then EXPIRE as two separate round-trips left a real
    gap - a process killed between them leaves the key permanently without
    a TTL. Now one atomic Lua script (_INCR_WITH_EXPIRE_SCRIPT). A unit
    test cannot literally simulate a mid-script crash (that's exactly the
    interleaving atomicity rules out), but it can confirm the one
    observable property that matters: right after the very first call, the
    key already carries a real TTL, not "EXPIRE hasn't run yet"."""
    key = _key()
    await check_rate_limit(redis_client, key, limit=5, window_seconds=60)

    ttl = await redis_client.ttl(key)
    assert 0 < ttl <= 60
    await redis_client.delete(key)


def test_client_key_falls_back_when_no_client_info_is_present():
    assert client_key("203.0.113.5") == "203.0.113.5"
    assert client_key(None) == "unknown"
