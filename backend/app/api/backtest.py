"""FastAPI router for the backtest endpoints.

A thin HTTP adapter over :mod:`packages.api.backtest` that preserves
URL paths and response field names 1:1.

Routes (mounted at ``/api/backtest``):

* ``GET  /``                — deprecated empty results
* ``GET  /compare``         — heuristic comparison for a season
* ``GET  /model-compare``   — model comparison for a season
* ``GET  /table``           — round-by-round table for a season
* ``GET  /seasons``         — available seasons
* ``GET  /current-season``  — current season performance
* ``POST /run``             — admin-only: run model backtest
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db_deps import get_db
from app.core.security import require_admin_key
from packages.shared.schemas import (
    BacktestListResponse,
    BacktestTableData,
    BacktestTableResponse,
    BacktestTableRow,
)
from packages.shared.services.backtest import BacktestService

router = APIRouter()


# ---------------------------------------------------------------------------
# Request body model
# ---------------------------------------------------------------------------


class BacktestRunRequest(BaseModel):
    """Request body for ``POST /api/backtest/run``."""

    model_config = {"extra": "ignore"}

    season: int = Field(..., ge=2000, description="Season year to backtest")
    round: Optional[int] = Field(
        default=None,
        ge=1,
        alias="round_id",
        description="Optional round number (unused today, kept for parity)",
    )
    heuristic: Optional[str] = Field(
        default=None,
        description="Optional heuristic filter (unused today, kept for parity)",
    )


# ---------------------------------------------------------------------------
# GET /  — deprecated
# ---------------------------------------------------------------------------


@router.get("/")
async def get_backtest_results():
    """Deprecated endpoint — always returns an empty result list.

    The frontend no longer calls this route; it was preserved verbatim
    to avoid breaking older clients (R7 in the FaaS research).
    """
    resp = BacktestListResponse(results=[], count=0)
    return resp.model_dump(mode="json")


# ---------------------------------------------------------------------------
# GET /compare
# ---------------------------------------------------------------------------


@router.get("/compare")
async def compare_heuristics(
    db: Annotated[AsyncSession, Depends(get_db)],
    season: Annotated[
        int,
        Query(ge=2000, description="Season year to compare"),
    ],
):
    """Compare heuristic performance for a season.

    Returns the per-heuristic metrics, plus ``best_overall`` (the
    heuristic with the highest ``overall_accuracy``).  ``season`` is
    required — FastAPI returns 422 when missing.
    """
    service = BacktestService()
    comparison = await service.compare_heuristics(db, season)

    if comparison:
        best_heuristic_name, best_heuristic_stats = max(
            comparison.items(),
            key=lambda x: x[1]["overall_accuracy"],
        )
        best = {
            "heuristic": best_heuristic_name,
            "accuracy": best_heuristic_stats["overall_accuracy"],
            "profit": best_heuristic_stats["total_profit"],
        }
    else:
        best = {"heuristic": None, "accuracy": 0.0, "profit": 0.0}

    return {
        "season": season,
        "comparison": comparison,
        "best_overall": best,
    }


# ---------------------------------------------------------------------------
# GET /model-compare
# ---------------------------------------------------------------------------


@router.get("/model-compare")
async def compare_models(
    db: Annotated[AsyncSession, Depends(get_db)],
    season: Annotated[
        int,
        Query(ge=2000, description="Season year to compare"),
    ],
):
    """Compare individual ML model performance for a season.

    Returns a list of model metrics (sorted by accuracy descending) and
    a ``best_overall`` summary pointing at the top model.
    """
    service = BacktestService()
    comparison = await service.compare_models(db, season)

    if comparison:
        best_model = comparison[0]  # already sorted by accuracy desc
        best = {
            "model_name": best_model["model_name"],
            "accuracy": best_model["overall_accuracy"],
            "profit": best_model["total_profit"],
        }
    else:
        best = {"model_name": None, "accuracy": 0.0, "profit": 0.0}

    return {
        "season": season,
        "comparison": comparison,
        "best_overall": best,
    }


# ---------------------------------------------------------------------------
# GET /table
# ---------------------------------------------------------------------------


@router.get("/table")
async def get_table(
    db: Annotated[AsyncSession, Depends(get_db)],
    season: Annotated[
        int,
        Query(ge=2000, description="Season year for the table"),
    ],
):
    """Return a round-by-round table of heuristic performance.

    For each available heuristic, returns per-round tips/accuracy/profit
    plus aggregate totals.  ``season`` is required — FastAPI returns 422
    when missing.
    """
    service = BacktestService()
    heuristics_list = []

    for heuristic in service.orchestrator.get_available_heuristics():
        round_data = await service.get_round_by_round_data(db, season, heuristic)

        total_profit = sum(r["profit"] for r in round_data)
        total_tips = sum(r["tips_made"] for r in round_data)
        total_correct = sum(r["tips_correct"] for r in round_data)
        total_accuracy = total_correct / total_tips if total_tips > 0 else 0.0

        heuristics_list.append(
            BacktestTableData(
                heuristic=heuristic,
                season=season,
                rounds=[
                    BacktestTableRow(
                        round_id=r["round_id"],
                        tips_made=r["tips_made"],
                        tips_correct=r["tips_correct"],
                        accuracy=r["accuracy"],
                        profit=r["profit"],
                    )
                    for r in round_data
                ],
                total_profit=total_profit,
                total_accuracy=total_accuracy,
            )
        )

    resp = BacktestTableResponse(season=season, heuristics=heuristics_list)
    return resp.model_dump(mode="json")


# ---------------------------------------------------------------------------
# GET /seasons
# ---------------------------------------------------------------------------


@router.get("/seasons")
async def get_seasons(
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """List seasons that have completed games with tips.

    Returns ``{available_years, current_year}``.  ``current_year`` is
    the calendar year on the server, not derived from data.
    """
    service = BacktestService()
    available_years = await service.get_available_seasons(db)
    current_year = datetime.now().year

    return {
        "available_years": available_years,
        "current_year": current_year,
    }


# ---------------------------------------------------------------------------
# GET /current-season
# ---------------------------------------------------------------------------


@router.get("/current-season")
async def get_current_season(
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Return year-to-date performance for the current season.

    Delegates to :meth:`BacktestService.get_current_season_performance`.
    """
    service = BacktestService()
    performance = await service.get_current_season_performance(db)
    return performance.model_dump(mode="json")


# ---------------------------------------------------------------------------
# POST /run  — admin
# ---------------------------------------------------------------------------


@router.post(
    "/run",
    dependencies=[require_admin_key],
)
async def run_backtest(
    body: Annotated[BacktestRunRequest, "Body"],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Run the model backtest for a season (fills missing predictions
    and returns the comparison).  Admin-only.

    The optional ``round`` and ``heuristic`` fields are accepted for
    parity with the FaaS contract but are not currently used by the
    service implementation.
    """
    season = body.season

    service = BacktestService()
    results = await service.run_model_backtest(db, season=season)

    return {
        "season": season,
        "round": body.round,
        "heuristic": body.heuristic,
        "count": len(results),
        "results": results,
    }
