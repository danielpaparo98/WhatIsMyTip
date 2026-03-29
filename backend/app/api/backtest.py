from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from slowapi import Limiter
from slowapi.util import get_remote_address
from typing import Optional

from app.db import get_db
from app.crud import BacktestCRUD
from app.schemas import BacktestResponse, BacktestListResponse

router = APIRouter()
limiter = Limiter(key_func=get_remote_address)


@router.get("", response_model=BacktestListResponse)
@limiter.limit("60/minute")
async def get_backtest_results(
    heuristic: Optional[str] = Query(None, description="Filter by heuristic type"),
    db: AsyncSession = Depends(get_db),
):
    """Get backtest results with optional filtering."""
    if heuristic:
        results = await BacktestCRUD.get_by_heuristic(db, heuristic)
    else:
        results = await BacktestCRUD.get_latest(db)
    
    return BacktestListResponse(
        results=[BacktestResponse.model_validate(r) for r in results],
        count=len(results),
    )


@router.get("/{heuristic}", response_model=BacktestListResponse)
@limiter.limit("60/minute")
async def get_backtest_by_heuristic(
    heuristic: str,
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
):
    """Get backtest results by heuristic type."""
    results = await BacktestCRUD.get_by_heuristic(db, heuristic, limit=limit)
    return BacktestListResponse(
        results=[BacktestResponse.model_validate(r) for r in results],
        count=len(results),
    )
