from datetime import datetime

from pydantic import BaseModel, Field


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
    heuristic: str = Field(..., description="Heuristic type: best_bet, yolo, weighted_tip")
    selected_team: str
    margin: int
    confidence: float = Field(..., ge=0, le=1)
    explanation: str


class TipListResponse(BaseModel):
    tips: list[TipResponse]
    count: int
