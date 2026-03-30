from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks, Request
from sqlalchemy.ext.asyncio import AsyncSession
from slowapi import Limiter
from slowapi.util import get_remote_address
from typing import Optional
from pydantic import BaseModel

from app.db import get_db, engine
from app.models import Base
from app.squiggle import SquiggleClient
from app.crud import GameCRUD


class SyncGamesResponse(BaseModel):
    message: str
    games_count: int


router = APIRouter()
limiter = Limiter(key_func=get_remote_address)


@router.post("/games", response_model=SyncGamesResponse)
async def sync_games(
    request: Request,
    background_tasks: BackgroundTasks,
    year: Optional[int] = None,
    db: AsyncSession = Depends(get_db),
):
    """Sync games from Squiggle API."""
    client = SquiggleClient()
    
    try:
        games = await GameCRUD.sync_from_squiggle(db, client, year)
        return SyncGamesResponse(
            message=f"Synced {len(games)} games from Squiggle API",
            games_count=len(games),
        )
    except Exception as e:
        print(f"DEBUG: Error during sync: {e}")
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        await client.close()


@router.post("/database")
@limiter.limit("5/minute")
async def init_database(request: Request):
    """Initialize database tables."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return {"message": "Database initialized successfully"}
