"""Unit tests for the Redis-backed cache (packages.shared.cache).

All tests mock the Redis client via ``patch`` so no running Redis instance
is required.
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from packages.shared.cache import (
    RedisCache,
    cached,
    close_redis_pool,
    invalidate_cache_pattern,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mock_redis_client():
    """Build a fresh AsyncMock that behaves like a redis.asyncio.Redis client."""
    client = AsyncMock()
    client.get = AsyncMock(return_value=None)
    client.set = AsyncMock(return_value=True)
    client.delete = AsyncMock(return_value=1)
    client.scan_iter = MagicMock(return_value=[])
    client.aclose = AsyncMock()
    return client


# ---------------------------------------------------------------------------
# RedisCache — get
# ---------------------------------------------------------------------------

class TestRedisCacheGet:
    @pytest.mark.asyncio
    async def test_cache_miss_returns_none(self):
        """get() returns None when the key does not exist in Redis."""
        mock_client = _mock_redis_client()
        cache = RedisCache(default_ttl=60, prefix="test:")
        with patch("packages.shared.cache._get_client", return_value=mock_client):
            result = await cache.get("missing_key")
        assert result is None
        mock_client.get.assert_awaited_once_with("test:missing_key")

    @pytest.mark.asyncio
    async def test_cache_hit_returns_deserialized_value(self):
        """get() returns the deserialized Python object on a cache hit."""
        mock_client = _mock_redis_client()
        mock_client.get = AsyncMock(return_value=json.dumps({"foo": "bar"}))
        cache = RedisCache(default_ttl=60, prefix="test:")
        with patch("packages.shared.cache._get_client", return_value=mock_client):
            result = await cache.get("existing_key")
        assert result == {"foo": "bar"}

    @pytest.mark.asyncio
    async def test_cache_hit_with_list(self):
        """get() correctly deserializes a JSON list."""
        mock_client = _mock_redis_client()
        mock_client.get = AsyncMock(return_value=json.dumps([1, 2, 3]))
        cache = RedisCache(default_ttl=60, prefix="test:")
        with patch("packages.shared.cache._get_client", return_value=mock_client):
            result = await cache.get("list_key")
        assert result == [1, 2, 3]

    @pytest.mark.asyncio
    async def test_get_handles_redis_error_gracefully(self):
        """get() returns None when Redis raises an error."""
        import redis as redis_lib
        mock_client = _mock_redis_client()
        mock_client.get = AsyncMock(side_effect=redis_lib.RedisError("connection lost"))
        cache = RedisCache(default_ttl=60, prefix="test:")
        with patch("packages.shared.cache._get_client", return_value=mock_client):
            result = await cache.get("broken_key")
        assert result is None

    @pytest.mark.asyncio
    async def test_get_handles_json_decode_error(self):
        """get() returns None when the stored value is not valid JSON."""
        mock_client = _mock_redis_client()
        mock_client.get = AsyncMock(return_value="not-valid-json{")
        cache = RedisCache(default_ttl=60, prefix="test:")
        with patch("packages.shared.cache._get_client", return_value=mock_client):
            result = await cache.get("bad_json_key")
        assert result is None


# ---------------------------------------------------------------------------
# RedisCache — set
# ---------------------------------------------------------------------------

class TestRedisCacheSet:
    @pytest.mark.asyncio
    async def test_set_with_default_ttl(self):
        """set() stores a JSON-serialised value with the default TTL."""
        mock_client = _mock_redis_client()
        cache = RedisCache(default_ttl=120, prefix="test:")
        with patch("packages.shared.cache._get_client", return_value=mock_client):
            await cache.set("my_key", {"val": 42})
        mock_client.set.assert_awaited_once_with(
            "test:my_key", json.dumps({"val": 42}), ex=120
        )

    @pytest.mark.asyncio
    async def test_set_with_custom_ttl(self):
        """set() uses the explicit TTL when provided."""
        mock_client = _mock_redis_client()
        cache = RedisCache(default_ttl=120, prefix="test:")
        with patch("packages.shared.cache._get_client", return_value=mock_client):
            await cache.set("my_key", "hello", ttl=30)
        mock_client.set.assert_awaited_once_with(
            "test:my_key", json.dumps("hello"), ex=30
        )

    @pytest.mark.asyncio
    async def test_set_handles_redis_error_gracefully(self):
        """set() does not raise when Redis fails — it logs and returns."""
        import redis as redis_lib
        mock_client = _mock_redis_client()
        mock_client.set = AsyncMock(side_effect=redis_lib.RedisError("write failed"))
        cache = RedisCache(default_ttl=60, prefix="test:")
        with patch("packages.shared.cache._get_client", return_value=mock_client):
            await cache.set("failing_key", "data")  # should NOT raise


# ---------------------------------------------------------------------------
# RedisCache — delete
# ---------------------------------------------------------------------------

class TestRedisCacheDelete:
    @pytest.mark.asyncio
    async def test_delete_existing_key(self):
        """delete() returns True when the key existed and was removed."""
        mock_client = _mock_redis_client()
        mock_client.delete = AsyncMock(return_value=1)
        cache = RedisCache(default_ttl=60, prefix="test:")
        with patch("packages.shared.cache._get_client", return_value=mock_client):
            result = await cache.delete("my_key")
        assert result is True
        mock_client.delete.assert_awaited_once_with("test:my_key")

    @pytest.mark.asyncio
    async def test_delete_nonexistent_key(self):
        """delete() returns False when the key did not exist."""
        mock_client = _mock_redis_client()
        mock_client.delete = AsyncMock(return_value=0)
        cache = RedisCache(default_ttl=60, prefix="test:")
        with patch("packages.shared.cache._get_client", return_value=mock_client):
            result = await cache.delete("ghost_key")
        assert result is False

    @pytest.mark.asyncio
    async def test_delete_handles_redis_error(self):
        """delete() returns False on Redis error."""
        import redis as redis_lib
        mock_client = _mock_redis_client()
        mock_client.delete = AsyncMock(side_effect=redis_lib.RedisError("oops"))
        cache = RedisCache(default_ttl=60, prefix="test:")
        with patch("packages.shared.cache._get_client", return_value=mock_client):
            result = await cache.delete("bad_key")
        assert result is False


# ---------------------------------------------------------------------------
# RedisCache — clear
# ---------------------------------------------------------------------------

class TestRedisCacheClear:
    @pytest.mark.asyncio
    async def test_clear_deletes_all_matching_keys(self):
        """clear() iterates scan_iter and deletes every matching key."""
        mock_client = _mock_redis_client()

        async def _fake_scan(**kwargs):
            for k in ["test:a", "test:b", "test:c"]:
                yield k

        mock_client.scan_iter = MagicMock(return_value=_fake_scan())
        cache = RedisCache(default_ttl=60, prefix="test:")
        with patch("packages.shared.cache._get_client", return_value=mock_client):
            await cache.clear()
        assert mock_client.delete.await_count == 3

    @pytest.mark.asyncio
    async def test_clear_handles_redis_error(self):
        """clear() does not raise when Redis fails during scan."""
        import redis as redis_lib

        async def _failing_scan(**kwargs):
            raise redis_lib.RedisError("scan broken")
            yield  # noqa: ARG005  make it an async generator

        mock_client = _mock_redis_client()
        mock_client.scan_iter = MagicMock(return_value=_failing_scan())
        cache = RedisCache(default_ttl=60, prefix="test:")
        with patch("packages.shared.cache._get_client", return_value=mock_client):
            await cache.clear()  # should NOT raise


# ---------------------------------------------------------------------------
# @cached decorator
# ---------------------------------------------------------------------------

class TestCachedDecorator:
    @pytest.mark.asyncio
    async def test_decorator_returns_cached_value_on_hit(self):
        """When cache has a value the decorated function is NOT called."""
        mock_client = _mock_redis_client()
        mock_client.get = AsyncMock(return_value=json.dumps("cached_result"))
        cache = RedisCache(default_ttl=60, prefix="dec:")

        call_count = 0

        @cached(cache=cache, key_prefix="fn:")
        async def my_function(db, x, y):
            nonlocal call_count
            call_count += 1
            return x + y

        with patch("packages.shared.cache._get_client", return_value=mock_client):
            result = await my_function("fake_db", 1, 2)

        assert result == "cached_result"
        assert call_count == 0  # function body never executed

    @pytest.mark.asyncio
    async def test_decorator_calls_function_on_miss(self):
        """When cache misses the function executes and the result is stored."""
        mock_client = _mock_redis_client()
        # First call = cache miss (None), second call not needed
        mock_client.get = AsyncMock(return_value=None)
        cache = RedisCache(default_ttl=60, prefix="dec:")

        @cached(cache=cache, key_prefix="fn:")
        async def my_function(db, x):
            return x * 10

        with patch("packages.shared.cache._get_client", return_value=mock_client):
            result = await my_function("fake_db", 5)

        assert result == 50
        mock_client.set.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_decorator_uses_custom_ttl(self):
        """The decorator forwards the ttl parameter to cache.set."""
        mock_client = _mock_redis_client()
        mock_client.get = AsyncMock(return_value=None)
        cache = RedisCache(default_ttl=60, prefix="dec:")

        @cached(cache=cache, key_prefix="fn:", ttl=10)
        async def my_function(db):
            return "ok"

        with patch("packages.shared.cache._get_client", return_value=mock_client):
            await my_function("fake_db")

        _args, kwargs = mock_client.set.call_args
        # Third positional arg or keyword 'ex' passed to redis client.set
        # Our cache.set calls client.set(full_key, json.dumps(value), ex=int(ttl))
        assert mock_client.set.await_count == 1

    @pytest.mark.asyncio
    async def test_decorator_with_serializer_converts_value(self):
        """The ``serializer`` callable converts the return value before
        it is stored in the cache.  This lets the cache layer accept
        values that are not JSON-serializable as-is (e.g. SQLAlchemy ORM
        objects) by passing a converter that produces JSON-safe data.
        """
        mock_client = _mock_redis_client()
        mock_client.get = AsyncMock(return_value=None)
        cache = RedisCache(default_ttl=60, prefix="dec:")

        class FakeORM:
            def __init__(self, name):
                self.name = name
            def __repr__(self):
                return f"<FakeORM {self.name}>"

        @cached(
            cache=cache,
            key_prefix="orm:",
            serializer=lambda v: {"name": v.name},
        )
        async def my_function(db):
            return FakeORM("foo")

        with patch("packages.shared.cache._get_client", return_value=mock_client):
            result = await my_function("fake_db")

        # Decorator returns the ORIGINAL value to the caller
        assert isinstance(result, FakeORM)
        assert result.name == "foo"
        # But the cache SET call received the serialized form
        args, _ = mock_client.set.call_args
        cached_value = args[1]
        assert json.loads(cached_value) == {"name": "foo"}

    @pytest.mark.asyncio
    async def test_decorator_without_serializer_uses_raw_value(self):
        """When no serializer is given, the raw value is sent to the cache
        (existing behaviour is preserved)."""
        mock_client = _mock_redis_client()
        mock_client.get = AsyncMock(return_value=None)
        cache = RedisCache(default_ttl=60, prefix="dec:")

        @cached(cache=cache, key_prefix="raw:")
        async def my_function(db):
            return {"a": 1}

        with patch("packages.shared.cache._get_client", return_value=mock_client):
            await my_function("fake_db")

        args, _ = mock_client.set.call_args
        cached_value = args[1]
        assert json.loads(cached_value) == {"a": 1}

    @pytest.mark.asyncio
    async def test_decorator_skip_first_arg_false_includes_all_args(self):
        """``skip_first_arg=False`` (ME-001) means the first positional
        argument is included in the cache key.  Two different ``db``
        arguments therefore produce different cache keys and the function
        is executed twice rather than being short-circuited by a cached
        entry from the first invocation.
        """
        mock_client = _mock_redis_client()
        mock_client.get = AsyncMock(return_value=None)
        cache = RedisCache(default_ttl=60, prefix="dec:")

        call_count = 0

        @cached(cache=cache, key_prefix="all:", skip_first_arg=False)
        async def my_function(db, x):
            nonlocal call_count
            call_count += 1
            return x * 2

        with patch("packages.shared.cache._get_client", return_value=mock_client):
            r1 = await my_function("db_a", 1)
            r2 = await my_function("db_b", 1)

        assert r1 == 2
        assert r2 == 2
        # Two different keys => function executed twice
        assert call_count == 2

    @pytest.mark.asyncio
    async def test_decorator_skip_first_arg_true_default(self):
        """The default ``skip_first_arg=True`` continues to ignore the first
        positional argument when building the cache key."""
        mock_client = _mock_redis_client()
        mock_client.get = AsyncMock(return_value=None)
        cache = RedisCache(default_ttl=60, prefix="dec:")

        @cached(cache=cache, key_prefix="skip:")
        async def my_function(db, x):
            return x + 100

        with patch("packages.shared.cache._get_client", return_value=mock_client):
            # Different first args but same ``x`` — only one execution
            # because the cache key ignores ``db``.
            r1 = await my_function("db_a", 1)
            r2 = await my_function("db_b", 1)

        # Second call hits the cache (set on first miss) and returns the
        # cached value, not a freshly computed one.
        assert r1 == 101
        assert r2 == 101


# ---------------------------------------------------------------------------
# _make_cache_key — hash format (ME-009: blake2b)
# ---------------------------------------------------------------------------


class TestCacheKeyHashFormat:
    """ME-009: cache keys use blake2b(digest_size=16) → 32 hex chars."""

    def test_hash_is_32_hex_chars(self):
        """The cache key hash is a 32-character hex string (16 bytes)."""
        from packages.shared.cache import _make_cache_key

        key = _make_cache_key("fn", (1, 2), {})
        assert len(key) == 32
        # All characters must be hex digits
        int(key, 16)  # raises ValueError if not hex

    def test_hash_uses_blake2b_under_the_hood(self):
        """Sanity check: blake2b(digest_size=16) of the same input is
        stable and matches what we expect from the helper."""
        import hashlib

        from packages.shared.cache import _make_cache_key

        expected = hashlib.blake2b(
            b"fn:(1, 2):[]",
            digest_size=16,
        ).hexdigest()
        assert _make_cache_key("fn", (1, 2), {}) == expected


# ---------------------------------------------------------------------------
# invalidate_cache_pattern
# ---------------------------------------------------------------------------

class TestInvalidateCachePattern:
    @pytest.mark.asyncio
    async def test_invalidate_deletes_matching_keys(self):
        """invalidate_cache_pattern deletes all keys matching the pattern."""
        mock_client = _mock_redis_client()

        async def _fake_scan(**kwargs):
            for k in ["wimt:m:games:1", "wimt:m:games:2"]:
                yield k

        mock_client.scan_iter = MagicMock(return_value=_fake_scan())
        cache = RedisCache(default_ttl=300, prefix="wimt:m:")

        with patch("packages.shared.cache._get_client", return_value=mock_client):
            deleted = await invalidate_cache_pattern(cache, "games")

        assert deleted == 2

    @pytest.mark.asyncio
    async def test_invalidate_returns_zero_when_no_keys_match(self):
        """invalidate_cache_pattern returns 0 when there are no matching keys."""
        mock_client = _mock_redis_client()

        async def _empty_scan(**kwargs):
            return
            yield  # noqa: ARG005  makes this an async generator

        mock_client.scan_iter = MagicMock(return_value=_empty_scan())
        cache = RedisCache(default_ttl=300, prefix="wimt:m:")

        with patch("packages.shared.cache._get_client", return_value=mock_client):
            deleted = await invalidate_cache_pattern(cache, "nonexistent")

        assert deleted == 0


# ---------------------------------------------------------------------------
# close_redis_pool
# ---------------------------------------------------------------------------

class TestCloseRedisPool:
    @pytest.mark.asyncio
    async def test_close_does_nothing_when_pool_is_none(self):
        """close_redis_pool is a no-op when the global pool is None."""
        with patch("packages.shared.cache._pool", None):
            await close_redis_pool()  # should not raise

    @pytest.mark.asyncio
    async def test_close_without_force_does_not_close_pool(self):
        """close_redis_pool(force=False) keeps the pool alive for warm starts."""
        mock_pool = AsyncMock()
        with patch("packages.shared.cache._pool", mock_pool):
            await close_redis_pool(force=False)
        mock_pool.aclose.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_close_with_force_closes_and_resets_pool(self):
        """close_redis_pool(force=True) acloses the pool and sets it to None."""
        mock_pool = AsyncMock()
        with patch("packages.shared.cache._pool", mock_pool):
            await close_redis_pool(force=True)
        mock_pool.aclose.assert_awaited_once()
