from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from slowapi import Limiter
from slowapi.util import get_remote_address
from typing import Optional

from app.db import get_db
from app.crud import GameCRUD
from app.schemas import GameResponse, GameListResponse

router = APIRouter()
limiter = Limiter(key_func=get_remote_address)


@router.get("", response_model=GameListResponse)
@limiter.limit("60/minute")
async def get_games(
    season: Optional[int] = Query(None, description="Filter by season year"),
    round_id: Optional[int] = Query(None, alias="round", description="Filter by round number"),
    upcoming: bool = Query(False, description="Get only upcoming games"),
    db: AsyncSession = Depends(get_db),
):
    """Get games with optional filtering."""
    if upcoming:
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
    game_id: int,
    db: AsyncSession = Depends(get_db),
):
    """Get a specific game by ID."""
    game = await GameCRUD.get_by_id(db, game_id)
    if not game:
        raise HTTPException(status_code=404, detail="Game not found")
    return GameResponse.model_validate(game)
