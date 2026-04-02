"""Test CronJobManager infrastructure."""

import asyncio
import sys
import io
from unittest.mock import Mock, AsyncMock, MagicMock
from sqlalchemy.ext.asyncio import AsyncSession

# Set UTF-8 encoding for Windows
if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

from app.cron import CronJobManager, init_cron_manager
from app.config import settings
from app.logger import get_logger

logger = get_logger(__name__)


async def test_cron_manager_initialization():
    """Test CronJobManager initialization."""
    print("\n1. Testing CronJobManager initialization...")
    try:
        # Create mock FastAPI app
        mock_app = Mock()
        
        # Initialize CronJobManager
        manager = CronJobManager(mock_app)
        
        # Verify initialization
        assert manager.app == mock_app, "App not set correctly"
        assert manager.jobs == {}, "Jobs dict should be empty initially"
        assert manager.instance_id is not None, "Instance ID should be set"
        assert manager.enabled is not None, "Enabled flag should be set"
        
        print(f"   [OK] CronJobManager initialized successfully")
        print(f"   [OK] Instance ID: {manager.instance_id}")
        print(f"   [OK] Cron enabled: {manager.enabled}")
        return True
    except Exception as e:
        print(f"   [FAIL] CronJobManager initialization failed: {e}")
        import traceback
        traceback.print_exc()
        return False


async def test_job_registration():
    """Test job registration."""
    print("\n2. Testing job registration...")
    try:
        # Create mock FastAPI app
        mock_app = Mock()
        
        # Initialize CronJobManager
        manager = CronJobManager(mock_app)
        
        # Create mock job class
        mock_job_class = Mock()
        
        # Register a job
        await manager.register_job(
            name="test_job",
            schedule="0 2 * * *",
            job_class=mock_job_class,
            enabled=True
        )
        
        # Verify job was registered
        assert "test_job" in manager.jobs, "Job not registered"
        assert manager.jobs["test_job"]["name"] == "test_job", "Job name not set"
        assert manager.jobs["test_job"]["schedule"] == "0 2 * * *", "Schedule not set"
        assert manager.jobs["test_job"]["job_class"] == mock_job_class, "Job class not set"
        assert manager.jobs["test_job"]["enabled"] == True, "Enabled flag not set"
        
        print(f"   [OK] Job registered successfully")
        return True
    except Exception as e:
        print(f"   [FAIL] Job registration failed: {e}")
        import traceback
        traceback.print_exc()
        return False


async def test_register_all_jobs():
    """Test registering all jobs."""
    print("\n3. Testing register_all_jobs...")
    try:
        # Create mock FastAPI app
        mock_app = Mock()
        
        # Initialize CronJobManager
        manager = CronJobManager(mock_app)
        
        # Register all jobs
        await manager.register_jobs()
        
        # Verify all jobs were registered
        expected_jobs = [
            "daily_game_sync",
            "match_completion_detector",
            "tip_generation",
            "historic_data_refresh"
        ]
        
        for job_name in expected_jobs:
            if job_name not in manager.jobs:
                print(f"   [FAIL] Job {job_name} not registered")
                return False
        
        print(f"   [OK] All {len(manager.jobs)} jobs registered successfully:")
        for job_name in manager.jobs:
            print(f"      - {job_name}")
        return True
    except Exception as e:
        print(f"   [FAIL] register_all_jobs failed: {e}")
        import traceback
        traceback.print_exc()
        return False


async def test_job_locking():
    """Test job locking mechanism."""
    print("\n4. Testing job locking mechanism...")
    try:
        from app.crud.jobs import JobLockCRUD
        from app.db import get_db
        
        async for db in get_db():
            lock_crud = JobLockCRUD(db)
            
            # Test acquiring lock
            lock = await lock_crud.acquire_lock(
                job_name="test_job",
                locked_by="test-instance",
                expires_seconds=300
            )
            
            if not lock:
                print(f"   [FAIL] Failed to acquire lock")
                return False
            
            print(f"   [OK] Lock acquired successfully")
            
            # Test checking if locked
            is_locked = await lock_crud.is_locked("test_job")
            if not is_locked:
                print(f"   [FAIL] Lock not detected")
                return False
            
            print(f"   [OK] Lock detected correctly")
            
            # Test releasing lock
            await lock_crud.release_lock("test_job", "test-instance")
            print(f"   [OK] Lock released successfully")
            
            # Verify lock is released
            is_locked = await lock_crud.is_locked("test_job")
            if is_locked:
                print(f"   [FAIL] Lock still detected after release")
                return False
            
            print(f"   [OK] Lock released correctly")
            return True
    except Exception as e:
        print(f"   [FAIL] Job locking test failed: {e}")
        import traceback
        traceback.print_exc()
        return False


