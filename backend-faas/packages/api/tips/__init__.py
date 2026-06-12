"""Digital Ocean Function: Tips API.

Handles tip-related HTTP requests routed through DO Functions
(Apache OpenWhisk) entry point.

Routes:
    GET  /                     List tips with filters (season, round, heuristic)
    GET  /games-with-tips      Games with tips for a round
    GET  /{heuristic}          Tips by heuristic type
    POST /generate             Generate tips for a round
"""

import os
import sys
import traceback

# Make shared package importable from the function's working directory
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from sqlalchemy import select

from packages.shared.api_helpers import (
    bool_query,
    check_rate_limit,
    check_request_size,
    int_query,
    parse_request,
    response,
    segments,
    to_dict,
    validate_request,
    verify_api_key,
)
from packages.shared.cache import close_redis_pool
from packages.shared.crud import GameCRUD, ModelPredictionCRUD, TipCRUD
from packages.shared.db import _get_session_factory, dispose_engine
from packages.shared.logger import get_logger
from packages.shared.models import Game, Tip
from packages.shared.schemas import (
    ModelPrediction as ModelPredictionSchema,
)
from packages.shared.schemas import (
    TipListResponse,
    TipResponse,
)
from packages.shared.schemas.admin import TipGenerateRequest
from packages.shared.services.tip_generation import TipGenerationService

logger = get_logger(__name__)

VALID_HEURISTICS = ["best_bet", "high_risk_high_reward", "yolo"]


# ---------------------------------------------------------------------------
# Route handlers
# ---------------------------------------------------------------------------


async def _handle_games_with_tips(session, query: dict) -> dict:
    """GET /games-with-tips — games with tips for a round."""
    season = int_query(query, "season")
    round_id = int_query(query, "round")
    heuristic = query.get("heuristic", "best_bet")

    if not season or not round_id:
        return response(400, error="Both 'season' and 'round' query parameters are required")

    if heuristic and heuristic not in VALID_HEURISTICS:
        return response(
            400,
            error=f"Invalid heuristic. Must be one of: {', '.join(VALID_HEURISTICS)}",
        )

    try:
        # Lock games for this round to prevent concurrent tip generation
        async with session.begin():
            stmt = (
                select(Game)
                .where(
                    Game.season == season,
                    Game.round_id == round_id,
                )
                .with_for_update()
            )

            games_result = await session.execute(stmt)
            games = list(games_result.scalars().all())

            if not games:
                return response(200, data={"games": [], "count": 0})

            game_ids = [g.id for g in games]
            if game_ids:
                if heuristic:
                    result = await session.execute(
                        select(Tip).where(
                            Tip.game_id.in_(game_ids),
                            Tip.heuristic == heuristic,
                        )
                    )
                else:
                    result = await session.execute(select(Tip).where(Tip.game_id.in_(game_ids)))
                tips = list(result.scalars().all())
            else:
                tips = []

            if not tips:
                logger.info(
                    f"No tips found for round {round_id}, season {season}. Returning pending state."
                )

        tips_by_game = {tip.game_id: tip for tip in tips}

        # Fetch model predictions for all games
        predictions_by_game = await ModelPredictionCRUD.get_by_games(session, game_ids)

        model_predictions_by_game = {}
        for gid, predictions_db in predictions_by_game.items():
            model_predictions_by_game[gid] = [
                ModelPredictionSchema(
                    model_name=p.model_name,
                    winner=p.winner,
                    confidence=p.confidence,
                    margin=p.margin,
                )
                for p in predictions_db
            ]

        games_with_tips = []
        for game in games:
            game_dict = {
                "id": game.id,
                "slug": game.slug,
                "squiggle_id": game.squiggle_id,
                "round_id": game.round_id,
                "season": game.season,
                "home_team": game.home_team,
                "away_team": game.away_team,
                "home_score": game.home_score,
                "away_score": game.away_score,
                "venue": game.venue,
                "date": game.date.isoformat() if game.date is not None else None,
                "completed": game.completed,
                "tip": None,
                "model_predictions": model_predictions_by_game.get(game.id, []),
            }

            if game.id in tips_by_game:
                tip = tips_by_game[game.id]
                game_dict["tip"] = {
                    "id": tip.id,
                    "heuristic": tip.heuristic,
                    "selected_team": tip.selected_team,
                    "margin": tip.margin,
                    "confidence": tip.confidence,
                    "explanation": tip.explanation,
                    "created_at": tip.created_at.isoformat()
                    if tip.created_at is not None
                    else None,
                }

            games_with_tips.append(game_dict)

        return response(200, data={"games": games_with_tips, "count": len(games_with_tips)})

    except Exception as e:
        logger.error(f"Error in get_games_with_tips: {e}", exc_info=True)
        return response(500, error="An error occurred while fetching tips")


