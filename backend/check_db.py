import asyncio
from app.db import get_db
from app.models import BacktestResult
from sqlalchemy import select, func

async def check():
    db_gen = get_db()
    db = await db_gen.__anext__()
    
    # Count total backtest results
    result = await db.execute(select(func.count(BacktestResult.id)))
    print(f'Total backtest results: {result.scalar()}')
    
    # Count by season
    result = await db.execute(
        select(BacktestResult.season, func.count(BacktestResult.id))
        .group_by(BacktestResult.season)
        .order_by(BacktestResult.season)
    )
    print('\nBacktest results by season:')
    for row in result.all():
        print(f'  {row[0]}: {row[1]} results')
    
    # Count by season and heuristic
    result = await db.execute(
        select(BacktestResult.season, BacktestResult.heuristic, func.count(BacktestResult.id))
        .group_by(BacktestResult.season, BacktestResult.heuristic)
        .order_by(BacktestResult.season, BacktestResult.heuristic)
    )
    print('\nBacktest results by season and heuristic:')
    for row in result.all():
        print(f'  {row[0]} {row[1]}: {row[2]} results')
    
    await db_gen.aclose()

if __name__ == '__main__':
    asyncio.run(check())
