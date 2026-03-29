from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from sqlalchemy.ext.asyncio import AsyncSession
from slowapi import Limiter
from slowapi.util import get_remote_address
from typing import Optional

from app.db import get_db, engine
from app.models import Base
from app.squiggle import SquiggleClient
from app.crud import GameCRUD

router = APIRouter()
limiter = Limiter(key_func=get_remote_address)


@router.post("/games")
@limiter.limit("10/minute")
async def sync_games(
    background_tasks: BackgroundTasks,
    year: Optional[int] = None,
    db: AsyncSession = Depends(get_db),
):
    """Sync games from Squiggle API."""
    client = SquiggleClient()
    
    try:
        games = await GameCRUD.sync_from_squiggle(db, client, year)
        return {
            "message": f"Synced {len(games)} games from Squiggle API",
            "games_count": len(games),
        }
    finally:
        await client.close()


@router.post("/database")
@limiter.limit("5/minute")
async def init_database():
    """Initialize database tables."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return {"message": "Database initialized successfully"}
