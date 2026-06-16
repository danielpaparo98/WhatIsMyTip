"""In-process :class:`BaseJob` for the daily game sync.

Wraps :func:`packages.shared.services.daily_sync.run_daily_sync` with
the :class:`app.cron.base.BaseJob` machinery (lock, retry, alerting,
execution-row tracking, timeout).
"""

from __future__ import annotations

from app.cron.base import BaseJob
from packages.shared.services.daily_sync import run_daily_sync


class DailySyncJob(BaseJob):
    """Cron job that syncs current-season games from the Squiggle API.

    The job runs every 15 minutes via APScheduler (see
    :mod:`app.core.scheduler`).  During the AFL off-season it only
    runs inside a 2 AM – 4 AM AWST window (handled by the service).
    """

    name = "daily-sync"
    timeout_seconds = 300  # 5 minutes — the underlying service is fast
    max_retries = 3
    backoff_multiplier = 2.0
    initial_delay = 1.0
    jitter = 0.1

    async def run(self) -> dict:
        """Invoke the daily-sync service within the active session."""
        async with self._session_factory() as session:
            return await run_daily_sync(session)
