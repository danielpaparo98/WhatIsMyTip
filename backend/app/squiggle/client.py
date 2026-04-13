import httpx
import asyncio
import time
from typing import List, Dict, Any, Optional
from datetime import datetime
from app.config import settings
from app.cache import medium_cache
from app.logger import get_logger

logger = get_logger(__name__)


class _RateLimiter:
    """Simple sliding-window rate limiter for Squiggle API requests.
    
    Limits to max_requests within the window_seconds period.
    Uses asyncio.Lock for async safety.
    """
    
    def __init__(self, max_requests: int = 10, window_seconds: float = 60.0):
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self._timestamps: list[float] = []
        self._lock = asyncio.Lock()
    
    async def acquire(self, timeout: float = 30.0) -> None:
        """Wait until a request slot is available, or raise TimeoutError.
        
        Args:
            timeout: Maximum seconds to wait for a slot
        """
        deadline = time.time() + timeout
        
        while True:
            async with self._lock:
                now = time.time()
                # Remove timestamps outside the window
                cutoff = now - self.window_seconds
                self._timestamps = [ts for ts in self._timestamps if ts > cutoff]
                
                if len(self._timestamps) < self.max_requests:
                    self._timestamps.append(now)
                    return
            
            # Calculate how long to wait for the oldest request to age out
            async with self._lock:
                oldest = self._timestamps[0] if self._timestamps else now
            
            wait_time = oldest + self.window_seconds - now
            
            if time.time() + wait_time > deadline:
                raise TimeoutError(
                    f"Rate limiter: could not acquire slot within {timeout}s "
                    f"(max {self.max_requests} requests per {self.window_seconds}s)"
                )
            
            # Wait outside the lock for the oldest timestamp to expire
            await asyncio.sleep(min(wait_time + 0.1, deadline - time.time()))


# Module-level rate limiter: max 10 requests per minute
_squiggle_rate_limiter = _RateLimiter(max_requests=10, window_seconds=60.0)


class SquiggleClient:
    """Client for interacting with the Squiggle API."""
    
    def __init__(self):
        self.base_url = settings.squiggle_api_base
        self.client = httpx.AsyncClient(
            timeout=30.0,
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
    ) -> List[Dict[str, Any]]:
        """Fetch games from Squiggle API with caching.
        
        Results are cached in medium_cache (5-min TTL) to reduce API calls.
        
        Args:
            year: Filter by season year
            round: Filter by round number
            complete: Filter by completion status
            
        Returns:
            List of game dictionaries
        """
        # Build cache key from query parameters
        cache_key = f"squiggle:games:year={year}:round={round}:complete={complete}"
        
        # Check cache first
        cached = medium_cache.get(cache_key)
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
        
        # Store in cache
        medium_cache.set(cache_key, games)
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
