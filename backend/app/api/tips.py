from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from slowapi import Limiter
from slowapi.util import get_remote_address
from typing import Optional

from app.db import get_db
from app.crud import TipCRUD
from app.schemas import TipResponse, TipListResponse

router = APIRouter()
limiter = Limiter(key_func=get_remote_address)


@router.get("", response_model=TipListResponse)
@limiter.limit("60/minute")
async def get_tips(
    heuristic: Optional[str] = Query(None, description="Filter by heuristic type"),
    season: Optional[int] = Query(None, description="Filter by season year"),
    round_id: Optional[int] = Query(None, alias="round", description="Filter by round number"),
    db: AsyncSession = Depends(get_db),
):
    """Get tips with optional filtering."""
    if season and round_id:
        tips = await TipCRUD.get_by_round(db, season, round_id)
    elif heuristic:
        tips = await TipCRUD.get_by_heuristic(db, heuristic)
    else:
        # Return recent tips
        tips = await TipCRUD.get_by_heuristic(db, "best_bet", limit=50)
    
    return TipListResponse(
        tips=[TipResponse.model_validate(t) for t in tips],
        count=len(tips),
    )


@router.get("/{heuristic}", response_model=TipListResponse)
@limiter.limit("60/minute")
async def get_tips_by_heuristic(
    heuristic: str,
    limit: int = Query(100, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
):
    """Get tips by heuristic type."""
    tips = await TipCRUD.get_by_heuristic(db, heuristic, limit=limit)
    return TipListResponse(
        tips=[TipResponse.model_validate(t) for t in tips],
        count=len(tips),
    )
