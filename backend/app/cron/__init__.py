"""In-process cron job infrastructure.

Each module under :mod:`app.cron` defines a :class:`BaseJob` subclass
that the :class:`apscheduler.schedulers.asyncio.AsyncIOScheduler` (built
in :mod:`app.core.scheduler`) picks up and runs on the cron expression
configured in :mod:`packages.shared.config`.

The base class lives in :mod:`app.cron.base`.
"""
