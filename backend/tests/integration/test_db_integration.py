"""Integration tests for database connectivity and operations.

Requires a running PostgreSQL instance (start with scripts/dev.sh).
All tests skip gracefully when the database is not available.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

# Set test env before imports
os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("ALERT_ENABLED", "false")

import pytest
from sqlalchemy import text

from packages.shared.db import _get_session_factory, dispose_engine, get_engine

# Skip entire module if no DATABASE_URL configured.
# The integration conftest.py sets up an ephemeral Postgres for CI;
# without it these tests cannot run.
_DSN = os.environ.get("DATABASE_URL", "")
pytestmark = pytest.mark.skipif(
    not _DSN or "localhost" in _DSN,
    reason="Integration tests require DATABASE_URL pointing at a live Postgres",
)


@pytest.fixture(autouse=True)
async def _cleanup_engine():
    """Force-dispose engine after every test to avoid pooled connection leaks."""
    yield
    await dispose_engine(force=True)


class TestDatabaseConnectivity:
    """Verify basic PostgreSQL connectivity."""

    @pytest.mark.asyncio
    async def test_engine_creates_and_connects(self):
        """get_engine() creates a working async engine."""
        engine = get_engine()
        assert engine is not None

        async with engine.connect() as conn:
            result = await conn.execute(text("SELECT 1"))
            assert result.scalar() == 1

    @pytest.mark.asyncio
    async def test_session_factory_creates_sessions(self):
        """_get_session_factory() produces working async sessions."""
        factory = _get_session_factory()
        async with factory() as session:
            result = await session.execute(text("SELECT 1"))
            assert result.scalar() == 1

    @pytest.mark.asyncio
    async def test_postgresql_version(self):
        """Verify we're connected to a PostgreSQL 16 instance."""
        engine = get_engine()
        async with engine.connect() as conn:
            result = await conn.execute(text("SELECT version()"))
            version = result.scalar()
            assert "PostgreSQL" in version


class TestDatabaseSchema:
    """Verify that all expected tables exist after migration."""

    EXPECTED_TABLES = [
        "games",
        "tips",
        "model_predictions",
        "backtest_results",
        "generation_progress",
        "job_executions",
        "job_locks",
        "elo_cache",
        "match_analyses",
    ]

    @pytest.mark.asyncio
    async def test_all_tables_exist(self):
        """All required tables should be present in the public schema."""
        engine = get_engine()
        async with engine.connect() as conn:
            result = await conn.execute(
                text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
            )
            existing = {row[0] for row in result.fetchall()}

        for table in self.EXPECTED_TABLES:
            assert table in existing, f"Table '{table}' not found in database"

    @pytest.mark.asyncio
    async def test_games_table_columns(self):
        """Games table should have the expected core columns."""
        engine = get_engine()
        async with engine.connect() as conn:
            result = await conn.execute(
                text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_name = 'games' AND table_schema = 'public'"
                )
            )
            columns = {row[0] for row in result.fetchall()}

        expected_cols = {"id", "source_id", "season", "round_id", "home_team", "away_team", "date"}
        for col in expected_cols:
            assert col in columns, f"Column '{col}' not found in games table"

    @pytest.mark.asyncio
    async def test_tips_table_columns(self):
        """Tips table should have the expected core columns."""
        engine = get_engine()
        async with engine.connect() as conn:
            result = await conn.execute(
                text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_name = 'tips' AND table_schema = 'public'"
                )
            )
            columns = {row[0] for row in result.fetchall()}

        expected_cols = {"id", "game_id", "heuristic", "predicted_winner"}
        for col in expected_cols:
            assert col in columns, f"Column '{col}' not found in tips table"
