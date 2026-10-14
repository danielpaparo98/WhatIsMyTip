from datetime import datetime
from typing import Dict, Optional

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
    AI layer.  FCL-14: ``confidence``/``margin`` ride on the row for
    the derived heuristics (the AFL card shape), ``None`` otherwise.
    """

    heuristic: str
    picked: Optional[str] = None
    explanation: Optional[str] = None
    confidence: Optional[float] = None
    margin: Optional[int] = None


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


class LeaguePickDetail(BaseModel):
    """One heuristic's stored pick for a fixture (FCL-14 AFL parity).

    ``picked`` is the side name (``None`` = draw-no-pick);
    ``confidence`` (0..1) and ``margin`` (points) ride on the row for
    the derived heuristics — the same numbers the AFL cards render —
    and are ``None`` for the base result-derived models.  PARITY-1:
    ``explanation`` is the persisted AI text (written by the nightly
    league NLP sweep), so round cards render the same explanation the
    AFL cards do at zero per-view LLM cost; ``None`` until generated.
    """

    picked: Optional[str] = None
    confidence: Optional[float] = None
    margin: Optional[int] = None
    explanation: Optional[str] = None


class LeagueRoundFixture(BaseModel):
    """One round fixture with its stored league picks (FCL-12).

    ``picks`` maps heuristic name → :class:`LeaguePickDetail`; absent
    keys mean the heuristic has no stored tip for the fixture.  NO AI
    explanations — the round page renders picks only, so a page view
    never pays the LLM pipeline.
    """

    event_id: int
    slug: str
    round: Optional[int] = None
    starts_at: Optional[datetime] = None
    status: str
    completed: bool
    home_team: str
    away_team: str
    picks: Dict[str, LeaguePickDetail] = {}


class LeagueRoundTipsResponse(BaseModel):
    """Bulk round tips for one league (GET /api/tips/league-round).

    Additive (FCL-12): a NEW route payload — every existing tips shape
    is untouched.  Scoped to the league's current season; ``count`` is
    derived server-side from ``fixtures``.
    """

    league: str
    round: int
    season: Optional[str] = None
    fixtures: list[LeagueRoundFixture] = []
    count: int = 0
