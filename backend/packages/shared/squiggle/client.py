import asyncio
import time
from typing import Any, Dict, List, Optional

import httpx

from ..cache import _get_client, medium_cache
from ..config import settings
from ..logger import get_logger

logger = get_logger(__name__)


class _RedisRateLimiter:
    """Redis-based sliding-window rate limiter for Squiggle API requests.

    Uses a Redis sorted set to track request timestamps, making it safe for
    stateless FaaS environments where in-memory state is lost between invocations.

    The sorted set key stores timestamps as both member and score, allowing
    efficient range queries to count requests within the sliding window.
    """

    def __init__(
        self,
        key: str = "wimt:rate_limit:squiggle",
        max_requests: int = 10,
        window_seconds: float = 60.0,
    ):
        self.key = key
        self.max_requests = max_requests
        self.window_seconds = window_seconds

    async def acquire(self, timeout: float = 30.0) -> None:
        """Wait until a request slot is available, or raise TimeoutError.

        Uses Redis ZREMRANGEBYSCORE to prune old entries, ZCARD to count
        current entries, and ZADD to register a new request timestamp.

        Args:
            timeout: Maximum seconds to wait for a slot
        """
        deadline = time.time() + timeout

        while True:
            now = time.time()
            cutoff = now - self.window_seconds

            try:
                client = _get_client()

                # Use a pipeline for atomicity
                async with client.pipeline(transaction=True) as pipe:
                    # Remove timestamps outside the sliding window
                    pipe.zremrangebyscore(self.key, "-inf", cutoff)
                    # Count current entries in the window
                    pipe.zcard(self.key)
                    # Set expiry on the key as a safety net
                    pipe.expire(self.key, int(self.window_seconds) + 1)

                    results = await pipe.execute()

                current_count = results[1]

                if current_count < self.max_requests:
                    # Add our timestamp — use ZADD with NX to avoid collisions
                    member = f"{now}:{id(pipe)}"  # Unique member per request
                    await client.zadd(self.key, {member: now})
                    return

            except Exception as e:
                logger.warning(f"Redis rate limiter error, allowing request: {e}")
                # If Redis is down, allow the request through
                return

            # Calculate how long to wait for the oldest request to age out
            try:
                client = _get_client()
                # Get the oldest entry in the sorted set
                oldest_entries = await client.zrange(self.key, 0, 0, withscores=True)
                if oldest_entries:
                    oldest_ts = oldest_entries[0][1]
                else:
                    oldest_ts = now
            except Exception:
                oldest_ts = now

            wait_time = oldest_ts + self.window_seconds - now

            if time.time() + wait_time > deadline:
                raise TimeoutError(
                    f"Rate limiter: could not acquire slot within {timeout}s "
                    f"(max {self.max_requests} requests per {self.window_seconds}s)"
                )

            # Wait outside the lock for the oldest timestamp to expire
            await asyncio.sleep(min(wait_time + 0.1, deadline - time.time()))


# Module-level rate limiter: max 10 requests per minute
_squiggle_rate_limiter = _RedisRateLimiter(max_requests=10, window_seconds=60.0)


def _filter_games_by_date(
    games: List[Dict[str, Any]],
    start_date: Optional[str],
    end_date: Optional[str],
) -> List[Dict[str, Any]]:
    """Filter ``games`` to those with ``date`` within the given window.

    ``start_date`` / ``end_date`` are ``YYYY-MM-DD`` strings (the
    same format Squiggle uses in its ``date`` field).  Comparison is
    done on the date prefix only, so time-of-day is ignored.

    The Squiggle API does not currently expose a date query
    parameter; this helper provides the equivalent client-side
    filter so callers can narrow the result set (ME-003).
    """
    if not (start_date or end_date):
        return games

    def _in_window(game_date: str) -> bool:
        if not game_date:
            return False
        # Squiggle returns ISO timestamps like "2025-03-15T10:00:00Z".
        game_day = game_date[:10]
        if start_date and game_day < start_date:
            return False
        if end_date and game_day > end_date:
            return False
        return True

    return [g for g in games if _in_window(g.get("date", ""))]


class SquiggleClient:
    """Client for interacting with the Squiggle API."""

    def __init__(self):
        self.base_url = settings.squiggle_api_base
        # SEC-LO-007: explicit `verify=True` so a future change to
        # httpx's default (or a deployment env that strips the CA
        # bundle) cannot silently disable TLS verification.
        self.client = httpx.AsyncClient(
            timeout=30.0,
            verify=True,
            headers={"User-Agent": f"WhatIsMyTip - {settings.squiggle_contact_email}"}
        )

    async def close(self):
        await self.client.aclose()

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()

    async def get_games(
        self,
        year: Optional[int] = None,
        round: Optional[int] = None,
        complete: Optional[bool] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Fetch games from Squiggle API with caching.

        Results are cached in medium_cache (5-min TTL) to reduce API calls.

        Args:
            year: Filter by season year
            round: Filter by round number
            complete: Filter by completion status
            start_date: Optional ISO date (YYYY-MM-DD) lower bound.
                When provided, the response is filtered client-side to
                games with ``date >= start_date``.  This lets callers
                shrink the result set without depending on the Squiggle
                API supporting an explicit date query parameter.
            end_date: Optional ISO date (YYYY-MM-DD) upper bound.
                Same client-side filter behaviour as ``start_date``.

        Returns:
            List of game dictionaries
        """
        # Build cache key from query parameters
        cache_key = (
            f"squiggle:games:year={year}:round={round}:complete={complete}"
            f":start={start_date}:end={end_date}"
        )

        # Check cache first
        cached = await medium_cache.get(cache_key)
        if cached is not None:
            logger.debug(f"Cache hit for Squiggle games: {cache_key}")
            return cached

        # Build query string for Squiggle API format: ?q=games;year=2024;round=1
        query_parts = ["games"]
        if year:
            query_parts.append(f"year={year}")
        if round:
            query_parts.append(f"round={round}")
        if complete is not None:
            query_parts.append(f"complete={str(complete).lower()}")

        query = ";".join(query_parts)
        url = f"{self.base_url}/?q={query}"

        # Acquire rate limiter slot before making request
        await _squiggle_rate_limiter.acquire()

        response = await self.client.get(url)
        response.raise_for_status()
        data = response.json()
        # Squiggle API returns {"games": [...]}
        games = data.get("games", [])

        # Client-side date-window filter (ME-003).  The Squiggle API
        # does not currently expose a date query parameter so we trim
        # the response here.  Filtering after the (cached) API call
        # means we still benefit from cache hits while reducing the
        # work the caller has to do.
        if start_date or end_date:
            games = _filter_games_by_date(games, start_date, end_date)

        # Store in cache
        await medium_cache.set(cache_key, games)
        logger.debug(f"Cache set for Squiggle games: {cache_key}")

        return games

    async def get_game(self, game_id: int) -> Dict[str, Any]:
        """Fetch a single game by ID.

        Args:
            game_id: Squiggle game ID

        Returns:
            Game dictionary
        """
        # Acquire rate limiter slot before making request
        await _squiggle_rate_limiter.acquire()

        response = await self.client.get(f"{self.base_url}/games/{game_id}")
        response.raise_for_status()
        return response.json()
