"""Shared test fixtures for backend tests."""

import os

# Set test environment variables BEFORE importing any application modules
# that might read env vars at import time (e.g. pydantic-settings).
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost/test")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/15")
os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("ADMIN_API_KEY", "test-api-key")

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

# ---------------------------------------------------------------------------
# Pytest CLI options
# ---------------------------------------------------------------------------

def pytest_addoption(parser: pytest.Parser) -> None:
    """Register the --run-integration flag for integration tests."""
    parser.addoption(
        "--run-integration",
        action="store_true",
        default=False,
        help="Run integration tests that require PostgreSQL and Redis",
    )


# ---------------------------------------------------------------------------
# Redis mock
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_redis():
    """Mock Redis client for unit tests.

    Provides async mocks for all Redis operations used by the cache layer.
    Does NOT require a running Redis instance.
    """
    redis_mock = AsyncMock()
    redis_mock.get = AsyncMock(return_value=None)
    redis_mock.set = AsyncMock(return_value=True)
    redis_mock.delete = AsyncMock(return_value=1)
    redis_mock.scan_iter = MagicMock(return_value=[])
    redis_mock.expire = AsyncMock(return_value=True)
    redis_mock.pipeline = MagicMock()
    redis_mock.aclose = AsyncMock()

    # Pipeline mock (context manager)
    pipe_mock = AsyncMock()
    pipe_mock.get = AsyncMock(return_value=None)
    pipe_mock.set = AsyncMock(return_value=True)
    pipe_mock.delete = AsyncMock(return_value=1)
    pipe_mock.execute = AsyncMock(return_value=[])
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=pipe_mock)
    ctx.__aexit__ = AsyncMock(return_value=None)
    redis_mock.pipeline.return_value = ctx

    return redis_mock


# ---------------------------------------------------------------------------
# DB session mock
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_db_session():
    """Mock async SQLAlchemy AsyncSession.

    All commonly-used methods are pre-configured as AsyncMocks so that
    individual tests only need to set return values they care about.
    """
    session = AsyncMock()
    session.execute = AsyncMock()
    session.scalar = AsyncMock()
    session.scalars = AsyncMock()
    session.get = AsyncMock()
    session.add = MagicMock()
    session.commit = AsyncMock()
    session.rollback = AsyncMock()
    session.close = AsyncMock()
    session.refresh = AsyncMock()
    session.begin = MagicMock()

    # Support `async with session.begin():`
    begin_ctx = MagicMock()
    begin_ctx.__aenter__ = AsyncMock(return_value=None)
    begin_ctx.__aexit__ = AsyncMock(return_value=None)
    session.begin.return_value = begin_ctx

    return session


# ---------------------------------------------------------------------------
# Sample data factories
# ---------------------------------------------------------------------------

@pytest.fixture
def sample_game_data():
    """Sample game data dict for tests."""
    return {
        "id": 1,
        "slug": "abc123def45",
        "squiggle_id": 12345,
        "round_id": 1,
        "season": 2025,
        "home_team": "Brisbane",
        "away_team": "Collingwood",
        "home_score": None,
        "away_score": None,
        "venue": "Gabba",
        "date": datetime(2025, 3, 15, 18, 0, tzinfo=timezone.utc),
        "completed": False,
        "predictions_generated": False,
        "tips_generated": False,
    }


@pytest.fixture
def sample_tip_data():
    """Sample tip data dict for tests."""
    return {
        "id": 1,
        "game_id": 1,
        "heuristic": "best_bet",
        "selected_team": "Brisbane",
        "margin": 12,
        "confidence": 0.75,
        "explanation": "Brisbane strong at home",
    }


@pytest.fixture
def sample_prediction_data():
    """Sample model prediction data dict for tests."""
    return {
        "id": 1,
        "game_id": 1,
        "model_name": "elo",
        "winner": "Brisbane",
        "confidence": 0.7,
        "margin": 12,
    }
