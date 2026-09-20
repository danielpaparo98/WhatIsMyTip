"""Process-wide reference to the running APScheduler instance.

Lives in its own tiny module to avoid a circular import:
``app.core.scheduler`` imports the cron job modules, and those need a
way to reach the scheduler (e.g. ``MatchCompletionJob`` scheduling the
one-shot round-completion tip rerun) without importing the scheduler
module back.
"""

from __future__ import annotations

from typing import Any, Optional

_scheduler: Optional[Any] = None


def set_scheduler(scheduler: Any) -> None:
    """Register the running scheduler (called from ``init_scheduler``)."""
    global _scheduler
    _scheduler = scheduler


def get_scheduler() -> Optional[Any]:
    """Return the registered scheduler, or ``None`` before startup."""
    return _scheduler


def clear_scheduler() -> None:
    """Drop the reference (called on shutdown)."""
    global _scheduler
    _scheduler = None
