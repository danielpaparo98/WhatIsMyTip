"""Response schemas for the event-scoped read endpoints (ADR 0001).

The 0010 events tables are sport-generic: an ``Event`` has
``EventParticipant`` sides (home/away/``n/a``) instead of the legacy
``games.home_team/away_team`` columns, so participants are always a
nested list — team sports yield two entries, races/field events any
number.
"""

from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel

# PARITY-1: the league detail/report responses reuse the AFL weather,
# tip and grand-final-report schemas verbatim — the payloads are
# field-identical to the AFL ones.  (games.py / tips.py / match_report.py
# import nothing from this module, so the edges are acyclic.)
from .games import WeatherResponse
from .match_report import GrandFinalReport
from .tips import LeagueTipExplanationResponse


class EventParticipantResponse(BaseModel):
    """One side of an event, with its score and result.

    ``is_winner`` is ``None`` for draws and unfinished events.
    """

    side: str  # home | away | n/a
    participant_name: str
    score: Optional[int] = None
    is_winner: Optional[bool] = None


class EventResponse(BaseModel):
    """An event plus its participants and competition/season names.

    ``competition``/``season`` are human-readable names resolved from
    the competitions/seasons framework tables (not raw FK ids).
    """

    id: int
    slug: str
    #: Round/week number; NULL for tournaments and race events.
    round_id: Optional[int] = None
    venue: Optional[str] = None
    #: Venue-local naive timestamp — interpreted via the competition
    #: timezone (see the multisport model docstring for the tz policy).
    starts_at: Optional[datetime] = None
    status: str  # scheduled | completed | cancelled | void
    completed: bool
    competition: str
    season: str
    participants: List[EventParticipantResponse] = []


class EventListResponse(BaseModel):
    events: List[EventResponse]
    count: int


class LeagueModelPredictionSchema(BaseModel):
    """One league model's numeric prediction (PARITY-1).

    Mirrors the AFL ``ModelPrediction`` shape field-for-field, with
    nullable numbers to match the league table's abstention contract.
    """

    model_name: str
    winner: Optional[str] = None
    confidence: Optional[float] = None
    margin: Optional[int] = None


class LeagueMatchAnalysisResponse(BaseModel):
    """Stored AI talking points for one league event (PARITY-1)."""

    id: int
    event_id: int
    analysis_text: str
    created_at: datetime

    class Config:
        from_attributes = True


class LeagueEventDetailResponse(BaseModel):
    """Full league event detail (PARITY-1) — the AFL ``/games/{slug}/detail``
    twin: event + tips (with persisted explanations) + the numeric model
    breakdown + talking points + weather."""

    event: EventResponse
    tips: List[LeagueTipExplanationResponse] = []
    model_predictions: List[LeagueModelPredictionSchema] = []
    match_analysis: Optional[LeagueMatchAnalysisResponse] = None
    weather: Optional[WeatherResponse] = None


class LeagueMatchReportResponse(BaseModel):
    """Stored league match report row (PARITY-1) — mirrors
    ``MatchReportResponse`` field-for-field with ``event_id`` in place of
    ``game_id``.  The ``report`` payload is the SAME ``GrandFinalReport``
    schema the AFL report endpoint returns."""

    id: int
    event_id: int
    report_type: str
    report: GrandFinalReport
    created_at: datetime

    class Config:
        from_attributes = True


__all__ = [
    "EventParticipantResponse",
    "EventResponse",
    "EventListResponse",
    "LeagueModelPredictionSchema",
    "LeagueMatchAnalysisResponse",
    "LeagueEventDetailResponse",
    "LeagueMatchReportResponse",
]
