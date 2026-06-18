"""Base class and shared helpers for in-process cron jobs.

Phase 3 of the FastAPI reimplementation ported the historical
``BaseJob`` / ``retry_with_backoff`` machinery that was lost during the
FaaS migration.  See ``docs/BACKEND-FAAS-CODE-REVIEW.md`` §3.11 and
``docs/FULL-REVIEW.md`` for the original design notes.

Public API:
    - :class:`BaseJob` — abstract base for cron jobs.
    - :func:`retry_with_backoff` — re-exported from
      :mod:`packages.shared.exceptions` so callers can ``from
      app.cron.base import retry_with_backoff`` without reaching into
      the shared package directly.
"""

from __future__ import annotations

import asyncio
import time
from abc import ABC, abstractmethod
from contextlib import AbstractAsyncContextManager
from datetime import datetime, timezone
from typing import Any, Optional

from packages.shared.alerting import AlertingService
from packages.shared.crud.jobs import JobExecutionCRUD, JobLockCRUD
from packages.shared.exceptions import classify_error
from packages.shared.logger import generate_execution_id, get_logger

# Re-export so tests and BaseJob subclasses can import retry_with_backoff
# from a single, app-level location.
from packages.shared.exceptions import retry_with_backoff  # noqa: F401

logger = get_logger(__name__)


