"""Unit tests for ``app.cron.model_retrain.ModelRetrainJob``.

Mirrors ``tests/unit/test_app_cron_historic_refresh.py``: pins the job's
class attributes and asserts ``run()`` opens a session via the session
factory and delegates to the retrain services.

BT-1 (decision 4): ONE weekly job now trains BOTH models.  The linear
``weighted_tip`` retrain keeps its exact top-level summary contract
(backward compat for ops/alerting reading ``status``/``model_name``) and
the boosted ``boosted_tip`` result is nested under ``"boosted_tip"``.
Two documented contract choices:

- ``settings.boosted_retrain_enabled=False`` → the summary has NO
  ``"boosted_tip"`` key at all and the boosted service is never called
  (omitted, not a "disabled" marker — a ``boosted_tip`` key therefore
  always carries a real boosted outcome).
- A boosted failure NEVER fails the job when the linear retrain succeeded
  (mirroring the orchestrator's never-crash philosophy): the top-level
  keys stay exactly the linear result — the partial failure is signalled
  by the nested ``{"status": "error", "reason": ...}`` dict, so the
  historical meaning of the top-level ``status`` (the LINEAR model's
  outcome) is unchanged for ``JobExecution.result_summary`` consumers.
"""

from __future__ import annotations

import subprocess
import sys
import textwrap
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.cron import model_retrain as cron_model_retrain
from app.cron.model_retrain import ModelRetrainJob

BACKEND_DIR = Path(__file__).resolve().parents[2]  # backend/

