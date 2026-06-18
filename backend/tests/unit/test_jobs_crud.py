"""Unit tests for JobLockCRUD (ME-004 — opportunistic lock cleanup)."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from packages.shared.crud.jobs import JobLockCRUD


# ---------------------------------------------------------------------------
# Opportunistic cleanup on acquire_lock (ME-004)
# ---------------------------------------------------------------------------


class TestAcquireLockTriggersStaleCleanup:
    """``acquire_lock`` must opportunistically clean up locks that
    have been expired for more than 24 hours, so the ``job_locks``
    table doesn't grow without bound."""

    @pytest.mark.asyncio
    async def test_acquire_lock_calls_cleanup_of_stale_locks(self):
        """``JobLockCRUD.acquire_lock`` must trigger a cleanup of
        stale (expired-for-24h+) locks before performing the atomic
        INSERT.  This is the ME-004 fix."""
        session = AsyncMock(spec=AsyncSession)

        # Pre-seed the result of the SELECT that ``acquire_lock`` does
        # so we control the ``rowcount`` returned by the SQL statement.
        # We do not care about the actual data here; the test
        # focuses on the cleanup call.
        result_mock = MagicMock()
        result_mock.rowcount = 1
        result_mock.scalar_one_or_none.return_value = SimpleNamespace(
            id=1,
            job_name="test_job",
            locked_at=None,
            locked_by="test_instance",
            expires_at=None,
        )
        session.execute = AsyncMock(return_value=result_mock)

        crud = JobLockCRUD(session)
        with patch.object(
            crud, "cleanup_expired_locks", new=AsyncMock(return_value=3)
        ) as cleanup_spy:
            await crud.acquire_lock(
                job_name="test_job",
                locked_by="test_instance",
                expires_seconds=60,
            )

        # ME-004: the opportunistic cleanup must have been invoked.
        assert cleanup_spy.await_count >= 1, (
            "ME-004: acquire_lock must call cleanup_expired_locks "
            "opportunistically to keep the job_locks table small."
        )

    @pytest.mark.asyncio
    async def test_acquire_lock_does_not_block_when_cleanup_fails(self):
        """If the opportunistic cleanup raises, ``acquire_lock`` must
        still proceed (the lock is more important than the cleanup)."""
        session = AsyncMock(spec=AsyncSession)
        result_mock = MagicMock()
        result_mock.rowcount = 1
        result_mock.scalar_one_or_none.return_value = SimpleNamespace(
            id=1,
            job_name="t",
            locked_at=None,
            locked_by="i",
            expires_at=None,
        )
        session.execute = AsyncMock(return_value=result_mock)

        crud = JobLockCRUD(session)
        with patch.object(
            crud,
            "cleanup_expired_locks",
            new=AsyncMock(side_effect=RuntimeError("cleanup boom")),
        ):
            # Should not raise — the cleanup failure is swallowed
            # so the acquire path stays robust.
            await crud.acquire_lock(
                job_name="t",
                locked_by="i",
                expires_seconds=60,
            )
