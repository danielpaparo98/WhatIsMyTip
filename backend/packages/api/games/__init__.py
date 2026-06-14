"""Digital Ocean Function: Games API.

Handles game-related HTTP requests routed through DO Functions
(Apache OpenWhisk) entry point.

Routes:
    GET  /                     List games (with filters: season, round, upcoming, latest)
    GET  /{slug}               Get game by slug
    GET  /{slug}/detail        Full game detail with tips, predictions, analysis
"""

import os
import sys
import time
import traceback
from datetime import datetime
from zoneinfo import ZoneInfo

# Make shared package importable from the function's working directory
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from sqlalchemy import and_, func, select

from packages.shared.api_helpers import (
    bool_query,
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
from packages.shared.config import settings
from packages.shared.crud import GameCRUD, MatchAnalysisCRUD, ModelPredictionCRUD, TipCRUD
from packages.shared.db import _get_session_factory, dispose_engine
from packages.shared.logger import get_logger
from packages.shared.models import Game, MatchWeather
from packages.shared.schemas import (
    GameDetailResponse,
    GameListResponse,
    GameResponse,
    WeatherResponse,
)
from packages.shared.schemas import (
    ModelPrediction as ModelPredictionSchema,
)
from packages.shared.schemas.match_analysis import MatchAnalysisResponse
from packages.shared.schemas.query import GamesQuery

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Route handlers
# ---------------------------------------------------------------------------

async def _handle_list_games(session, query: dict) -> dict:
    """GET / — list games with optional filtering."""
    latest = bool_query(query, "latest")
    upcoming = bool_query(query, "upcoming")
    season = int_query(query, "season")
    round_id = int_query(query, "round")

    if latest:
        current_year = datetime.now().year
        now = datetime.now(tz=ZoneInfo(settings.cron_timezone)).replace(tzinfo=None)

        # Find the nearest upcoming game
        future_game = await session.execute(
            select(Game.round_id, Game.season)
            .where(and_(Game.date >= now, not Game.completed))
            .order_by(Game.date.asc())
            .limit(1)
        )
        target = future_game.first()
        has_upcoming = target is not None

        if not target:
            past_game = await session.execute(
                select(Game.round_id, Game.season)
                .where(Game.date < now)
                .order_by(Game.date.desc())
                .limit(1)
            )
            target = past_game.first()

        if target:
            result = await session.execute(
                select(
                    Game.season,
                    Game.round_id,
                    func.count(Game.id).label("game_count"),
                )
                .where(and_(Game.round_id == target.round_id, Game.season == target.season))
                .group_by(Game.season, Game.round_id)
            )
            row = result.first()
            if row:
                return response(
                    200,
                    data={
                        "season": row.season,
                        "round_id": row.round_id,
                        "game_count": row.game_count,
                        "is_current_year": row.season == current_year,
                        "has_upcoming": has_upcoming,
                    },
                )

        return response(
            200,
            data={
                "season": None,
                "round_id": None,
                "game_count": 0,
                "is_current_year": False,
                "has_upcoming": False,
            },
        )

    if upcoming:
        games = await GameCRUD.get_upcoming(session)
    elif season and round_id:
        games = await GameCRUD.get_by_round(session, season, round_id)
    elif season:
        games = await GameCRUD.get_by_season(session, season)
    else:
        games = await GameCRUD.get_upcoming(session)

    resp = GameListResponse(
        games=[GameResponse.model_validate(g) for g in games],
        count=len(games),
    )
    return response(200, data=to_dict(resp))


async def _handle_get_game(session, slug: str) -> dict:
    """GET /{slug} — get a single game by slug."""
    game = await GameCRUD.get_by_slug(session, slug)
    if not game:
        return response(404, error="Game not found")
    resp = GameResponse.model_validate(game)
    return response(200, data=to_dict(resp))


async def _handle_game_detail(session, slug: str) -> dict:
    """GET /{slug}/detail — full game detail with tips, predictions, analysis."""
    start_time = time.time()
    logger.debug("get_game_detail: STARTING for slug=%s", slug)

    game = await GameCRUD.get_by_slug(session, slug)
    if not game:
        return response(404, error="Game not found")

    game_id = game.id

    # Fetch tips
    tips = await TipCRUD.get_by_game(session, game_id)

    # Fetch model predictions
    model_predictions_db = await ModelPredictionCRUD.get_by_game(session, game_id)
    model_predictions_list = [
        ModelPredictionSchema(
            model_name=p.model_name,
            winner=p.winner,
            confidence=p.confidence,
            margin=p.margin,
        )
        for p in model_predictions_db
    ]

    # Fetch match analysis
    match_analysis_db = await MatchAnalysisCRUD.get_by_game_id(session, game_id)
    match_analysis = (
        MatchAnalysisResponse.model_validate(match_analysis_db)
        if match_analysis_db
        else None
    )

    # Fetch weather data
    weather_db = await session.execute(
        select(MatchWeather).where(MatchWeather.game_id == game_id)
    )
    weather_row = weather_db.scalar_one_or_none()
    weather = WeatherResponse.model_validate(weather_row) if weather_row else None

    total_time = time.time() - start_time
    logger.debug("get_game_detail: COMPLETED in %.4fs", total_time)

    resp = GameDetailResponse(
        game=GameResponse.model_validate(game),
        tips=[t for t in tips],
        model_predictions=model_predictions_list,
        match_analysis=match_analysis,
        weather=weather,
    )
    return response(200, data=to_dict(resp))


_PUBLIC_METHODS = ["GET", "OPTIONS"]


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

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

    # Support {"action": "health"} via POST for environments without path routing
    if body.get("action") == "health":
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
            if method == "GET" and len(segs) == 0:
                # Validate query parameters
                validated, err = validate_request(query, GamesQuery)
                if err:
                    return err
                return await _handle_list_games(session, query)

            if method == "GET" and len(segs) == 1:
                slug = segs[0]
                return await _handle_get_game(session, slug)

            if method == "GET" and len(segs) == 2 and segs[1] == "detail":
                slug = segs[0]
                return await _handle_game_detail(session, slug)

            return response(404, error="Not found", allowed_methods=_PUBLIC_METHODS)

        except Exception as e:
            had_error = True
            logger.error("Error in games function: %s\n%s", e, traceback.format_exc())
            return response(500, error="Internal server error", allowed_methods=_PUBLIC_METHODS)
        finally:
            await close_redis_pool(force=had_error)
            await dispose_engine(force=had_error)
