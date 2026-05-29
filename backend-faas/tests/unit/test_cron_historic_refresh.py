"""Unit tests for the Historic Refresh scheduled function.

Tests the ``main()`` entry point and ``_resolve_batch()`` helper by mocking
the database session factory, CRUD operations, services, and Redis pool.
No external dependencies required.

Note: The cron function directories use hyphens (e.g. ``historic-refresh/``) which
are not valid Python identifiers.  We use ``importlib`` to load the module
from its file path.
"""

import importlib.util
import os
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


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


class TestHistoricRefreshFunction:
    """Test the historic-refresh scheduled function."""

    @pytest.mark.asyncio
    async def test_successful_batch_processing(self):
        """A normal historic-refresh run processes all batches and returns 200."""
        mod = _import_historic_refresh()

        mock_session = AsyncMock()
        mock_execution = MagicMock(id=1)

        mock_stats = {
            "seasons_processed": 4,
            "games_synced": 120,
            "tips_generated": 80,
            "errors": [],
        }

        with patch.object(mod, "_get_session_factory") as mock_factory, \
             patch.object(mod, "JobLockCRUD") as mock_lock_crud_cls, \
             patch.object(mod, "JobExecutionCRUD") as mock_exec_crud_cls, \
             patch.object(mod, "HistoricDataRefreshService") as mock_refresh_cls, \
             patch.object(mod, "close_redis_pool", new_callable=AsyncMock):

            mock_factory.return_value.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_factory.return_value.return_value.__aexit__ = AsyncMock(return_value=None)

            mock_lock_crud = mock_lock_crud_cls.return_value
            mock_lock_crud.acquire_lock = AsyncMock(return_value=MagicMock())
            mock_lock_crud.release_lock = AsyncMock()

            mock_exec_crud = mock_exec_crud_cls.return_value
            mock_exec_crud.create_execution = AsyncMock(return_value=mock_execution)
            mock_exec_crud.update_execution = AsyncMock()

            mock_refresh = mock_refresh_cls.return_value
            mock_refresh.refresh_from_string = AsyncMock(return_value=mock_stats)

            result = await mod.main({})

        assert result["statusCode"] == 200
        # Batch chaining: all 4 batches processed (16 seasons total)
        assert "Processed 16 seasons across 4 batch(es)" in result["body"]["message"]
        assert "Synced 480 games" in result["body"]["message"]
        assert "Generated 320 tips" in result["body"]["message"]

    @pytest.mark.asyncio
    async def test_lock_acquisition_failure(self):
        """When lock cannot be acquired, returns 200 'already running'."""
        mod = _import_historic_refresh()

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
    async def test_error_returns_500(self):
        """When refresh raises an exception, returns 500."""
        mod = _import_historic_refresh()

        mock_session = AsyncMock()
        mock_execution = MagicMock(id=1)

        with patch.object(mod, "_get_session_factory") as mock_factory, \
             patch.object(mod, "JobLockCRUD") as mock_lock_crud_cls, \
             patch.object(mod, "JobExecutionCRUD") as mock_exec_crud_cls, \
             patch.object(mod, "HistoricDataRefreshService") as mock_refresh_cls, \
             patch.object(mod, "close_redis_pool", new_callable=AsyncMock):

            mock_factory.return_value.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_factory.return_value.return_value.__aexit__ = AsyncMock(return_value=None)

            mock_lock_crud = mock_lock_crud_cls.return_value
            mock_lock_crud.acquire_lock = AsyncMock(return_value=MagicMock())
            mock_lock_crud.release_lock = AsyncMock()

            mock_exec_crud = mock_exec_crud_cls.return_value
            mock_exec_crud.create_execution = AsyncMock(return_value=mock_execution)
            mock_exec_crud.update_execution = AsyncMock()

            mock_refresh = mock_refresh_cls.return_value
            mock_refresh.refresh_from_string = AsyncMock(
                side_effect=RuntimeError("Database connection lost")
            )

            result = await mod.main({})

        assert result["statusCode"] == 500
        assert "Database connection lost" in result["body"]["error"]

    @pytest.mark.asyncio
    async def test_batch_selection_with_start_season(self):
        """When start_season is provided, processing starts from that batch and continues."""
        mod = _import_historic_refresh()

        mock_session = AsyncMock()
        mock_execution = MagicMock(id=1)

        mock_stats = {
            "seasons_processed": 4,
            "games_synced": 100,
            "tips_generated": 60,
            "errors": [],
        }

        with patch.object(mod, "_get_session_factory") as mock_factory, \
             patch.object(mod, "JobLockCRUD") as mock_lock_crud_cls, \
             patch.object(mod, "JobExecutionCRUD") as mock_exec_crud_cls, \
             patch.object(mod, "HistoricDataRefreshService") as mock_refresh_cls, \
             patch.object(mod, "close_redis_pool", new_callable=AsyncMock):

            mock_factory.return_value.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_factory.return_value.return_value.__aexit__ = AsyncMock(return_value=None)

            mock_lock_crud = mock_lock_crud_cls.return_value
            mock_lock_crud.acquire_lock = AsyncMock(return_value=MagicMock())
            mock_lock_crud.release_lock = AsyncMock()

            mock_exec_crud = mock_exec_crud_cls.return_value
            mock_exec_crud.create_execution = AsyncMock(return_value=mock_execution)
            mock_exec_crud.update_execution = AsyncMock()

            mock_refresh = mock_refresh_cls.return_value
            mock_refresh.refresh_from_string = AsyncMock(return_value=mock_stats)

            # start_season=2020 should start from batch [2018, 2019, 2020, 2021]
            # and continue through remaining batches (3 batches total)
            result = await mod.main({"start_season": "2020"})

        assert result["statusCode"] == 200
        # Should process 3 batches (2018-2021, 2022-2025, and the one before was skipped)
        # First constructor call should be with the batch containing 2020
        first_constructor_call = mock_refresh_cls.call_args_list[0]
        assert first_constructor_call.kwargs["seasons"] == [2018, 2019, 2020, 2021]
        # Verify refresh_from_string was called 2 times (2 remaining batches from index 2)
        assert mock_refresh.refresh_from_string.await_count == 2

    @pytest.mark.asyncio
    async def test_batch_selection_default(self):
        """When no start_season is provided, all batches are processed starting from the first."""
        mod = _import_historic_refresh()

        mock_session = AsyncMock()
        mock_execution = MagicMock(id=1)

        mock_stats = {
            "seasons_processed": 4,
            "games_synced": 80,
            "tips_generated": 50,
            "errors": [],
        }

        with patch.object(mod, "_get_session_factory") as mock_factory, \
             patch.object(mod, "JobLockCRUD") as mock_lock_crud_cls, \
             patch.object(mod, "JobExecutionCRUD") as mock_exec_crud_cls, \
             patch.object(mod, "HistoricDataRefreshService") as mock_refresh_cls, \
             patch.object(mod, "close_redis_pool", new_callable=AsyncMock):

            mock_factory.return_value.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_factory.return_value.return_value.__aexit__ = AsyncMock(return_value=None)

            mock_lock_crud = mock_lock_crud_cls.return_value
            mock_lock_crud.acquire_lock = AsyncMock(return_value=MagicMock())
            mock_lock_crud.release_lock = AsyncMock()

            mock_exec_crud = mock_exec_crud_cls.return_value
            mock_exec_crud.create_execution = AsyncMock(return_value=mock_execution)
            mock_exec_crud.update_execution = AsyncMock()

            mock_refresh = mock_refresh_cls.return_value
            mock_refresh.refresh_from_string = AsyncMock(return_value=mock_stats)

            # No start_season provided — should default to first batch and process all
            result = await mod.main({})

        assert result["statusCode"] == 200
        # First constructor call should be with the first batch
        first_constructor_call = mock_refresh_cls.call_args_list[0]
        assert first_constructor_call.kwargs["seasons"] == [2010, 2011, 2012, 2013]
        # All 4 batches should be processed
        assert mock_refresh.refresh_from_string.await_count == 4
