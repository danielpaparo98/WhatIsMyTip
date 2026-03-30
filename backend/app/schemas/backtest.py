from pydantic import BaseModel
from datetime import datetime
from typing import Optional, List


class BacktestResponse(BaseModel):
    id: int
    heuristic: str
    season: int
    round_id: int
    tips_made: int
    tips_correct: int
    accuracy: float
    profit: float
    created_at: datetime
    
    class Config:
        from_attributes = True


class BacktestListResponse(BaseModel):
    results: list[BacktestResponse]
    count: int


class BacktestSummary(BaseModel):
    """Summary statistics for backtest results."""
    total_rounds: int
    total_tips: int
    total_correct: int
    overall_accuracy: float
    total_profit: float
    avg_profit_per_round: float
    best_round_accuracy: float
    worst_round_accuracy: float


class BacktestComparison(BaseModel):
    """Comparison of heuristics."""
    season: int
    comparison: dict[str, BacktestSummary]
    best_overall: dict[str, str | float]


class AvailableSeasonsResponse(BaseModel):
    """Response containing available seasons for backtesting."""
    available_years: List[int]
    current_year: int


class BacktestTableRow(BaseModel):
    """Single row of backtest table data."""
    round_id: int
    tips_made: int
    tips_correct: int
    accuracy: float
    profit: float


class BacktestTableData(BaseModel):
    """Table data for a single heuristic."""
    heuristic: str
    season: int
    rounds: List[BacktestTableRow]
    total_profit: float
    total_accuracy: float


class BacktestTableResponse(BaseModel):
    """Response containing detailed table data for all heuristics."""
    season: int
    heuristics: List[BacktestTableData]


class HistoricalSyncResponse(BaseModel):
    """Response for historical data sync operation."""
    season: int
    games_synced: int
    tips_generated: int
    message: str


class CurrentSeasonHeuristicPerformance(BaseModel):
    """Performance data for a single heuristic in the current season."""
    heuristic: str
    total_profit: float
    total_accuracy: float
    rounds_played: int
    avg_profit_per_round: float
    projected_annual_profit: float


class CurrentSeasonResponse(BaseModel):
    """Response containing current season performance data with projections."""
    season: int
    heuristics: List[CurrentSeasonHeuristicPerformance]
    rounds_completed: int
    total_rounds: int
