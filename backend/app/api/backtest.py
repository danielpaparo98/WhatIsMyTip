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
* ``GET  /active-model``   — active weighted_tip model + coefficients
* ``GET  /active-boosted-model`` — active boosted_tip model + SHAP
  importances (BT-1)
* ``POST /run``             — admin-only: run model backtest

League dispatch (performance-per-league D4): ``/compare``, ``/seasons``
and ``/current-season`` accept an optional ``league`` query param.  An
absent, empty or ``afl`` value rides the legacy AFL path unchanged; any
registered ``STATE_LEAGUES`` key is resolved to its ``competitions.id``
(by canonical ``competitions.name`` — the frontend
``LEAGUE_COMPETITION_NAMES`` contract) and served from the multisport
tables via :class:`LeagueBacktestService`.  An unknown key is a 404; a
registered-but-never-synced league degrades to the service's graceful
zero payloads.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, Query
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db_deps import get_db
from app.core.exceptions import http_error
from app.core.security import require_admin_key
from packages.shared.ingestion.state_leagues import STATE_LEAGUES
from packages.shared.models import Competition
from packages.shared.schemas import (
    BacktestListResponse,
    BacktestTableData,
    BacktestTableResponse,
    BacktestTableRow,
)
from packages.shared.services.backtest import BacktestService

# BT-1: importing these symbols at module level is safe *only* because
# ``boosted_explanations`` defers every heavy import (xgboost, shap,
# numpy) to function bodies — the always-on API workers must not pay
# the resident-memory cost at import time.  Pinned by
# ``TestBacktestRouterLazyImports`` and ``tests/unit/test_lazy_sklearn.py``.
from packages.shared.services.boosted_explanations import (
    get_active_boosted_model,
)
from packages.shared.services.league_backtest import LeagueBacktestService

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
# League dispatch helpers (performance-per-league D4)
# ---------------------------------------------------------------------------


#: ``competition_id`` stand-in for a REGISTERED-but-never-synced league:
#: no autoincrement id can be negative, so every service fetcher returns
#: zero rows and the service's own empty-state machinery produces the
#: graceful zero payloads (empty ``available_years`` / zeroed heuristics)
#: instead of a 500.  The zero shapes are pinned by
#: ``test_league_backtest_service.py`` and
#: ``tests/unit/test_backtest_api_league.py``.
_UNSYNCED_COMPETITION_ID = -1


async def _league_competition_id(db: AsyncSession, league: str) -> int:
    """Resolve a league key to its ``competitions.id``.

    Resolution is by the canonical ``competitions.name`` from the
    ``STATE_LEAGUES`` registry — the exact names the frontend's
    ``LEAGUE_COMPETITION_NAMES`` mapping pins (the two must stay in
    sync; keys are matched case-sensitively).

    Raises:
        BackendServiceError: 404 ``not_found`` for a key outside the
            registry (``afl`` never reaches here — the legacy branch
            handles it before dispatch).
    """
    config = STATE_LEAGUES.get(league)
    if config is None:
        raise http_error(
            404,
            "not_found",
            f"Unknown league {league!r} — not in the state-league registry",
        )
    competition_id = (
        await db.execute(
            select(Competition.id).where(Competition.name == config.name)
        )
    ).scalar_one_or_none()
    if competition_id is None:
        # Registered but never synced (no competition row yet) — ride the
        # service's empty-state machinery, never a 500.
        return _UNSYNCED_COMPETITION_ID
    return int(competition_id)


def _legacy_season_year(season: str) -> int:
    """Coerce the raw ``season`` query value for the legacy AFL path.

    The league path consumes the season LABEL verbatim, so ``/compare``
    declares ``season`` as a string; the legacy AFL path keeps its
    original ``int, ge=2000`` validation semantics — numeric strings
    coerce, anything else re-raises the native FastAPI validation-error
    shapes (both handlers return the repo-standard 422 body).
    """
    try:
        year = int(season)
    except ValueError:
        raise RequestValidationError(
            [
                {
                    "type": "int_parsing",
                    "loc": ["query", "season"],
                    "msg": (
                        "Input should be a valid integer, unable to parse "
                        "string as an integer"
                    ),
                    "input": season,
                }
            ]
        ) from None
    if year < 2000:
        raise RequestValidationError(
            [
                {
                    "type": "greater_than_equal",
                    "loc": ["query", "season"],
                    "msg": "Input should be greater than or equal to 2000",
                    "input": season,
                }
            ]
        )
    return year


# ---------------------------------------------------------------------------
# GET /  — deprecated
# ---------------------------------------------------------------------------


