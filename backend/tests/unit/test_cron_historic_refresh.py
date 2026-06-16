"""Unit tests for the Historic Refresh scheduled function.

Tests the ``main()`` FaaS entry point, which (post-Phase 3 refactor)
delegates to ``packages.shared.services.historic_refresh.run_historic_refresh``.
We mock at the service layer to verify the FaaS handler still wires up
lock acquisition, execution tracking, and the OpenWhisk-shaped
``statusCode`` / ``body`` response.
"""

import importlib.util
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Load the historic-refresh module from its file path (hyphen in directory name)
_MODULE_PATH = os.path.join(
    os.path.dirname(__file__), "..", "..", "packages", "cron", "historic-refresh", "__init__.py"
)
_spec = importlib.util.spec_from_file_location("historic_refresh", _MODULE_PATH)
historic_refresh = importlib.util.module_from_spec(_spec)


def _import_historic_refresh():
    """Import (or re-import) the historic-refresh module."""
    _spec.loader.exec_module(historic_refresh)
    return historic_refresh


SERVICE_PATH = "packages.shared.services.historic_refresh"


def _make_cache(get_return=None):
    cache = AsyncMock()
    cache.get = AsyncMock(return_value=get_return)
    cache.set = AsyncMock()
    cache.delete = AsyncMock()
    return cache


class TestHistoricRefreshFunction:
    """Test the historic-refresh scheduled function."""

    @pytest.mark.asyncio
    async def test_successful_batch_processing(self):
        """A normal historic-refresh run processes all batches and returns 200."""
        mod = _import_historic_refresh()

        mock_session = AsyncMock()
        mock_execution = MagicMock(id=1)
        mock_cache = _make_cache(get_return=None)

        mock_service_result = {
            "status": "success",
            "message": "Processed 16 seasons across 8 batch(es); Synced 480 games; Generated 320 tips",
            "batches_processed": 8,
            "total_seasons_processed": 16,
            "total_games_synced": 480,
            "total_tips_generated": 320,
            "total_errors": 0,
            "timed_out": False,
            "duration_seconds": 60,
        }

        with patch.object(mod, "_get_session_factory") as mock_factory, \
             patch.object(mod, "JobLockCRUD") as mock_lock_crud_cls, \
             patch.object(mod, "JobExecutionCRUD") as mock_exec_crud_cls, \
             patch(f"{SERVICE_PATH}.run_historic_refresh", new_callable=AsyncMock, return_value=mock_service_result), \
             patch.object(mod, "RedisCache", return_value=mock_cache), \
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
        assert "Processed 16 seasons" in result["body"]["message"]

    @pytest.mark.asyncio
    async def test_lock_acquisition_failure(self):
        """When lock cannot be acquired, returns 200 'already running'."""
        mod = _import_historic_refresh()

        mock_session = AsyncMock()
        mock_cache = _make_cache(get_return=None)

        with patch.object(mod, "_get_session_factory") as mock_factory, \
             patch.object(mod, "JobLockCRUD") as mock_lock_crud_cls, \
             patch.object(mod, "RedisCache", return_value=mock_cache), \
             patch.object(mod, "close_redis_pool", new_callable=AsyncMock):

            mock_factory.return_value.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_factory.return_value.return_value.__aexit__ = AsyncMock(return_value=None)

            mock_lock_crud = mock_lock_crud_cls.return_value
            mock_lock_crud.acquire_lock = AsyncMock(return_value=None)  # Lock failed

            result = await mod.main({})

        assert result["statusCode"] == 200
        assert "already running" in result["body"]["message"].lower()

    @pytest.mark.asyncio
    async def test_error_returns_500(self):
        """When the service raises an exception, returns 500."""
        mod = _import_historic_refresh()

        mock_session = AsyncMock()
        mock_execution = MagicMock(id=1)
        mock_cache = _make_cache(get_return=None)

        with patch.object(mod, "_get_session_factory") as mock_factory, \
             patch.object(mod, "JobLockCRUD") as mock_lock_crud_cls, \
             patch.object(mod, "JobExecutionCRUD") as mock_exec_crud_cls, \
             patch(f"{SERVICE_PATH}.run_historic_refresh", new_callable=AsyncMock, side_effect=RuntimeError("Database connection lost")), \
             patch.object(mod, "RedisCache", return_value=mock_cache), \
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
        assert "Database connection lost" in result["body"]["error"]
