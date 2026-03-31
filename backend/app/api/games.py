import asyncio
import time
import logging
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, func
from slowapi import Limiter
from slowapi.util import get_remote_address
from typing import Optional, List
from datetime import datetime

from app.db import get_db
from app.crud import GameCRUD, TipCRUD, ModelPredictionCRUD
from app.schemas import GameResponse, GameListResponse, GameDetailResponse, ModelPrediction as ModelPredictionSchema
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
        
        # Step 1: Try to find upcoming games in current year (earliest date)
        result = await db.execute(
            select(
                Game.season,
                Game.round_id,
                func.count(Game.id).label("game_count"),
            )
            .where(and_(Game.completed == False, Game.season == current_year))
            .group_by(Game.season, Game.round_id)
            .order_by(Game.round_id.asc())
            .limit(1)
        )
        latest_round = result.first()
        
        if latest_round:
            return {
                "season": latest_round[0],
                "round_id": latest_round[1],
                "game_count": latest_round[2],
                "is_current_year": True,
                "has_upcoming": True,
            }
        
        # Step 2: Try to find latest round in current year (even if completed)
        result = await db.execute(
            select(
                Game.season,
                Game.round_id,
                func.count(Game.id).label("game_count"),
            )
            .where(Game.season == current_year)
            .group_by(Game.season, Game.round_id)
            .order_by(Game.round_id.desc())
            .limit(1)
        )
        latest_round = result.first()
        
        if latest_round:
            return {
                "season": latest_round[0],
                "round_id": latest_round[1],
                "game_count": latest_round[2],
                "is_current_year": True,
                "has_upcoming": False,
            }
        
        # Step 3: Fall back to most recent round from any year
        result = await db.execute(
            select(
                Game.season,
                Game.round_id,
                func.count(Game.id).label("game_count"),
            )
            .group_by(Game.season, Game.round_id)
            .order_by(Game.season.desc(), Game.round_id.desc())
            .limit(1)
        )
        latest_round = result.first()
        
        if latest_round:
            return {
                "season": latest_round[0],
                "round_id": latest_round[1],
                "game_count": latest_round[2],
                "is_current_year": False,
                "has_upcoming": False,
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


@router.get("/{game_id}", response_model=GameResponse)
@limiter.limit("60/minute")
async def get_game(
    request: Request,
    game_id: int,
    db: AsyncSession = Depends(get_db),
):
    """Get a specific game by ID."""
    game = await GameCRUD.get_by_id(db, game_id)
    if not game:
        raise HTTPException(status_code=404, detail="Game not found")
    return GameResponse.model_validate(game)


@router.get("/{game_id}/detail", response_model=GameDetailResponse)
@limiter.limit("60/minute")
async def get_game_detail(
    request: Request,
    game_id: int,
    db: AsyncSession = Depends(get_db),
):
    """
    Get comprehensive game details including:
    - Game information
    - All tips for all heuristics (best_bet, yolo, high_risk_high_reward)
    - Model predictions from all 4 ML models (elo, form, home_advantage, value)
    """
    start_time = time.time()
    logger.warning(f"get_game_detail: STARTING for game_id={game_id}")
    
    # 1. Fetch game by id
    game_start = time.time()
    game = await GameCRUD.get_by_id(db, game_id)
    game_time = time.time() - game_start
    logger.warning(f"get_game_detail: Game fetch took {game_time:.4f}s")
    
    if not game:
        raise HTTPException(status_code=404, detail="Game not found")
    
    # 2. Fetch all tips for this game (all heuristics)
    tips_start = time.time()
    tips = await TipCRUD.get_by_game(db, game_id)
    tips_time = time.time() - tips_start
    logger.warning(f"get_game_detail: Tips fetch took {tips_time:.4f}s, found {len(tips)} tips")
    
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
    logger.warning(f"get_game_detail: Model predictions fetch took {model_predictions_time:.4f}s, found {len(model_predictions_list)} predictions")
    
    total_time = time.time() - start_time
    logger.warning(f"get_game_detail: COMPLETED in {total_time:.4f}s")
    
    # 4. Return combined response
    return GameDetailResponse(
        game=GameResponse.model_validate(game),
        tips=[tip for tip in tips],
        model_predictions=model_predictions_list
    )
