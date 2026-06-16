"""Unit tests for ``app.cron.daily_sync.DailySyncJob``."""

from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.cron.daily_sync import DailySyncJob


@asynccontextmanager
async def _session_ctx(session):
    yield session


def _session_factory(session):
    return lambda: _session_ctx(session)


def _patch_base_crud(monkeypatch, *, lock_acquired=True, execution_id=99):
    mock_execution = MagicMock(id=execution_id)
    mock_execution_crud = MagicMock()
    mock_execution_crud.create_execution = AsyncMock(return_value=mock_execution)
    mock_execution_crud.update_execution = AsyncMock()

    mock_lock_crud = MagicMock()
    if lock_acquired:
        mock_lock = MagicMock()
        mock_lock_crud.acquire_lock = AsyncMock(return_value=mock_lock)
    else:
        mock_lock_crud.acquire_lock = AsyncMock(return_value=None)
    mock_lock_crud.release_lock = AsyncMock()

    monkeypatch.setattr(
        "app.cron.base.JobExecutionCRUD", lambda s: mock_execution_crud
    )
    monkeypatch.setattr(
        "app.cron.base.JobLockCRUD", lambda s: mock_lock_crud
    )
    return mock_execution_crud, mock_lock_crud


def _patch_service(monkeypatch, **return_value):
    """Patch the run_daily_sync service function with a configurable mock."""
    service_mock = AsyncMock(return_value=return_value)
    monkeypatch.setattr(
        "app.cron.daily_sync.run_daily_sync", service_mock
    )
    return service_mock


class TestDailySyncJob:
    @pytest.mark.asyncio
    async def test_happy_path_writes_success(self, monkeypatch):
        session = AsyncMock()
        session.commit = AsyncMock()
        session.refresh = AsyncMock()

        exec_crud, lock_crud = _patch_base_crud(monkeypatch, lock_acquired=True)
        service = _patch_service(
            monkeypatch,
            status="success",
            message="Synced 9 games",
            total_games=9,
            games_created=2,
            games_updated=5,
            games_skipped=2,
            errors=0,
        )

        job = DailySyncJob(_session_factory(session))
        result = await job.execute()

        assert result["status"] == "success"
        assert result["total_games"] == 9
        # Service was called with a session
        service.assert_awaited_once()
        call_args = service.call_args
        assert call_args.args[0] is session
        # Execution row written as completed
        update_call = exec_crud.update_execution.call_args
        assert update_call.kwargs["status"] == "completed"

    @pytest.mark.asyncio
    async def test_off_season_skipped_still_writes_success(self, monkeypatch):
        """Off-season skip is a no-op success; the JobExecution row is still 'completed'."""
        session = AsyncMock()
        session.commit = AsyncMock()
        session.refresh = AsyncMock()

        exec_crud, lock_crud = _patch_base_crud(monkeypatch, lock_acquired=True)
        _patch_service(
            monkeypatch,
            status="skipped",
            message="off-season reduced frequency",
            total_games=0,
            games_created=0,
            games_updated=0,
            games_skipped=0,
            errors=0,
        )

        job = DailySyncJob(_session_factory(session))
        result = await job.execute()

        # Service reported skipped, but the job is still a success
        assert result["status"] == "skipped"
        update_call = exec_crud.update_execution.call_args
        assert update_call.kwargs["status"] == "completed"

    @pytest.mark.asyncio
    async def test_lock_held_skips(self, monkeypatch):
        session = AsyncMock()
        session.commit = AsyncMock()
        session.refresh = AsyncMock()
        _patch_base_crud(monkeypatch, lock_acquired=False)
        service = _patch_service(monkeypatch, status="success", message="x")

        job = DailySyncJob(_session_factory(session))
        result = await job.execute()

        assert result.get("skipped") is True
        service.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_service_failure_marks_failed(self, monkeypatch):
        session = AsyncMock()
        session.commit = AsyncMock()
        session.refresh = AsyncMock()

        exec_crud, lock_crud = _patch_base_crud(monkeypatch, lock_acquired=True)
        service_mock = AsyncMock(side_effect=RuntimeError("Squiggle down"))
        monkeypatch.setattr("app.cron.daily_sync.run_daily_sync", service_mock)

        alerting = MagicMock()
        alerting.send_failure_alert = AsyncMock()

        job = DailySyncJob(_session_factory(session), alerting=alerting)
        with pytest.raises(RuntimeError):
            await job.execute()

        # Execution row finalised as failed
        update_call = exec_crud.update_execution.call_args
        assert update_call.kwargs["status"] == "failed"
        # Alert sent
        alerting.send_failure_alert.assert_awaited_once()
        # Lock still released
        lock_crud.release_lock.assert_awaited_once()
