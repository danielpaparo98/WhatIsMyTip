import httpx
from typing import List, Dict, Any, Optional
from datetime import datetime
from app.config import settings


class SquiggleClient:
    """Client for interacting with the Squiggle API."""
    
    def __init__(self):
        self.base_url = settings.squiggle_api_base
        self.client = httpx.AsyncClient(timeout=30.0)
    
    async def close(self):
        await self.client.aclose()
    
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
        params = {}
        if year:
            params["year"] = year
        if round:
            params["round"] = round
        if complete is not None:
            params["complete"] = str(complete).lower()
        
        response = await self.client.get(f"{self.base_url}/games", params=params)
        response.raise_for_status()
        return response.json()
    
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
    
    async def get_teams(self) -> List[Dict[str, Any]]:
        """Fetch all teams from Squiggle API.
        
        Returns:
            List of team dictionaries
        """
        response = await self.client.get(f"{self.base_url}/teams")
        response.raise_for_status()
        return response.json()
    
    async def get_stats(
        self,
        year: Optional[int] = None,
        round: Optional[int] = None,
        team: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Fetch team statistics from Squiggle API.
        
        Args:
            year: Filter by season year
            round: Filter by round number
            team: Filter by team name
            
        Returns:
            List of statistics dictionaries
        """
        params = {}
        if year:
            params["year"] = year
        if round:
            params["round"] = round
        if team:
            params["team"] = team
        
        response = await self.client.get(f"{self.base_url}/stats", params=params)
        response.raise_for_status()
        return response.json()
