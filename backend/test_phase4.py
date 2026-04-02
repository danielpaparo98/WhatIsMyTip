"""Test Phase 4: Tip Generation Job implementation."""

import asyncio
from app.db import get_db
from app.services.tip_generation import TipGenerationService
from app.cron.jobs.tip_generation import TipGenerationJob
from app.config import settings
from app.logger import get_logger


logger = get_logger(__name__)


async def test_tip_generation_service():
    """Test TipGenerationService can be instantiated and used."""
    logger.info("Testing TipGenerationService...")
    
    async for db in get_db():
        # Test 1: Create service instance
        service = TipGenerationService(
            db_session=db,
            season=2026,
            round_id=1
        )
        logger.info("✓ TipGenerationService instantiated successfully")
        
        # Test 2: Check available heuristics
        heuristics = service.orchestrator.get_available_heuristics()
        logger.info(f"✓ Available heuristics: {heuristics}")
        
        # Test 3: Check available models
        models = [model.get_name() for model in service.orchestrator.models]
        logger.info(f"✓ Available models: {models}")
        
        logger.info("✓ TipGenerationService tests passed")


async def test_tip_generation_job():
    """Test TipGenerationJob can be instantiated."""
    logger.info("Testing TipGenerationJob...")
    
    async for db in get_db():
        # Test 1: Create job instance
        job = TipGenerationJob(
            db_session=db,
            settings=settings,
            instance_id="test-instance",
            season=2026,
            round_id=1,
            regenerate=False
        )
        logger.info("✓ TipGenerationJob instantiated successfully")
        
        # Test 2: Check job name
        assert job.job_name == "tip_generation"
        logger.info(f"✓ Job name: {job.job_name}")
        
        # Test 3: Check regenerate flag
        assert job.regenerate == False
        logger.info(f"✓ Regenerate flag: {job.regenerate}")
        
        logger.info("✓ TipGenerationJob tests passed")


async def test_imports():
    """Test all imports work correctly."""
    logger.info("Testing imports...")
    
    # Test 1: Import TipGenerationService
    from app.services.tip_generation import TipGenerationService
    logger.info("✓ TipGenerationService imported successfully")
    
    # Test 2: Import TipGenerationJob
    from app.cron.jobs.tip_generation import TipGenerationJob
    logger.info("✓ TipGenerationJob imported successfully")
    
    # Test 3: Import CRUD operations
    from app.crud.tips import TipCRUD
    from app.crud.model_predictions import ModelPredictionCRUD
    logger.info("✓ TipCRUD and ModelPredictionCRUD imported successfully")
    
    # Test 4: Import orchestrator
    from app.orchestrator import ModelOrchestrator
    logger.info("✓ ModelOrchestrator imported successfully")
    
    logger.info("✓ All imports successful")


async def test_config_settings():
    """Test config settings are available."""
    logger.info("Testing config settings...")
    
    # Test 1: Check tip generation enabled
    assert hasattr(settings, 'tip_generation_enabled')
    logger.info(f"✓ tip_generation_enabled: {settings.tip_generation_enabled}")
    
    # Test 2: Check regenerate existing setting
    assert hasattr(settings, 'tip_generation_regenerate_existing')
    logger.info(f"✓ tip_generation_regenerate_existing: {settings.tip_generation_regenerate_existing}")
    
    # Test 3: Check tip generation schedule
    assert hasattr(settings, 'cron_tip_generation')
    logger.info(f"✓ cron_tip_generation: {settings.cron_tip_generation}")
    
    # Test 4: Check tip generation timeout
    assert hasattr(settings, 'tip_generation_timeout_seconds')
    logger.info(f"✓ tip_generation_timeout_seconds: {settings.tip_generation_timeout_seconds}")
    
    logger.info("✓ Config settings tests passed")


async def main():
    """Run all Phase 4 tests."""
    logger.info("=" * 60)
    logger.info("Starting Phase 4 Tests: Tip Generation Job")
    logger.info("=" * 60)
    
    try:
        # Test imports
        await test_imports()
        
        # Test config settings
        await test_config_settings()
        
        # Test TipGenerationService
        await test_tip_generation_service()
        
        # Test TipGenerationJob
        await test_tip_generation_job()
        
        logger.info("=" * 60)
        logger.info("✓ ALL PHASE 4 TESTS PASSED")
        logger.info("=" * 60)
        
    except Exception as e:
        logger.error(f"✗ Test failed: {str(e)}", exc_info=True)
        raise


if __name__ == "__main__":
    asyncio.run(main())
