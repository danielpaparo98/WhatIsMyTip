"""Test script to verify cron job infrastructure setup."""

import asyncio
from app.db import AsyncSessionLocal
from sqlalchemy import text


async def check_tables():
    """Check that all expected tables exist."""
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            text("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
        )
        tables = [row[0] for row in result.fetchall()]
        
        print('Tables in database:')
        for table in tables:
            print(f'  - {table}')
        
        # Check for new cron tables
        expected_tables = ['job_executions', 'job_locks', 'elo_cache']
        missing_tables = [t for t in expected_tables if t not in tables]
        
        if missing_tables:
            print(f'\n[X] Missing tables: {missing_tables}')
            return False
        else:
            print(f'\n[OK] All cron tables present')
            return True


async def check_columns():
    """Check that new columns were added to existing tables."""
    async with AsyncSessionLocal() as db:
        # Check games table
        result = await db.execute(
            text("PRAGMA table_info(games)")
        )
        columns = [row[1] for row in result.fetchall()]
        
        print('\nGames table columns:')
        for col in columns:
            print(f'  - {col}')
        
        expected_columns = ['last_synced_at', 'sync_version']
        missing_columns = [c for c in expected_columns if c not in columns]
        
        if missing_columns:
            print(f'\n[X] Missing columns in games table: {missing_columns}')
            return False
        else:
            print(f'\n[OK] Games table has all expected columns')
        
        # Check generation_progress table
        result = await db.execute(
            text("PRAGMA table_info(generation_progress)")
        )
        columns = [row[1] for row in result.fetchall()]
        
        print('\nGenerationProgress table columns:')
        for col in columns:
            print(f'  - {col}')
        
        if 'job_execution_id' not in columns:
            print(f'\n[X] Missing job_execution_id in generation_progress table')
            return False
        else:
            print(f'\n[OK] GenerationProgress table has job_execution_id column')
        
        return True


async def test_cron_manager():
    """Test CronJobManager initialization."""
    from app.cron import init_cron_manager
    from fastapi import FastAPI
    
    app = FastAPI()
    mgr = init_cron_manager(app)
    
    print(f'\nCronJobManager initialized: {mgr}')
    print(f'Instance ID: {mgr.instance_id}')
    print(f'Enabled: {mgr.enabled}')
    
    # Test job registration
    await mgr.register_jobs()
    print(f'\n[OK] CronJobManager can register jobs')
    
    return True


async def main():
    """Run all tests."""
    print('=' * 60)
    print('Testing Cron Job Infrastructure')
    print('=' * 60)
    
    tables_ok = await check_tables()
    columns_ok = await check_columns()
    manager_ok = await test_cron_manager()
    
    print('\n' + '=' * 60)
    if tables_ok and columns_ok and manager_ok:
        print('[OK] All tests passed!')
    else:
        print('[X] Some tests failed')
    print('=' * 60)


if __name__ == '__main__':
    asyncio.run(main())
