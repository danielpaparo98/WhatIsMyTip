"""Tests for job-lock expiry semantics (SEC-ME-009, revised 2026-09).

**Original rule (SEC-ME-009):** every lock expiry was hard-capped at
``JOB_LOCK_EXPIRE_SECONDS`` (300 s) regardless of the caller-supplied
value, to stop a stuck in-process job from holding its lock for hours.

**Why that rule was wrong (review finding JOBS-H2):** ``BaseJob``
requests ``expires_seconds = timeout_seconds``, and several jobs have
timeouts *longer* than the 300 s ceiling (tip-generation: 1800 s,
historic-refresh: 900 s).  The clamp therefore expired the lock
**while the job was still legitimately running**, letting the next
scheduler tick (or another replica) start the same job concurrently —
duplicate scraping, duplicate tip writes, and races on
``job_executions``.

**Revised contract (the invariant):**

* A lock must never expire before the caller's own timeout does — the
  caller-supplied ``expires_seconds`` is a **floor**, honoured as-is.
* Stuck-job protection now comes from ``BaseJob``'s own
  ``asyncio.wait_for`` timeout (a lock can only outlive its job by the
  small buffer BaseJob adds on top of the timeout).
* An absolute sanity max (``JOB_LOCK_MAX_SECONDS``, default 4 h) still
  bounds truly absurd values — a caller cannot accidentally hold a lock
  for a day.
* ``expires_seconds=None`` falls back to ``JOB_LOCK_EXPIRE_SECONDS``
  (300 s default) as before.
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from packages.shared.config import settings
from packages.shared.crud.jobs import JobLockCRUD


def _make_session() -> MagicMock:
    session = MagicMock()
    session.execute = AsyncMock()
    session.commit = AsyncMock()
    return session


def _last_insert_bind(crud: JobLockCRUD) -> dict:
    """Read the bind params of the last execute() call (the INSERT)."""
    last_call = crud.db.execute.call_args_list[-1]
    return last_call.args[1] if len(last_call.args) > 1 else last_call.kwargs


def _expires_delta_seconds(bind: dict) -> float:
    expires_at: datetime = bind["expires_at"]
    now = datetime.now(timezone.utc)
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    return (expires_at - now).total_seconds()


class TestJobLockExpireSetting:
    """The default-expiry setting defaults to 300 and is configurable."""

    def test_default_is_300(self) -> None:
        from packages.shared.config import Settings

        s = Settings()
        assert s.job_lock_expire_seconds == 300, (
            f"default job_lock_expire_seconds must be 300 s, "
            f"got {s.job_lock_expire_seconds}"
        )

    def test_setting_is_int_and_positive(self) -> None:
        from packages.shared.config import Settings

        s = Settings()
        assert isinstance(s.job_lock_expire_seconds, int)
        assert s.job_lock_expire_seconds > 0

    def test_hard_max_setting_exists(self) -> None:
        """JOBS-H2: the absolute sanity max must exist and default to 4 h."""
        from packages.shared.config import Settings

        s = Settings()
        assert getattr(s, "job_lock_max_seconds", None) == 14_400
        # The max must never be below the default expiry.
        assert s.job_lock_max_seconds >= s.job_lock_expire_seconds


class TestAcquireLockExpiryContract:
    """Lock TTL must honour the caller (floor), bounded by an absolute max."""

    @pytest.mark.asyncio
    async def test_job_timeout_expiry_above_old_ceiling_is_honoured(
        self, monkeypatch
    ) -> None:
        """JOBS-H2 regression: a job whose timeout (1860 s = 1800 s tip-gen
        timeout + buffer) exceeds the 300 s setting must get its full TTL —
        the old code clamped it to 300 s and let duplicates start mid-run."""
        monkeypatch.setattr(settings, "job_lock_expire_seconds", 300)
        monkeypatch.setattr(settings, "job_lock_max_seconds", 14_400)

        crud = JobLockCRUD(_make_session())
        result = MagicMock()
        result.rowcount = 0
        crud.db.execute = AsyncMock(return_value=result)

        await crud.acquire_lock(
            job_name="tip-generation",
            locked_by="test",
            expires_seconds=1860,
        )

        delta = _expires_delta_seconds(_last_insert_bind(crud))
        assert delta >= 1855, (
            f"lock TTL must honour the caller's 1860 s (got {delta:.0f} s) — "
            f"the lock must never expire before the job's own timeout"
        )

    @pytest.mark.asyncio
    async def test_absurd_expiry_capped_at_hard_max(self, monkeypatch) -> None:
        """SEC-ME-009 preserved: an absurd caller value is capped at the
        absolute max (4 h default) — no multi-hour accidental locks."""
        monkeypatch.setattr(settings, "job_lock_expire_seconds", 300)
        monkeypatch.setattr(settings, "job_lock_max_seconds", 14_400)

        crud = JobLockCRUD(_make_session())
        result = MagicMock()
        result.rowcount = 0
        crud.db.execute = AsyncMock(return_value=result)

        await crud.acquire_lock(
            job_name="nightly-batch",
            locked_by="test",
            expires_seconds=10 * 3600,  # 10 hours — absurd
        )

        delta = _expires_delta_seconds(_last_insert_bind(crud))
        assert delta <= 14_400 + 5, (
            f"expires_at must be capped at job_lock_max_seconds (got {delta:.0f} s)"
        )

    @pytest.mark.asyncio
    async def test_short_expiry_is_honoured(self, monkeypatch) -> None:
        """A short caller-supplied expiry must NOT be inflated to the default."""
        monkeypatch.setattr(settings, "job_lock_expire_seconds", 300)

        crud = JobLockCRUD(_make_session())
        result = MagicMock()
        result.rowcount = 0
        crud.db.execute = AsyncMock(return_value=result)

        await crud.acquire_lock(
            job_name="quick",
            locked_by="test",
            expires_seconds=30,
        )

        delta = _expires_delta_seconds(_last_insert_bind(crud))
        assert 25 <= delta <= 35, (
            f"expires_at should honour the caller's 30 s, got delta={delta:.0f} s"
        )

    @pytest.mark.asyncio
    async def test_none_uses_default_setting(self, monkeypatch) -> None:
        """``expires_seconds=None`` falls back to JOB_LOCK_EXPIRE_SECONDS."""
        monkeypatch.setattr(settings, "job_lock_expire_seconds", 300)

        crud = JobLockCRUD(_make_session())
        result = MagicMock()
        result.rowcount = 0
        crud.db.execute = AsyncMock(return_value=result)

        await crud.acquire_lock(job_name="defaulted", locked_by="test")

        delta = _expires_delta_seconds(_last_insert_bind(crud))
        assert 290 <= delta <= 310, (
            f"None should resolve to the 300 s default, got {delta:.0f} s"
        )


class TestAcquireLockSignature:
    """The function signature exposes the setting as the default."""

    def test_default_expires_seconds_reflects_setting(self) -> None:
        import inspect

        sig = inspect.signature(JobLockCRUD.acquire_lock)
        assert "expires_seconds" in sig.parameters
        assert sig.parameters["expires_seconds"].default is not inspect.Parameter.empty
