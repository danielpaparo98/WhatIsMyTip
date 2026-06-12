from .games import (
    GameDetailResponse,
    GameListResponse,
    GameResponse,
    ModelPrediction,
    WeatherResponse,
)
from .match_analysis import MatchAnalysisResponse
from .tips import TipCreate, TipListResponse, TipResponse

# Rebuild GameDetailResponse to resolve forward references
GameDetailResponse.model_rebuild()
from .admin import (  # noqa: E402
    DailySyncTriggerRequest,
    HistoricRefreshTriggerRequest,
    MatchCompletionTriggerRequest,
    TipGenerateRequest,
    TipGenerationTriggerRequest,
)
from .backtest import (  # noqa: E402
    AvailableSeasonsResponse,
    BacktestListResponse,
    BacktestResponse,
    BacktestTableData,
    BacktestTableResponse,
    BacktestTableRow,
    CurrentSeasonHeuristicPerformance,
    CurrentSeasonResponse,
    HistoricalSyncResponse,
    PreGenerateResponse,
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