# Heavy ML libraries that must NOT load at import of the always-on path
# (same guard list as tests/unit/test_lazy_sklearn.py).
_HEAVY_LIBS = ("sklearn", "scipy", "numpy", "xgboost", "shap")


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
    async def test_run_calls_both_services_once_and_merges_summaries(
        self, monkeypatch
    ):
        """BT-1: one run trains BOTH models; linear keys stay top-level."""
        session = MagicMock()
        linear = {
            "status": "trained",
            "model_name": "weighted_tip",
            "version": 1,
            "training_rows": 42,
        }
        boosted = {
            "status": "trained",
            "model_name": "boosted_tip",
            "version": 3,
            "training_rows": 42,
        }
        linear_service = AsyncMock(return_value=dict(linear))
        boosted_service = AsyncMock(return_value=dict(boosted))
        monkeypatch.setattr(
            "app.cron.model_retrain.run_model_retrain", linear_service
        )
        monkeypatch.setattr(
            "app.cron.model_retrain.run_boosted_retrain", boosted_service
        )
        # Mirror test_app_scheduler.py: fresh Settings instance, module attr.
        from packages.shared import config as config_module

        monkeypatch.setattr(
            cron_model_retrain,
            "settings",
            config_module.Settings(boosted_retrain_enabled=True),
        )

        job = ModelRetrainJob(_session_factory(session))
        result = await job.run()

        linear_service.assert_awaited_once()
        boosted_service.assert_awaited_once()
        # Both services receive the session yielded by the factory.
        assert linear_service.call_args.args[0] is session
        assert boosted_service.call_args.args[0] is session
        # Linear keys stay at the top level EXACTLY as before (backward
        # compat for ops/alerting); the boosted summary is nested.
        assert result == {**linear, "boosted_tip": boosted}
        assert result["status"] == "trained"
        assert result["model_name"] == "weighted_tip"
        assert result["boosted_tip"]["model_name"] == "boosted_tip"

    @pytest.mark.asyncio
    async def test_run_linear_then_boosted_same_session_in_order(
        self, monkeypatch
    ):
        """Boosted runs strictly AFTER the linear retrain, same session."""
        session = MagicMock()
        order: list[str] = []
        sessions_seen: list[MagicMock] = []

        async def linear(svc_session):
            order.append("linear")
            sessions_seen.append(svc_session)
            return {
                "status": "skipped",
                "reason": "insufficient_training_rows",
                "rows": 5,
                "min_required": 100,
            }

        async def boosted(svc_session):
            order.append("boosted")
            sessions_seen.append(svc_session)
            return {
                "status": "skipped",
                "reason": "insufficient_training_rows",
                "rows": 5,
                "min_required": 100,
            }

        monkeypatch.setattr("app.cron.model_retrain.run_model_retrain", linear)
        monkeypatch.setattr(
            "app.cron.model_retrain.run_boosted_retrain", boosted
        )
        from packages.shared import config as config_module

        monkeypatch.setattr(
            cron_model_retrain,
            "settings",
            config_module.Settings(boosted_retrain_enabled=True),
        )

        job = ModelRetrainJob(_session_factory(session))
        result = await job.run()

        assert order == ["linear", "boosted"]
        assert sessions_seen == [session, session]
        # Top level keeps the LINEAR outcome; boosted skip nests verbatim.
        assert result["status"] == "skipped"
        assert result["boosted_tip"]["status"] == "skipped"

    @pytest.mark.asyncio
    async def test_run_boosted_disabled_gates_boosted_step(self, monkeypatch):
        """Enabled=False → verbatim linear dict, boosted service untouched.

        Documented choice: the ``"boosted_tip"`` key is OMITTED (not a
        "disabled" marker) and ``run_boosted_retrain`` is never called.
        """
        session = MagicMock()
        linear = {
            "status": "trained",
            "model_name": "weighted_tip",
            "version": 7,
            "training_rows": 1234,
        }
        linear_service = AsyncMock(return_value=dict(linear))
        boosted_service = AsyncMock()
        monkeypatch.setattr(
            "app.cron.model_retrain.run_model_retrain", linear_service
        )
        monkeypatch.setattr(
            "app.cron.model_retrain.run_boosted_retrain", boosted_service
        )
        from packages.shared import config as config_module

        monkeypatch.setattr(
            cron_model_retrain,
            "settings",
            config_module.Settings(boosted_retrain_enabled=False),
        )

        job = ModelRetrainJob(_session_factory(session))
        result = await job.run()

        linear_service.assert_awaited_once()
        boosted_service.assert_not_awaited()
        assert result == linear
        assert "boosted_tip" not in result

    @pytest.mark.asyncio
    async def test_run_boosted_error_does_not_fail_job(self, monkeypatch):
        """Boosted failure → logged, nested error, job still succeeds.

        The top-level summary remains exactly the LINEAR result (partial
        success is signalled by the nested ``boosted_tip.status``), and no
        exception escapes ``run()`` (so BaseJob does not retry/alert).
        """
        session = MagicMock()
        linear = {
            "status": "trained",
            "model_name": "weighted_tip",
            "version": 2,
            "training_rows": 500,
        }
        linear_service = AsyncMock(return_value=dict(linear))
        boosted_service = AsyncMock(side_effect=RuntimeError("xgb exploded"))
        monkeypatch.setattr(
            "app.cron.model_retrain.run_model_retrain", linear_service
        )
        monkeypatch.setattr(
            "app.cron.model_retrain.run_boosted_retrain", boosted_service
        )
        from packages.shared import config as config_module

        monkeypatch.setattr(
            cron_model_retrain,
            "settings",
            config_module.Settings(boosted_retrain_enabled=True),
        )

        job = ModelRetrainJob(_session_factory(session))
        # Does NOT raise.
        result = await job.run()

        linear_service.assert_awaited_once()
        boosted_service.assert_awaited_once()
        # Linear result fully intact at the top level.
        assert result["status"] == "trained"
        assert result["model_name"] == "weighted_tip"
        assert result["version"] == 2
        assert result["training_rows"] == 500
        # Partial failure reported in the nested dict only.
        assert result["boosted_tip"] == {
            "status": "error",
            "reason": "xgb exploded",
        }


def test_importing_cron_job_does_not_load_heavy_ml_libs():
    """BT-1: the cron module now also imports the boosted-retrain service.

    ``app.core.scheduler`` imports ``ModelRetrainJob`` (and therefore BOTH
    retrain services) at module top, so importing the cron job must not
    load numpy/xgboost/shap/sklearn — both services keep their heavy
    imports inside function bodies, past the skip gates.  Mirrors
    ``tests/unit/test_lazy_sklearn.py`` (isolated subprocess, same guard
    list); pinned here because this module is the unit under test.
    """
    code = textwrap.dedent(
        f"""
        import sys
        from app.cron.model_retrain import ModelRetrainJob
        offenders = sorted(lib for lib in {_HEAVY_LIBS!r} if lib in sys.modules)
        assert not offenders, (
            "Importing app.cron.model_retrain loaded heavy ML libs at "
            f"module load time: {{offenders}}"
        )
        """
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        cwd=str(BACKEND_DIR),
        timeout=120,
    )
    assert result.returncode == 0, (
        "Isolated import check failed — a heavy ML lib was loaded at import "
        "time (or the import itself errored):\n"
        f"--- code ---\n{code}"
        f"--- stdout ---\n{result.stdout}"
        f"--- stderr ---\n{result.stderr}"
    )
