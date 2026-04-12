"""Test configuration settings and API endpoints."""

import asyncio
import sys
import io
from unittest.mock import Mock, patch

# Set UTF-8 encoding for Windows
if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

from app.config import settings
from app.logger import get_logger

logger = get_logger(__name__)


async def test_cron_settings():
    """Test cron-related configuration settings."""
    print("\n1. Testing cron settings...")
    try:
        # Test basic cron settings
        assert hasattr(settings, 'cron_enabled'), "Missing cron_enabled setting"
        print(f"   [OK] cron_enabled: {settings.cron_enabled}")
        
        assert hasattr(settings, 'job_lock_expire_seconds'), "Missing job_lock_expire_seconds setting"
        print(f"   [OK] job_lock_expire_seconds: {settings.job_lock_expire_seconds}")
        
        # Test job-specific settings
        assert hasattr(settings, 'cron_daily_sync'), "Missing cron_daily_sync setting"
        print(f"   [OK] cron_daily_sync: {settings.cron_daily_sync}")
        
        assert hasattr(settings, 'cron_match_completion_check'), "Missing cron_match_completion_check setting"
        print(f"   [OK] cron_match_completion_check: {settings.cron_match_completion_check}")
        
        assert hasattr(settings, 'cron_tip_generation'), "Missing cron_tip_generation setting"
        print(f"   [OK] cron_tip_generation: {settings.cron_tip_generation}")
        
        assert hasattr(settings, 'cron_historical_refresh'), "Missing cron_historical_refresh setting"
        print(f"   [OK] cron_historical_refresh: {settings.cron_historical_refresh}")
        
        return True
    except Exception as e:
        print(f"   [FAIL] Cron settings test failed: {e}")
        import traceback
        traceback.print_exc()
        return False


async def test_daily_sync_settings():
    """Test daily sync configuration settings."""
    print("\n2. Testing daily sync settings...")
    try:
        assert hasattr(settings, 'current_season'), "Missing current_season setting"
        print(f"   [OK] current_season: {settings.current_season}")
        
        assert hasattr(settings, 'squiggle_api_base'), "Missing squiggle_api_base setting"
        print(f"   [OK] squiggle_api_base: {settings.squiggle_api_base}")
        
        return True
    except Exception as e:
        print(f"   [FAIL] Daily sync settings test failed: {e}")
        import traceback
        traceback.print_exc()
        return False


async def test_match_completion_settings():
    """Test match completion configuration settings."""
    print("\n3. Testing match completion settings...")
    try:
        assert hasattr(settings, 'match_completion_buffer_minutes'), "Missing match_completion_buffer_minutes setting"
        print(f"   [OK] match_completion_buffer_minutes: {settings.match_completion_buffer_minutes}")
        
        assert hasattr(settings, 'match_completion_check_enabled'), "Missing match_completion_check_enabled setting"
        print(f"   [OK] match_completion_check_enabled: {settings.match_completion_check_enabled}")
        
        return True
    except Exception as e:
        print(f"   [FAIL] Match completion settings test failed: {e}")
        import traceback
        traceback.print_exc()
        return False


async def test_tip_generation_settings():
    """Test tip generation configuration settings."""
    print("\n4. Testing tip generation settings...")
    try:
        assert hasattr(settings, 'tip_generation_enabled'), "Missing tip_generation_enabled setting"
        print(f"   [OK] tip_generation_enabled: {settings.tip_generation_enabled}")
        
        assert hasattr(settings, 'tip_generation_regenerate_existing'), "Missing tip_generation_regenerate_existing setting"
        print(f"   [OK] tip_generation_regenerate_existing: {settings.tip_generation_regenerate_existing}")
        
        assert hasattr(settings, 'tip_generation_timeout_seconds'), "Missing tip_generation_timeout_seconds setting"
        print(f"   [OK] tip_generation_timeout_seconds: {settings.tip_generation_timeout_seconds}")
        
        return True
    except Exception as e:
        print(f"   [FAIL] Tip generation settings test failed: {e}")
        import traceback
        traceback.print_exc()
        return False


