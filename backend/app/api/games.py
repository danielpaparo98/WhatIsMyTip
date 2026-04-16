import asyncio
import time
import logging
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, and_
from slowapi import Limiter
from slowapi.util import get_remote_address
from typing import Optional, List
from datetime import datetime, timezone

from app.db import get_db
from app.crud import GameCRUD, TipCRUD, ModelPredictionCRUD, MatchAnalysisCRUD
from app.schemas import GameResponse, GameListResponse, GameDetailResponse, ModelPrediction as ModelPredictionSchema
from app.schemas.match_analysis import MatchAnalysisResponse
from app.models import Game

router = APIRouter()
limiter = Limiter(key_func=get_remote_address)
logger = logging.getLogger(__name__)


@router.get("")
@limiter.limit("60/minute")
async def get_games(
    request: Request,
    season: Optional[int] = Query(None, description="Filter by season year"),
    round_id: Optional[int] = Query(None, alias="round", description="Filter by round number"),
    upcoming: bool = Query(False, description="Get only upcoming games"),
    latest: bool = Query(False, description="Get latest round info"),
    db: AsyncSession = Depends(get_db),
):
    """Get games with optional filtering."""
    if latest:
        current_year = datetime.now().year
        now = datetime.now(timezone.utc)

        # Step 1: Find the round containing the nearest upcoming/future game
        future_game = await db.execute(
            select(Game.round_id, Game.season)
            .where(Game.date >= now)
            .order_by(Game.date.asc())
            .limit(1)
        )
        target = future_game.first()
        has_upcoming = target is not None

        # Step 2: Fallback - if no future games, find the round with the most recent past game
        if not target:
            past_game = await db.execute(
                select(Game.round_id, Game.season)
                .where(Game.date < now)
                .order_by(Game.date.desc())
                .limit(1)
            )
            target = past_game.first()

        if target:
            # Get the full round info with game count
            result = await db.execute(
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
                return {
                    "season": row.season,
                    "round_id": row.round_id,
                    "game_count": row.game_count,
                    "is_current_year": row.season == current_year,
                    "has_upcoming": has_upcoming,
                }

        return {
            "season": None,
            "round_id": None,
            "game_count": 0,
            "is_current_year": False,
            "has_upcoming": False,
        }
    elif upcoming:
        games = await GameCRUD.get_upcoming(db)
    elif season and round_id:
        games = await GameCRUD.get_by_round(db, season, round_id)
    elif season:
        games = await GameCRUD.get_by_season(db, season)
    else:
        # Return all games (limit to recent)
        games = await GameCRUD.get_upcoming(db)
    
    return GameListResponse(
        games=[GameResponse.model_validate(g) for g in games],
        count=len(games),
    )


@router.get("/{slug}", response_model=GameResponse)
@limiter.limit("60/minute")
async def get_game(
    request: Request,
    slug: str,
    db: AsyncSession = Depends(get_db),
):
    """Get a specific game by slug."""
    game = await GameCRUD.get_by_slug(db, slug)
    if not game:
        raise HTTPException(status_code=404, detail="Game not found")
    return GameResponse.model_validate(game)


@router.get("/{slug}/detail", response_model=GameDetailResponse)
@limiter.limit("60/minute")
async def get_game_detail(
    request: Request,
    slug: str,
    db: AsyncSession = Depends(get_db),
):
    """
    Get comprehensive game details including:
    - Game information
    - All tips for all heuristics (best_bet, yolo, high_risk_high_reward)
    - Model predictions from all 4 ML models (elo, form, home_advantage, value)
    """
    start_time = time.time()
    logger.debug(f"get_game_detail: STARTING for slug={slug}")
    
    # 1. Fetch game by slug
    game_start = time.time()
    game = await GameCRUD.get_by_slug(db, slug)
    game_time = time.time() - game_start
    logger.debug(f"get_game_detail: Game fetch took {game_time:.4f}s")
    
    if not game:
        raise HTTPException(status_code=404, detail="Game not found")
    
    game_id = game.id
    
    # 2. Fetch all tips for this game (all heuristics)
    tips_start = time.time()
    tips = await TipCRUD.get_by_game(db, game_id)
    tips_time = time.time() - tips_start
    logger.debug(f"get_game_detail: Tips fetch took {tips_time:.4f}s, found {len(tips)} tips")
    
    # 3. Fetch stored model predictions
    model_predictions_start = time.time()
    model_predictions_db = await ModelPredictionCRUD.get_by_game(db, game_id)
    
    # Convert database models to schema
    model_predictions_list = [
        ModelPredictionSchema(
            model_name=p.model_name,
            winner=p.winner,
            confidence=p.confidence,
            margin=p.margin
        )
        for p in model_predictions_db
    ]
    
    model_predictions_time = time.time() - model_predictions_start
    logger.debug(f"get_game_detail: Model predictions fetch took {model_predictions_time:.4f}s, found {len(model_predictions_list)} predictions")
    
    # 4. Fetch match analysis if available
    analysis_start = time.time()
    match_analysis_db = await MatchAnalysisCRUD.get_by_game_id(db, game_id)
    match_analysis = MatchAnalysisResponse.model_validate(match_analysis_db) if match_analysis_db else None
    analysis_time = time.time() - analysis_start
    logger.debug(f"get_game_detail: Match analysis fetch took {analysis_time:.4f}s")

    total_time = time.time() - start_time
    logger.debug(f"get_game_detail: COMPLETED in {total_time:.4f}s")
    
    # 5. Return combined response
    return GameDetailResponse(
        game=GameResponse.model_validate(game),
        tips=[tip for tip in tips],
        model_predictions=model_predictions_list,
        match_analysis=match_analysis
    )
