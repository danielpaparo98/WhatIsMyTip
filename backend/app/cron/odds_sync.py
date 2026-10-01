"""In-process :class:`BaseJob` for the daily AFL odds sync (BT-ODDS).

Wraps :func:`packages.shared.services.odds_sync.run_odds_sync` with the
:class:`app.cron.base.BaseJob` machinery (locking, retry, timeout,
alerting, execution-row bookkeeping).

The job takes one snapshot per day of The Odds API AFL head-to-head
market (~30 credits/month on the free tier), so by game day each
``game_odds`` row approximates closing odds for backtest settlement.
"""

from __future__ import annotations

from app.cron.base import BaseJob
from packages.shared.services.odds_sync import run_odds_sync


class OddsSyncJob(BaseJob):
    """Cron job refreshing bookmaker head-to-head odds daily.

    Runs daily (06:15 app-tz by default — see
    ``settings.odds_sync_cron``).  Skips cleanly when ``ODDS_API_KEY``
    is unset; backtests then settle every game at the representative
    fallback price.
    """

    name = "odds-sync"
    timeout_seconds = 300
    max_retries = 3
    backoff_multiplier = 2.0
    initial_delay = 10.0
    jitter = 0.1

    async def run(self) -> dict:
        """Invoke the odds sync within the active session."""
        async with self._session_factory() as session:
            return await run_odds_sync(session)
