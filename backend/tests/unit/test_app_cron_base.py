"""Unit tests for :mod:`app.cron.base`.

Covers:
- :func:`retry_with_backoff` behavior (success, retry-then-success, exhaustion, jitter, scope)
- :class:`BaseJob.execute` happy path (writes a `success` JobExecution row)
- :class:`BaseJob.execute` failure path (writes a `failed` row, calls alerting)
- :class:`BaseJob.execute` lock-held (skips)
- :class:`BaseJob.execute` timeout enforcement
- :class:`BaseJob.execute` lock release on failure
"""

from __future__ import annotations

import asyncio
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.cron.base import BaseJob, retry_with_backoff


# ---------------------------------------------------------------------------
# retry_with_backoff
# ---------------------------------------------------------------------------


class TestRetryWithBackoff:
    @pytest.mark.asyncio
    async def test_succeeds_on_first_try(self):
        """Func that returns immediately should not be retried."""
        attempts = 0

        async def _ok():
            nonlocal attempts
            attempts += 1
            return "ok"

        result = await retry_with_backoff(
            _ok,
            max_retries=3,
            initial_delay=0.0,
            backoff_multiplier=2.0,
            jitter=0.0,
        )

        assert result == "ok"
        assert attempts == 1

    @pytest.mark.asyncio
    async def test_succeeds_after_two_failures(self):
        """Two transient failures should be retried, then succeed."""
        attempts = 0

        async def _flaky():
            nonlocal attempts
            attempts += 1
            if attempts < 3:
                raise ConnectionError("transient")
            return "recovered"

        result = await retry_with_backoff(
            _flaky,
            max_retries=3,
            initial_delay=0.0,
            backoff_multiplier=2.0,
            jitter=0.0,
        )

        assert result == "recovered"
        assert attempts == 3

    @pytest.mark.asyncio
    async def test_exhausts_retries_and_raises_last_exception(self):
        """When all attempts fail, the last exception is re-raised."""
        attempts = 0
        last_err: Exception | None = None

        async def _always_fail():
            nonlocal attempts, last_err
            attempts += 1
            err = ConnectionError(f"failure-{attempts}")
            last_err = err
            raise err

        with pytest.raises(ConnectionError) as exc_info:
            await retry_with_backoff(
                _always_fail,
                max_retries=2,
                initial_delay=0.0,
                backoff_multiplier=2.0,
                jitter=0.0,
            )

        assert attempts == 3  # initial + 2 retries
        assert "failure-3" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_max_retries_zero_means_no_retry(self):
        """max_retries=0 → only one attempt (the initial call)."""
        attempts = 0

        async def _fail():
            nonlocal attempts
            attempts += 1
            raise ValueError("nope")

        with pytest.raises(ValueError):
            await retry_with_backoff(
                _fail,
                max_retries=0,
                initial_delay=0.0,
                backoff_multiplier=2.0,
                jitter=0.0,
            )

        assert attempts == 1

    @pytest.mark.asyncio
    async def test_total_sleep_does_not_exceed_budget(self):
        """Sleeps should not exceed initial * (multiplier ** max_retries) plus jitter."""
        attempts = 0

        async def _fail():
            nonlocal attempts
            attempts += 1
            raise RuntimeError("boom")

        # max_retries=2 → sleeps after attempt 0 and attempt 1
        # = initial * 1 + initial * 2 = 3 * initial
        # With max_delay cap & jitter, total should be bounded
        initial = 0.1
        multiplier = 2.0
        max_retries = 2
        max_total = initial * (multiplier ** (max_retries)) * 1.5  # 50% headroom for jitter

        t0 = time.monotonic()
        with pytest.raises(RuntimeError):
            await retry_with_backoff(
                _fail,
                max_retries=max_retries,
                initial_delay=initial,
                backoff_multiplier=multiplier,
                jitter=0.1,
            )
        elapsed = time.monotonic() - t0

        assert attempts == max_retries + 1
        # Allow generous slack; the test is about catastrophic runaway, not micro-bench
        assert elapsed <= max_total + 1.0, f"elapsed {elapsed:.2f}s exceeds budget {max_total + 1.0:.2f}s"

    @pytest.mark.asyncio
    async def test_only_catches_retryable_exceptions(self):
        """Non-retryable exceptions are re-raised immediately."""
        attempts = 0

        async def _permanent():
            nonlocal attempts
            attempts += 1
            raise ValueError("permanent")

        with pytest.raises(ValueError):
            await retry_with_backoff(
                _permanent,
                max_retries=5,
                initial_delay=0.0,
                backoff_multiplier=2.0,
                jitter=0.0,
                retryable_exceptions=(ConnectionError,),
            )

        # Should have given up after the first attempt — no retries for ValueError
        assert attempts == 1


