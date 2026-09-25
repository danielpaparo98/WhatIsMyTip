from .events import (
    EventListResponse,
    EventParticipantResponse,
    EventResponse,
)
from .games import (
    GameDetailResponse,
    GameListResponse,
    GameResponse,
    ModelPrediction,
    WeatherResponse,
)
from .sports import (
    CompetitionResponse,
    SeasonResponse,
    SportResponse,
    SportsListResponse,
)
from .match_analysis import MatchAnalysisResponse
from .match_report import (
    GrandFinalReport,
    InjuryNote,
    InjuryWatch,
    MatchReportResponse,
    ModelConsensus,
    PlayerSpotlight,
    Prediction,
    SeasonStory,
    TeamPlayers,
    TeamStory,
)
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
    "EventResponse",
    "EventParticipantResponse",
    "EventListResponse",
    "GameResponse",
    "GameListResponse",
    "GameDetailResponse",
    "ModelPrediction",
    "WeatherResponse",
    "TipResponse",
    "TipCreate",
    "TipListResponse",
    "MatchAnalysisResponse",
    "GrandFinalReport",
    "InjuryNote",
    "InjuryWatch",
    "MatchReportResponse",
    "ModelConsensus",
    "PlayerSpotlight",
    "Prediction",
    "SeasonStory",
    "TeamPlayers",
    "TeamStory",
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
