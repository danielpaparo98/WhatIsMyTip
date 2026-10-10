from datetime import datetime
from typing import Optional

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
    heuristic: str = Field(
        ..., description="Heuristic type: best_bet, yolo, weighted_tip, boosted_tip"
    )
    selected_team: str
    margin: int
    confidence: float = Field(..., ge=0, le=1)
    explanation: str


class TipListResponse(BaseModel):
    tips: list[TipResponse]
    count: int


class LeagueTipExplanationResponse(BaseModel):
    """One rugby-league model tip for an event, with its AI explanation.

    ``picked`` is ``None`` when the model expects a draw (the
    ``league_tips`` draw-no-pick convention); ``explanation`` is
    ``None`` when generation degraded — a tip is never blocked by the
    AI layer.
    """

    heuristic: str
    picked: Optional[str] = None
    explanation: Optional[str] = None


class LeagueTipsResponse(BaseModel):
    """Rugby-league tips for one event (GET /api/tips/league).

    Additive (Phase 5.2): a NEW route payload — the legacy AFL tips
    response shapes above are untouched.  ``count`` is derived
    server-side from ``tips``.
    """

    league: str
    event: str
    competition: str
    tips: list[LeagueTipExplanationResponse] = []
    count: int = 0
