"""Unit tests for ``app.cron.match_completion.MatchCompletionJob``."""

from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.cron.match_completion import MatchCompletionJob


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
        "app.cron.match_completion.run_match_completion", service_mock
    )
    return service_mock


class TestMatchCompletionJob:
    @pytest.mark.asyncio
    async def test_happy_path_writes_success(self, monkeypatch):
        session = AsyncMock()
        session.commit = AsyncMock()
        session.refresh = AsyncMock()

        exec_crud, lock_crud = _patch_base_crud(monkeypatch, lock_acquired=True)
        service = _patch_service(
            monkeypatch,
            status="success",
            message="Checked 5 games for completion; Marked 2 as complete",
            games_checked=5,
            games_completed=2,
            games_already_completed=1,
            games_not_ready=2,
            errors=0,
            elo_cache_updated=True,
        )

        job = MatchCompletionJob(_session_factory(session))
        result = await job.execute()

        assert result["status"] == "success"
        assert result["games_completed"] == 2
        # Service was called with a session
        service.assert_awaited_once()
        # Execution row finalised as completed
        update_call = exec_crud.update_execution.call_args
        assert update_call.kwargs["status"] == "completed"

    @pytest.mark.asyncio
    async def test_no_completed_games_still_success(self, monkeypatch):
        """Zero games completed → job is still a success, Elo not updated."""
        session = AsyncMock()
        session.commit = AsyncMock()
        session.refresh = AsyncMock()

        exec_crud, _ = _patch_base_crud(monkeypatch, lock_acquired=True)
        _patch_service(
            monkeypatch,
            status="success",
            message="No games to update",
            games_checked=3,
            games_completed=0,
            games_already_completed=1,
            games_not_ready=2,
            errors=0,
            elo_cache_updated=False,
        )

        job = MatchCompletionJob(_session_factory(session))
        result = await job.execute()

        assert result["status"] == "success"
        assert result["games_completed"] == 0
        update_call = exec_crud.update_execution.call_args
        assert update_call.kwargs["status"] == "completed"

    @pytest.mark.asyncio
    async def test_lock_held_skips(self, monkeypatch):
        session = AsyncMock()
        session.commit = AsyncMock()
        session.refresh = AsyncMock()
        _patch_base_crud(monkeypatch, lock_acquired=False)
        service = _patch_service(monkeypatch, status="success", message="x")

        job = MatchCompletionJob(_session_factory(session))
        result = await job.execute()

        assert result.get("skipped") is True
        service.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_service_failure_marks_failed(self, monkeypatch):
        session = AsyncMock()
        session.commit = AsyncMock()
        session.refresh = AsyncMock()

        exec_crud, lock_crud = _patch_base_crud(monkeypatch, lock_acquired=True)
        service_mock = AsyncMock(side_effect=RuntimeError("Squiggle timeout"))
        monkeypatch.setattr(
            "app.cron.match_completion.run_match_completion", service_mock
        )

        alerting = MagicMock()
        alerting.send_failure_alert = AsyncMock()

        job = MatchCompletionJob(_session_factory(session), alerting=alerting)
        with pytest.raises(RuntimeError):
            await job.execute()

        update_call = exec_crud.update_execution.call_args
        assert update_call.kwargs["status"] == "failed"
        alerting.send_failure_alert.assert_awaited_once()
        lock_crud.release_lock.assert_awaited_once()