async def test_historic_refresh_settings():
    """Test historic refresh configuration settings."""
    print("\n5. Testing historic refresh settings...")
    try:
        assert hasattr(settings, 'historic_refresh_enabled'), "Missing historic_refresh_enabled setting"
        print(f"   [OK] historic_refresh_enabled: {settings.historic_refresh_enabled}")
        
        assert hasattr(settings, 'historic_refresh_seasons'), "Missing historic_refresh_seasons setting"
        print(f"   [OK] historic_refresh_seasons: {settings.historic_refresh_seasons}")
        
        assert hasattr(settings, 'historic_refresh_regenerate_tips'), "Missing historic_refresh_regenerate_tips setting"
        print(f"   [OK] historic_refresh_regenerate_tips: {settings.historic_refresh_regenerate_tips}")
        
        return True
    except Exception as e:
        print(f"   [FAIL] Historic refresh settings test failed: {e}")
        import traceback
        traceback.print_exc()
        return False


async def test_api_schemas():
    """Test API schema imports and validation."""
    print("\n6. Testing API schemas...")
    try:
        from app.schemas.cron import (
            JobStatusResponse,
            JobTriggerResponse,
            CronHealthResponse,
            JobMetrics
        )
        print(f"   [OK] Cron schemas imported successfully")
        
        from app.api.admin.jobs import (
            DailySyncTriggerRequest,
            DailySyncTriggerResponse,
            MatchCompletionTriggerRequest,
            MatchCompletionTriggerResponse,
            TipGenerationTriggerRequest,
            TipGenerationTriggerResponse,
            HistoricRefreshTriggerRequest,
            HistoricRefreshTriggerResponse,
            HistoricRefreshProgressResponse
        )
        print(f"   [OK] Admin API schemas imported successfully")
        
        # Test schema instantiation
        job_status = JobStatusResponse(
            job_name="test_job",
            status="enabled",
            total_runs=10,
            successful_runs=9,
            failed_runs=1,
            success_rate=0.9
        )
        print(f"   [OK] JobStatusResponse schema validation passed")
        
        trigger_response = JobTriggerResponse(
            job_name="test_job",
            status="success",
            execution_id=1,
            message="Job completed"
        )
        print(f"   [OK] JobTriggerResponse schema validation passed")
        
        return True
    except Exception as e:
        print(f"   [FAIL] API schemas test failed: {e}")
        import traceback
        traceback.print_exc()
        return False


async def test_api_router():
    """Test API router registration."""
    print("\n7. Testing API router...")
    try:
        from app.api.admin.jobs import router as jobs_router
        
        # Check router has routes
        routes = [route.path for route in jobs_router.routes]
        print(f"   [OK] Jobs router has {len(routes)} routes:")
        
        expected_routes = [
            "/daily-sync/trigger",
            "/match-completion/trigger",
            "/tip-generation/trigger",
            "/historic-refresh/trigger",
            "/historic-refresh/progress"
        ]
        
        for expected_route in expected_routes:
            if expected_route in routes:
                print(f"      [OK] {expected_route}")
            else:
                print(f"      [FAIL] Missing route: {expected_route}")
                return False
        
        return True
    except Exception as e:
        print(f"   [FAIL] API router test failed: {e}")
        import traceback
        traceback.print_exc()
        return False


async def test_health_endpoint_schema():
    """Test health endpoint schema."""
    print("\n8. Testing health endpoint schema...")
    try:
        from app.schemas.cron import CronHealthResponse
        from datetime import datetime
        
        health_response = CronHealthResponse(
            status="healthy",
            timestamp=datetime.utcnow(),
            jobs=[],
            database="connected",
            cron_enabled=True
        )
        print(f"   [OK] CronHealthResponse schema validation passed")
        
        return True
    except Exception as e:
        print(f"   [FAIL] Health endpoint schema test failed: {e}")
        import traceback
        traceback.print_exc()
        return False


async def main():
    """Run all configuration and API tests."""
    print("=" * 60)
    print("CONFIGURATION AND API TESTING")
    print("=" * 60)
    
    results = []
    
    # Test cron settings
    results.append(await test_cron_settings())
    
    # Test daily sync settings
    results.append(await test_daily_sync_settings())
    
    # Test match completion settings
    results.append(await test_match_completion_settings())
    
    # Test tip generation settings
    results.append(await test_tip_generation_settings())
    
    # Test historic refresh settings
    results.append(await test_historic_refresh_settings())
    
    # Test API schemas
    results.append(await test_api_schemas())
    
    # Test API router
    results.append(await test_api_router())
    
    # Test health endpoint schema
    results.append(await test_health_endpoint_schema())
    
    print("\n" + "=" * 60)
    if all(results):
        print("[OK] ALL CONFIGURATION AND API TESTS PASSED")
    else:
        print("[FAIL] SOME CONFIGURATION AND API TESTS FAILED")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
