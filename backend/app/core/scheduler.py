"""APScheduler setup for in-process cron jobs (Phase 3).

Wires the four :class:`app.cron.base.BaseJob` subclasses into an
:class:`apscheduler.schedulers.asyncio.AsyncIOScheduler` using the
cron expressions in :mod:`packages.shared.config`.

The scheduler is started during the FastAPI ``lifespan`` startup
phase (see :mod:`app.core.lifespan`) and shut down on app shutdown.
"""

from __future__ import annotations

from typing import Any, Optional

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from app.cron.daily_sync import DailySyncJob
from app.cron.historic_refresh import HistoricRefreshJob
from app.cron.match_completion import MatchCompletionJob
from app.cron.tip_generation import TipGenerationJob
from packages.shared.config import settings


# Mis-fire grace time (seconds) per job type.
#
# NOTE: ``misfire_grace_time`` and the per-job ``timeout_seconds`` are
# two *different* mechanisms (ME-007):
#
# * ``timeout_seconds`` lives on the :class:`BaseJob` subclass and
#   bounds the runtime of a *single* job invocation via
#   ``asyncio.wait_for``.  It exists so a stuck job cannot block
#   forever; once it elapses the job is cancelled and the lock is
#   released.
# * ``misfire_grace_time`` is an APScheduler property.  When the
#   scheduler is busy at the moment a cron trigger fires, the
#   "misfired" run is queued and APScheduler will only dispatch it
#   if the wall-clock is still inside this grace window.  Anything
#   older is dropped (or coalesced with the next run thanks to
#   ``coalesce=True``).
#
# The values below are kept slightly *larger* than the matching
# ``timeout_seconds`` for the same job.  This way a job that just
# missed its window because the previous invocation was still
# running has a chance to fire after the predecessor finishes.
_MISFIRE_GRACE = {
    "daily-sync": 900,        # 15 min — was 5 min; bumped (ME-007)
    "match-completion": 900,  # 15 min — was 5 min; bumped
    "tip-generation": 1200,   # 20 min — was 10 min; bumped
    "historic-refresh": 3600, # 1 hour — weekly batch job
}


def build_scheduler(session_factory: Any) -> AsyncIOScheduler:
    """Build the APScheduler instance. Does not start it.

    The returned scheduler is registered with four jobs (one per
    ``BaseJob`` subclass), each with:

    - ``max_instances=1``: rely on the JobLock for cross-instance safety.
    - ``coalesce=True``: combine missed runs into one.
    - ``misfire_grace_time`` per the table above.

    Args:
        session_factory: A zero-argument callable that returns an
            async context manager wrapping an :class:`AsyncSession`.
            The same factory is passed to every job.
    """
    scheduler = AsyncIOScheduler(timezone=settings.cron_timezone)

    scheduler.add_job(
        DailySyncJob(session_factory).execute,
        CronTrigger.from_crontab(settings.daily_sync_cron, timezone=settings.cron_timezone),
        id="daily-sync",
        name="Daily game sync",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=_MISFIRE_GRACE["daily-sync"],
    )
    scheduler.add_job(
        MatchCompletionJob(session_factory).execute,
        CronTrigger.from_crontab(
            settings.match_completion_cron, timezone=settings.cron_timezone
        ),
        id="match-completion",
        name="Match completion detector",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=_MISFIRE_GRACE["match-completion"],
    )
    scheduler.add_job(
        TipGenerationJob(session_factory).execute,
        CronTrigger.from_crontab(
            settings.tip_generation_cron, timezone=settings.cron_timezone
        ),
        id="tip-generation",
        name="Tip generation",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=_MISFIRE_GRACE["tip-generation"],
    )
    scheduler.add_job(
        HistoricRefreshJob(session_factory).execute,
        CronTrigger.from_crontab(
            settings.historic_refresh_cron, timezone=settings.cron_timezone
        ),
        id="historic-refresh",
        name="Historic data refresh",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=_MISFIRE_GRACE["historic-refresh"],
    )
    return scheduler


async def init_scheduler(
    session_factory: Any,
    *,
    existing: Optional[AsyncIOScheduler] = None,
) -> AsyncIOScheduler:
    """Build (or reuse) the scheduler and start it.

    Args:
        session_factory: Passed to :func:`build_scheduler` if
            ``existing`` is not provided.
        existing: Optional pre-built scheduler (used by tests).

    Returns:
        The started :class:`AsyncIOScheduler`.
    """
    scheduler = existing if existing is not None else build_scheduler(session_factory)
    if not scheduler.running:
        scheduler.start()
    return scheduler


async def shutdown_scheduler(scheduler: Optional[AsyncIOScheduler]) -> None:
    """Stop the scheduler. Idempotent — safe to call on a non-running scheduler."""
    if scheduler is None:
        return
    if scheduler.running:
        scheduler.shutdown(wait=False)
