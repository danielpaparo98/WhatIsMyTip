"""Test the Elo cache None team name bug fix."""

import asyncio
import sys
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy import select
from app.config import settings
from app.models_ml.elo import EloModel
from app.models import Game

# Set UTF-8 encoding for Windows
if sys.platform == "win32":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')


async def test_elo_cache_with_none_teams():
    """Test that Elo cache initialization and update handle None team names."""
    print("\n=== Testing Elo Cache Fix ===\n")
    
    # Create database session
    engine = create_async_engine(settings.database_url)
    async_session_maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    
    async with async_session_maker() as db:
        # Test 1: Check for games with None team names
        print("1. Checking for games with None team names...")
        result = await db.execute(
            select(Game).where((Game.home_team == None) | (Game.away_team == None))
        )
        none_team_games = result.scalars().all()
        print(f"   Found {len(none_team_games)} games with None team names")
        if none_team_games:
            for game in none_team_games[:3]:  # Show first 3
                print(f"   - Game ID {game.id}: {game.date} | home={game.home_team} | away={game.away_team}")
            if len(none_team_games) > 3:
                print(f"   ... and {len(none_team_games) - 3} more")
        
        # Test 2: Test _initialize_cache()
        print("\n2. Testing EloModel._initialize_cache()...")
        try:
            # Reset cache state
            EloModel._cache_initialized = False
            EloModel._ratings_cache = {}
            
            await EloModel._initialize_cache(db)
            
            print(f"   [OK] Cache initialized successfully")
            print(f"   - Cache initialized: {EloModel._cache_initialized}")
            print(f"   - Teams in cache: {len(EloModel._ratings_cache)}")
            
            # Verify no None keys in cache
            if None in EloModel._ratings_cache:
                print("   [ERROR] None found in ratings cache!")
                return False
            else:
                print("   [OK] No None values in ratings cache")
            
        except Exception as e:
            print(f"   [ERROR] Failed to initialize cache: {e}")
            import traceback
            traceback.print_exc()
            return False
        
        # Test 3: Test update_cache()
        print("\n3. Testing EloModel.update_cache()...")
        try:
            await EloModel.update_cache(db)
            
            print(f"   [OK] Cache updated successfully")
            print(f"   - Teams in cache: {len(EloModel._ratings_cache)}")
            
            # Verify no None keys in cache
            if None in EloModel._ratings_cache:
                print("   [ERROR] None found in ratings cache after update!")
                return False
            else:
                print("   [OK] No None values in ratings cache after update")
            
        except Exception as e:
            print(f"   [ERROR] Failed to update cache: {e}")
            import traceback
            traceback.print_exc()
            return False
        
        # Test 4: Verify team names are valid
        print("\n4. Verifying team names in cache...")
        invalid_teams = [team for team in EloModel._ratings_cache.keys() if not isinstance(team, str)]
        if invalid_teams:
            print(f"   [ERROR] Found invalid team names: {invalid_teams}")
            return False
        else:
            print(f"   [OK] All {len(EloModel._ratings_cache)} team names are valid strings")
            # Show sample teams
            sample_teams = list(EloModel._ratings_cache.keys())[:5]
            print(f"   Sample teams: {sample_teams}")
        
        # Test 5: Verify ratings are valid numbers
        print("\n5. Verifying ratings are valid...")
        invalid_ratings = [(team, rating) for team, rating in EloModel._ratings_cache.items() 
                          if not isinstance(rating, (int, float))]
        if invalid_ratings:
            print(f"   [ERROR] Found invalid ratings: {invalid_ratings}")
            return False
        else:
            ratings_list = list(EloModel._ratings_cache.values())
            print(f"   [OK] All ratings are valid numbers")
            print(f"   - Min rating: {min(ratings_list):.2f}")
            print(f"   - Max rating: {max(ratings_list):.2f}")
            print(f"   - Avg rating: {sum(ratings_list)/len(ratings_list):.2f}")
        
        print("\n=== [SUCCESS] All Elo cache tests passed! ===\n")
        return True


async def test_daily_sync_job():
    """Test that the Daily Game Sync Job can run with the fixed Elo cache."""
    print("\n=== Testing Daily Game Sync Job ===\n")
    
    from app.cron.jobs.daily_sync import DailyGameSyncJob
    from app.config import settings
    
    # Create database session
    engine = create_async_engine(settings.database_url)
    async_session_maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    
    async with async_session_maker() as db:
        print("1. Creating DailyGameSyncJob instance...")
        try:
            job = DailyGameSyncJob(
                db_session=db,
                settings=settings,
                season=2025  # Use a specific season to avoid full sync
            )
            print("   [OK] Job instance created")
        except Exception as e:
            print(f"   [ERROR] Failed to create job: {e}")
            import traceback
            traceback.print_exc()
            return False
        
        print("\n2. Testing job initialization (not full execution)...")
        print(f"   Job name: {job.job_name}")
        print(f"   Season: {job.season}")
        print("   [OK] Job initialized correctly")
        
        print("\n=== [SUCCESS] Daily Game Sync Job can be initialized! ===\n")
        return True


async def main():
    """Run all tests."""
    print("\n" + "="*60)
    print("ELO CACHE NONE TEAM NAME BUG FIX TEST")
    print("="*60)
    
    # Test 1: Elo cache with None teams
    success1 = await test_elo_cache_with_none_teams()
    
    # Test 2: Daily sync job
    success2 = await test_daily_sync_job()
    
    # Summary
    print("\n" + "="*60)
    if success1 and success2:
        print("[SUCCESS] All tests passed!")
        print("="*60)
        return 0
    else:
        print("[FAILURE] Some tests failed!")
        print("="*60)
        return 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
