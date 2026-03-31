"""
Simple in-memory cache with TTL support.

Uses only Python standard library - no external dependencies.
Thread-safe for basic use cases.
"""
import time
import threading
from typing import Any, Callable, Optional, TypeVar
from functools import wraps

T = TypeVar('T')


class CacheEntry:
    """A single cache entry with expiration time."""
    
    def __init__(self, value: T, ttl_seconds: float):
        self.value = value
        self.expires_at = time.time() + ttl_seconds
    
    def is_expired(self) -> bool:
        """Check if this entry has expired."""
        return time.time() > self.expires_at


class InMemoryCache:
    """
    Simple thread-safe in-memory cache with TTL support.
    
    Args:
        default_ttl: Default time-to-live in seconds for cache entries
        max_size: Maximum number of entries to store (0 for unlimited)
    """
    
    def __init__(self, default_ttl: float = 600.0, max_size: int = 0):
        self.default_ttl = default_ttl
        self.max_size = max_size
        self._cache: dict[str, CacheEntry] = {}
        self._lock = threading.RLock()
    
    def get(self, key: str) -> Optional[Any]:
        """Get a value from the cache if it exists and hasn't expired."""
        with self._lock:
            entry = self._cache.get(key)
            if entry is None:
                return None
            if entry.is_expired():
                del self._cache[key]
                return None
            return entry.value
    
    def set(self, key: str, value: T, ttl: Optional[float] = None) -> None:
        """
        Set a value in the cache.
        
        Args:
            key: Cache key
            value: Value to cache
            ttl: Time-to-live in seconds (uses default_ttl if None)
        """
        with self._lock:
            # Enforce max size by removing oldest entries if needed
            if self.max_size > 0 and len(self._cache) >= self.max_size:
                # Simple FIFO eviction
                oldest_key = next(iter(self._cache))
                del self._cache[oldest_key]
            
            ttl = ttl if ttl is not None else self.default_ttl
            self._cache[key] = CacheEntry(value, ttl)
    
    def delete(self, key: str) -> bool:
        """Delete a key from the cache. Returns True if key existed."""
        with self._lock:
            if key in self._cache:
                del self._cache[key]
                return True
            return False
    
    def clear(self) -> None:
        """Clear all entries from the cache."""
        with self._lock:
            self._cache.clear()
    
    def cleanup_expired(self) -> int:
        """Remove all expired entries. Returns number of entries removed."""
        with self._lock:
            expired_keys = [
                key for key, entry in self._cache.items()
                if entry.is_expired()
            ]
            for key in expired_keys:
                del self._cache[key]
            return len(expired_keys)
    
    def size(self) -> int:
        """Get the current number of entries in the cache."""
        with self._lock:
            return len(self._cache)


# Global cache instances with different TTLs
# Short-lived cache (5 minutes) for frequently changing data
short_cache = InMemoryCache(default_ttl=300.0, max_size=1000)

# Medium-lived cache (15 minutes) for moderately changing data
medium_cache = InMemoryCache(default_ttl=900.0, max_size=500)

# Long-lived cache (1 hour) for rarely changing data
long_cache = InMemoryCache(default_ttl=3600.0, max_size=100)


def cached(
    cache: InMemoryCache = medium_cache,
    key_prefix: str = "",
    ttl: Optional[float] = None,
):
    """
    Decorator for caching function results.
    
    Args:
        cache: The cache instance to use
        key_prefix: Prefix for cache keys
        ttl: Override default TTL for this function
    
    Example:
        @cached(cache=short_cache, key_prefix="games:")
        async def get_games(db, season):
            ...
    """
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        async def async_wrapper(*args, **kwargs):
            # Build cache key from function name and arguments
            cache_key = f"{key_prefix}{func.__name__}:{str(args)}:{str(sorted(kwargs.items()))}"
            
            # Try to get from cache
            cached_value = cache.get(cache_key)
            if cached_value is not None:
                return cached_value
            
            # Execute function and cache result
            result = await func(*args, **kwargs)
            cache.set(cache_key, result, ttl)
            return result
        
        @wraps(func)
        def sync_wrapper(*args, **kwargs):
            # Build cache key from function name and arguments
            cache_key = f"{key_prefix}{func.__name__}:{str(args)}:{str(sorted(kwargs.items()))}"
            
            # Try to get from cache
            cached_value = cache.get(cache_key)
            if cached_value is not None:
                return cached_value
            
            # Execute function and cache result
            result = func(*args, **kwargs)
            cache.set(cache_key, result, ttl)
            return result
        
        # Return appropriate wrapper based on whether function is async
        import asyncio
        if asyncio.iscoroutinefunction(func):
            return async_wrapper  # type: ignore
        else:
            return sync_wrapper  # type: ignore
    
    return decorator


def invalidate_cache_pattern(cache: InMemoryCache, pattern: str) -> int:
    """
    Invalidate all cache keys matching a pattern.
    
    Args:
        cache: The cache instance
        pattern: String pattern to match (simple substring match)
    
    Returns:
        Number of keys invalidated
    """
    with cache._lock:
        keys_to_delete = [
            key for key in cache._cache.keys()
            if pattern in key
        ]
        for key in keys_to_delete:
            del cache._cache[key]
        return len(keys_to_delete)