# ---------------------------------------------------------------------------
# BaseJob.execute
# ---------------------------------------------------------------------------


class _FakeSessionCtx:
    """Async context manager wrapper that returns a fixed session."""

    def __init__(self, session: AsyncMock):
        self._session = session

    async def __aenter__(self) -> AsyncMock:
        return self._session

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None


def _make_session_factory(session: AsyncMock):
    """Return an async context manager factory usable by BaseJob."""

    @asynccontextmanager
    async def _factory():
        yield session

    return _factory


class _StubJob(BaseJob):
    """Concrete BaseJob for testing."""

    name = "test-job"
    timeout_seconds = 5
    max_retries = 1
    backoff_multiplier = 2.0
    initial_delay = 0.0
    jitter = 0.0
    run_sleep: float = 0.0  # class attr — override in subclass to test timeout

    def __init__(self, session_factory, alerting=None, run_result=None, run_error=None):
        super().__init__(session_factory, alerting)
        self._run_result = run_result if run_result is not None else {"ok": True}
        self._run_error = run_error
        self.run_calls = 0

    async def run(self) -> dict:
        self.run_calls += 1
        if self.run_sleep > 0:
            await asyncio.sleep(self.run_sleep)
        if self._run_error is not None:
            raise self._run_error
        return self._run_result


def _make_fake_session(*, lock_acquired: bool = True, execution_id: int = 99):
    """Build a mock AsyncSession suitable for BaseJob.execute."""
    session = AsyncMock()

    # acquire_lock returns a JobLock-like object on success, None on failure
    if lock_acquired:
        session.execute = AsyncMock()  # used by acquire_lock SQL
    else:
        session.execute = AsyncMock()

    # create_execution returns a JobExecution-like object
    mock_execution = MagicMock()
    mock_execution.id = execution_id
    # create_execution in the actual CRUD commits and refreshes — no return value needed
    return session, mock_execution


def _patch_crud_on_session_module(monkeypatch, *, lock_acquired: bool = True, execution_id: int = 99):
    """Patch the CRUD classes that BaseJob imports.

    BaseJob imports ``JobExecutionCRUD`` and ``JobLockCRUD`` from
    ``packages.shared.crud.jobs``.  We replace them with callables
    that return pre-configured mock instances when called (BaseJob
    uses them as ``JobLockCRUD(session)`` / ``JobExecutionCRUD(session)``).
    """
    mock_execution = MagicMock(id=execution_id)
    mock_execution_crud = MagicMock()
    mock_execution_crud.create_execution = AsyncMock(return_value=mock_execution)
    mock_execution_crud.update_execution = AsyncMock()

    mock_lock_crud = MagicMock()
    if lock_acquired:
        mock_lock = MagicMock()
        mock_lock_crud.acquire_lock = AsyncMock(return_value=mock_lock)
    else:
        mock_lock_crud.acquire_lock = AsyncMock(return_value=None)
    mock_lock_crud.release_lock = AsyncMock()

    # Replace the class names with callables that return the mock instance.
    # BaseJob does ``JobLockCRUD(session)`` so we need a callable, not the
    # mock instance directly.
    monkeypatch.setattr(
        "app.cron.base.JobExecutionCRUD", lambda session: mock_execution_crud
    )
    monkeypatch.setattr(
        "app.cron.base.JobLockCRUD", lambda session: mock_lock_crud
    )

    return mock_execution_crud, mock_lock_crud