async def test_job_execution_tracking():
    """Test job execution tracking."""
    print("\n5. Testing job execution tracking...")
    try:
        from app.crud.jobs import JobExecutionCRUD
        from app.db import get_db
        
        async for db in get_db():
            execution_crud = JobExecutionCRUD(db)
            
            # Test creating execution
            execution = await execution_crud.create_execution(
                job_name="test_job",
                status="running"
            )
            
            if not execution:
                print(f"   [FAIL] Failed to create execution")
                return False
            
            print(f"   [OK] Execution created successfully (ID: {execution.id})")
            
            # Test updating execution
            from datetime import datetime
            await execution_crud.update_execution(
                execution_id=execution.id,
                status="completed",
                completed_at=datetime.utcnow(),
                duration_seconds=10,
                items_processed=5,
                items_failed=0,
                result_summary="Test completed"
            )
            
            print(f"   [OK] Execution updated successfully")
            
            # Test getting executions by job
            executions = await execution_crud.get_executions_by_job("test_job", limit=1)
            if not executions:
                print(f"   [FAIL] Failed to get executions")
                return False
            
            print(f"   [OK] Executions retrieved successfully")
            
            # Test getting job metrics
            metrics = await execution_crud.get_job_metrics("test_job")
            if not metrics:
                print(f"   [FAIL] Failed to get job metrics")
                return False
            
            print(f"   [OK] Job metrics retrieved successfully:")
            print(f"      - Total runs: {metrics.get('total_runs', 0)}")
            print(f"      - Successful runs: {metrics.get('successful_runs', 0)}")
            print(f"      - Failed runs: {metrics.get('failed_runs', 0)}")
            
            return True
    except Exception as e:
        print(f"   [FAIL] Job execution tracking test failed: {e}")
        import traceback
        traceback.print_exc()
        return False


async def test_cleanup_expired_locks():
    """Test cleanup of expired locks."""
    print("\n6. Testing cleanup of expired locks...")
    try:
        from app.crud.jobs import JobLockCRUD
        from app.db import get_db
        
        async for db in get_db():
            lock_crud = JobLockCRUD(db)
            
            # Create an expired lock
            from datetime import datetime, timedelta
            expired_time = datetime.utcnow() - timedelta(seconds=600)
            
            # Directly insert an expired lock
            from sqlalchemy import text
            await db.execute(text("""
                INSERT INTO job_locks (job_name, locked_at, locked_by, expires_at)
                VALUES ('expired_job', :locked_at, 'test-instance', :expires_at)
            """), {"locked_at": expired_time, "expires_at": expired_time})
            await db.commit()
            
            # Cleanup expired locks
            count = await lock_crud.cleanup_expired_locks()
            
            print(f"   [OK] Cleaned up {count} expired locks")
            
            # Verify lock is gone
            is_locked = await lock_crud.is_locked("expired_job")
            if is_locked:
                print(f"   [FAIL] Expired lock still exists")
                return False
            
            print(f"   [OK] Expired lock removed successfully")
            return True
    except Exception as e:
        print(f"   [FAIL] Cleanup expired locks test failed: {e}")
        import traceback
        traceback.print_exc()
        return False


async def test_enable_disable_jobs():
    """Test enabling and disabling jobs."""
    print("\n7. Testing enable/disable jobs...")
    try:
        # Create mock FastAPI app
        mock_app = Mock()
        
        # Initialize CronJobManager
        manager = CronJobManager(mock_app)
        
        # Register a job
        mock_job_class = Mock()
        await manager.register_job(
            name="test_job",
            schedule="0 2 * * *",
            job_class=mock_job_class,
            enabled=True
        )
        
        # Test disabling job
        result = await manager.disable_job("test_job")
        if not result:
            print(f"   [FAIL] Failed to disable job")
            return False
        
        assert manager.jobs["test_job"]["enabled"] == False, "Job not disabled"
        print(f"   [OK] Job disabled successfully")
        
        # Test enabling job
        result = await manager.enable_job("test_job")
        if not result:
            print(f"   [FAIL] Failed to enable job")
            return False
        
        assert manager.jobs["test_job"]["enabled"] == True, "Job not enabled"
        print(f"   [OK] Job enabled successfully")
        
        return True
    except Exception as e:
        print(f"   [FAIL] Enable/disable jobs test failed: {e}")
        import traceback
        traceback.print_exc()
        return False


async def test_global_cron_manager():
    """Test global cron manager instance."""
    print("\n8. Testing global cron manager instance...")
    try:
        # Create mock FastAPI app
        mock_app = Mock()
        
        # Initialize global cron manager
        manager = init_cron_manager(mock_app)
        
        # Verify it's the same instance
        from app.cron import get_cron_manager
        same_manager = get_cron_manager()
        
        assert manager == same_manager, "Global instance not working correctly"
        print(f"   [OK] Global cron manager instance working correctly")
        
        return True
    except Exception as e:
        print(f"   [FAIL] Global cron manager test failed: {e}")
        import traceback
        traceback.print_exc()
        return False


async def main():
    """Run all infrastructure tests."""
    print("=" * 60)
    print("CRON JOB MANAGER INFRASTRUCTURE TESTING")
    print("=" * 60)
    
    results = []
    
    # Test CronJobManager initialization
    results.append(await test_cron_manager_initialization())
    
    # Test job registration
    results.append(await test_job_registration())
    
    # Test register all jobs
    results.append(await test_register_all_jobs())
    
    # Test job locking
    results.append(await test_job_locking())
    
    # Test job execution tracking
    results.append(await test_job_execution_tracking())
    
    # Test cleanup expired locks
    results.append(await test_cleanup_expired_locks())
    
    # Test enable/disable jobs
    results.append(await test_enable_disable_jobs())
    
    # Test global cron manager
    results.append(await test_global_cron_manager())
    
    print("\n" + "=" * 60)
    if all(results):
        print("[OK] ALL INFRASTRUCTURE TESTS PASSED")
    else:
        print("[FAIL] SOME INFRASTRUCTURE TESTS FAILED")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
