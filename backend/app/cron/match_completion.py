"""In-process :class:`BaseJob` for the match-completion detector.

Wraps :func:`packages.shared.services.match_completion.run_match_completion`
with the :class:`app.cron.base.BaseJob` machinery.

GF-TRIGGER: when the detector observes that a round's last game just
finished, this job schedules a one-shot tip-generation rerun for that
night (see :func:`app.cron.tip_generation.schedule_round_completion_rerun`)
so the next round is fully (re)generated without waiting for the 3AM
cron — and without depending on ``tips_created > 0``.
"""

from __future__ import annotations

from app.cron.base import BaseJob
from app.cron.tip_generation import schedule_round_completion_rerun
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
            result = await run_match_completion(session)

        # GF-TRIGGER: rounds whose last game just completed → schedule
        # the one-shot night rerun.  Best-effort; never fails the job.
        try:
            rounds = result.get("rounds_completed") or []
            if rounds:
                schedule_round_completion_rerun(self._session_factory, rounds)
        except Exception:  # noqa: BLE001 — scheduling must not fail the job
            from packages.shared.logger import get_logger

            get_logger(__name__).exception(
                "Failed to schedule round-completion tip rerun (non-fatal)"
            )

        return result
