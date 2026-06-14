"""Digital Ocean Function: Backtest API.

Handles backtest-related HTTP requests routed through DO Functions
(Apache OpenWhisk) entry point.

Routes:
    GET  /                     Backtest results (deprecated)
    GET  /current-season       Current season performance
    GET  /compare              Compare heuristics
    GET  /model-compare        Compare individual ML models
    GET  /table                Detailed round-by-round table
    GET  /seasons              Available seasons
    GET  /{heuristic}          Backtest by heuristic (deprecated)
"""

import os
import sys
import traceback
from datetime import datetime

# Make shared package importable from the function's working directory
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from packages.shared.api_helpers import (
    check_rate_limit,
    check_request_size,
    handle_health,
    int_query,
    parse_request,
    response,
    segments,
    to_dict,
    validate_request,
)
from packages.shared.cache import close_redis_pool
from packages.shared.db import _get_session_factory, dispose_engine
from packages.shared.logger import get_logger
from packages.shared.schemas.query import BacktestCompareQuery, BacktestModelCompareQuery, BacktestTableQuery
from packages.shared.schemas import (
    AvailableSeasonsResponse,
    BacktestListResponse,
    BacktestTableData,
    BacktestTableResponse,
    BacktestTableRow,
)
from packages.shared.services.backtest import BacktestService

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Route handlers
# ---------------------------------------------------------------------------

async def _handle_backtest_results(session, query: dict) -> dict:
    """GET / — deprecated, returns empty results."""
    return response(
        200,
        data=to_dict(BacktestListResponse(results=[], count=0)),
    )


async def _handle_current_season(session) -> dict:
    """GET /current-season — current season performance with projections."""
    service = BacktestService()
    performance = await service.get_current_season_performance(session)
    return response(200, data=to_dict(performance))


async def _handle_compare(session, query: dict) -> dict:
    """GET /compare — compare all heuristics for a season."""
    season = int_query(query, "season")
    if not season:
        return response(400, error="'season' query parameter is required")

    service = BacktestService()
    comparison = await service.compare_heuristics(session, season)

    # Find best performing heuristic
    if comparison:
        best_heuristic = max(
            comparison.items(),
            key=lambda x: x[1]["overall_accuracy"],
        )
        best = {
            "heuristic": best_heuristic[0],
            "accuracy": best_heuristic[1]["overall_accuracy"],
            "profit": best_heuristic[1]["total_profit"],
        }
    else:
        best = {"heuristic": None, "accuracy": 0.0, "profit": 0.0}

    return response(
        200,
        data={
            "season": season,
            "comparison": comparison,
            "best_overall": best,
        },
    )


async def _handle_model_compare(session, query: dict) -> dict:
    """GET /model-compare — compare all individual ML models for a season."""
    season = int_query(query, "season")
    if not season:
        return response(400, error="'season' query parameter is required")

    service = BacktestService()
    comparison = await service.compare_models(session, season)

    # Find best performing model
    if comparison:
        best_model = comparison[0]  # Already sorted by accuracy desc
        best = {
            "model_name": best_model["model_name"],
            "accuracy": best_model["overall_accuracy"],
            "profit": best_model["total_profit"],
        }
    else:
        best = {"model_name": None, "accuracy": 0.0, "profit": 0.0}

    return response(
        200,
        data={
            "season": season,
            "comparison": comparison,
            "best_overall": best,
        },
    )


async def _handle_table(session, query: dict) -> dict:
    """GET /table — detailed round-by-round table."""
    season = int_query(query, "season")
    if not season:
        return response(400, error="'season' query parameter is required")

    service = BacktestService()

    heuristics_list = []
    for heuristic in service.orchestrator.get_available_heuristics():
        round_data = await service.get_round_by_round_data(session, season, heuristic)

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
    return response(200, data=to_dict(resp))


async def _handle_seasons(session) -> dict:
    """GET /seasons — available seasons."""
    service = BacktestService()
    available_years = await service.get_available_seasons(session)
    current_year = datetime.now().year

    resp = AvailableSeasonsResponse(
        available_years=available_years,
        current_year=current_year,
    )
    return response(200, data=to_dict(resp))


async def _handle_by_heuristic(session, heuristic: str) -> dict:
    """GET /{heuristic} — deprecated, returns empty results."""
    return response(
        200,
        data=to_dict(BacktestListResponse(results=[], count=0)),
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

_PUBLIC_METHODS = ["GET", "OPTIONS"]


async def main(args: dict) -> dict:
    """DO Function entry point."""
    method, path, query, body, headers = parse_request(args)
    segs = segments(path)

    # Handle CORS preflight
    if method == "OPTIONS":
        return response(204, allowed_methods=_PUBLIC_METHODS)

    # Health check — uses shared helper (no rate limiting or DB session required)
    if method == "GET" and segs == ["health"]:
        return await handle_health(request_args=args)

    # Reject malformed JSON bodies early
    if query.get("_body_parse_error"):
        return response(400, error=query["_body_parse_error"], request_args=args, allowed_methods=_PUBLIC_METHODS)

    # Security checks — request size then rate limit
    size_error = check_request_size(args)
    if size_error:
        return size_error

    rate_limit_response = await check_rate_limit(args)
    if rate_limit_response:
        return rate_limit_response

    factory = _get_session_factory()
    async with factory() as session:
        had_error = False
        try:
            # ---- Routing ----

            if method != "GET":
                return response(405, error="Method not allowed", allowed_methods=_PUBLIC_METHODS)

            # Named routes (must be checked before catch-all {heuristic})
            if len(segs) == 1:
                named = segs[0]
                if named == "current-season":
                    return await _handle_current_season(session)
                if named == "compare":
                    validated, err = validate_request(query, BacktestCompareQuery)
                    if err:
                        return err
                    return await _handle_compare(session, query)
                if named == "model-compare":
                    validated, err = validate_request(query, BacktestModelCompareQuery)
                    if err:
                        return err
                    return await _handle_model_compare(session, query)
                if named == "table":
                    validated, err = validate_request(query, BacktestTableQuery)
                    if err:
                        return err
                    return await _handle_table(session, query)
                if named == "seasons":
                    return await _handle_seasons(session)

            # GET / — deprecated root
            if len(segs) == 0:
                return await _handle_backtest_results(session, query)

            # GET /{heuristic} — deprecated catch-all
            if len(segs) == 1:
                return await _handle_by_heuristic(session, segs[0])

            return response(404, error="Not found", allowed_methods=_PUBLIC_METHODS)

        except Exception as e:
            had_error = True
            logger.error("Error in backtest function: %s\n%s", e, traceback.format_exc())
            return response(500, error="Internal server error", allowed_methods=_PUBLIC_METHODS)
        finally:
            await close_redis_pool(force=had_error)
            await dispose_engine(force=had_error)
