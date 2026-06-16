"""Unit tests for ``packages.shared.services.historic_refresh`` service function.

The service function is the reusable core that both the FaaS handler
and the new ``app.cron.historic_refresh.HistoricRefreshJob`` invoke.
"""

from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock

import pytest

from packages.shared.services.historic_refresh import (
    ALL_SEASONS,
    BATCH_SIZE,
    MAX_RUNTIME_SECONDS,
    run_historic_refresh,
)


def _make_session() -> AsyncMock:
    session = AsyncMock()
    session.commit = AsyncMock()
    session.refresh = AsyncMock()
    return session


def _make_cache(get_return=None, set_return=True) -> MagicMock:
    cache = AsyncMock()
    cache.get = AsyncMock(return_value=get_return)
    cache.set = AsyncMock(return_value=set_return)
    cache.delete = AsyncMock(return_value=True)
    return cache


def _patch_refresh_service(monkeypatch, refresh_return: dict):
    service = MagicMock()
    service.refresh_from_string = AsyncMock(return_value=refresh_return)
    monkeypatch.setattr(
        "packages.shared.services.historic_refresh.HistoricDataRefreshService",
        lambda **kwargs: service,
    )
    return service


def _patch_progress(monkeypatch, in_progress=None):
    """Patch GenerationProgressCRUD.

    Args:
        in_progress: A list of in-progress records to return from
            ``get_in_progress_operations``.  ``None`` = no records.
    """
    records = in_progress if in_progress is not None else []

    upsert_active = AsyncMock()
    mark_completed = AsyncMock()
    get_in_progress_operations = AsyncMock(return_value=records)

    monkeypatch.setattr(
        "packages.shared.services.historic_refresh.GenerationProgressCRUD.upsert_active",
        upsert_active,
    )
    monkeypatch.setattr(
        "packages.shared.services.historic_refresh.GenerationProgressCRUD.mark_completed",
        mark_completed,
    )
    monkeypatch.setattr(
        "packages.shared.services.historic_refresh.GenerationProgressCRUD.get_in_progress_operations",
        get_in_progress_operations,
    )
    return {
        "upsert_active": upsert_active,
        "mark_completed": mark_completed,
        "get_in_progress_operations": get_in_progress_operations,
    }


def _patch_alerting(monkeypatch):
    timeout = AsyncMock()
    failure = AsyncMock()
    alerting = MagicMock()
    alerting.send_timeout_alert = timeout
    alerting.send_failure_alert = failure
    monkeypatch.setattr(
        "packages.shared.services.historic_refresh.AlertingService",
        lambda: alerting,
    )
    return alerting


def _patch_invalidate(monkeypatch):
    invalidate = AsyncMock(return_value=0)
    monkeypatch.setattr(
        "packages.shared.services.historic_refresh.invalidate_cache_pattern",
        invalidate,
    )
    return invalidate


