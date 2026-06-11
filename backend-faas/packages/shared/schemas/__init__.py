from .games import GameResponse, GameListResponse, GameDetailResponse, ModelPrediction, WeatherResponse
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
from .admin import (
    DailySyncTriggerRequest,
    MatchCompletionTriggerRequest,
    TipGenerationTriggerRequest,
    HistoricRefreshTriggerRequest,
    TipGenerateRequest,
)

__all__ = [
    "GameResponse",
    "GameListResponse",
    "GameDetailResponse",
    "ModelPrediction",
    "WeatherResponse",
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
    "DailySyncTriggerRequest",
    "MatchCompletionTriggerRequest",
    "TipGenerationTriggerRequest",
    "HistoricRefreshTriggerRequest",
    "TipGenerateRequest",
]
