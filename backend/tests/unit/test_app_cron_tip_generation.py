"""Unit tests for ``app.cron.tip_generation.TipGenerationJob``."""

from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.cron.tip_generation import TipGenerationJob


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
        "app.cron.tip_generation.run_tip_generation", service_mock
    )
    return service_mock


class TestTipGenerationJob:
    @pytest.mark.asyncio
    async def test_happy_path_writes_success(self, monkeypatch):
        session = AsyncMock()
        session.commit = AsyncMock()
        session.refresh = AsyncMock()

        exec_crud, _ = _patch_base_crud(monkeypatch, lock_acquired=True)
        service = _patch_service(
            monkeypatch,
            status="success",
            message="Generated tips; Processed 9 games",
            games_processed=9,
            tips_created=27,
            tips_skipped=0,
            tips_updated=0,
            model_predictions_created=36,
            errors=0,
            explanations_generated=27,
        )

        job = TipGenerationJob(_session_factory(session))
        result = await job.execute()

        assert result["status"] == "success"
        assert result["tips_created"] == 27
        service.assert_awaited_once()
        # Execution row finalised
        update_call = exec_crud.update_execution.call_args
        assert update_call.kwargs["status"] == "completed"

    @pytest.mark.asyncio
    async def test_no_upcoming_round_is_success(self, monkeypatch):
        """No upcoming round is still a successful run."""
        session = AsyncMock()
        session.commit = AsyncMock()
        session.refresh = AsyncMock()

        exec_crud, _ = _patch_base_crud(monkeypatch, lock_acquired=True)
        _patch_service(
            monkeypatch,
            status="success",
            message="No upcoming rounds found that need tips",
            games_processed=0,
            tips_created=0,
            tips_skipped=0,
            tips_updated=0,
            model_predictions_created=0,
            errors=0,
            explanations_generated=0,
        )

        job = TipGenerationJob(_session_factory(session))
        result = await job.execute()

        assert result["status"] == "success"
        update_call = exec_crud.update_execution.call_args
        assert update_call.kwargs["status"] == "completed"

    @pytest.mark.asyncio
    async def test_lock_held_skips(self, monkeypatch):
        session = AsyncMock()
        session.commit = AsyncMock()
        session.refresh = AsyncMock()
        _patch_base_crud(monkeypatch, lock_acquired=False)
        service = _patch_service(monkeypatch, status="success", message="x")

        job = TipGenerationJob(_session_factory(session))
        result = await job.execute()

        assert result.get("skipped") is True
        service.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_service_failure_marks_failed(self, monkeypatch):
        session = AsyncMock()
        session.commit = AsyncMock()
        session.refresh = AsyncMock()

        exec_crud, lock_crud = _patch_base_crud(monkeypatch, lock_acquired=True)
        service_mock = AsyncMock(side_effect=RuntimeError("OpenRouter 500"))
        monkeypatch.setattr(
            "app.cron.tip_generation.run_tip_generation", service_mock
        )

        alerting = MagicMock()
        alerting.send_failure_alert = AsyncMock()

        job = TipGenerationJob(_session_factory(session), alerting=alerting)
        with pytest.raises(RuntimeError):
            await job.execute()

        update_call = exec_crud.update_execution.call_args
        assert update_call.kwargs["status"] == "failed"
        alerting.send_failure_alert.assert_awaited_once()
        lock_crud.release_lock.assert_awaited_once()
