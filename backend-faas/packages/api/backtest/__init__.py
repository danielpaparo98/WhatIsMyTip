"""Digital Ocean Function: Backtest API.

Handles backtest-related HTTP requests routed through DO Functions
(Apache OpenWhisk) entry point.

Routes:
    GET  /                     Backtest results (deprecated)
    GET  /current-season       Current season performance
    GET  /compare              Compare heuristics
    GET  /table                Detailed round-by-round table
    GET  /seasons              Available seasons
    GET  /{heuristic}          Backtest by heuristic (deprecated)
"""

import json
import os
import sys
import traceback
from datetime import datetime
from urllib.parse import parse_qs

# Make shared package importable from the function's working directory
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from packages.shared.db import _get_session_factory, dispose_engine
from packages.shared.cache import close_redis_pool
from packages.shared.config import settings
from packages.shared.logger import get_logger
from packages.shared.schemas import (
    BacktestListResponse,
    AvailableSeasonsResponse,
    BacktestTableResponse,
    BacktestTableData,
    BacktestTableRow,
    CurrentSeasonResponse,
)
from packages.shared.services.backtest import BacktestService

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_request(args: dict) -> tuple:
    """Parse DO Function args into (method, path, query, body, headers)."""
    method = args.get("__ow_method", "GET").upper()
    path = args.get("__ow_path", "/").strip("/")
    raw_query = args.get("__ow_query", "")
    if isinstance(raw_query, str) and raw_query:
        parsed = parse_qs(raw_query)
        query = {k: v[0] if len(v) == 1 else v for k, v in parsed.items()}
    elif isinstance(raw_query, dict):
        query = raw_query
    else:
        query = {}
    body_raw = args.get("__ow_body", "")
    headers = args.get("__ow_headers", {}) or {}

    body: dict = {}
    if body_raw:
        if isinstance(body_raw, str):
            try:
                body = json.loads(body_raw)
            except json.JSONDecodeError:
                body = {}
        elif isinstance(body_raw, dict):
            body = body_raw

    return method, path, query, body, headers


def _response(status_code: int, data=None, error: str | None = None) -> dict:
    """Build a DO Function response dict."""
    body = {}
    if error:
        body = {"error": error}
    elif data is not None:
        body = data

    return {
        "statusCode": status_code,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": settings.cors_origins[0] if settings.cors_origins else "*",
            "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
            "Access-Control-Allow-Headers": "Content-Type, X-API-Key",
        },
        "body": body,
    }


def _segments(path: str) -> list[str]:
    """Split path into non-empty segments."""
    return [s for s in path.split("/") if s]


def _to_dict(obj):
    """Recursively convert Pydantic models / lists to JSON-safe dicts."""
    if obj is None:
        return None
    if hasattr(obj, "model_dump"):
        return obj.model_dump(mode="json")
    if isinstance(obj, list):
        return [_to_dict(item) for item in obj]
    if isinstance(obj, dict):
        return {k: _to_dict(v) for k, v in obj.items()}
    return obj


def _int_query(query: dict, key: str) -> int | None:
    val = query.get(key)
    if val is None:
        return None
    try:
        return int(val)
    except (ValueError, TypeError):
        return None


# ---------------------------------------------------------------------------
# Route handlers
# ---------------------------------------------------------------------------

async def _handle_backtest_results(session, query: dict) -> dict:
    """GET / — deprecated, returns empty results."""
    return _response(
        200,
        data=_to_dict(BacktestListResponse(results=[], count=0)),
    )


async def _handle_current_season(session) -> dict:
    """GET /current-season — current season performance with projections."""
    service = BacktestService()
    performance = await service.get_current_season_performance(session)
    return _response(200, data=_to_dict(performance))


async def _handle_compare(session, query: dict) -> dict:
    """GET /compare — compare all heuristics for a season."""
    season = _int_query(query, "season")
    if not season:
        return _response(400, error="'season' query parameter is required")

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

    return _response(
        200,
        data={
            "season": season,
            "comparison": comparison,
            "best_overall": best,
        },
    )


async def _handle_table(session, query: dict) -> dict:
    """GET /table — detailed round-by-round table."""
    season = _int_query(query, "season")
    if not season:
        return _response(400, error="'season' query parameter is required")

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
    return _response(200, data=_to_dict(resp))


async def _handle_seasons(session) -> dict:
    """GET /seasons — available seasons."""
    service = BacktestService()
    available_years = await service.get_available_seasons(session)
    current_year = datetime.now().year

    resp = AvailableSeasonsResponse(
        available_years=available_years,
        current_year=current_year,
    )
    return _response(200, data=_to_dict(resp))


async def _handle_by_heuristic(session, heuristic: str) -> dict:
    """GET /{heuristic} — deprecated, returns empty results."""
    return _response(
        200,
        data=_to_dict(BacktestListResponse(results=[], count=0)),
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

async def main(args: dict) -> dict:
    """DO Function entry point."""
    method, path, query, body, headers = _parse_request(args)
    segs = _segments(path)

    # Handle CORS preflight
    if method == "OPTIONS":
        return _response(204)

    factory = _get_session_factory()
    async with factory() as session:
        try:
            # ---- Routing ----

            if method != "GET":
                return _response(405, error="Method not allowed")

            # Named routes (must be checked before catch-all {heuristic})
            if len(segs) == 1:
                named = segs[0]
                if named == "current-season":
                    return await _handle_current_season(session)
                if named == "compare":
                    return await _handle_compare(session, query)
                if named == "table":
                    return await _handle_table(session, query)
                if named == "seasons":
                    return await _handle_seasons(session)

            # GET / — deprecated root
            if len(segs) == 0:
                return await _handle_backtest_results(session, query)

            # GET /{heuristic} — deprecated catch-all
            if len(segs) == 1:
                return await _handle_by_heuristic(session, segs[0])

            return _response(404, error="Not found")

        except Exception as e:
            logger.error(f"Error in backtest function: {e}\n{traceback.format_exc()}")
            return _response(500, error=str(e))
        finally:
            await close_redis_pool()
            await dispose_engine()
