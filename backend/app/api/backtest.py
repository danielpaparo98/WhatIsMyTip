from fastapi import APIRouter, Depends, Query, BackgroundTasks, Request
from sqlalchemy.ext.asyncio import AsyncSession
from slowapi import Limiter
from slowapi.util import get_remote_address
from typing import Optional

from app.db import get_db
from app.crud import BacktestCRUD
from app.schemas import BacktestResponse, BacktestListResponse
from app.services.backtest import BacktestService

router = APIRouter()
limiter = Limiter(key_func=get_remote_address)


@router.get("", response_model=BacktestListResponse)
@limiter.limit("60/minute")
async def get_backtest_results(
    request: Request,
    heuristic: Optional[str] = Query(None, description="Filter by heuristic type"),
    season: Optional[int] = Query(None, description="Filter by season year"),
    db: AsyncSession = Depends(get_db),
):
    """Get backtest results with optional filtering."""
    if heuristic:
        results = await BacktestCRUD.get_by_heuristic(db, heuristic)
    elif season:
        # Get results for a specific season
        from sqlalchemy import select
        from app.models import BacktestResult
        
        result = await db.execute(
            select(BacktestResult)
            .where(BacktestResult.season == season)
            .order_by(BacktestResult.heuristic, BacktestResult.round_id)
        )
        results = list(result.scalars().all())
    else:
        results = await BacktestCRUD.get_latest(db)
    
    return BacktestListResponse(
        results=[BacktestResponse.model_validate(r) for r in results],
        count=len(results),
    )


@router.get("/{heuristic}", response_model=BacktestListResponse)
@limiter.limit("60/minute")
async def get_backtest_by_heuristic(
    request: Request,
    heuristic: str,
    season: Optional[int] = Query(None, description="Filter by season year"),
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
):
    """Get backtest results by heuristic type."""
    if season:
        from sqlalchemy import select
        from app.models import BacktestResult
        
        result = await db.execute(
            select(BacktestResult)
            .where(
                BacktestResult.heuristic == heuristic,
                BacktestResult.season == season,
            )
            .order_by(BacktestResult.round_id)
            .limit(limit)
        )
        results = list(result.scalars().all())
    else:
        results = await BacktestCRUD.get_by_heuristic(db, heuristic, limit=limit)
    
    return BacktestListResponse(
        results=[BacktestResponse.model_validate(r) for r in results],
        count=len(results),
    )


@router.post("/run")
@limiter.limit("5/minute")
async def run_backtest(
    request: Request,
    season: int = Query(..., description="Season year to backtest"),
    round_id: Optional[int] = Query(None, alias="round", description="Round to backtest (if None, entire season)"),
    heuristic: Optional[str] = Query(None, description="Heuristic to backtest (if None, all)"),
    db: AsyncSession = Depends(get_db),
):
    """Run backtest for specified parameters."""
    service = BacktestService()
    
    if heuristic:
        # Backtest single heuristic
        if round_id:
            result = await service.backtest_round(db, season, round_id, heuristic)
            results = [result]
        else:
            results = await service.backtest_season(db, season, heuristic)
        
        summary = service.calculate_summary_stats(results)
        
        return {
            "message": f"Backtest completed for {heuristic}",
            "heuristic": heuristic,
            "season": season,
            "round": round_id,
            "results_count": len(results),
            "summary": summary,
        }
    else:
        # Backtest all heuristics
        results = await service.backtest_all_heuristics(db, season, round_id)
        
        summaries = {}
        for h, res in results.items():
            summaries[h] = service.calculate_summary_stats(res)
        
        total_results = sum(len(r) for r in results.values())
        
        return {
            "message": f"Backtest completed for all heuristics",
            "season": season,
            "round": round_id,
            "heuristics_tested": list(results.keys()),
            "total_results": total_results,
            "summaries": summaries,
        }


@router.get("/compare")
@limiter.limit("30/minute")
async def compare_heuristics(
    request: Request,
    season: int = Query(..., description="Season year to compare"),
    db: AsyncSession = Depends(get_db),
):
    """Compare all heuristics for a season."""
    service = BacktestService()
    
    comparison = await service.compare_heuristics(db, season)
    
    # Find best performing heuristic
    best_heuristic = max(
        comparison.items(),
        key=lambda x: x[1]["overall_accuracy"],
    )
    
    return {
        "season": season,
        "comparison": comparison,
        "best_overall": {
            "heuristic": best_heuristic[0],
            "accuracy": best_heuristic[1]["overall_accuracy"],
            "profit": best_heuristic[1]["total_profit"],
        },
    }
