"""In-process :class:`BaseJob` for the weekly ``weighted_tip`` model retrain.

Wraps :func:`packages.shared.services.model_retrain.run_model_retrain` with
the :class:`app.cron.base.BaseJob` machinery (locking, retry, timeout,
alerting, execution-row bookkeeping).  The service gathers the historical
training rows, fits the scikit-learn ``LinearRegression``, and persists a new
active model version.

Mirrors :class:`app.cron.historic_refresh.HistoricRefreshJob` — another weekly
long-running job — so the timeout/retry guard rails match.
"""

from __future__ import annotations

from app.cron.base import BaseJob
from packages.shared.services.model_retrain import run_model_retrain


class ModelRetrainJob(BaseJob):
    """Cron job that retrains the ``weighted_tip`` model weekly.

    Runs weekly (Mon 05:00 AWST by default — see
    ``settings.model_retrain_cron``).  The service handles gathering rows,
    fitting the regression, and atomically promoting the new active version.
    """

    name = "model-retrain"
    # Mirror the weekly historic-refresh job's guard rails.
    timeout_seconds = 900
    max_retries = 3
    backoff_multiplier = 2.0
    initial_delay = 10.0
    jitter = 0.1

    async def run(self) -> dict:
        """Invoke the retrain service within the active session."""
        async with self._session_factory() as session:
            return await run_model_retrain(session)
