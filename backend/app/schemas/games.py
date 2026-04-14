from pydantic import BaseModel, Field
from datetime import datetime
from typing import Optional, List, TYPE_CHECKING

if TYPE_CHECKING:
    from app.schemas.tips import TipResponse
    from app.schemas.match_analysis import MatchAnalysisResponse


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


class GameListResponse(BaseModel):
    games: list[GameResponse]
    count: int
