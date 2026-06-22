"""Unit tests for ``app.cron.model_retrain.ModelRetrainJob``.

Mirrors ``tests/unit/test_app_cron_historic_refresh.py``: pins the job's class
attributes and asserts ``run()`` opens a session via the session factory and
delegates to ``run_model_retrain`` exactly once, returning its summary dict.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.cron.model_retrain import ModelRetrainJob


@asynccontextmanager
async def _session_ctx(session):
    yield session


def _session_factory(session):
    return lambda: _session_ctx(session)


class TestModelRetrainJob:
    def test_class_attributes(self):
        assert ModelRetrainJob.name == "model-retrain"
        assert ModelRetrainJob.timeout_seconds == 900
        # Mirror the weekly historic-refresh job's retry guard rails.
        assert ModelRetrainJob.max_retries == 3
        assert ModelRetrainJob.backoff_multiplier == 2.0
        assert ModelRetrainJob.initial_delay == 10.0
        assert ModelRetrainJob.jitter == 0.1

    @pytest.mark.asyncio
    async def test_run_calls_service_once_and_returns_its_dict(self, monkeypatch):
        session = MagicMock()
        summary = {
            "status": "trained",
            "model_name": "weighted_tip",
            "version": 1,
            "training_rows": 42,
        }
        service = AsyncMock(return_value=summary)
        monkeypatch.setattr("app.cron.model_retrain.run_model_retrain", service)

        job = ModelRetrainJob(_session_factory(session))
        result = await job.run()

        # Called exactly once, with the session yielded by the factory.
        service.assert_awaited_once()
        assert service.call_args.args[0] is session
        assert result == summary

    @pytest.mark.asyncio
    async def test_run_propagates_skip_result(self, monkeypatch):
        """A 'skipped' (insufficient data) result still flows through unchanged."""
        session = MagicMock()
        summary = {
            "status": "skipped",
            "reason": "insufficient_training_rows",
            "rows": 5,
            "min_required": 20,
        }
        service = AsyncMock(return_value=summary)
        monkeypatch.setattr("app.cron.model_retrain.run_model_retrain", service)

        job = ModelRetrainJob(_session_factory(session))
        result = await job.run()

        assert result == summary
        service.assert_awaited_once()
