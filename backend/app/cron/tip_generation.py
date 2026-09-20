"""In-process :class:`BaseJob` for tip generation.

Wraps :func:`packages.shared.services.tip_generation.run_tip_generation`
with the :class:`app.cron.base.BaseJob` machinery.

After a successful run, a site-rebuild webhook
(``SITE_REBUILD_WEBHOOK_URL``) is fired (best-effort) so the statically
generated frontend rebuilds with the fresh tips — see
:mod:`packages.shared.services.site_rebuild`.

GF-TRIGGER redesign: besides the fixed nightly cron (safety net), a
round-completion event schedules a ONE-SHOT rerun for that night —
when the match-completion detector observes a round's last game
finish, :func:`schedule_round_completion_rerun` queues this job via
the in-process scheduler so the next round's tips, AI explanations,
match analyses and the grand-final report are all (re)generated the
same night, and the static site rebuilds with them.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from apscheduler.triggers.date import DateTrigger

from app.core.scheduler_ref import get_scheduler
from app.cron.base import BaseJob
from packages.shared.config import settings
from packages.shared.logger import get_logger
from packages.shared.services.site_rebuild import trigger_site_rebuild
from packages.shared.services.tip_generation import run_tip_generation

logger = get_logger(__name__)


class TipGenerationJob(BaseJob):
    """Cron job that generates tips (with AI explanations) for the next round.

    Runs daily at 3:00 AM AWST via APScheduler as the safety net, plus
    one-shot reruns scheduled by round-completion events (GF-TRIGGER).
    The actual next-round lookup, tip generation, and explanation
    generation all live in the service function.
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
            result = await run_tip_generation(session)

        # Best-effort SSG freshness: rebuild the static frontend so the
        # baked HTML picks up the new content.  Fired after every
        # successful run; the payload now carries every content signal
        # (tips, analyses, GF reports) for observability.
        rebuild = await trigger_site_rebuild(
            tips_created=result.get("tips_created", 0),
            extra={
                "tips_updated": result.get("tips_updated", 0),
                "match_analyses_created": result.get("match_analyses_created", 0),
                "match_reports_created": result.get("match_reports_created", 0),
            },
        )
        if rebuild is not None:
            result["site_rebuild"] = rebuild

        return result


# ---------------------------------------------------------------------------
# GF-TRIGGER: one-shot round-completion rerun
# ---------------------------------------------------------------------------

# When a completion is detected AFTER the configured rerun hour (late
# game finishing late), the rerun is scheduled this many minutes later
# instead of waiting for the next day.
_LATE_COMPLETION_DELAY_MINUTES = 30

RERUN_JOB_ID = "tip-generation-round-completion"


def compute_rerun_time(now: datetime, rerun_hour: int) -> datetime:
    """Compute when the night rerun should fire.

    Pure helper (unit-tested): the next occurrence of ``rerun_hour``
    in the scheduler timezone — same day when that hour is still
    ahead, otherwise a short delay so the rerun still happens THAT
    night (per the GF-TRIGGER design: "the night that the last game
    is finished").
    """
    candidate = now.replace(
        hour=rerun_hour, minute=0, second=0, microsecond=0
    )
    if candidate > now:
        return candidate
    return now + timedelta(minutes=_LATE_COMPLETION_DELAY_MINUTES)


def schedule_round_completion_rerun(
    session_factory: Any,
    rounds_completed: List[Dict[str, Any]],
    *,
    now: Optional[datetime] = None,
) -> Optional[datetime]:
    """Schedule a one-shot tip-generation rerun for tonight (best-effort).

    Args:
        session_factory: The app's session factory (forwarded to the job).
        rounds_completed: ``[{"season": ..., "round_id": ...}]`` entries
            from the match-completion detector.
        now: Override "now" (tests); defaults to the scheduler timezone's
            current time via ``compute_rerun_time``.

    Returns:
        The scheduled run time, or ``None`` when there is nothing to
        rerun or no running scheduler (single-instance deploys only —
        the advisory job lock keeps cross-instance runs safe).
    """
    if not rounds_completed:
        return None

    scheduler = get_scheduler()
    if scheduler is None or not scheduler.running:
        logger.warning(
            "Round completed but no running scheduler; "
            "the 3AM tip-generation cron remains the fallback"
        )
        return None

    rerun_at = compute_rerun_time(
        now if now is not None else datetime.now(tz=scheduler.timezone),
        settings.round_completion_rerun_hour,
    )

    rounds_desc = ", ".join(
        f"s{r.get('season')}r{r.get('round_id')}" for r in rounds_completed
    )
    scheduler.add_job(
        TipGenerationJob(session_factory).execute,
        DateTrigger(run_date=rerun_at),
        id=RERUN_JOB_ID,
        name="Round-completion tip rerun",
        max_instances=1,
        coalesce=True,
        # A rerun misfiring past midnight is still worth running.
        misfire_grace_time=3600,
        replace_existing=True,
    )
    logger.info(
        "Scheduled round-completion tip rerun for %s (rounds: %s)",
        rerun_at.isoformat(),
        rounds_desc,
    )
    return rerun_at
