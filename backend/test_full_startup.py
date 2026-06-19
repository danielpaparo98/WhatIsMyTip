"""Test the full application startup process like FastAPI does."""
import sys
import os
import logging
import asyncio

# Configure logging to see all diagnostic messages
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'backend'))

print("=" * 60)
print("Testing full application startup process")
print("=" * 60)

# Import the modules in the same order as the FastAPI app
print("\n1. Importing config...")
from packages.shared.config import settings
print(f"   DATABASE_URL: {settings.database_url}")

print("\n2. Importing database module...")
from packages.shared import db

print("\n3. Creating engine (like in lifespan)...")
try:
    engine = db.get_engine()
    print("   SUCCESS: Engine created!")
    print(f"   Driver: {engine.driver}")
except Exception as e:
    print(f"   FAILURE: {e}")
    import traceback
    traceback.print_exc()

print("\n4. Importing cache module...")
try:
    from packages.shared import cache
    redis_client = cache._get_client()
    print("   SUCCESS: Redis client created!")
except Exception as e:
    print(f"   FAILURE: {e}")

print("\n5. Testing session factory...")
try:
    session = db.get_session()
    print(f"   SUCCESS: Session created! Type: {type(session)}")
except Exception as e:
    print(f"   FAILURE: {e}")
    import traceback
    traceback.print_exc()

print("\n6. Importing and initializing scheduler...")
try:
    from app.core.scheduler import init_scheduler
    session_factory = db.get_session
    
    async def test_scheduler():
        scheduler = await init_scheduler(session_factory)
        print(f"   SUCCESS: Scheduler initialized with {len(scheduler.get_jobs())} jobs")
        await scheduler.shutdown()
    
    asyncio.run(test_scheduler())
except Exception as e:
    print(f"   FAILURE: {e}")
    import traceback
    traceback.print_exc()

print("\n" + "=" * 60)
print("Startup test complete")
print("=" * 60)