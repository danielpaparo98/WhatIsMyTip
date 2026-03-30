from fastapi import APIRouter, Depends, Query, BackgroundTasks, Request
from sqlalchemy.ext.asyncio import AsyncSession
from slowapi import Limiter
from slowapi.util import get_remote_address
from typing import Optional
from datetime import datetime

from app.db import get_db
from app.crud import BacktestCRUD
from app.schemas import (
    BacktestResponse,
    BacktestListResponse,
    AvailableSeasonsResponse,
    BacktestTableResponse,
    BacktestTableData,
    BacktestTableRow,
    HistoricalSyncResponse,
    CurrentSeasonResponse,
)
from app.services.backtest import BacktestService
from app.squiggle import SquiggleClient

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


@router.get("/current-season", response_model=CurrentSeasonResponse)
@limiter.limit("60/minute")
async def get_current_season_performance(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Get current season performance with year-to-date results and projections."""
    service = BacktestService()
    
    performance = await service.get_current_season_performance(db)
    
    return performance


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
    
    # Check if backtest results exist for the season
    from sqlalchemy import select, func
    from app.models import BacktestResult
    
    result = await db.execute(
        select(func.count(BacktestResult.id)).where(BacktestResult.season == season)
    )
    backtest_count = result.scalar()
    
    # If no backtest results exist, sync historical data first
    if backtest_count == 0:
        squiggle_client = SquiggleClient()
        try:
            sync_result = await service.sync_historical_season(db, season, squiggle_client)
            # Run backtest after sync
            await service.backtest_all_heuristics(db, season)
        finally:
            await squiggle_client.close()
    
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


@router.get("/table", response_model=BacktestTableResponse)
@limiter.limit("30/minute")
async def get_table_data(
    request: Request,
    season: int = Query(..., description="Season year to get table data for"),
    db: AsyncSession = Depends(get_db),
):
    """Get detailed table data for all heuristics for a season."""
    # Check if backtest results exist for the season
    from sqlalchemy import select, func
    from app.models import BacktestResult
    
    result = await db.execute(
        select(func.count(BacktestResult.id)).where(BacktestResult.season == season)
    )
    backtest_count = result.scalar()
    
    # If no backtest results exist, sync historical data first
    if backtest_count == 0:
        service = BacktestService()
        squiggle_client = SquiggleClient()
        try:
            sync_result = await service.sync_historical_season(db, season, squiggle_client)
            # Run backtest after sync
            await service.backtest_all_heuristics(db, season)
        finally:
            await squiggle_client.close()
    
    results = await BacktestCRUD.get_table_data(db, season)
    
    # Group results by heuristic
    heuristics_data: dict[str, list] = {}
    for result in results:
        if result.heuristic not in heuristics_data:
            heuristics_data[result.heuristic] = []
        
        # Calculate totals for this heuristic
        total_profit = sum(r.profit for r in heuristics_data[result.heuristic] + [result])
        total_tips = sum(r.tips_made for r in heuristics_data[result.heuristic] + [result])
        total_correct = sum(r.tips_correct for r in heuristics_data[result.heuristic] + [result])
        total_accuracy = total_correct / total_tips if total_tips > 0 else 0.0
        
        heuristics_data[result.heuristic].append({
            "round_id": result.round_id,
            "tips_made": result.tips_made,
            "tips_correct": result.tips_correct,
            "accuracy": result.accuracy,
            "profit": result.profit,
            "_total_profit": total_profit,
            "_total_accuracy": total_accuracy,
        })
    
    # Build response
    heuristics_list = []
    for heuristic, rounds_data in heuristics_data.items():
        # Get the final totals from the last round
        final_round = rounds_data[-1]
        heuristics_list.append(
            BacktestTableData(
                heuristic=heuristic,
                season=season,
                rounds=[
                    BacktestTableRow(
                        round_id=r["round_id"],
                        tips_made=r["tips_made"],
                        tips_correct=r["tips_correct"],
                        accuracy=r["accuracy"],
                        profit=r["profit"],
                    )
                    for r in rounds_data
                ],
                total_profit=final_round["_total_profit"],
                total_accuracy=final_round["_total_accuracy"],
            )
        )
    
    return BacktestTableResponse(
        season=season,
        heuristics=heuristics_list,
    )


@router.get("/seasons", response_model=AvailableSeasonsResponse)
@limiter.limit("60/minute")
async def get_available_seasons(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Get available seasons for backtesting."""
    available_years = await BacktestCRUD.get_available_seasons(db)
    current_year = datetime.now().year
    
    return AvailableSeasonsResponse(
        available_years=available_years,
        current_year=current_year,
    )


@router.post("/sync", response_model=HistoricalSyncResponse)
@limiter.limit("5/minute")
async def sync_historical_data(
    request: Request,
    season: int = Query(..., description="Season year to sync"),
    db: AsyncSession = Depends(get_db),
):
    """Sync historical game data and generate tips for a season."""
    service = BacktestService()
    squiggle_client = SquiggleClient()
    
    try:
        result = await service.sync_historical_season(db, season, squiggle_client)
        return result
    finally:
        await squiggle_client.close()
