from .games import GameResponse, GameListResponse, GameDetailResponse, ModelPrediction
from .tips import TipResponse, TipCreate, TipListResponse

# Rebuild GameDetailResponse to resolve forward reference to TipResponse
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