async def _handle_list_tips(session, query: dict) -> dict:
    """GET / — list tips with optional filtering."""
    season = int_query(query, "season")
    round_id = int_query(query, "round")
    heuristic = query.get("heuristic")

    try:
        if season and round_id:
            tips = await TipCRUD.get_by_round(session, season, round_id)
        elif heuristic:
            tips = await TipCRUD.get_by_heuristic(session, heuristic)
        else:
            tips = await TipCRUD.get_by_heuristic(session, "best_bet", limit=50)

        resp = TipListResponse(
            tips=[TipResponse.model_validate(t) for t in tips],
            count=len(tips),
        )
        return response(200, data=to_dict(resp))

    except Exception as e:
        logger.error(f"Error in get_tips: {e}", exc_info=True)
        return response(500, error="An error occurred while fetching tips")


async def _handle_generate_tips(session, query: dict, body: dict) -> dict:
    """POST /generate — generate tips for a specific round."""
    # Merge query params into body for unified validation.
    # Accept both "round" (legacy/external) and "round_id" (schema) keys.
    merged = {**body}
    if not merged.get("season"):
        merged["season"] = int_query(query, "season")
    if not merged.get("round_id"):
        merged["round_id"] = merged.pop("round", None) or int_query(query, "round")
    if not merged.get("regenerate"):
        merged["regenerate"] = bool_query(query, "regenerate") or False

    validated, err = validate_request(merged, TipGenerateRequest)
    if err:
        return err

    season = validated.season
    round_id = validated.round_id
    regenerate = validated.regenerate

    # Validate heuristics if provided
    if validated.heuristics:
        invalid = [h for h in validated.heuristics if h not in VALID_HEURISTICS]
        if invalid:
            return response(
                400,
                error=(
                    f"Invalid heuristic(s): {', '.join(invalid)}. "
                    f"Must be one of: {', '.join(VALID_HEURISTICS)}"
                ),
            )

    if not season or not round_id:
        return response(400, error="Both 'season' and 'round' are required")

    try:
        games = await GameCRUD.get_by_round(session, season, round_id)
        if not games:
            return response(
                404,
                error=f"No games found for season {season}, round {round_id}",
            )

        generation_service = TipGenerationService(
            db_session=session,
            season=season,
            round_id=round_id,
        )

        stats = await generation_service.generate_for_round(
            season=season,
            round_id=round_id,
            regenerate=regenerate,
        )

        logger.info(
            f"On-demand tip generation for season {season}, round {round_id}: "
            f"{stats['tips_created']} created, {stats['tips_skipped']} skipped"
        )

        return response(
            200,
            data={
                "status": "success",
                "season": season,
                "round_id": round_id,
                "games_processed": stats["games_processed"],
                "tips_created": stats["tips_created"],
                "tips_skipped": stats["tips_skipped"],
                "tips_updated": stats.get("tips_updated", 0),
                "errors": stats.get("errors", []),
            },
        )

    except Exception as e:
        logger.error(f"Error in generate_tips: {e}", exc_info=True)
        return response(500, error="An error occurred while generating tips")


async def _handle_tips_by_heuristic(session, heuristic: str, query: dict) -> dict:
    """GET /{heuristic} — tips by heuristic type."""
    if heuristic not in VALID_HEURISTICS:
        return response(
            400,
            error=(
                f"Invalid heuristic '{heuristic}'. "
                f"Must be one of: {', '.join(sorted(VALID_HEURISTICS))}"
            ),
        )

    limit = int_query(query, "limit") or 100
    limit = max(1, min(500, limit))

    tips = await TipCRUD.get_by_heuristic(session, heuristic, limit=limit)
    resp = TipListResponse(
        tips=[TipResponse.model_validate(t) for t in tips],
        count=len(tips),
    )
    return response(200, data=to_dict(resp))


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
        return response(204, request_args=args, allowed_methods=_PUBLIC_METHODS)

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

            # POST /generate — requires API key auth
            if method == "POST" and len(segs) == 1 and segs[0] == "generate":
                if not verify_api_key(headers, query, body):
                    return response(
                        401,
                        error="Invalid or missing API key",
                        request_args=args,
                        allowed_methods=_PUBLIC_METHODS,
                    )
                return await _handle_generate_tips(session, query, body)

            # GET /games-with-tips
            if method == "GET" and len(segs) == 1 and segs[0] == "games-with-tips":
                return await _handle_games_with_tips(session, query)

            # GET /{heuristic}
            if method == "GET" and len(segs) == 1 and segs[0] in VALID_HEURISTICS:
                return await _handle_tips_by_heuristic(session, segs[0], query)

            # GET / — list tips
            if method == "GET" and len(segs) == 0:
                return await _handle_list_tips(session, query)

            return response(
                404, error="Not found", request_args=args, allowed_methods=_PUBLIC_METHODS
            )

        except Exception as e:
            had_error = True
            logger.error(f"Error in tips function: {e}\n{traceback.format_exc()}")
            return response(500, error=str(e), request_args=args, allowed_methods=_PUBLIC_METHODS)
        finally:
            await close_redis_pool(force=had_error)
            await dispose_engine(force=had_error)
