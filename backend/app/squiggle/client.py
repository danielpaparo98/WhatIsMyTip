import httpx
from typing import List, Dict, Any, Optional
from datetime import datetime
from app.config import settings


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
        """Fetch games from Squiggle API.
        
        Args:
            year: Filter by season year
            round: Filter by round number
            complete: Filter by completion status
            
        Returns:
            List of game dictionaries
        """
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
        
        response = await self.client.get(url)
        response.raise_for_status()
        data = response.json()
        # Squiggle API returns {"games": [...]}
        return data.get("games", [])
    
    async def get_game(self, game_id: int) -> Dict[str, Any]:
        """Fetch a single game by ID.
        
        Args:
            game_id: Squiggle game ID
            
        Returns:
            Game dictionary
        """
        response = await self.client.get(f"{self.base_url}/games/{game_id}")
        response.raise_for_status()
        return response.json()
    
