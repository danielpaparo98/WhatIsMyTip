from datetime import datetime
from typing import TYPE_CHECKING, List, Optional

from pydantic import BaseModel

if TYPE_CHECKING:
    from .match_analysis import MatchAnalysisResponse
    from .tips import TipResponse


class WeatherResponse(BaseModel):
    """Weather conditions for a match venue."""
    temperature: Optional[float] = None
    precipitation: Optional[float] = None
    wind_speed: Optional[float] = None
    wind_gusts: Optional[float] = None
    wind_direction: Optional[int] = None
    humidity: Optional[int] = None
    weather_code: Optional[int] = None
    data_type: Optional[str] = "historical"

    class Config:
        from_attributes = True


class GameResponse(BaseModel):
    id: int
    slug: str
    squiggle_id: int
    round_id: int
    season: int
    # home_team / away_team / venue are nullable in Postgres to support
    # stub future-fixture rows from the Squiggle feed (e.g. unannounced
    # AFL games in the current season).  We accept ``None`` here so the
    # /api/games list endpoint does not 500 when one of these is null;
    # the frontend renders a 'TBD' placeholder for null values.
    home_team: Optional[str] = None
    away_team: Optional[str] = None
    home_score: Optional[int] = None
    away_score: Optional[int] = None
    venue: Optional[str] = None
    date: Optional[datetime] = None
    completed: bool

    class Config:
        from_attributes = True


class ModelPrediction(BaseModel):
    model_name: str  # 'elo', 'form', 'home_advantage', 'value'
    winner: str
    confidence: float
    margin: int


class GameDetailResponse(BaseModel):
    game: GameResponse
    tips: List['TipResponse']  # All tips for all heuristics
    model_predictions: List[ModelPrediction]  # On-demand predictions from all 4 models
    match_analysis: Optional['MatchAnalysisResponse'] = None  # AI-generated balanced talking points
    weather: Optional[WeatherResponse] = None  # Weather conditions at match venue


class GameListResponse(BaseModel):
    games: list[GameResponse]
    count: int
