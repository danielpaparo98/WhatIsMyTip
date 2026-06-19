"""Simple diagnostic script to check database configuration."""
import sys
import os

# Add backend to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'backend'))

from packages.shared.config import settings
from packages.shared import db

print("=" * 60)
print("DATABASE CONFIGURATION DIAGNOSTICS")
print("=" * 60)

print(f"\n1. DATABASE_URL from settings: {settings.database_url}")
print(f"   - Has +asyncpg prefix? {('+asyncpg' in settings.database_url)}")
print(f"   - Has +psycopg prefix? {('+psycopg' in settings.database_url)}")
print(f"   - Starts with postgresql://? {settings.database_url.startswith('postgresql://')}")

print(f"\n2. Environment variables:")
print(f"   - DATABASE_URL env var: {os.environ.get('DATABASE_URL', 'NOT SET')}")
print(f"   - DB_SSL_VERIFY env var: {os.environ.get('DB_SSL_VERIFY', 'NOT SET')}")

print(f"\n3. Requirements check:")
print(f"   - asyncpg should be installed (async driver)")
print(f"   - psycopg2-binary should NOT be used for async operations")

print("\n" + "=" * 60)
print("Testing database engine creation...")
print("=" * 60)

try:
    engine = db.get_engine()
    print(f"✓ Engine created successfully!")
    print(f"  - Driver: {engine.driver}")
    print(f"  - URL: {engine.url}")
except Exception as e:
    print(f"✗ Engine creation failed!")
    print(f"  - Error: {e}")
    import traceback
    print(f"\n  Full traceback:")
    traceback.print_exc()

print("\n" + "=" * 60)