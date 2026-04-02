"""Test Phase 3: Match Completion Detection."""

import asyncio
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker

from app.config import settings
from app.db import Base
from app.squiggle import SquiggleClient
from app.services.match_completion import MatchCompletionDetectorService
from app.cron.jobs.match_completion import MatchCompletionDetectionJob
from app.crud.games import GameCRUD
from app.logger import get_logger


logger = get_logger(__name__)


async def test_match_completion_detector_service():
    """Test MatchCompletionDetectorService."""
    logger.info("Testing MatchCompletionDetectorService...")
    
    # Create database session
    engine = create_async_engine(settings.database_url)
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    
    async with async_session() as db:
        try:
            # Create Squiggle client
            squiggle_client = SquiggleClient()
            
            try:
                # Create detector service
                detector = MatchCompletionDetectorService(
                    squiggle_client=squiggle_client,
                    db_session=db,
                    buffer_minutes=60
                )
                
                # Test: Get recently finished games
                logger.info("Testing get_recently_finished_games...")
                recently_finished = await GameCRUD.get_recently_finished_games(db, buffer_minutes=60)
                logger.info(f"Found {len(recently_finished)} recently finished games")
                
                # Test: Detect and process completed matches
                logger.info("Testing detect_and_process_completed_matches...")
                stats = await detector.detect_and_process_completed_matches()
                
                logger.info(f"Detection stats:")
                logger.info(f"  Games checked: {stats['games_checked']}")
                logger.info(f"  Games completed: {stats['games_completed']}")
                logger.info(f"  Games already completed: {stats['games_already_completed']}")
                logger.info(f"  Games not ready: {stats['games_not_ready']}")
                logger.info(f"  Errors: {len(stats.get('errors', []))}")
                logger.info(f"  Duration: {stats['duration_seconds']:.2f}s")
                
                if stats['errors']:
                    logger.warning(f"Errors encountered: {stats['errors']}")
                
                logger.info("MatchCompletionDetectorService test passed")
                
            finally:
                await squiggle_client.close()
                
        except Exception as e:
            logger.error(f"MatchCompletionDetectorService test failed: {e}", exc_info=True)
            raise


async def test_match_completion_detection_job():
    """Test MatchCompletionDetectionJob."""
    logger.info("Testing MatchCompletionDetectionJob...")
    
    # Create database session
    engine = create_async_engine(settings.database_url)
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    
    async with async_session() as db:
        try:
            # Create job instance
            job = MatchCompletionDetectionJob(
                db_session=db,
                settings=settings,
                instance_id="test-instance"
            )
            
            # Test: Execute job
            logger.info("Testing job execution...")
            result = await job.execute()
            
            logger.info(f"Job result:")
            logger.info(f"  Items processed: {result['items_processed']}")
            logger.info(f"  Items succeeded: {result['items_succeeded']}")
            logger.info(f"  Items failed: {result['items_failed']}")
            logger.info(f"  Games checked: {result['games_checked']}")
            logger.info(f"  Games completed: {result['games_completed']}")
            logger.info(f"  Games not ready: {result['games_not_ready']}")
            logger.info(f"  Elo cache updated: {result['elo_cache_updated']}")
            logger.info(f"  Summary: {result['summary']}")
            
            logger.info("MatchCompletionDetectionJob test passed")
            
        except Exception as e:
            logger.error(f"MatchCompletionDetectionJob test failed: {e}", exc_info=True)
            raise


async def test_game_crud_methods():
    """Test GameCRUD completion detection methods."""
    logger.info("Testing GameCRUD completion detection methods...")
    
    # Create database session
    engine = create_async_engine(settings.database_url)
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    
    async with async_session() as db:
        try:
            # Test: Get recently finished games
            logger.info("Testing get_recently_finished_games...")
            recently_finished = await GameCRUD.get_recently_finished_games(
                db,
                buffer_minutes=60
            )
            logger.info(f"Found {len(recently_finished)} recently finished games")
            
            # Test: Get recently finished games with different buffer
            logger.info("Testing get_recently_finished_games with 30 minute buffer...")
            recently_finished_30 = await GameCRUD.get_recently_finished_games(
                db,
                buffer_minutes=30
            )
            logger.info(f"Found {len(recently_finished_30)} games with 30 min buffer")
            
            logger.info("GameCRUD completion detection methods test passed")
            
        except Exception as e:
            logger.error(f"GameCRUD test failed: {e}", exc_info=True)
            raise


async def test_config_settings():
    """Test configuration settings."""
    logger.info("Testing configuration settings...")
    
    # Test: Check match completion settings exist
    assert hasattr(settings, 'match_completion_buffer_minutes'), \
        "Missing match_completion_buffer_minutes setting"
    assert hasattr(settings, 'match_completion_check_enabled'), \
        "Missing match_completion_check_enabled setting"
    assert hasattr(settings, 'cron_match_completion_check'), \
        "Missing cron_match_completion_check setting"
    
    logger.info(f"Match completion buffer minutes: {settings.match_completion_buffer_minutes}")
    logger.info(f"Match completion check enabled: {settings.match_completion_check_enabled}")
    logger.info(f"Match completion schedule: {settings.cron_match_completion_check}")
    
    logger.info("Configuration settings test passed")


async def main():
    """Run all Phase 3 tests."""
    logger.info("=" * 60)
    logger.info("Starting Phase 3 Tests: Match Completion Detection")
    logger.info("=" * 60)
    
    try:
        # Test configuration
        await test_config_settings()
        logger.info("")
        
        # Test GameCRUD methods
        await test_game_crud_methods()
        logger.info("")
        
        # Test MatchCompletionDetectorService
        await test_match_completion_detector_service()
        logger.info("")
        
        # Test MatchCompletionDetectionJob
        await test_match_completion_detection_job()
        logger.info("")
        
        logger.info("=" * 60)
        logger.info("All Phase 3 tests passed!")
        logger.info("=" * 60)
        
    except Exception as e:
        logger.error("=" * 60)
        logger.error(f"Phase 3 tests failed: {e}")
        logger.error("=" * 60)
        raise


if __name__ == "__main__":
    asyncio.run(main())
