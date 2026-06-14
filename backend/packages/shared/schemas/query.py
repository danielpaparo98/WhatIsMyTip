"""Pydantic models for validating API query parameters.

These provide the equivalent of FastAPI's ``Query(...)`` validation
in the DO Functions serverless shim.  Each model corresponds to the
query-string parameters accepted by one API function.
"""

from typing import Optional

from pydantic import BaseModel, Field

_VALID_HEURISTICS = {"best_bet", "high_risk_high_reward", "yolo"}


class GamesQuery(BaseModel):
    """Query parameters for ``GET /api/games``."""

    season: Optional[int] = Field(default=None, ge=2000, description="Filter by season year")
    round: Optional[int] = Field(default=None, ge=1, alias="round_id", description="Filter by round number")
    upcoming: bool = Field(default=False)
    latest: bool = Field(default=False)


class TipsQuery(BaseModel):
    """Query parameters for ``GET /api/tips``."""

    season: Optional[int] = Field(default=None, ge=2000)
    round: Optional[int] = Field(default=None, ge=1, alias="round_id")
    heuristic: Optional[str] = Field(default=None, pattern=r"^(best_bet|high_risk_high_reward|yolo)$")
    limit: int = Field(default=100, ge=1, le=500)


class TipsGameWithTipsQuery(BaseModel):
    """Query parameters for ``GET /api/tips/games-with-tips``."""

    season: int = Field(..., ge=2000)
    round: int = Field(..., ge=1, alias="round_id")
    heuristic: str = Field(default="best_bet", pattern=r"^(best_bet|high_risk_high_reward|yolo)$")


class TipsByHeuristicQuery(BaseModel):
    """Query parameters for ``GET /api/tips/{heuristic}``."""

    limit: int = Field(default=100, ge=1, le=500)


class BacktestQuery(BaseModel):
    """Query parameters for ``GET /api/backtest`` (compare / table endpoints)."""

    season: Optional[int] = Field(default=None, ge=2000, description="Required for /compare and /table")


class BacktestModelCompareQuery(BaseModel):
    """Query parameters for ``GET /api/backtest/model-compare``."""

    season: int = Field(..., ge=2000)


class BacktestTableQuery(BaseModel):
    """Query parameters for ``GET /api/backtest/table``."""

    season: int = Field(..., ge=2000)


class BacktestCompareQuery(BaseModel):
    """Query parameters for ``GET /api/backtest/compare``."""

    season: int = Field(..., ge=2000)


class BacktestByHeuristicQuery(BaseModel):
    """Query parameters for ``GET /api/backtest/{heuristic}`` (deprecated)."""

    season: Optional[int] = Field(default=None, ge=2000)
    limit: int = Field(default=50, ge=1, le=200)
