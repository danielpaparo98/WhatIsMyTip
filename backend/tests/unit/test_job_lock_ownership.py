"""BaseJob lock-ownership and TTL tests (review findings JOBS-H2 / JOBS-H3).

* **JOBS-H2** — ``BaseJob`` must request a lock TTL of at least its own
  ``timeout_seconds`` (+ buffer).  The old code requested exactly
  ``timeout_seconds`` which the CRUD then clamped *down* to 300 s, so
  long jobs (tip-generation: 1800 s) lost their lock mid-run.
* **JOBS-H3** — ``locked_by`` used to be the constant
  ``"fastapi-{name}"`` on every replica.  After a TTL expiry, instance B
  could acquire the lock with the SAME ``locked_by`` string, and
  instance A's ``release_lock`` (matching on ``job_name AND locked_by``)
  would then delete **B's** lock.  ``locked_by`` must now be unique per
  run, and release must use the exact value acquired with.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.cron.base import BaseJob, LOCK_TTL_BUFFER_SECONDS


class _StubJob(BaseJob):
    name = "test-job"
    timeout_seconds = 1800  # like tip-generation
    max_retries = 1
    backoff_multiplier = 2.0
    initial_delay = 0.0
    jitter = 0.0

    def __init__(self, session_factory, alerting=None):
        super().__init__(session_factory, alerting)
        self.run_calls = 0

    async def run(self) -> dict:
        self.run_calls += 1
        return {"ok": True}


def _make_session_factory(session):
    @asynccontextmanager
    async def _factory():
        yield session

    return _factory


def _patch_cruds(monkeypatch, *, lock_acquired: bool = True):
    """Patch JobLockCRUD/JobExecutionCRUD on app.cron.base with mocks."""
    mock_execution = MagicMock(id=99)
    exec_crud = MagicMock()
    exec_crud.create_execution = AsyncMock(return_value=mock_execution)
    exec_crud.update_execution = AsyncMock()

    lock_crud = MagicMock()
    mock_lock = MagicMock()
    lock_crud.acquire_lock = AsyncMock(return_value=mock_lock if lock_acquired else None)
    lock_crud.release_lock = AsyncMock()

    monkeypatch.setattr("app.cron.base.JobExecutionCRUD", lambda session: exec_crud)
    monkeypatch.setattr("app.cron.base.JobLockCRUD", lambda session: lock_crud)
    return exec_crud, lock_crud


def _make_session():
    session = AsyncMock()
    session.commit = AsyncMock()
    session.refresh = AsyncMock()
    return session


class TestLockTtlCoversTimeout:
    @pytest.mark.asyncio
    async def test_lock_ttl_at_least_timeout_plus_buffer(self, monkeypatch):
        """JOBS-H2: the requested TTL must be >= timeout + buffer."""
        _, lock_crud = _patch_cruds(monkeypatch, lock_acquired=True)
        job = _StubJob(_make_session_factory(_make_session()))

        await job.execute()

        kwargs = lock_crud.acquire_lock.await_args.kwargs
        requested = kwargs["expires_seconds"]
        assert requested >= job.timeout_seconds + LOCK_TTL_BUFFER_SECONDS, (
            f"lock TTL ({requested}s) must be >= timeout "
            f"({job.timeout_seconds}s) + buffer ({LOCK_TTL_BUFFER_SECONDS}s)"
        )


class TestLockOwnership:
    @pytest.mark.asyncio
    async def test_locked_by_unique_per_run(self, monkeypatch):
        """JOBS-H3: two runs must not produce the same locked_by value."""
        _, lock_crud = _patch_cruds(monkeypatch, lock_acquired=True)
        job = _StubJob(_make_session_factory(_make_session()))

        await job.execute()
        first = lock_crud.acquire_lock.await_args.kwargs["locked_by"]

        _, lock_crud2 = _patch_cruds(monkeypatch, lock_acquired=True)
        job2 = _StubJob(_make_session_factory(_make_session()))
        await job2.execute()
        second = lock_crud2.acquire_lock.await_args.kwargs["locked_by"]

        assert first != second, (
            f"locked_by must be unique per run — got '{first}' twice"
        )
        # And it must still identify the job.
        assert "test-job" in first

    @pytest.mark.asyncio
    async def test_release_uses_same_locked_by_as_acquire(self, monkeypatch):
        """JOBS-H3: release must target exactly the lock this run acquired —
        never another replica's lock."""
        _, lock_crud = _patch_cruds(monkeypatch, lock_acquired=True)
        job = _StubJob(_make_session_factory(_make_session()))

        await job.execute()

        acquired = lock_crud.acquire_lock.await_args.kwargs["locked_by"]
        released = lock_crud.release_lock.await_args.kwargs["locked_by"]
        assert released == acquired, (
            f"release locked_by '{released}' != acquire locked_by '{acquired}'"
        )

    @pytest.mark.asyncio
    async def test_lock_held_path_skips_without_release(self, monkeypatch):
        """When the lock is held elsewhere, the job skips and must NOT
        release anything."""
        _, lock_crud = _patch_cruds(monkeypatch, lock_acquired=False)
        job = _StubJob(_make_session_factory(_make_session()))

        result = await job.execute()

        assert result == {"skipped": True, "reason": "lock_held"}
        lock_crud.release_lock.assert_not_awaited()