class BaseJob(ABC):
    """Abstract cron job. Subclasses implement :meth:`run`.

    The :meth:`execute` wrapper handles:

    1. Generating a unique ``execution_id`` for correlation in logs.
    2. Acquiring a :class:`JobLock` (DB row with ``expires_at``); the
       job is skipped if another instance is already running.
    3. Writing a :class:`JobExecution` row in ``running`` state.
    4. Calling :func:`retry_with_backoff` around :meth:`run` with
       ``max_retries`` and exponential backoff + jitter.
    5. Enforcing an ``asyncio`` timeout via
       :func:`asyncio.wait_for` so a stuck job cannot block the
       scheduler forever.
    6. Sending a webhook alert via :class:`AlertingService` on final
       failure.
    7. Finalising the :class:`JobExecution` row (``completed`` /
       ``failed``) and releasing the :class:`JobLock` — *always*, even
       on failure.

    Class attributes are overridden by subclasses to customise the
    schedule, timeout, and retry behaviour.
    """

    name: str = "base"
    timeout_seconds: int = 300
    max_retries: int = 3
    backoff_multiplier: float = 2.0
    initial_delay: float = 1.0
    jitter: float = 0.1

    def __init__(
        self,
        session_factory: Any,
        alerting: Optional[AlertingService] = None,
    ) -> None:
        """Initialise the job.

        Args:
            session_factory: A zero-argument callable that returns an
                async context manager wrapping an :class:`AsyncSession`.
                This is normally ``packages.shared.db.get_session``.
            alerting: Optional :class:`AlertingService` instance. If
                omitted, a default is instantiated (reads webhook
                settings from :mod:`packages.shared.config`).
        """
        self._session_factory = session_factory
        self._alerting = alerting or AlertingService()

    @abstractmethod
    async def run(self) -> dict:
        """Execute the job. Return a JSON-serialisable result dict.

        Subclasses do their actual work here.  The base class wraps
        this in locking, retry, timeout, alerting, and execution-row
        bookkeeping.
        """
        raise NotImplementedError

    async def execute(self) -> dict:
        """Top-level entry point. Wraps :meth:`run` in the full cron lifecycle.

        Returns:
            The result dict from :meth:`run` on success, or
            ``{"skipped": True, "reason": "lock_held"}`` if another
            instance is already running.

        Raises:
            RuntimeError: When the job fails after all retries (the
                original exception is wrapped so the scheduler sees a
                non-:class:`PermanentJobError` failure as a hard error
                and triggers a webhook alert).
        """
        execution_id = generate_execution_id()
        started_at = datetime.now(timezone.utc)
        result: dict = {}
        error_message: Optional[str] = None
        status: str = "running"
        execution_pk: Optional[int] = None
        lock_acquired = False

        # ----- 1. Acquire lock and write execution row in one session -----
        try:
            async with self._session_factory() as session:
                lock_crud = JobLockCRUD(session)
                execution_crud = JobExecutionCRUD(session)

                lock = await lock_crud.acquire_lock(
                    job_name=self.name,
                    locked_by=f"fastapi-{self.name}",
                    expires_seconds=self.timeout_seconds,
                )
                if lock is None:
                    logger.info(
                        "job %s skipped: lock held by another instance",
                        self.name,
                        extra={"job_name": self.name, "execution_id": execution_id},
                    )
                    return {"skipped": True, "reason": "lock_held"}

                lock_acquired = True

                execution = await execution_crud.create_execution(
                    job_name=self.name,
                    status="running",
                )
                execution_pk = execution.id
                await session.commit()
        except Exception as exc:  # noqa: BLE001
            logger.exception(
                "job %s failed to acquire lock or create execution: %r",
                self.name,
                exc,
                extra={"job_name": self.name, "execution_id": execution_id},
            )
            # If we couldn't even acquire a lock, there's nothing to alert
            # about (the infrastructure is down) — let the caller decide.
            raise

        # ----- 2. Run with retry + timeout -----
        #
        # ``start_monotonic`` is captured HERE (immediately before the
        # ``asyncio.wait_for`` call) so that ``duration_seconds`` only
        # covers the actual run window.  Previously it was captured at
        # the top of ``execute()`` and included the lock-acquisition
        # + execution-row-write overhead, which made the recorded
        # duration misleadingly large under DB contention.
        start_monotonic = time.monotonic()
        try:
            result = await asyncio.wait_for(
                retry_with_backoff(
                    self.run,
                    max_retries=self.max_retries,
                    initial_delay=self.initial_delay,
                    backoff_multiplier=self.backoff_multiplier,
                    jitter=self.jitter,
                ),
                timeout=self.timeout_seconds,
            )
            status = "completed"
        except asyncio.TimeoutError as exc:
            error_message = (
                f"TimeoutError: job exceeded {self.timeout_seconds}s timeout"
            )
            status = "failed"
            logger.error(
                "job %s timed out after %ss",
                self.name,
                self.timeout_seconds,
                extra={"job_name": self.name, "execution_id": execution_id},
            )
            try:
                await self._alerting.send_timeout_alert(
                    job_name=self.name,
                    elapsed_seconds=float(self.timeout_seconds),
                    remaining_work="job killed by timeout",
                )
            except Exception:  # noqa: BLE001
                logger.exception(
                    "job %s failed to send timeout alert", self.name
                )
        except Exception as exc:
            error_message = repr(exc)
            status = "failed"
            error_type = classify_error(exc)
            logger.exception(
                "job %s failed: %r", self.name, exc,
                extra={
                    "job_name": self.name,
                    "execution_id": execution_id,
                    "error_type": type(error_type).__name__,
                },
            )
            try:
                await self._alerting.send_failure_alert(
                    job_name=self.name,
                    error=error_message,
                    execution_id=execution_id,
                    duration_seconds=time.monotonic() - start_monotonic,
                )
            except Exception:  # noqa: BLE001
                logger.exception(
                    "job %s failed to send failure alert", self.name
                )

        # ----- 3. Finalise execution row and release lock -----
        duration_ms = int((time.monotonic() - start_monotonic) * 1000)
        try:
            async with self._session_factory() as session:
                execution_crud = JobExecutionCRUD(session)
                if execution_pk is not None:
                    await execution_crud.update_execution(
                        execution_pk,
                        status=status,
                        completed_at=datetime.now(timezone.utc),
                        duration_seconds=duration_ms // 1000,
                        error_message=error_message,
                        result_summary=str(result) if status == "completed" else None,
                    )
                lock_crud = JobLockCRUD(session)
                await lock_crud.release_lock(
                    job_name=self.name,
                    locked_by=f"fastapi-{self.name}",
                )
                await session.commit()
        except Exception:  # noqa: BLE001
            logger.exception(
                "job %s failed to finalise execution / release lock", self.name
            )

        if status == "failed":
            raise RuntimeError(f"job {self.name!r} failed: {error_message}")
        return result
