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

FCL-5: after the existing retrains, ``run()`` ALSO runs the per-league
pass (:func:`packages.shared.services.league_retrain.run_all_league_retrains`)
over every competition with completed-event history.  The outcome is
nested under a NEW ``"leagues"`` key — the existing top-level keys stay
EXACTLY as before — and a league-pass failure is logged + reported in
that nested dict, never failing the job.  The league pass is NOT gated
by ``boosted_retrain_enabled``: it owns the league boosted fit itself
and every league is first-class.
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
        """BT-1: one run trains BOTH models; linear keys stay top-level.

        FCL-5: the per-league pass also runs (stubbed here) and nests
        under ``"leagues"`` — the linear/boosted merge is unchanged.
        """
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
        leagues = {"competitions": 0, "results": {}}
        linear_service = AsyncMock(return_value=dict(linear))
        boosted_service = AsyncMock(return_value=dict(boosted))
        leagues_service = AsyncMock(return_value=dict(leagues))
        monkeypatch.setattr(
            "app.cron.model_retrain.run_model_retrain", linear_service
        )
        monkeypatch.setattr(
            "app.cron.model_retrain.run_boosted_retrain", boosted_service
        )
        monkeypatch.setattr(
            "app.cron.model_retrain.run_all_league_retrains", leagues_service
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
        leagues_service.assert_awaited_once()
        # Every service receives the session yielded by the factory.
        assert linear_service.call_args.args[0] is session
        assert boosted_service.call_args.args[0] is session
        assert leagues_service.call_args.args[0] is session
        # Linear keys stay at the top level EXACTLY as before (backward
        # compat for ops/alerting); the boosted and league summaries nest.
        assert result == {**linear, "boosted_tip": boosted, "leagues": leagues}
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

        async def leagues(svc_session):
            order.append("leagues")
            sessions_seen.append(svc_session)
            return {"competitions": 0, "results": {}}

        monkeypatch.setattr("app.cron.model_retrain.run_model_retrain", linear)
        monkeypatch.setattr(
            "app.cron.model_retrain.run_boosted_retrain", boosted
        )
        monkeypatch.setattr(
            "app.cron.model_retrain.run_all_league_retrains", leagues
        )
        from packages.shared import config as config_module

        monkeypatch.setattr(
            cron_model_retrain,
            "settings",
            config_module.Settings(boosted_retrain_enabled=True),
        )

        job = ModelRetrainJob(_session_factory(session))
        result = await job.run()

        # FCL-5: the league pass runs strictly AFTER the existing retrains.
        assert order == ["linear", "boosted", "leagues"]
        assert sessions_seen == [session, session, session]
        # Top level keeps the LINEAR outcome; boosted/league skips nest.
        assert result["status"] == "skipped"
        assert result["boosted_tip"]["status"] == "skipped"
        assert result["leagues"] == {"competitions": 0, "results": {}}

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
        leagues = {"competitions": 0, "results": {}}
        linear_service = AsyncMock(return_value=dict(linear))
        boosted_service = AsyncMock()
        leagues_service = AsyncMock(return_value=dict(leagues))
        monkeypatch.setattr(
            "app.cron.model_retrain.run_model_retrain", linear_service
        )
        monkeypatch.setattr(
            "app.cron.model_retrain.run_boosted_retrain", boosted_service
        )
        monkeypatch.setattr(
            "app.cron.model_retrain.run_all_league_retrains", leagues_service
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
        leagues_service.assert_awaited_once()  # FCL-5: not gated by the flag
        assert result == {**linear, "leagues": leagues}
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
        leagues_service = AsyncMock(return_value={"competitions": 0, "results": {}})
        monkeypatch.setattr(
            "app.cron.model_retrain.run_model_retrain", linear_service
        )
        monkeypatch.setattr(
            "app.cron.model_retrain.run_boosted_retrain", boosted_service
        )
        monkeypatch.setattr(
            "app.cron.model_retrain.run_all_league_retrains", leagues_service
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
        leagues_service.assert_awaited_once()
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
        # The league pass still ran and nested its (stubbed) outcome.
        assert result["leagues"] == {"competitions": 0, "results": {}}


class TestModelRetrainJobLeaguePass:
    """FCL-5: the per-league retrain pass runs AFTER the existing retrains.

    The outcome nests under a NEW ``"leagues"`` key; every existing
    top-level key keeps its exact meaning for ``result_summary``
    consumers, and a league-pass failure never fails the job.
    """

    @staticmethod
    def _patch_league_service(monkeypatch, league_service) -> None:
        monkeypatch.setattr(
            "app.cron.model_retrain.run_all_league_retrains", league_service
        )

    @pytest.mark.asyncio
    async def test_run_runs_league_pass_last_and_nests_outcome(self, monkeypatch):
        """Order is linear -> boosted -> leagues; the leagues outcome nests
        under ``"leagues"`` and every existing top-level key is unchanged."""
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
        leagues = {
            "competitions": 2,
            "results": {
                "11": {"status": "trained", "competition_id": 11},
                "12": {"status": "skipped", "reason": "insufficient_training_rows"},
            },
        }
        order: list[str] = []
        sessions_seen: list[MagicMock] = []

        async def linear_svc(svc_session):
            order.append("linear")
            sessions_seen.append(svc_session)
            return dict(linear)

        async def boosted_svc(svc_session):
            order.append("boosted")
            sessions_seen.append(svc_session)
            return dict(boosted)

        async def leagues_svc(svc_session):
            order.append("leagues")
            sessions_seen.append(svc_session)
            return leagues

        monkeypatch.setattr("app.cron.model_retrain.run_model_retrain", linear_svc)
        monkeypatch.setattr(
            "app.cron.model_retrain.run_boosted_retrain", boosted_svc
        )
        self._patch_league_service(monkeypatch, leagues_svc)
        from packages.shared import config as config_module

        monkeypatch.setattr(
            cron_model_retrain,
            "settings",
            config_module.Settings(boosted_retrain_enabled=True),
        )

        job = ModelRetrainJob(_session_factory(session))
        result = await job.run()

        assert order == ["linear", "boosted", "leagues"]
        assert sessions_seen == [session, session, session]  # one session
        # Existing top-level keys keep their EXACT meaning; the league
        # outcome is nested under the new "leagues" key.
        assert set(result) == set(linear) | {"boosted_tip", "leagues"}
        assert result == {**linear, "boosted_tip": boosted, "leagues": leagues}

    @pytest.mark.asyncio
    async def test_run_league_pass_error_does_not_fail_job(self, monkeypatch):
        """A league-pass failure is logged + reported in the nested dict;
        the linear/boosted summary stays intact and no exception escapes."""
        session = MagicMock()
        linear = {
            "status": "trained",
            "model_name": "weighted_tip",
            "version": 2,
            "training_rows": 500,
        }
        leagues = {"competitions": 3, "results": {}}
        linear_service = AsyncMock(return_value=dict(linear))
        boosted_service = AsyncMock(return_value={"status": "trained"})
        leagues_service = AsyncMock(return_value=dict(leagues))
        monkeypatch.setattr(
            "app.cron.model_retrain.run_model_retrain", linear_service
        )
        monkeypatch.setattr(
            "app.cron.model_retrain.run_boosted_retrain", boosted_service
        )
        self._patch_league_service(monkeypatch, leagues_service)
        from packages.shared import config as config_module

        monkeypatch.setattr(
            cron_model_retrain,
            "settings",
            config_module.Settings(boosted_retrain_enabled=True),
        )

        job = ModelRetrainJob(_session_factory(session))
        result = await job.run()

        assert result["leagues"] == leagues
        assert result["status"] == "trained"
        assert result["boosted_tip"] == {"status": "trained"}

        # Now the pass itself explodes — the job must STILL succeed.
        exploding = AsyncMock(side_effect=RuntimeError("league pass exploded"))
        self._patch_league_service(monkeypatch, exploding)
        result2 = await ModelRetrainJob(_session_factory(session)).run()
        assert result2["leagues"] == {
            "status": "error",
            "reason": "league pass exploded",
        }
        # Existing keys still untouched.
        assert result2["status"] == "trained"
        assert result2["model_name"] == "weighted_tip"
        assert result2["boosted_tip"] == {"status": "trained"}

    @pytest.mark.asyncio
    async def test_run_league_pass_not_gated_by_boosted_flag(self, monkeypatch):
        """``boosted_retrain_enabled=False`` gates ONLY the AFL boosted step —
        the league pass (which owns its own league boosted fit) still runs."""
        session = MagicMock()
        linear = {
            "status": "trained",
            "model_name": "weighted_tip",
            "version": 7,
            "training_rows": 1234,
        }
        linear_service = AsyncMock(return_value=dict(linear))
        boosted_service = AsyncMock()
        leagues_service = AsyncMock(
            return_value={"competitions": 0, "results": {}}
        )
        monkeypatch.setattr(
            "app.cron.model_retrain.run_model_retrain", linear_service
        )
        monkeypatch.setattr(
            "app.cron.model_retrain.run_boosted_retrain", boosted_service
        )
        self._patch_league_service(monkeypatch, leagues_service)
        from packages.shared import config as config_module

        monkeypatch.setattr(
            cron_model_retrain,
            "settings",
            config_module.Settings(boosted_retrain_enabled=False),
        )

        job = ModelRetrainJob(_session_factory(session))
        result = await job.run()

        boosted_service.assert_not_awaited()
        leagues_service.assert_awaited_once()
        assert "boosted_tip" not in result
        assert result["leagues"] == {"competitions": 0, "results": {}}
        assert result == {**linear, "leagues": {"competitions": 0, "results": {}}}


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
