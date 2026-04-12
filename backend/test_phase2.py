"""Test Phase 2: Daily Game Sync Implementation."""

import asyncio
from sqlalchemy.ext.asyncio import AsyncSession
from app.db import AsyncSessionLocal
from app.squiggle import SquiggleClient
from app.services.game_sync import GameSyncService
from app.models_ml.elo import EloModel
from app.cron.jobs.daily_sync import DailyGameSyncJob
from app.config import settings


async def test_game_sync_service():
    """Test GameSyncService."""
    print("Testing GameSyncService...")
    
    async with AsyncSessionLocal() as db:
        squiggle_client = SquiggleClient()
        
        try:
            sync_service = GameSyncService(
                squiggle_client=squiggle_client,
                db_session=db,
                season=2026
            )
            
            stats = await sync_service.sync_games()
            print(f"Sync stats: {stats}")
            
        finally:
            await squiggle_client.close()


async def test_elo_cache():
    """Test Elo cache persistence."""
    print("\nTesting Elo cache persistence...")
    
    async with AsyncSessionLocal() as db:
        # Initialize and update Elo cache
        await EloModel._initialize_cache(db)
        
        # Save to database
        saved_count = await EloModel.save_to_cache(db, EloModel._ratings_cache, 2026)
        print(f"Saved {saved_count} ratings to cache")
        
        # Load from database
        success = await EloModel.load_from_cache(db, 2026)
        print(f"Load from cache {'succeeded' if success else 'failed'}")
        
        # Verify loaded ratings
        if success:
            print(f"Loaded {len(EloModel._ratings_cache)} ratings from cache")


async def test_daily_sync_job():
    """Test DailyGameSyncJob."""
    print("\nTesting DailyGameSyncJob...")
    
    async with AsyncSessionLocal() as db:
        job = DailyGameSyncJob(
            db_session=db,
            settings=settings,
            season=2026
        )
        
        result = await job.execute()
        print(f"Job result: {result}")


async def main():
    """Run all tests."""
    print("=== Phase 2 Implementation Tests ===\n")
    
    # Test GameSyncService
    await test_game_sync_service()
    
    # Test Elo cache (skip for now - requires games in DB)
    # await test_elo_cache()
    
    # Test DailyGameSyncJob
    await test_daily_sync_job()
    
    print("\n=== All tests completed ===")


if __name__ == "__main__":
    asyncio.run(main())
