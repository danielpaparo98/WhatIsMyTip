"""In-process :class:`BaseJob` for the scheduled state-league sync (P5).

Wraps :func:`packages.shared.services.local_competition_sync.run_all_leagues_sync`
with the :class:`app.cron.base.BaseJob` machinery (locking, retry,
timeout, alerting, execution-row bookkeeping).

Keeps every "live" competition in ``STATE_LEAGUES`` (WAFL, SANFL, VFL,
QAFL, the PlayHQ Tasmania leagues, …) fresh without a per-league cron
entry: the registry is the single source of truth and this job follows
it.
"""

from __future__ import annotations

from app.cron.base import BaseJob
from packages.shared.services.local_competition_sync import (
    run_all_leagues_sync,
)


class LeagueSyncJob(BaseJob):
    """Cron job syncing every live state-league competition-season.

    Runs daily (04:30 app-tz by default — see
    ``settings.league_sync_cron``), before the morning tip-generation
    pass so local-competition fixtures/results are fresh.
    """

    name = "league-sync"
    timeout_seconds = 900
    max_retries = 3
    backoff_multiplier = 2.0
    initial_delay = 10.0
    jitter = 0.1

    async def run(self) -> dict:
        """Invoke the all-leagues sync within the active session."""
        async with self._session_factory() as session:
            return await run_all_leagues_sync(session)
