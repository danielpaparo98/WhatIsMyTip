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
    home_team: str
    away_team: str
    home_score: Optional[int] = None
    away_score: Optional[int] = None
    venue: str
    date: datetime
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
    match_analysis: Optional['MatchAnalysisResponse'] = None  # AI-generated casual talking points
    weather: Optional[WeatherResponse] = None  # Weather conditions at match venue


class GameListResponse(BaseModel):
    games: list[GameResponse]
    count: int
