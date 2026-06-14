"""Unit tests for the Historic Refresh scheduled function.

Tests the ``main()`` entry point by mocking the database session factory,
CRUD operations, services, Redis cache, and Redis pool.  No external
dependencies required.

Note: The cron function directories use hyphens (e.g. ``historic-refresh/``) which
are not valid Python identifiers.  We use ``importlib`` to load the module
from its file path.
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


def _make_cache(get_return=None):
    """Create a mock RedisCache with standard async methods."""
    cache = AsyncMock()
    cache.get = AsyncMock(return_value=get_return)
    cache.set = AsyncMock()
    cache.delete = AsyncMock()
    return cache


class TestHistoricRefreshFunction:
    """Test the historic-refresh scheduled function."""

    @pytest.mark.asyncio
    async def test_successful_batch_processing(self):
        """A normal historic-refresh run processes all 8 batches and returns 200."""
        mod = _import_historic_refresh()

        mock_session = AsyncMock()
        mock_execution = MagicMock(id=1)
        mock_cache = _make_cache(get_return=None)

        mock_stats = {
            "seasons_processed": 2,
            "games_synced": 60,
            "tips_generated": 40,
            "errors": [],
        }

        with patch.object(mod, "_get_session_factory") as mock_factory, \
             patch.object(mod, "JobLockCRUD") as mock_lock_crud_cls, \
             patch.object(mod, "JobExecutionCRUD") as mock_exec_crud_cls, \
             patch.object(mod, "HistoricDataRefreshService") as mock_refresh_cls, \
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

            mock_refresh = mock_refresh_cls.return_value
            mock_refresh.refresh_from_string = AsyncMock(return_value=mock_stats)

            result = await mod.main({})

        assert result["statusCode"] == 200
        # BATCH_SIZE=2 → 16 seasons / 2 = 8 batches
        assert "Processed 16 seasons across 8 batch(es)" in result["body"]["message"]
        assert "Synced 480 games" in result["body"]["message"]
        assert "Generated 320 tips" in result["body"]["message"]
        # Continuation marker should be cleared after all done
        mock_cache.delete.assert_awaited_once()

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
        """When refresh raises an exception, returns 500."""
        mod = _import_historic_refresh()

        mock_session = AsyncMock()
        mock_execution = MagicMock(id=1)
        mock_cache = _make_cache(get_return=None)

        with patch.object(mod, "_get_session_factory") as mock_factory, \
             patch.object(mod, "JobLockCRUD") as mock_lock_crud_cls, \
             patch.object(mod, "JobExecutionCRUD") as mock_exec_crud_cls, \
             patch.object(mod, "HistoricDataRefreshService") as mock_refresh_cls, \
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

            mock_refresh = mock_refresh_cls.return_value
            mock_refresh.refresh_from_string = AsyncMock(
                side_effect=RuntimeError("Database connection lost")
            )

            result = await mod.main({})

        assert result["statusCode"] == 500
        assert "Database connection lost" in result["body"]["error"]

    @pytest.mark.asyncio
    async def test_continuation_resumes_from_redis(self):
        """When a continuation marker exists in Redis, processing resumes from remaining seasons."""
        mod = _import_historic_refresh()

        mock_session = AsyncMock()
        mock_execution = MagicMock(id=1)

        # Simulate 4 remaining seasons from a previous partial run
        remaining_seasons = [2022, 2023, 2024, 2025]
        mock_cache = _make_cache(get_return=remaining_seasons)

        mock_stats = {
            "seasons_processed": 2,
            "games_synced": 60,
            "tips_generated": 40,
            "errors": [],
        }

        with patch.object(mod, "_get_session_factory") as mock_factory, \
             patch.object(mod, "JobLockCRUD") as mock_lock_crud_cls, \
             patch.object(mod, "JobExecutionCRUD") as mock_exec_crud_cls, \
             patch.object(mod, "HistoricDataRefreshService") as mock_refresh_cls, \
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

            mock_refresh = mock_refresh_cls.return_value
            mock_refresh.refresh_from_string = AsyncMock(return_value=mock_stats)

            result = await mod.main({})

        assert result["statusCode"] == 200
        # 4 remaining seasons / BATCH_SIZE=2 = 2 batches
        assert mock_refresh.refresh_from_string.await_count == 2
        # First batch should be [2022, 2023]
        first_call_seasons = mock_refresh_cls.call_args_list[0].kwargs["seasons"]
        assert first_call_seasons == [2022, 2023]
        # Continuation marker should be cleared after completion
        mock_cache.delete.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_continuation_stores_remaining_on_timeout(self):
        """When time limit is approached, remaining seasons are stored in Redis."""
        mod = _import_historic_refresh()

        mock_session = AsyncMock()
        mock_execution = MagicMock(id=1)
        mock_cache = _make_cache(get_return=None)

        mock_stats = {
            "seasons_processed": 2,
            "games_synced": 60,
            "tips_generated": 40,
            "errors": [],
        }

        # Simulate time progression so that timeout hits after 4 batches
        # With BATCH_SIZE=2 and 16 seasons → 8 batches.
        # Each batch iteration calls time.time() 3 times:
        #   1) elapsed check, 2) batch_start, 3) batch_duration
        # Plus 1 call for overall_start and 1 for overall_duration at the end.
        #
        # We make the elapsed check at batch 4 return > MAX_RUNTIME_SECONDS (780).
        time_values = iter([
            0.0,    # overall_start
            100.0,  # batch 0 elapsed check  (100 < 780 → continue)
            100.0,  # batch 0 batch_start
            200.0,  # batch 0 batch_end
            200.0,  # batch 1 elapsed check  (200 < 780 → continue)
            200.0,  # batch 1 batch_start
            300.0,  # batch 1 batch_end
            300.0,  # batch 2 elapsed check  (300 < 780 → continue)
            300.0,  # batch 2 batch_start
            400.0,  # batch 2 batch_end
            400.0,  # batch 3 elapsed check  (400 < 780 → continue)
            400.0,  # batch 3 batch_start
            500.0,  # batch 3 batch_end
            900.0,  # batch 4 elapsed check  (900 > 780 → TIMEOUT)
            900.0,  # overall_duration
        ])

        mock_time_mod = MagicMock()
        mock_time_mod.time = MagicMock(side_effect=lambda: next(time_values, 900.0))

        with patch.object(mod, "time", mock_time_mod), \
             patch.object(mod, "_get_session_factory") as mock_factory, \
             patch.object(mod, "JobLockCRUD") as mock_lock_crud_cls, \
             patch.object(mod, "JobExecutionCRUD") as mock_exec_crud_cls, \
             patch.object(mod, "HistoricDataRefreshService") as mock_refresh_cls, \
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

            mock_refresh = mock_refresh_cls.return_value
            mock_refresh.refresh_from_string = AsyncMock(return_value=mock_stats)

            result = await mod.main({})

        assert result["statusCode"] == 200
        # 4 batches should have been processed before timeout
        assert mock_refresh.refresh_from_string.await_count == 4

        # Remaining seasons (batches 4–7) should be stored in Redis
        mock_cache.set.assert_awaited_once()
        set_call = mock_cache.set.call_args
        remaining_stored = set_call[0][1]  # second positional arg = value
        expected_remaining = [2018, 2019, 2020, 2021, 2022, 2023, 2024, 2025]
        assert remaining_stored == expected_remaining
        assert set_call[1]["ttl"] == mod.CONTINUATION_TTL

        # Continuation marker should NOT be deleted (work remains)
        mock_cache.delete.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_continuation_cleared_after_full_completion(self):
        """After all batches complete (from continuation), the marker is cleared."""
        mod = _import_historic_refresh()

        mock_session = AsyncMock()
        mock_execution = MagicMock(id=1)

        # Small set: just 4 seasons → 2 batches
        remaining_seasons = [2024, 2025, 2010, 2011]
        mock_cache = _make_cache(get_return=remaining_seasons)

        mock_stats = {
            "seasons_processed": 2,
            "games_synced": 60,
            "tips_generated": 40,
            "errors": [],
        }

        with patch.object(mod, "_get_session_factory") as mock_factory, \
             patch.object(mod, "JobLockCRUD") as mock_lock_crud_cls, \
             patch.object(mod, "JobExecutionCRUD") as mock_exec_crud_cls, \
             patch.object(mod, "HistoricDataRefreshService") as mock_refresh_cls, \
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

            mock_refresh = mock_refresh_cls.return_value
            mock_refresh.refresh_from_string = AsyncMock(return_value=mock_stats)

            result = await mod.main({})

        assert result["statusCode"] == 200
        # 4 seasons / 2 = 2 batches
        assert mock_refresh.refresh_from_string.await_count == 2
        # delete should be called once to clear the continuation marker
        mock_cache.delete.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_default_processes_all_batches(self):
        """When no continuation marker exists, all 8 batches are processed from scratch."""
        mod = _import_historic_refresh()

        mock_session = AsyncMock()
        mock_execution = MagicMock(id=1)
        mock_cache = _make_cache(get_return=None)

        mock_stats = {
            "seasons_processed": 2,
            "games_synced": 80,
            "tips_generated": 50,
            "errors": [],
        }

        with patch.object(mod, "_get_session_factory") as mock_factory, \
             patch.object(mod, "JobLockCRUD") as mock_lock_crud_cls, \
             patch.object(mod, "JobExecutionCRUD") as mock_exec_crud_cls, \
             patch.object(mod, "HistoricDataRefreshService") as mock_refresh_cls, \
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

            mock_refresh = mock_refresh_cls.return_value
            mock_refresh.refresh_from_string = AsyncMock(return_value=mock_stats)

            result = await mod.main({})

        assert result["statusCode"] == 200
        # First constructor call should be with the first batch [2010, 2011]
        first_constructor_call = mock_refresh_cls.call_args_list[0]
        assert first_constructor_call.kwargs["seasons"] == [2010, 2011]
        # All 8 batches should be processed
        assert mock_refresh.refresh_from_string.await_count == 8
        # Continuation marker cleared
        mock_cache.delete.assert_awaited_once()
