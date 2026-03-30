from pydantic import BaseModel, Field
from datetime import datetime
from typing import Optional


class TipResponse(BaseModel):
    id: int
    game_id: int
    heuristic: str
    selected_team: str
    margin: int
    confidence: float
    explanation: str
    created_at: datetime
    
    class Config:
        from_attributes = True


class TipCreate(BaseModel):
    game_id: int
    heuristic: str = Field(..., description="Heuristic type: best_bet, yolo, high_risk_high_reward")
    selected_team: str
    margin: int
    confidence: float = Field(..., ge=0, le=1)
    explanation: str


class TipListResponse(BaseModel):
    tips: list[TipResponse]
    count: int