# No-trailing-slash alias — see the comment on `list_games` in games.py.
# Lets the ingress-trimmed `/api/backtest` resolve without a redirect that
# drops the `/api` prefix.  Hidden from OpenAPI (duplicate operationId).
@router.get("", include_in_schema=False)
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
        str,
        Query(description="Season year to compare (AFL) or season label (leagues)"),
    ],
    league: Annotated[
        Optional[str],
        Query(
            description=(
                "League key (e.g. 'wafl'). Absent, empty, or 'afl' "
                "selects the legacy AFL backtest."
            )
        ),
    ] = None,
):
    """Compare heuristic performance for a season.

    AFL (no ``league``/``afl``): per-heuristic metrics plus
    ``best_overall``; ``season`` is a calendar year — missing or
    non-numeric values are a 422, exactly as before the league param
    existed.

    League (``league=<state-league key>``): the same response shape from
    the multisport tables, with ``season`` taken as the season LABEL
    string.  An unknown league is a 404; a registered-but-unsynced
    league returns the zero comparison structure.
    """
    if league and league != "afl":
        league_service = LeagueBacktestService()
        competition_id = await _league_competition_id(db, league)
        return await league_service.compare_season(
            db, competition_id=competition_id, season_label=season
        )

    year = _legacy_season_year(season)
    service = BacktestService()
    comparison = await service.compare_heuristics(db, year)

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
        "season": year,
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
                        odds_coverage=r["odds_coverage"],
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
    league: Annotated[
        Optional[str],
        Query(
            description=(
                "League key (e.g. 'wafl'). Absent, empty, or 'afl' "
                "selects the legacy AFL backtest."
            )
        ),
    ] = None,
):
    """List seasons that have completed games with tips.

    AFL (no ``league``/``afl``): ``{available_years, current_year}``
    with calendar-year ints — ``current_year`` is the calendar year on
    the server, not derived from data.

    League: the same shape for that competition, with season LABEL
    strings (latest first).  Unknown league → 404; unsynced league →
    empty ``available_years``.
    """
    if league and league != "afl":
        league_service = LeagueBacktestService()
        competition_id = await _league_competition_id(db, league)
        return await league_service.get_available_seasons(
            db, competition_id=competition_id
        )

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
    league: Annotated[
        Optional[str],
        Query(
            description=(
                "League key (e.g. 'wafl'). Absent, empty, or 'afl' "
                "selects the legacy AFL backtest."
            )
        ),
    ] = None,
):
    """Return year-to-date performance for the current season.

    AFL (no ``league``/``afl``): delegates to
    :meth:`BacktestService.get_current_season_performance` unchanged.

    League: the same ``CurrentSeasonResponse`` shape from the multisport
    tables, with the season LABEL stringified (the label is
    competition-relative, not a calendar year).  Unknown league → 404;
    unsynced league → zeroed heuristics.
    """
    if league and league != "afl":
        league_service = LeagueBacktestService()
        competition_id = await _league_competition_id(db, league)
        return await league_service.get_current_season_performance(
            db, competition_id=competition_id
        )

    service = BacktestService()
    performance = await service.get_current_season_performance(db)
    return performance.model_dump(mode="json")


# ---------------------------------------------------------------------------
# GET /active-model
# ---------------------------------------------------------------------------


@router.get("/active-model")
async def get_active_model(
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Return the currently-active ``weighted_tip`` model version with its
    learned coefficients and training metadata.

    Returns ``{"active": false, "message": "..."}`` when no trained version
    exists (e.g. before the first weekly retrain has run).
    """
    service = BacktestService()
    result = await service.get_active_weighted_model(db)
    if result is None:
        return {
            "active": False,
            "message": "No active Weighted Tip model version found. "
            "The model will be trained after the first weekly retrain job runs.",
        }
    return {"active": True, "model": result}


# ---------------------------------------------------------------------------
# GET /active-boosted-model  (BT-1)
# ---------------------------------------------------------------------------


@router.get("/active-boosted-model")
async def get_boosted_active_model(
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Return the currently-active ``boosted_tip`` (XGBoost) model version
    with its global SHAP feature importances and training metadata (BT-1).

    Mirrors ``GET /active-model`` but raises 404 ``not_found`` when no
    active boosted version exists (e.g. before the first weekly retrain,
    or while ``BOOSTED_RETRAIN_ENABLED=false``) — unlike the weighted
    section, the SHAP section has no meaningful "empty" rendering, so the
    client gets the repo-standard error shape.
    """
    result = await get_active_boosted_model(db)
    if result is None:
        raise http_error(
            404,
            "not_found",
            "No active boosted_tip model version found. "
            "The model will be trained after the first weekly retrain job runs.",
        )
    return result


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