class TestRunHistoricRefresh:
    @pytest.mark.asyncio
    async def test_happy_path_processes_all_batches(self, monkeypatch):
        session = _make_session()
        cache = _make_cache(get_return=None)
        _patch_progress(monkeypatch)
        alerting = _patch_alerting(monkeypatch)
        _patch_invalidate(monkeypatch)

        stats = {
            "seasons_processed": 2,
            "games_synced": 60,
            "tips_generated": 40,
            "errors": [],
        }
        refresh = _patch_refresh_service(monkeypatch, stats)

        # Simpler: just make time.time always return 0
        monkeypatch.setattr(
            "packages.shared.services.historic_refresh.time",
            MagicMock(time=lambda: 0.0),
        )

        result = await run_historic_refresh(session, cache=cache)

        # 16 seasons / BATCH_SIZE=2 = 8 batches
        assert refresh.refresh_from_string.await_count == 8
        assert result["status"] == "success"
        assert result["batches_processed"] == 8
        # Continuation marker cleared
        cache.delete.assert_awaited_once()
        # No timeout alert
        alerting.send_timeout_alert.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_resumes_from_redis_continuation(self, monkeypatch):
        session = _make_session()
        cache = _make_cache(get_return=[2022, 2023, 2024, 2025])
        _patch_progress(monkeypatch)
        _patch_alerting(monkeypatch)
        _patch_invalidate(monkeypatch)

        stats = {
            "seasons_processed": 2,
            "games_synced": 60,
            "tips_generated": 40,
            "errors": [],
        }
        refresh = _patch_refresh_service(monkeypatch, stats)
        monkeypatch.setattr(
            "packages.shared.services.historic_refresh.time",
            MagicMock(time=lambda: 0.0),
        )

        result = await run_historic_refresh(session, cache=cache)

        # 4 seasons / 2 = 2 batches
        assert refresh.refresh_from_string.await_count == 2
        # First batch should be [2022, 2023]
        first_call = refresh.refresh_from_string.call_args_list[0]
        assert first_call.kwargs["seasons_str"] == "2022,2023"
        # Continuation cleared
        cache.delete.assert_awaited_once()
        assert result["status"] == "success"

    @pytest.mark.asyncio
    async def test_resumes_from_db_progress_when_redis_empty(self, monkeypatch):
        session = _make_session()
        cache = _make_cache(get_return=None)
        # Simulate 4 seasons already done (so 12 remain)
        db_record = MagicMock()
        db_record.completed_items = 4
        db_record.id = 7
        _patch_progress(monkeypatch, in_progress=[db_record])
        _patch_alerting(monkeypatch)
        _patch_invalidate(monkeypatch)

        stats = {
            "seasons_processed": 2,
            "games_synced": 60,
            "tips_generated": 40,
            "errors": [],
        }
        refresh = _patch_refresh_service(monkeypatch, stats)
        monkeypatch.setattr(
            "packages.shared.services.historic_refresh.time",
            MagicMock(time=lambda: 0.0),
        )

        result = await run_historic_refresh(session, cache=cache)

        # 12 remaining / 2 = 6 batches
        assert refresh.refresh_from_string.await_count == 6
        # First batch: [2014, 2015]
        first_call = refresh.refresh_from_string.call_args_list[0]
        assert first_call.kwargs["seasons_str"] == "2014,2015"
        assert result["status"] == "success"

    @pytest.mark.asyncio
    async def test_time_budget_exhausted_stores_continuation(self, monkeypatch):
        session = _make_session()
        cache = _make_cache(get_return=None)
        _patch_progress(monkeypatch)
        alerting = _patch_alerting(monkeypatch)
        _patch_invalidate(monkeypatch)

        stats = {
            "seasons_processed": 2,
            "games_synced": 60,
            "tips_generated": 40,
            "errors": [],
        }
        refresh = _patch_refresh_service(monkeypatch, stats)

        # Simulate time progression: each batch takes 100s, so batch 8
        # (after 7 batches at 100s each = 700s) is below 780s, but
        # batch 8 elapsed check returns 900s > 780s.
        time_values = iter([
            0.0,    # overall_start
            100.0,  # batch 0 elapsed (100 < 780)
            100.0,  # batch 0 batch_start
            200.0,  # batch 0 batch_end
            200.0,  # batch 1 elapsed
            200.0,  # batch 1 batch_start
            300.0,  # batch 1 batch_end
            300.0,  # batch 2 elapsed
            300.0,  # batch 2 batch_start
            400.0,  # batch 2 batch_end
            400.0,  # batch 3 elapsed
            400.0,  # batch 3 batch_start
            500.0,  # batch 3 batch_end
            500.0,  # batch 4 elapsed
            500.0,  # batch 4 batch_start
            600.0,  # batch 4 batch_end
            600.0,  # batch 5 elapsed
            600.0,  # batch 5 batch_start
            700.0,  # batch 5 batch_end
            700.0,  # batch 6 elapsed
            700.0,  # batch 6 batch_start
            800.0,  # batch 6 batch_end
            900.0,  # batch 7 elapsed (> 780) → TIMEOUT
            900.0,  # overall_duration
        ])

        monkeypatch.setattr(
            "packages.shared.services.historic_refresh.time",
            MagicMock(time=lambda: next(time_values, 900.0)),
        )

        result = await run_historic_refresh(session, cache=cache)

        # 7 batches processed before timeout
        assert refresh.refresh_from_string.await_count == 7
        # Remaining seasons stored
        cache.set.assert_awaited_once()
        # Timeout alert sent
        alerting.send_timeout_alert.assert_awaited_once()
        # Continuation NOT cleared (work remains)
        cache.delete.assert_not_awaited()
        assert result["status"] == "success"
        assert result["timed_out"] is True
