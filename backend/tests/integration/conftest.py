"""Integration test configuration.

Provides fixtures and connection checks for integration tests that need
real PostgreSQL and Redis instances running in Docker (started via scripts/dev.sh).

Tests skip gracefully when infrastructure is not available, so CI can pass
without Docker.
"""

import os
import sys

# Ensure shared packages are importable from the function's working directory
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

# Set test environment variables BEFORE importing any app modules
# that might read env vars at import time (e.g. pydantic-settings).
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://whatismytip:whatismytip@localhost:5432/whatismytip")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("ALERT_ENABLED", "false")
os.environ.setdefault("OPENROUTER_API_KEY", "test-key")
os.environ.setdefault("ADMIN_API_KEY", "test-api-key")

import asyncio

from sqlalchemy import text


def _check_postgresql() -> bool:
    """Check if PostgreSQL is reachable and accept a connection."""
    try:
        from packages.shared.db import get_engine

        engine = get_engine()

        async def _ping() -> None:
            async with engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
            await engine.dispose()

        asyncio.run(_ping())
        return True
    except Exception:
        return False


def _check_redis() -> bool:
    """Check if Redis is reachable and respond to PING."""
    try:
        import redis as redis_sync

        r = redis_sync.Redis.from_url(os.environ.get("REDIS_URL", "redis://localhost:6379/0"))
        ok = r.ping()
        r.aclose() if hasattr(r, "aclose") else r.close()
        return bool(ok)
    except Exception:
        return False


# Module-level flags — evaluated once at collection time
POSTGRES_AVAILABLE = _check_postgresql()
REDIS_AVAILABLE = _check_redis()
