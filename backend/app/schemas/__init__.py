from .games import GameResponse, GameListResponse, GameDetailResponse, ModelPrediction
from .tips import TipResponse, TipCreate, TipListResponse
from .match_analysis import MatchAnalysisResponse

# Rebuild GameDetailResponse to resolve forward references
GameDetailResponse.model_rebuild()
from .backtest import (
    BacktestResponse,
    BacktestListResponse,
    AvailableSeasonsResponse,
    BacktestTableRow,
    BacktestTableData,
    BacktestTableResponse,
    HistoricalSyncResponse,
    CurrentSeasonHeuristicPerformance,
    CurrentSeasonResponse,
    PreGenerateResponse,
)

__all__ = [
    "GameResponse",
    "GameListResponse",
    "GameDetailResponse",
    "ModelPrediction",
    "TipResponse",
    "TipCreate",
    "TipListResponse",
    "MatchAnalysisResponse",
    "BacktestResponse",
    "BacktestListResponse",
    "AvailableSeasonsResponse",
    "BacktestTableRow",
    "BacktestTableData",
    "BacktestTableResponse",
    "HistoricalSyncResponse",
    "CurrentSeasonHeuristicPerformance",
    "CurrentSeasonResponse",
    "PreGenerateResponse",
]
