"""In-process :class:`BaseJob` for tip generation.

Wraps :func:`packages.shared.services.tip_generation.run_tip_generation`
with the :class:`app.cron.base.BaseJob` machinery.
"""

from __future__ import annotations

from app.cron.base import BaseJob
from packages.shared.services.tip_generation import run_tip_generation


class TipGenerationJob(BaseJob):
    """Cron job that generates tips (with AI explanations) for the next round.

    Runs daily at 7 PM AWST (= 11:00 UTC) via APScheduler.  The actual
    next-round lookup, tip generation, and explanation generation all
    live in the service function.
    """

    name = "tip-generation"
    timeout_seconds = 1800  # 30 minutes — OpenRouter can be slow
    max_retries = 3
    backoff_multiplier = 2.0
    initial_delay = 5.0
    jitter = 0.1

    async def run(self) -> dict:
        """Invoke the tip-generation service within the active session."""
        async with self._session_factory() as session:
            return await run_tip_generation(session)
