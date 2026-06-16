"""Unit tests for the Daily Sync scheduled function.

Tests the ``main()`` FaaS entry point, which (post-Phase 3 refactor)
delegates to ``packages.shared.services.daily_sync.run_daily_sync``.
We mock at the service layer to verify the FaaS handler still wires
up lock acquisition, execution tracking, and the OpenWhisk-shaped
``statusCode`` / ``body`` response.

Note: The cron function directories use hyphens (e.g. ``daily-sync/``)
which are not valid Python identifiers, so we use ``importlib`` to
load the module from its file path.
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


# Service module path used to mock the delegated logic
SERVICE_PATH = "packages.shared.services.daily_sync"


class TestDailySyncFunction:
    """Test the daily-sync scheduled function."""

    @pytest.mark.asyncio
    async def test_successful_sync(self):
        """A normal sync run completes with statusCode 200."""
        mod = _import_daily_sync()

        mock_session = AsyncMock()
        mock_execution = MagicMock(id=1)

        mock_service_result = {
            "status": "success",
            "message": "Synced 9 games for season 2025; Created: 2, Updated: 5, Skipped: 2; Elo cache updated",
            "total_games": 9,
            "games_created": 2,
            "games_updated": 5,
            "games_skipped": 2,
            "errors": 0,
            "duration_seconds": 3,
        }

        with patch.object(mod, "_get_session_factory") as mock_factory, \
             patch.object(mod, "JobLockCRUD") as mock_lock_crud_cls, \
             patch.object(mod, "JobExecutionCRUD") as mock_exec_crud_cls, \
             patch(f"{SERVICE_PATH}.run_daily_sync", new_callable=AsyncMock, return_value=mock_service_result), \
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
        assert "Synced 9 games" in result["body"]["message"]
        # Execution row finalised
        update_call = mock_exec_crud.update_execution.call_args
        assert update_call.kwargs["status"] == "completed"
        assert update_call.kwargs["items_processed"] == 9

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
    async def test_service_error_returns_500(self):
        """When the service raises an exception, returns 500."""
        mod = _import_daily_sync()

        mock_session = AsyncMock()
        mock_execution = MagicMock(id=1)

        with patch.object(mod, "_get_session_factory") as mock_factory, \
             patch.object(mod, "JobLockCRUD") as mock_lock_crud_cls, \
             patch.object(mod, "JobExecutionCRUD") as mock_exec_crud_cls, \
             patch(f"{SERVICE_PATH}.run_daily_sync", new_callable=AsyncMock, side_effect=RuntimeError("API down")), \
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

        assert result["statusCode"] == 500
        assert "API down" in result["body"]["error"]

    @pytest.mark.asyncio
    async def test_off_season_skip(self):
        """When the service returns a skipped result, the FaaS still returns 200."""
        mod = _import_daily_sync()

        mock_session = AsyncMock()
        mock_execution = MagicMock(id=1)

        mock_service_result = {
            "status": "skipped",
            "message": "Skipping daily sync – off-season reduced frequency (month=11, hour=10)",
            "total_games": 0,
            "games_created": 0,
            "games_updated": 0,
            "games_skipped": 0,
            "errors": 0,
        }

        with patch.object(mod, "_get_session_factory") as mock_factory, \
             patch.object(mod, "JobLockCRUD") as mock_lock_crud_cls, \
             patch.object(mod, "JobExecutionCRUD") as mock_exec_crud_cls, \
             patch(f"{SERVICE_PATH}.run_daily_sync", new_callable=AsyncMock, return_value=mock_service_result), \
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
