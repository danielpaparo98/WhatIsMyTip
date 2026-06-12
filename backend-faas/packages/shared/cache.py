"""
Redis-backed cache with TTL support.

Replaces the in-memory cache for FaaS environments where instances are ephemeral.
Uses redis.asyncio for non-blocking operations with lazy connection initialization
to handle FaaS cold starts gracefully.
"""

import hashlib
import json
import logging
import time
from functools import wraps
from typing import Any, Callable, Optional, TypeVar

import redis.asyncio as redis
from redis.asyncio import ConnectionPool
from sqlalchemy.ext.asyncio import AsyncSession

from .config import settings

T = TypeVar("T")
logger = logging.getLogger(__name__)

# Module-level connection pool (lazy-initialized)
_pool: Optional[ConnectionPool] = None


def _get_pool() -> ConnectionPool:
    """Get or create the Redis connection pool (lazy singleton)."""
    global _pool
    if _pool is None:
        _pool = ConnectionPool.from_url(
            settings.redis_url,
            decode_responses=True,
            max_connections=10,
        )
    return _pool


def _get_client() -> redis.Redis:
    """Get a Redis client from the shared connection pool."""
    return redis.Redis(connection_pool=_get_pool())


class RedisCache:
    """
    Redis-backed cache with TTL support.

    Args:
        default_ttl: Default time-to-live in seconds for cache entries
        prefix: Key prefix for namespacing in shared Redis
    """

    def __init__(self, default_ttl: float = 600.0, prefix: str = "wimt:"):
        self.default_ttl = default_ttl
        self.prefix = prefix

    def _prefixed_key(self, key: str) -> str:
        """Apply prefix to a cache key."""
        return f"{self.prefix}{key}"

    async def get(self, key: str) -> Optional[Any]:
        """Get a value from the cache if it exists and hasn't expired."""
        client = _get_client()
        full_key = self._prefixed_key(key)
        try:
            raw = await client.get(full_key)
            if raw is None:
                return None
            return json.loads(raw)
        except (redis.RedisError, json.JSONDecodeError) as e:
            logger.warning(f"Cache GET error for key '{key}': {e}")
            return None

    async def set(self, key: str, value: Any, ttl: Optional[float] = None) -> None:
        """
        Set a value in the cache.

        Args:
            key: Cache key
            value: Value to cache (must be JSON-serializable)
            ttl: Time-to-live in seconds (uses default_ttl if None)
        """
        client = _get_client()
        full_key = self._prefixed_key(key)
        ttl = ttl if ttl is not None else self.default_ttl
        try:
            await client.set(full_key, json.dumps(value), ex=int(ttl))
        except (redis.RedisError, json.JSONDecodeError, TypeError) as e:
            logger.warning(f"Cache SET error for key '{key}': {e}")

    async def delete(self, key: str) -> bool:
        """Delete a key from the cache. Returns True if key existed."""
        client = _get_client()
        full_key = self._prefixed_key(key)
        try:
            result = await client.delete(full_key)
            return result > 0
        except redis.RedisError as e:
            logger.warning(f"Cache DELETE error for key '{key}': {e}")
            return False

    async def clear(self) -> None:
        """Clear all entries with this cache's prefix."""
        client = _get_client()
        try:
            async for key in client.scan_iter(match=f"{self.prefix}*"):
                await client.delete(key)
        except redis.RedisError as e:
            logger.warning(f"Cache CLEAR error: {e}")


# Global cache instances with different TTLs
# Short-lived cache (1 minute) for frequently changing data
short_cache = RedisCache(default_ttl=60, prefix="wimt:s:")

# Medium-lived cache (5 minutes) for moderately changing data
medium_cache = RedisCache(default_ttl=300, prefix="wimt:m:")

# Long-lived cache (1 hour) for rarely changing data
long_cache = RedisCache(default_ttl=3600, prefix="wimt:l:")


def _make_cache_key(func_id: str, args: tuple, kwargs: dict) -> str:
    """Generate a deterministic, hash-based cache key.

    Filters out non-serializable arguments (e.g. AsyncSession objects)
    that should not be part of the cache key.

    Args:
        func_id: Function identifier (prefix + name).
        args: Positional arguments (already excluding self/db session).
        kwargs: Keyword arguments.

    Returns:
        MD5 hex digest string for use as a cache key.
    """
    # Filter out non-serializable positional args (sessions, None)
    filtered_args = tuple(a for a in args if not isinstance(a, (AsyncSession, type(None))))

    # Filter out non-serializable keyword args
    filtered_kwargs = {
        k: v for k, v in kwargs.items() if not isinstance(v, (AsyncSession, type(None)))
    }

    # Build a deterministic string representation
    key_data = f"{func_id}:{filtered_args}:{sorted(filtered_kwargs.items())}"
    return hashlib.md5(key_data.encode()).hexdigest()


def cached(
    cache: RedisCache = medium_cache,
    key_prefix: str = "",
    ttl: Optional[float] = None,
):
    """
    Decorator for caching async function results in Redis.

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
            # Skip first arg (typically db session) to avoid non-deterministic memory addresses
            cache_args = args[1:] if args else ()
            cache_key = _make_cache_key(f"{key_prefix}{func.__name__}", cache_args, kwargs)

            # Try to get from cache
            start_time = time.time()
            cached_value = await cache.get(cache_key)
            cache_get_time = time.time() - start_time

            if cached_value is not None:
                logger.debug(f"CACHE HIT: {func.__name__} | cache_get_time: {cache_get_time:.4f}s")
                return cached_value

            logger.debug(f"CACHE MISS: {func.__name__} | cache_get_time: {cache_get_time:.4f}s")

            # Execute function and cache result
            func_start = time.time()
            result = await func(*args, **kwargs)
            func_time = time.time() - func_start

            set_start = time.time()
            await cache.set(cache_key, result, ttl)
            set_time = time.time() - set_start

            logger.debug(
                f"CACHE SET: {func.__name__} "
                f"| func_time: {func_time:.4f}s | set_time: {set_time:.4f}s"
            )
            return result

        return async_wrapper  # type: ignore

    return decorator


async def invalidate_cache_pattern(cache: RedisCache, pattern: str) -> int:
    """
    Invalidate all cache keys matching a pattern.

    Uses Redis SCAN to find matching keys and deletes them.

    Args:
        cache: The cache instance
        pattern: String pattern to match (substring match within prefixed keys)

    Returns:
        Number of keys invalidated
    """
    start_time = time.time()
    client = _get_client()
    keys_deleted = 0

    try:
        async for key in client.scan_iter(match=f"{cache.prefix}*{pattern}*"):
            await client.delete(key)
            keys_deleted += 1
    except redis.RedisError as e:
        logger.warning(f"Cache INVALIDATE error for pattern '{pattern}': {e}")

    elapsed = time.time() - start_time
    logger.debug(
        f"CACHE INVALIDATE: pattern='{pattern}' | keys_deleted={keys_deleted} | time={elapsed:.4f}s"
    )
    return keys_deleted


async def close_redis_pool(force: bool = False) -> None:
    """Close the Redis connection pool.

    Only force-closes when ``force=True`` (i.e. on error). On normal
    completion the pool is kept alive so warm starts can reuse connections.

    Args:
        force: When True, close and reset the pool. Defaults to False.
    """
    global _pool
    if _pool is not None and force:
        await _pool.aclose()
        _pool = None
