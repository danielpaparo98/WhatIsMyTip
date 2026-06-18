"""In-process :class:`BaseJob` for the match-completion detector.

Wraps :func:`packages.shared.services.match_completion.run_match_completion`
with the :class:`app.cron.base.BaseJob` machinery.
"""

from __future__ import annotations

from app.cron.base import BaseJob
from packages.shared.services.match_completion import run_match_completion


class MatchCompletionJob(BaseJob):
    """Cron job that detects recently completed matches.

    Runs every 15 minutes (offset by 5) via APScheduler.  Updates
    final scores, refreshes the Elo ratings cache when new games are
    completed, and invalidates related cache entries.
    """

    name = "match-completion"
    timeout_seconds = 300  # 5 minutes
    max_retries = 3
    backoff_multiplier = 2.0
    initial_delay = 1.0
    jitter = 0.1

    async def run(self) -> dict:
        """Invoke the match-completion service within the active session."""
        async with self._session_factory() as session:
            return await run_match_completion(session)
