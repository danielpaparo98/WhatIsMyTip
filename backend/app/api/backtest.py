from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession
from slowapi import Limiter
from slowapi.util import get_remote_address
from typing import Optional
from datetime import datetime

from app.db import get_db
from app.schemas import (
    BacktestResponse,
    BacktestListResponse,
    AvailableSeasonsResponse,
    BacktestTableResponse,
    BacktestTableData,
    BacktestTableRow,
    CurrentSeasonResponse,
)
from app.services.backtest import BacktestService

router = APIRouter()
limiter = Limiter(key_func=get_remote_address)


@router.get("", response_model=BacktestListResponse)
@limiter.limit("20/minute")
async def get_backtest_results(
    request: Request,
    heuristic: Optional[str] = Query(None, description="Filter by heuristic type"),
    season: Optional[int] = Query(None, description="Filter by season year"),
    db: AsyncSession = Depends(get_db),
):
    """Get backtest results with optional filtering (deprecated - use /compare or /table)."""
    # This endpoint is kept for backward compatibility
    # For better results, use /compare or /table endpoints
    service = BacktestService()
    
    if season and heuristic:
        # Get specific season/heuristic comparison
        stats = await service.calculate_backtest_from_tips(db, season, heuristic)
        
        # Convert to BacktestResponse format (single result)
        return BacktestListResponse(
            results=[],
            count=0,
        )
    else:
        # No meaningful data for this endpoint without specific season/heuristic
        return BacktestListResponse(
            results=[],
            count=0,
        )


@router.get("/current-season", response_model=CurrentSeasonResponse)
@limiter.limit("20/minute")
async def get_current_season_performance(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Get current season performance with year-to-date results and projections."""
    service = BacktestService()
    
    performance = await service.get_current_season_performance(db)
    
    return performance


@router.get("/compare")
@limiter.limit("30/minute")
async def compare_heuristics(
    request: Request,
    season: int = Query(..., description="Season year to compare"),
    db: AsyncSession = Depends(get_db),
):
    """Compare all heuristics for a season by calculating from tips."""
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


@router.get("/table", response_model=BacktestTableResponse)
@limiter.limit("30/minute")
async def get_table_data(
    request: Request,
    season: int = Query(..., description="Season year to get table data for"),
    db: AsyncSession = Depends(get_db),
):
    """Get detailed table data for all heuristics for a season by calculating from tips."""
    service = BacktestService()
    
    # Build response
    heuristics_list = []
    for heuristic in service.orchestrator.get_available_heuristics():
        round_data = await service.get_round_by_round_data(db, season, heuristic)
        
        # Calculate totals for this heuristic
        total_profit = sum(r["profit"] for r in round_data)
        total_tips = sum(r["tips_made"] for r in round_data)
        total_correct = sum(r["tips_correct"] for r in round_data)
        total_accuracy = total_correct / total_tips if total_tips > 0 else 0.0
        
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
                    for r in round_data
                ],
                total_profit=total_profit,
                total_accuracy=total_accuracy,
            )
        )
    
    return BacktestTableResponse(
        season=season,
        heuristics=heuristics_list,
    )


@router.get("/seasons", response_model=AvailableSeasonsResponse)
@limiter.limit("20/minute")
async def get_available_seasons(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Get available seasons for backtesting by checking seasons with tips."""
    service = BacktestService()
    available_years = await service.get_available_seasons(db)
    current_year = datetime.now().year
    
    return AvailableSeasonsResponse(
        available_years=available_years,
        current_year=current_year,
    )


@router.get("/{heuristic}", response_model=BacktestListResponse)
@limiter.limit("20/minute")
async def get_backtest_by_heuristic(
    request: Request,
    heuristic: str,
    season: Optional[int] = Query(None, description="Filter by season year"),
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
):
    """Get backtest results by heuristic type (deprecated - use /compare or /table)."""
    # This endpoint is kept for backward compatibility
    # For better results, use /compare or /table endpoints
    return BacktestListResponse(
        results=[],
        count=0,
    )
