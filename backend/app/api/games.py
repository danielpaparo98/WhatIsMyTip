import asyncio
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, func
from slowapi import Limiter
from slowapi.util import get_remote_address
from typing import Optional, List
from datetime import datetime

from app.db import get_db
from app.crud import GameCRUD, TipCRUD
from app.schemas import GameResponse, GameListResponse, GameDetailResponse, ModelPrediction
from app.models import Game
from app.orchestrator import ModelOrchestrator

router = APIRouter()
limiter = Limiter(key_func=get_remote_address)


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
        
        # Step 1: Try to find upcoming games in the current year (earliest date)
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
        
        # Step 2: Try to find the latest round in the current year (even if completed)
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
        
        # Step 3: Fall back to the most recent round from any year
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
    # 1. Fetch game by id
    game = await GameCRUD.get_by_id(db, game_id)
    if not game:
        raise HTTPException(status_code=404, detail="Game not found")
    
    # 2. Fetch all tips for this game (all heuristics)
    tips = await TipCRUD.get_by_game(db, game_id)
    
    # 3. Use ModelOrchestrator to get model predictions from all 4 models
    orchestrator = ModelOrchestrator()
    
    # Get predictions from each model sequentially (to avoid DB session conflicts)
    model_predictions: List[ModelPrediction] = []
    for model in orchestrator.models:
        winner, confidence, margin = await model.predict(game)
        model_predictions.append(
            ModelPrediction(
                model_name=model.get_name(),
                winner=winner,
                confidence=confidence,
                margin=margin
            )
        )
    
    # 4. Return combined response
    return GameDetailResponse(
        game=GameResponse.model_validate(game),
        tips=[tip for tip in tips],
        model_predictions=model_predictions
    )
