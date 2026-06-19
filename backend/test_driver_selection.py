"""Test SQLAlchemy driver selection when both asyncpg and psycopg2 are available."""
import sys
import os
import asyncio
import logging

# Configure logging to see all diagnostic messages
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'backend'))

print("=" * 60)
print("SQLAlchemy Driver Selection Test")
print("=" * 60)

# Check what drivers are available
print("\n1. Checking available PostgreSQL drivers:")
try:
    import asyncpg
    print(f"   - asyncpg available: YES (version {asyncpg.__version__})")
except ImportError:
    print("   - asyncpg available: NO")

try:
    import psycopg2
    print(f"   - psycopg2 available: YES")
except ImportError:
    print("   - psycopg2 available: NO")

print("\n2. Testing SQLAlchemy URL parsing:")
from sqlalchemy.engine.url import make_url

test_urls = [
    "postgresql+asyncpg://user:pass@localhost/db",
    "postgresql://user:pass@localhost/db",
    "postgresql+psycopg2://user:pass@localhost/db",
]

for url_str in test_urls:
    url = make_url(url_str)
    print(f"   - URL: {url_str}")
    print(f"     Driver: {url.drivername}")
    print(f"     Dialect: {url.get_dialect()}")

print("\n3. Testing async engine creation with explicit asyncpg URL:")
from sqlalchemy.ext.asyncio import create_async_engine

async def test_async_engine():
    try:
        engine = create_async_engine("postgresql+asyncpg://user:pass@localhost/db", echo=True)
        print(f"   SUCCESS: Engine created!")
        print(f"   Driver: {engine.driver}")
        print(f"   Dialect: {engine.dialect}")
        await engine.dispose()
    except Exception as e:
        print(f"   FAILURE: {e}")
        import traceback
        traceback.print_exc()

asyncio.run(test_async_engine())

print("\n4. Testing what happens if SQLAlchemy detects psycopg2 first:")
print("   (This simulates the potential issue in the actual app)")

# Import in the order that might cause issues
import sqlalchemy
print(f"   SQLAlchemy version: {sqlalchemy.__version__}")

async def test_with_psycopg2():
    # Check if there's any psycopg2 import that might interfere
    try:
        # Force import psycopg2 to see if it affects SQLAlchemy
        import psycopg2
        print(f"   psycopg2 imported successfully")
        
        # Now try to create async engine
        engine = create_async_engine("postgresql+asyncpg://user:pass@localhost/db")
        print(f"   SUCCESS: Engine created even after psycopg2 import!")
        print(f"   Driver: {engine.driver}")
        await engine.dispose()
    except Exception as e:
        print(f"   FAILURE: {e}")
        import traceback
        traceback.print_exc()

asyncio.run(test_with_psycopg2())

print("\n" + "=" * 60)