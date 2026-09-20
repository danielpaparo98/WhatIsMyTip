"""Schemas for stored match reports (grand-final pre-match report).

Field names here are mirrored verbatim by the frontend
(``frontend/composables/useApi.ts``) — do NOT rename them.
"""

from datetime import datetime

from pydantic import BaseModel


class TeamStory(BaseModel):
    """One team's season narrative plus its finals path."""

    team: str
    narrative: str
    finals_path: list[str]


class PlayerSpotlight(BaseModel):
    """A key player to watch, with the reason they matter."""

    name: str
    team: str
    note: str


class InjuryNote(BaseModel):
    """An injured player and their expected availability."""

    player: str
    status: str
    note: str


class SeasonStory(BaseModel):
    """Season story for both grand-final participants."""

    home: TeamStory
    away: TeamStory


class TeamPlayers(BaseModel):
    """Key players for both sides."""

    home: list[PlayerSpotlight]
    away: list[PlayerSpotlight]


class InjuryWatch(BaseModel):
    """Injury notes for both sides."""

    home: list[InjuryNote]
    away: list[InjuryNote]


class ModelConsensus(BaseModel):
    """What the app's models collectively say about the game."""

    summary: str
    models_picking_home: int
    models_picking_away: int
    season_accuracy_note: str


class Prediction(BaseModel):
    """The agent's verdict (confidence is 0-1)."""

    winner: str
    margin: int
    confidence: float


class GrandFinalReport(BaseModel):
    """Structured grand-final PRE-MATCH report (a prediction, not a recap)."""

    headline: str
    executive_summary: str
    season_story: SeasonStory
    keys_to_the_game: list[str]
    key_players: TeamPlayers
    injury_watch: InjuryWatch
    model_consensus: ModelConsensus
    weather_impact: str
    x_factor: str
    prediction: Prediction
    talking_points: list[str]


class MatchReportResponse(BaseModel):
    """Stored match report row as returned by ``GET /api/games/{slug}/report``."""

    id: int
    game_id: int
    report_type: str
    report: GrandFinalReport
    created_at: datetime

    class Config:
        from_attributes = True
