"""Unit tests for the Daily Sync scheduled function.

Tests the ``main()`` entry point by mocking the database session factory,
CRUD operations, services, and Redis pool. No external dependencies required.

Note: The cron function directories use hyphens (e.g. ``daily-sync/``) which
are not valid Python identifiers.  We use ``importlib`` to load the module
from its file path.
"""

import importlib.util
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Load the daily-sync module from its file path (hyphen in directory name)
_MODULE_PATH = os.path.join(
    os.path.dirname(__file__), "..", "..", "packages", "cron", "daily-sync", "__init__.py"
)
_spec = importlib.util.spec_from_file_location("daily_sync", _MODULE_PATH)
daily_sync = importlib.util.module_from_spec(_spec)


def _import_daily_sync():
    """Import (or re-import) the daily-sync module."""
    _spec.loader.exec_module(daily_sync)
    return daily_sync


class TestDailySyncFunction:
    """Test the daily-sync scheduled function."""

    @pytest.mark.asyncio
    async def test_successful_sync(self):
        """A normal sync run completes with statusCode 200."""
        mod = _import_daily_sync()

        mock_session = AsyncMock()
        mock_execution = MagicMock(id=1)

        mock_stats = {
            "games_created": 2,
            "games_updated": 5,
            "games_skipped": 2,
            "total_games": 9,
            "errors": [],
        }

        with patch.object(mod, "_get_session_factory") as mock_factory, \
             patch.object(mod, "JobLockCRUD") as mock_lock_crud_cls, \
             patch.object(mod, "JobExecutionCRUD") as mock_exec_crud_cls, \
             patch.object(mod, "SquiggleClient") as mock_squiggle_cls, \
             patch.object(mod, "GameSyncService") as mock_sync_cls, \
             patch.object(mod, "EloModel") as mock_elo, \
             patch.object(mod, "close_redis_pool", new_callable=AsyncMock):

            mock_factory.return_value.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_factory.return_value.return_value.__aexit__ = AsyncMock(return_value=None)

            mock_lock_crud = mock_lock_crud_cls.return_value
            mock_lock_crud.acquire_lock = AsyncMock(return_value=MagicMock())
            mock_lock_crud.release_lock = AsyncMock()

            mock_exec_crud = mock_exec_crud_cls.return_value
            mock_exec_crud.create_execution = AsyncMock(return_value=mock_execution)
            mock_exec_crud.update_execution = AsyncMock()

            mock_squiggle = mock_squiggle_cls.return_value
            mock_squiggle.close = AsyncMock()

            mock_sync = mock_sync_cls.return_value
            mock_sync.sync_games = AsyncMock(return_value=mock_stats)

            mock_elo.update_cache = AsyncMock()

            result = await mod.main({})

        assert result["statusCode"] == 200
        assert "Synced 9 games" in result["body"]["message"]

    @pytest.mark.asyncio
    async def test_lock_acquisition_failure(self):
        """When lock cannot be acquired, returns 200 'already running'."""
        mod = _import_daily_sync()

        mock_session = AsyncMock()

        with patch.object(mod, "_get_session_factory") as mock_factory, \
             patch.object(mod, "JobLockCRUD") as mock_lock_crud_cls, \
             patch.object(mod, "close_redis_pool", new_callable=AsyncMock):

            mock_factory.return_value.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_factory.return_value.return_value.__aexit__ = AsyncMock(return_value=None)

            mock_lock_crud = mock_lock_crud_cls.return_value
            mock_lock_crud.acquire_lock = AsyncMock(return_value=None)  # Lock failed

            result = await mod.main({})

        assert result["statusCode"] == 200
        assert "already running" in result["body"]["message"].lower()

    @pytest.mark.asyncio
    async def test_sync_error_returns_500(self):
        """When sync raises an exception, returns 500."""
        mod = _import_daily_sync()

        mock_session = AsyncMock()
        mock_execution = MagicMock(id=1)

        with patch.object(mod, "_get_session_factory") as mock_factory, \
             patch.object(mod, "JobLockCRUD") as mock_lock_crud_cls, \
             patch.object(mod, "JobExecutionCRUD") as mock_exec_crud_cls, \
             patch.object(mod, "SquiggleClient") as mock_squiggle_cls, \
             patch.object(mod, "GameSyncService") as mock_sync_cls, \
             patch.object(mod, "close_redis_pool", new_callable=AsyncMock):

            mock_factory.return_value.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_factory.return_value.return_value.__aexit__ = AsyncMock(return_value=None)

            mock_lock_crud = mock_lock_crud_cls.return_value
            mock_lock_crud.acquire_lock = AsyncMock(return_value=MagicMock())
            mock_lock_crud.release_lock = AsyncMock()

            mock_exec_crud = mock_exec_crud_cls.return_value
            mock_exec_crud.create_execution = AsyncMock(return_value=mock_execution)
            mock_exec_crud.update_execution = AsyncMock()

            mock_squiggle = mock_squiggle_cls.return_value
            mock_squiggle.close = AsyncMock()

            mock_sync = mock_sync_cls.return_value
            mock_sync.sync_games = AsyncMock(side_effect=RuntimeError("API down"))

            result = await mod.main({})

        assert result["statusCode"] == 500
        assert "API down" in result["body"]["error"]

    @pytest.mark.asyncio
    async def test_off_season_skip(self):
        """During off-season outside 2-4 AM window, sync is skipped."""
        mod = _import_daily_sync()

        mock_session = AsyncMock()
        mock_execution = MagicMock(id=1)

        # Mock datetime to return a month in the off-season (November) at 10 AM
        mock_dt = MagicMock()
        mock_dt.now.return_value = MagicMock(month=11, hour=10)

        with patch.object(mod, "_get_session_factory") as mock_factory, \
             patch.object(mod, "JobLockCRUD") as mock_lock_crud_cls, \
             patch.object(mod, "JobExecutionCRUD") as mock_exec_crud_cls, \
             patch.object(mod, "datetime", mock_dt), \
             patch.object(mod, "close_redis_pool", new_callable=AsyncMock):

            mock_factory.return_value.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_factory.return_value.return_value.__aexit__ = AsyncMock(return_value=None)

            mock_lock_crud = mock_lock_crud_cls.return_value
            mock_lock_crud.acquire_lock = AsyncMock(return_value=MagicMock())
            mock_lock_crud.release_lock = AsyncMock()

            mock_exec_crud = mock_exec_crud_cls.return_value
            mock_exec_crud.create_execution = AsyncMock(return_value=mock_execution)
            mock_exec_crud.update_execution = AsyncMock()

            result = await mod.main({})

        assert result["statusCode"] == 200
        assert "off-season" in result["body"]["message"].lower()
