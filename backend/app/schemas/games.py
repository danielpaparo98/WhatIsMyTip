from pydantic import BaseModel, Field
from datetime import datetime
from typing import Optional


class GameResponse(BaseModel):
    id: int
    squiggle_id: int
    round_id: int
    season: int
    home_team: str
    away_team: str
    home_score: Optional[int] = None
    away_score: Optional[int] = None
    venue: str
    date: datetime
    completed: bool
    
    class Config:
        from_attributes = True


class GameListResponse(BaseModel):
    games: list[GameResponse]
    count: int