class TestBaseJobExecute:
    @pytest.mark.asyncio
    async def test_happy_path_writes_success_execution(self, monkeypatch):
        session = AsyncMock()
        session.commit = AsyncMock()
        session.refresh = AsyncMock()
        exec_crud, lock_crud = _patch_crud_on_session_module(monkeypatch, lock_acquired=True)

        alerting = MagicMock()
        alerting.send_failure_alert = AsyncMock()

        job = _StubJob(_make_session_factory(session), alerting=alerting)
        result = await job.execute()

        assert result == {"ok": True}
        assert job.run_calls == 1
        # Lock was acquired
        lock_crud.acquire_lock.assert_awaited_once()
        # Execution row created
        exec_crud.create_execution.assert_awaited_once()
        # Execution row finalised with success
        update_call = exec_crud.update_execution.call_args
        assert update_call.kwargs["status"] == "completed"
        # No alert sent
        alerting.send_failure_alert.assert_not_awaited()
        # Lock released
        lock_crud.release_lock.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_failure_writes_failed_execution_and_alerts(self, monkeypatch):
        session = AsyncMock()
        session.commit = AsyncMock()
        session.refresh = AsyncMock()
        exec_crud, lock_crud = _patch_crud_on_session_module(monkeypatch, lock_acquired=True)

        alerting = MagicMock()
        alerting.send_failure_alert = AsyncMock()

        job = _StubJob(
            _make_session_factory(session),
            alerting=alerting,
            run_error=ConnectionError("transient failure"),
        )
        with pytest.raises(RuntimeError) as exc_info:
            await job.execute()

        assert "transient failure" in str(exc_info.value)
        # Finalise with failed status
        update_call = exec_crud.update_execution.call_args
        assert update_call.kwargs["status"] == "failed"
        assert "transient failure" in (update_call.kwargs.get("error_message") or "")
        # Alert sent
        alerting.send_failure_alert.assert_awaited_once()
        # Lock still released on failure
        lock_crud.release_lock.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_skips_when_lock_held(self, monkeypatch):
        session = AsyncMock()
        session.commit = AsyncMock()
        session.refresh = AsyncMock()
        exec_crud, lock_crud = _patch_crud_on_session_module(monkeypatch, lock_acquired=False)

        alerting = MagicMock()
        alerting.send_failure_alert = AsyncMock()

        job = _StubJob(_make_session_factory(session), alerting=alerting)
        result = await job.execute()

        assert result.get("skipped") is True
        assert result.get("reason") == "lock_held"
        # Job body never ran
        assert job.run_calls == 0
        # No execution row created when lock not acquired
        exec_crud.create_execution.assert_not_awaited()
        # No alert
        alerting.send_failure_alert.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_honours_timeout(self, monkeypatch):
        session = AsyncMock()
        session.commit = AsyncMock()
        session.refresh = AsyncMock()
        exec_crud, lock_crud = _patch_crud_on_session_module(monkeypatch, lock_acquired=True)

        alerting = MagicMock()
        alerting.send_failure_alert = AsyncMock()

        # A job that sleeps longer than its timeout
        class _SlowJob(_StubJob):
            timeout_seconds = 0.1
            run_sleep = 1.0

        job = _SlowJob(_make_session_factory(session), alerting=alerting)
        with pytest.raises(RuntimeError):
            await job.execute()

        # Finalised as failed (timeout is a failure)
        update_call = exec_crud.update_execution.call_args
        assert update_call.kwargs["status"] == "failed"
        # Lock still released
        lock_crud.release_lock.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_lock_released_on_failure(self, monkeypatch):
        session = AsyncMock()
        session.commit = AsyncMock()
        session.refresh = AsyncMock()
        exec_crud, lock_crud = _patch_crud_on_session_module(monkeypatch, lock_acquired=True)

        alerting = MagicMock()
        alerting.send_failure_alert = AsyncMock()

        job = _StubJob(
            _make_session_factory(session),
            alerting=alerting,
            run_error=ValueError("nope"),
        )
        with pytest.raises(RuntimeError):
            await job.execute()

        # Even though execution failed, the lock must be released
        lock_crud.release_lock.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_alerting_failure_does_not_crash(self, monkeypatch):
        """If send_failure_alert raises, the job still finalises cleanly."""
        session = AsyncMock()
        session.commit = AsyncMock()
        session.refresh = AsyncMock()
        exec_crud, lock_crud = _patch_crud_on_session_module(monkeypatch, lock_acquired=True)

        alerting = MagicMock()
        alerting.send_failure_alert = AsyncMock(side_effect=RuntimeError("webhook down"))

        job = _StubJob(
            _make_session_factory(session),
            alerting=alerting,
            run_error=ConnectionError("transient"),
        )
        with pytest.raises(RuntimeError) as exc_info:
            await job.execute()

        # The outer RuntimeError is from BaseJob wrapping the inner error
        assert "transient" in str(exc_info.value).lower() or "transient" in str(
            exec_crud.update_execution.call_args.kwargs.get("error_message", "")
        )
        # Execution still finalised as failed
        update_call = exec_crud.update_execution.call_args
        assert update_call.kwargs["status"] == "failed"
        # Lock released
        lock_crud.release_lock.assert_awaited_once()
