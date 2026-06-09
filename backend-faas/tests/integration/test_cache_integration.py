"""Integration tests for Redis caching.

Requires a running Redis instance (start with scripts/dev.sh).
All tests skip gracefully when Redis is not available.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("ALERT_ENABLED", "false")

import asyncio

import pytest

from packages.shared.cache import short_cache, medium_cache, long_cache, close_redis_pool


# Skip entire module if no Redis available
pytestmark = pytest.mark.skipif(
    "not config.getoption('--run-integration', default=False)",
    reason="Integration tests require --run-integration flag and running Redis",
)


@pytest.fixture(autouse=True)
async def _cleanup_redis():
    """Force-close Redis pool after every test to avoid connection leaks."""
    yield
    await close_redis_pool(force=True)


class TestCacheBasicOperations:
    """Test basic set/get/delete on RedisCache."""

    @pytest.mark.asyncio
    async def test_set_get_delete(self):
        """Basic set → get → delete → miss cycle."""
        await short_cache.set("test:integration:key1", {"data": "value"}, ttl=60)
        result = await short_cache.get("test:integration:key1")
        assert result == {"data": "value"}

        deleted = await short_cache.delete("test:integration:key1")
        assert deleted is True

        result = await short_cache.get("test:integration:key1")
        assert result is None

    @pytest.mark.asyncio
    async def test_cache_miss_returns_none(self):
        """Missing keys should return None without errors."""
        result = await short_cache.get("test:integration:nonexistent_key")
        assert result is None

    @pytest.mark.asyncio
    async def test_set_overwrites_existing(self):
        """Setting the same key again should overwrite the previous value."""
        await short_cache.set("test:integration:overwrite", "first", ttl=60)
        await short_cache.set("test:integration:overwrite", "second", ttl=60)

        result = await short_cache.get("test:integration:overwrite")
        assert result == "second"

        await short_cache.delete("test:integration:overwrite")

    @pytest.mark.asyncio
    async def test_delete_nonexistent_returns_false(self):
        """Deleting a key that doesn't exist should return False."""
        result = await short_cache.delete("test:integration:never_existed")
        assert result is False


class TestCacheTTLExpiry:
    """Test TTL-based expiration of cache entries."""

    @pytest.mark.asyncio
    async def test_entry_expires_after_ttl(self):
        """Cache entries should expire after their TTL."""
        await short_cache.set("test:integration:expiry", "temporary", ttl=1)  # 1 second TTL

        result = await short_cache.get("test:integration:expiry")
        assert result == "temporary"

        # Wait for TTL to expire
        await asyncio.sleep(1.5)

        result = await short_cache.get("test:integration:expiry")
        assert result is None  # Should have expired


class TestCacheTiers:
    """Test that all three cache tiers (short, medium, long) work."""

    @pytest.mark.asyncio
    async def test_all_tiers_set_and_get(self):
        """All three cache tiers should independently store and retrieve."""
        tiers = [
            (short_cache, "short"),
            (medium_cache, "medium"),
            (long_cache, "long"),
        ]

        for cache, label in tiers:
            key = f"test:integration:tier:{label}"
            await cache.set(key, f"value-{label}", ttl=60)
            result = await cache.get(key)
            assert result == f"value-{label}", f"Failed for {label} cache"
            await cache.delete(key)


class TestCachePrefixIsolation:
    """Test that different cache prefixes don't collide."""

    @pytest.mark.asyncio
    async def test_different_prefixes_no_collision(self):
        """Setting the same key in different caches should not collide."""
        await short_cache.set("test:integration:collision", "from-short", ttl=60)
        await medium_cache.set("test:integration:collision", "from-medium", ttl=60)

        assert await short_cache.get("test:integration:collision") == "from-short"
        assert await medium_cache.get("test:integration:collision") == "from-medium"

        await short_cache.delete("test:integration:collision")
        await medium_cache.delete("test:integration:collision")

    @pytest.mark.asyncio
    async def test_clear_only_affects_own_prefix(self):
        """Clearing one cache should not affect entries in another cache."""
        await medium_cache.set("test:integration:clear_test", "should-survive", ttl=60)
        await short_cache.set("test:integration:clear_test", "will-be-cleared", ttl=60)

        await short_cache.clear()

        # Medium cache entry should survive
        assert await medium_cache.get("test:integration:clear_test") == "should-survive"
        # Short cache entry should be gone
        assert await short_cache.get("test:integration:clear_test") is None

        await medium_cache.delete("test:integration:clear_test")
