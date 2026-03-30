from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, func
from slowapi import Limiter
from slowapi.util import get_remote_address
from typing import Optional

from app.db import get_db
from app.crud import GameCRUD
from app.schemas import GameResponse, GameListResponse
from app.models import Game

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
        # Get the most recent round with upcoming games
        result = await db.execute(
            select(
                Game.season,
                Game.round_id,
                func.count(Game.id).label("game_count"),
            )
            .where(Game.completed == False)
            .group_by(Game.season, Game.round_id)
            .order_by(Game.season.desc(), Game.round_id.desc())
            .limit(1)
        )
        latest_round = result.first()
        
        if not latest_round:
            # If no upcoming games, get the last completed round
            result = await db.execute(
                select(
                    Game.season,
                    Game.round_id,
                    func.count(Game.id).label("game_count"),
                )
                .where(Game.completed == True)
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
            }
        
        return {"season": None, "round_id": None, "game_count": 0}
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
