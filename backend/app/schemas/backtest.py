from pydantic import BaseModel
from datetime import datetime
from typing import Optional


class BacktestResponse(BaseModel):
    id: int
    heuristic: str
    season: int
    round_id: int
    tips_made: int
    tips_correct: int
    accuracy: float
    profit: float
    created_at: datetime
    
    class Config:
        from_attributes = True


class BacktestListResponse(BaseModel):
    results: list[BacktestResponse]
    count: int
