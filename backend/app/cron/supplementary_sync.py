"""In-process :class:`BaseJob` for the supplementary-data refresh (P3-3).

Wraps :func:`packages.shared.services.supplementary_sync.run_supplementary_sync`
with the :class:`app.cron.base.BaseJob` machinery (locking, retry,
timeout, alerting, execution-row bookkeeping).

Closes the P0-6/P3-3 gap: injuries and weather were previously
refreshed only by manual scripts, silently starving the injury model's
point-in-time guard.
"""

from __future__ import annotations

from app.cron.base import BaseJob
from packages.shared.services.supplementary_sync import run_supplementary_sync


class SupplementarySyncJob(BaseJob):
    """Cron job refreshing injury + weather feeds daily.

    Runs daily (05:45 app-tz by default — see
    ``settings.supplementary_sync_cron``), well before the morning
    tip-generation pass so predictions see fresh data.
    """

    name = "supplementary-sync"
    timeout_seconds = 600
    max_retries = 3
    backoff_multiplier = 2.0
    initial_delay = 10.0
    jitter = 0.1

    async def run(self) -> dict:
        """Invoke the sync service within the active session."""
        async with self._session_factory() as session:
            return await run_supplementary_sync(session)
