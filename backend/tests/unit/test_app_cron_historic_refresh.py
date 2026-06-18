"""Unit tests for ``app.cron.historic_refresh.HistoricRefreshJob``."""

from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.cron.historic_refresh import HistoricRefreshJob


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
    service_mock = AsyncMock(return_value=return_value)
    monkeypatch.setattr(
        "app.cron.historic_refresh.run_historic_refresh", service_mock
    )
    return service_mock


class TestHistoricRefreshJob:
    @pytest.mark.asyncio
    async def test_happy_path_writes_success(self, monkeypatch):
        session = AsyncMock()
        session.commit = AsyncMock()
        session.refresh = AsyncMock()

        exec_crud, _ = _patch_base_crud(monkeypatch, lock_acquired=True)
        service = _patch_service(
            monkeypatch,
            status="success",
            message="Processed 16 seasons across 8 batch(es)",
            batches_processed=8,
            total_seasons_processed=16,
            total_games_synced=480,
            total_tips_generated=320,
            total_errors=0,
            timed_out=False,
            duration_seconds=60,
        )

        job = HistoricRefreshJob(_session_factory(session))
        result = await job.execute()

        assert result["status"] == "success"
        assert result["batches_processed"] == 8
        service.assert_awaited_once()
        update_call = exec_crud.update_execution.call_args
        assert update_call.kwargs["status"] == "completed"

    @pytest.mark.asyncio
    async def test_timed_out_still_writes_success(self, monkeypatch):
        """A timed-out run is still a success — work was stored for resumption."""
        session = AsyncMock()
        session.commit = AsyncMock()
        session.refresh = AsyncMock()

        exec_crud, _ = _patch_base_crud(monkeypatch, lock_acquired=True)
        _patch_service(
            monkeypatch,
            status="success",
            message="Processed 7 seasons; Timeout: continuation marker stored",
            batches_processed=7,
            total_seasons_processed=14,
            total_games_synced=420,
            total_tips_generated=280,
            total_errors=0,
            timed_out=True,
            duration_seconds=780,
        )

        job = HistoricRefreshJob(_session_factory(session))
        result = await job.execute()

        assert result["status"] == "success"
        assert result["timed_out"] is True
        update_call = exec_crud.update_execution.call_args
        assert update_call.kwargs["status"] == "completed"

    @pytest.mark.asyncio
    async def test_lock_held_skips(self, monkeypatch):
        session = AsyncMock()
        session.commit = AsyncMock()
        session.refresh = AsyncMock()
        _patch_base_crud(monkeypatch, lock_acquired=False)
        service = _patch_service(monkeypatch, status="success", message="x")

        job = HistoricRefreshJob(_session_factory(session))
        result = await job.execute()

        assert result.get("skipped") is True
        service.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_service_failure_marks_failed(self, monkeypatch):
        session = AsyncMock()
        session.commit = AsyncMock()
        session.refresh = AsyncMock()

        exec_crud, lock_crud = _patch_base_crud(monkeypatch, lock_acquired=True)
        service_mock = AsyncMock(side_effect=RuntimeError("DB down"))
        monkeypatch.setattr(
            "app.cron.historic_refresh.run_historic_refresh", service_mock
        )

        alerting = MagicMock()
        alerting.send_failure_alert = AsyncMock()

        job = HistoricRefreshJob(_session_factory(session), alerting=alerting)
        with pytest.raises(RuntimeError):
            await job.execute()

        update_call = exec_crud.update_execution.call_args
        assert update_call.kwargs["status"] == "failed"
        alerting.send_failure_alert.assert_awaited_once()
        lock_crud.release_lock.assert_awaited_once()
