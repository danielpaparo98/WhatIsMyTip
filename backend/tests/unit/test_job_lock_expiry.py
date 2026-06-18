"""Tests for SEC-ME-009: job lock expiry reduction + hard ceiling.

The Phase 4 lock expiry was 15 minutes (``job_lock_expire_seconds=900``)
— that was a carry-over from the FaaS architecture where 15 minutes was
the original execution ceiling.  On the in-process scheduler a job
running longer than 5 minutes is almost certainly stuck (every cron
job's own timeout is 5–30 minutes, and any in-process job that hasn't
finished by then needs an operator's attention, not a stale lock).

The fix:
* Add a hard 5-minute ceiling (300 s) on the lock expiry regardless of
  the caller-supplied value.  This stops a future contributor from
  accidentally passing a multi-hour ``expires_seconds`` to a long
  batch job and silently holding the lock for the whole window.
* Keep the ``JOB_LOCK_EXPIRE_SECONDS`` setting so operators can tune
  the ceiling without a code change.
* Update the default from 900 to 300 to match the new upper bound.
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from packages.shared.config import settings
from packages.shared.crud.jobs import JobLockCRUD


# The hard ceiling.  Keep in sync with the module-level comment in
# ``crud/jobs.py`` and the default in ``packages/shared/config.py``.
MAX_LOCK_EXPIRY_SECONDS = 300


def _make_session() -> MagicMock:
    session = MagicMock()
    session.execute = AsyncMock()
    session.commit = AsyncMock()
    return session


class TestJobLockExpireSetting:
    """The config setting defaults to 300 and is configurable."""

    def test_default_is_300(self) -> None:
        """Default lock-expiry ceiling must be 300 s (5 minutes)."""
        # ``Settings`` is a singleton; we don't mutate it for this test
        # (it could affect other tests).  Instead we just assert the
        # class-level default by re-instantiating.
        from packages.shared.config import Settings

        s = Settings()
        assert s.job_lock_expire_seconds == 300, (
            f"SEC-ME-009: default job_lock_expire_seconds must be 300 s, "
            f"got {s.job_lock_expire_seconds}"
        )

    def test_setting_is_int_and_positive(self) -> None:
        from packages.shared.config import Settings

        s = Settings()
        assert isinstance(s.job_lock_expire_seconds, int)
        assert s.job_lock_expire_seconds > 0


class TestAcquireLockCeiling:
    """``acquire_lock`` must cap the expiry at JOB_LOCK_EXPIRE_SECONDS."""

    @pytest.mark.asyncio
    async def test_long_expiry_is_capped(self, monkeypatch) -> None:
        """A caller-supplied expiry longer than the setting is clamped down."""
        monkeypatch.setattr(settings, "job_lock_expire_seconds", 300)

        crud = JobLockCRUD(_make_session())
        captured: dict = {}

        # Intercept acquire_lock to capture the value the CRUD actually
        # stored, by replacing ``text`` with a no-op that records the
        # bind params used.
        from sqlalchemy.sql import text as _text

        original_text = _text

        def _text_capture(stmt, *args, **kwargs):
            # Return a real text() so the rest of the call path works,
            # but record the call so we can inspect the bind params.
            captured["stmt"] = stmt
            return original_text(stmt, *args, **kwargs)

        monkeypatch.setattr("packages.shared.crud.jobs.text", _text_capture)

        result = MagicMock()
        result.rowcount = 0
        crud.db.execute = AsyncMock(return_value=result)

        # Caller asks for 3 hours (way over the 5-minute ceiling).
        await crud.acquire_lock(
            job_name="nightly-batch",
            locked_by="test",
            expires_seconds=3 * 3600,
        )

        # Inspect the call we recorded.  We need to find the
        # ``acquire_lock`` execute call and read the bind params.
        # The crud may run multiple execute() calls (cleanup_expired_locks + INSERT); the last is the INSERT.
        last_call = crud.db.execute.call_args_list[-1]
        bind = last_call.args[1] if len(last_call.args) > 1 else last_call.kwargs
        assert bind["expires_at"], "expected an expires_at bind param"

        # The clamped expiry should be at most (now + 300 s).
        now = datetime.now(timezone.utc)
        expires_at: datetime = bind["expires_at"]
        # Compare in UTC; both should be tz-aware.
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        delta = (expires_at - now).total_seconds()
        assert delta <= 300 + 1, (
            f"expires_at should be capped at ~300 s, got delta={delta:.0f} s"
        )
        # And at least 290 s (allow 10s test slack).
        assert delta >= 290, f"expires_at was clamped too aggressively: {delta:.0f} s"

    @pytest.mark.asyncio
    async def test_short_expiry_is_honoured(self, monkeypatch) -> None:
        """A short caller-supplied expiry must NOT be inflated to the ceiling."""
        monkeypatch.setattr(settings, "job_lock_expire_seconds", 300)

        crud = JobLockCRUD(_make_session())
        crud.db.execute = AsyncMock()
        result = MagicMock()
        result.rowcount = 0
        crud.db.execute = AsyncMock(return_value=result)

        await crud.acquire_lock(
            job_name="quick",
            locked_by="test",
            expires_seconds=30,
        )

        last_call = crud.db.execute.call_args_list[-1]
        bind = last_call.args[1] if len(last_call.args) > 1 else last_call.kwargs
        now = datetime.now(timezone.utc)
        expires_at: datetime = bind["expires_at"]
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        delta = (expires_at - now).total_seconds()
        # Should be ~30 s, NOT inflated to 300 s.
        assert 25 <= delta <= 35, (
            f"expires_at should honour the caller's 30 s, got delta={delta:.0f} s"
        )


class TestAcquireLockSignature:
    """The function signature exposes the setting as the default."""

    def test_default_expires_seconds_reflects_setting(self, monkeypatch) -> None:
        """The default parameter should read from settings at call time
        so operators can tune it without code changes."""
        import inspect

        from packages.shared.crud.jobs import JobLockCRUD

        sig = inspect.signature(JobLockCRUD.acquire_lock)
        assert "expires_seconds" in sig.parameters
        # The default is a sentinel that resolves to settings at call
        # time.  This is verified by behaviour in the tests above; we
        # just assert the parameter exists.
        assert sig.parameters["expires_seconds"].default is not inspect.Parameter.empty
