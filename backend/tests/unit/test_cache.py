import pytest
import time
from app.cache import InMemoryCache


class TestInMemoryCache:
    def test_get_returns_none_for_missing_key(self):
        cache = InMemoryCache(default_ttl=60, max_size=100)
        assert cache.get("missing_key") is None

    def test_set_and_get(self):
        cache = InMemoryCache(default_ttl=60, max_size=100)
        cache.set("test_key", "test_value")
        assert cache.get("test_key") == "test_value"

    def test_ttl_expiration(self):
        cache = InMemoryCache(default_ttl=0.1, max_size=100)
        cache.set("test_key", "test_value")
        assert cache.get("test_key") == "test_value"
        time.sleep(0.2)
        assert cache.get("test_key") is None

    def test_max_size_eviction(self):
        cache = InMemoryCache(default_ttl=60, max_size=2)
        cache.set("key1", "value1")
        cache.set("key2", "value2")
        cache.set("key3", "value3")  # Should evict oldest (key1)
        assert cache.get("key3") == "value3"
        # key1 should have been evicted
        assert cache.get("key1") is None

    def test_delete_existing_key(self):
        cache = InMemoryCache(default_ttl=60, max_size=100)
        cache.set("test_key", "test_value")
        assert cache.delete("test_key") is True
        assert cache.get("test_key") is None

    def test_delete_nonexistent_key(self):
        cache = InMemoryCache(default_ttl=60, max_size=100)
        assert cache.delete("nonexistent") is False

    def test_clear(self):
        cache = InMemoryCache(default_ttl=60, max_size=100)
        cache.set("key1", "value1")
        cache.set("key2", "value2")
        cache.clear()
        assert cache.get("key1") is None
        assert cache.get("key2") is None

    def test_size(self):
        cache = InMemoryCache(default_ttl=60, max_size=100)
        assert cache.size() == 0
        cache.set("key1", "value1")
        assert cache.size() == 1
        cache.set("key2", "value2")
        assert cache.size() == 2

    def test_cleanup_expired(self):
        cache = InMemoryCache(default_ttl=0.1, max_size=100)
        cache.set("key1", "value1")
        cache.set("key2", "value2")
        time.sleep(0.2)
        removed = cache.cleanup_expired()
        assert removed == 2
        assert cache.size() == 0

    def test_overwrite_existing_key(self):
        cache = InMemoryCache(default_ttl=60, max_size=100)
        cache.set("key", "old_value")
        cache.set("key", "new_value")
        assert cache.get("key") == "new_value"

    def test_custom_ttl_per_entry(self):
        cache = InMemoryCache(default_ttl=60, max_size=100)
        cache.set("short", "value", ttl=0.1)
        cache.set("long", "value", ttl=60)
        time.sleep(0.2)
        assert cache.get("short") is None
        assert cache.get("long") == "value"
