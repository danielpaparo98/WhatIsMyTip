"""In-process :class:`BaseJob` for the weekly model retrains.

Wraps :func:`packages.shared.services.model_retrain.run_model_retrain`
and — BT-1 decision 4 — :func:`packages.shared.services.boosted_retrain.
run_boosted_retrain` in the SAME weekly slot, with the
:class:`app.cron.base.BaseJob` machinery (locking, retry, timeout,
alerting, execution-row bookkeeping).  There is deliberately NO separate
boosted cron entry: one Monday 05:00 AWST run (``model_retrain_cron``)
trains BOTH models, gated by ``settings.boosted_retrain_enabled``.

Each ``run()`` first retrains the linear ``weighted_tip`` model, then the
``boosted_tip`` XGBoost model.  The returned summary keeps the linear
dict's keys at the top level EXACTLY as before (ops/alerting read
``status``/``model_name`` with the weighted-tip meaning — the value is
stored verbatim in ``JobExecution.result_summary``) and nests the boosted
outcome under ``"boosted_tip"``.  A boosted failure is logged and
reported in that nested dict; it never fails the job when the linear
retrain succeeded (mirroring the orchestrator's never-crash philosophy) —
only a LINEAR retrain failure propagates to :meth:`BaseJob.execute`'s
retry/alert handling, unchanged.

Mirrors :class:`app.cron.historic_refresh.HistoricRefreshJob` — another
weekly long-running job — so the timeout/retry guard rails match.
"""

from __future__ import annotations

from app.cron.base import BaseJob
from packages.shared.config import settings
from packages.shared.logger import get_logger
from packages.shared.services.boosted_retrain import run_boosted_retrain
from packages.shared.services.model_retrain import run_model_retrain

logger = get_logger(__name__)


class ModelRetrainJob(BaseJob):
    """Cron job that retrains BOTH prediction models weekly (BT-1 decision 4).

    Runs weekly (Mon 05:00 AWST by default — see
    ``settings.model_retrain_cron``).  Each run gathers the historical
    training rows once per model, fits the linear regression and the
    gradient-boosted regression, and atomically promotes both new active
    versions.  The boosted step is skipped entirely when
    ``settings.boosted_retrain_enabled`` is off (linear-only run).
    """

    name = "model-retrain"
    # Mirror the weekly historic-refresh job's guard rails.
    timeout_seconds = 900
    max_retries = 3
    backoff_multiplier = 2.0
    initial_delay = 10.0
    jitter = 0.1

    async def run(self) -> dict:
        """Invoke both retrain services within the active session.

        Returns:
            The linear retrain's summary dict with its keys untouched at
            the top level, plus ``"boosted_tip": <boosted summary>`` when
            the boosted step ran and ``settings.boosted_retrain_enabled``
            is on.  If the boosted fit raises, the nested value is
            ``{"status": "error", "reason": str(exc)}`` and the job still
            succeeds — a partial-success run.  Only a LINEAR retrain
            failure raises (existing retry/alert behaviour, unchanged).
        """
        async with self._session_factory() as session:
            summary = await run_model_retrain(session)

            if settings.boosted_retrain_enabled:
                try:
                    summary["boosted_tip"] = await run_boosted_retrain(session)
                except Exception as exc:  # noqa: BLE001 — boosted never crashes the job (BT-1)
                    logger.exception(
                        "boosted retrain failed; keeping linear result: %r", exc
                    )
                    summary["boosted_tip"] = {
                        "status": "error",
                        "reason": str(exc),
                    }

            return summary
