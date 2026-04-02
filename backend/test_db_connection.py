"""Test database connection and check migration status."""

import asyncio
import sys
import io
from sqlalchemy import text
from app.db import engine
from app.config import settings

# Set UTF-8 encoding for Windows
if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')


async def test_database_connection():
    """Test database connection."""
    print("Testing database connection...")
    try:
        async with engine.connect() as conn:
            result = await conn.execute(text("SELECT 1"))
            print("[OK] Database connected successfully")
            return True
    except Exception as e:
        print(f"[FAIL] Database connection failed: {e}")
        return False


async def check_tables():
    """Check if all required tables exist."""
    print("\nChecking database tables...")
    try:
        async with engine.connect() as conn:
            # SQLite-specific query to get table names
            result = await conn.execute(text("""
                SELECT name
                FROM sqlite_master
                WHERE type='table'
                AND name NOT LIKE 'sqlite_%'
                ORDER BY name
            """))
            tables = [row[0] for row in result.fetchall()]
            print(f"Found {len(tables)} tables:")
            for table in sorted(tables):
                print(f"  - {table}")
            
            # Check for required tables
            required_tables = [
                'games', 'tips', 'model_predictions', 'generation_progress',
                'job_executions', 'job_locks', 'elo_cache'
            ]
            missing_tables = [t for t in required_tables if t not in tables]
            
            if missing_tables:
                print(f"\n[FAIL] Missing tables: {missing_tables}")
                return False
            else:
                print(f"\n[OK] All required tables exist")
                return True
    except Exception as e:
        print(f"[FAIL] Failed to check tables: {e}")
        return False


async def check_columns():
    """Check if all required columns exist."""
    print("\nChecking database columns...")
    try:
        async with engine.connect() as conn:
            # Check games table columns (SQLite-specific)
            result = await conn.execute(text("PRAGMA table_info(games)"))
            game_columns = [row[1] for row in result.fetchall()]
            
            required_game_columns = ['last_synced_at', 'sync_version']
            missing_game_columns = [c for c in required_game_columns if c not in game_columns]
            
            if missing_game_columns:
                print(f"[FAIL] Missing columns in games table: {missing_game_columns}")
                return False
            else:
                print(f"[OK] Games table has required columns: {required_game_columns}")
            
            # Check generation_progress table columns (SQLite-specific)
            result = await conn.execute(text("PRAGMA table_info(generation_progress)"))
            progress_columns = [row[1] for row in result.fetchall()]
            
            required_progress_columns = ['job_execution_id']
            missing_progress_columns = [c for c in required_progress_columns if c not in progress_columns]
            
            if missing_progress_columns:
                print(f"[FAIL] Missing columns in generation_progress table: {missing_progress_columns}")
                return False
            else:
                print(f"[OK] Generation_progress table has required columns: {required_progress_columns}")
            
            return True
    except Exception as e:
        print(f"[FAIL] Failed to check columns: {e}")
        return False


async def check_indexes():
    """Check if indexes are created."""
    print("\nChecking database indexes...")
    try:
        async with engine.connect() as conn:
            # SQLite-specific query to get indexes
            result = await conn.execute(text("""
                SELECT name, tbl_name
                FROM sqlite_master
                WHERE type='index'
                AND name NOT LIKE 'sqlite_%'
                ORDER BY tbl_name, name
            """))
            indexes = result.fetchall()
            print(f"Found {len(indexes)} indexes:")
            for idx in indexes:
                print(f"  - {idx[1]}.{idx[0]}")
            print("[OK] Indexes check completed")
            return True
    except Exception as e:
        print(f"[FAIL] Failed to check indexes: {e}")
        return False


async def check_job_executions():
    """Check job_executions table."""
    print("\nChecking job_executions table...")
    try:
        async with engine.connect() as conn:
            result = await conn.execute(text("SELECT COUNT(*) FROM job_executions"))
            count = result.fetchone()[0]
            print(f"[OK] job_executions table exists with {count} records")
            return True
    except Exception as e:
        print(f"[FAIL] Failed to check job_executions: {e}")
        return False


async def check_job_locks():
    """Check job_locks table."""
    print("\nChecking job_locks table...")
    try:
        async with engine.connect() as conn:
            result = await conn.execute(text("SELECT COUNT(*) FROM job_locks"))
            count = result.fetchone()[0]
            print(f"[OK] job_locks table exists with {count} records")
            return True
    except Exception as e:
        print(f"[FAIL] Failed to check job_locks: {e}")
        return False


async def check_elo_cache():
    """Check elo_cache table."""
    print("\nChecking elo_cache table...")
    try:
        async with engine.connect() as conn:
            result = await conn.execute(text("SELECT COUNT(*) FROM elo_cache"))
            count = result.fetchone()[0]
            print(f"[OK] elo_cache table exists with {count} records")
            return True
    except Exception as e:
        print(f"[FAIL] Failed to check elo_cache: {e}")
        return False


async def main():
    """Run all database checks."""
    print("=" * 60)
    print("DATABASE MIGRATION TESTING")
    print("=" * 60)
    
    results = []
    
    # Test database connection
    results.append(await test_database_connection())
    
    # Check tables
    results.append(await check_tables())
    
    # Check columns
    results.append(await check_columns())
    
    # Check indexes
    results.append(await check_indexes())
    
    # Check job_executions table
    results.append(await check_job_executions())
    
    # Check job_locks table
    results.append(await check_job_locks())
    
    # Check elo_cache table
    results.append(await check_elo_cache())
    
    print("\n" + "=" * 60)
    if all(results):
        print("[OK] ALL DATABASE MIGRATION TESTS PASSED")
    else:
        print("[FAIL] SOME DATABASE MIGRATION TESTS FAILED")
    print("=" * 60)
    
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
