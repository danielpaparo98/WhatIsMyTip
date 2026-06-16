"""In-process :class:`BaseJob` for the historic data refresh.

Wraps :func:`packages.shared.services.historic_refresh.run_historic_refresh`
with the :class:`app.cron.base.BaseJob` machinery.  The resumable
batch loop and Redis continuation marker live in the service.
"""

from __future__ import annotations

from app.cron.base import BaseJob
from packages.shared.cache import RedisCache
from packages.shared.services.historic_refresh import run_historic_refresh


class HistoricRefreshJob(BaseJob):
    """Cron job that refreshes historical games and tips (2010-2025).

    Runs weekly.  The service handles batch processing with a hard
    time budget, Redis continuation, and DB fallback.
    """

    name = "historic-refresh"
    # The service enforces its own ``MAX_RUNTIME_SECONDS`` of 780s
    # (13 minutes), well below the 15-minute DO Functions hard limit.
    # We give the BaseJob timeout 15 minutes to match.
    timeout_seconds = 900
    max_retries = 3
    backoff_multiplier = 2.0
    initial_delay = 10.0
    jitter = 0.1

    def __init__(self, session_factory, alerting=None):
        super().__init__(session_factory, alerting)
        # Cache instance is created on demand by the service but we
        # pre-build it here so it uses the shared pool.
        self._cache = RedisCache()

    async def run(self) -> dict:
        """Invoke the historic-refresh service within the active session."""
        async with self._session_factory() as session:
            return await run_historic_refresh(session, cache=self._cache)
