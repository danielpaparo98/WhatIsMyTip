"""Unit tests for ``GenerationProgressCRUD.get_by_operation`` (Issue #3).

These tests pin the **CRUD-level contract** of
:meth:`packages.shared.crud.generation_progress.GenerationProgressCRUD.get_by_operation`
against a real PostgreSQL instance.  They are the fast-regression net
that complements the slow live-sweep harness at
``tests/integration/test_curl_sweep_harness.py`` and the
service-mocked HTTP contract tests at
``tests/unit/test_app_api_admin.py::TestAdminHistoricRefreshProgress``.

Contract (R4 follow-up):

1. When at least one row with ``status == 'in_progress'`` exists for
   the ``(operation_type, season)`` pair, the most recently started
   such row is returned.
2. Otherwise, when at least one row with ``status IN ('completed',
   'failed')`` exists, the most recently started such row is returned.
3. Otherwise, ``None`` is returned.

Original regression (commit ``103a345``) is also pinned: the function
must not raise :class:`sqlalchemy.exc.MultipleResultsFound` when the
table has 2+ rows for the same ``(operation_type, season)`` pair.

Container management
--------------------

This project uses **Podman** (not Docker) for local dev and CI.  The
``pg_container`` fixture spawns a one-shot ``postgres:16-alpine``
container via ``podman run`` and tears it down on module exit.  It is
the same lifecycle model that ``testcontainers`` provides, but
Podman-native so the test never reaches for a Docker daemon.

The fixture is **module-scoped** (one Postgres per pytest process);
individual tests use **function-scoped** table-truncation, so the
suite is fast after the first container start (<1s per test).

Skips gracefully when ``podman`` is not on ``PATH``.
"""
from __future__ import annotations

import shutil
import socket
import subprocess
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import AsyncIterator, Iterator

import pytest
import pytest_asyncio
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from packages.shared.crud.generation_progress import GenerationProgressCRUD
from packages.shared.models import Base, GenerationProgress


# Mark all tests in this module with ``@pytest.mark.postgres`` so the
# standard unit-test run can be filtered on machines without Podman.
pytestmark = pytest.mark.postgres


# ---------------------------------------------------------------------------
# Podman availability check
# ---------------------------------------------------------------------------


def _podman_unavailable_reason() -> str | None:
    """Return a skip reason if Podman is not usable, else ``None``."""
    if shutil.which("podman") is None:
        return "podman is not on PATH"
    try:
        result = subprocess.run(
            ["podman", "info", "--format", "{{.Host.OS}}"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        return f"podman info failed: {exc}"
    if result.returncode != 0:
        return f"podman daemon is not reachable: {result.stderr.strip() or 'unknown error'}"
    return None


_SKIP_REASON = _podman_unavailable_reason()
if _SKIP_REASON is not None:
    pytest.skip(_SKIP_REASON, allow_module_level=True)


# ---------------------------------------------------------------------------
# Module-scope Postgres container (Podman)
# ---------------------------------------------------------------------------


_POSTGRES_IMAGE = "docker.io/library/postgres:16-alpine"
_CONTAINER_NAME_PREFIX = "wimt-pg-test-"
_STARTUP_TIMEOUT_S = 60.0
_POLL_INTERVAL_S = 0.25


def _free_port() -> int:
    """Return a free TCP port on localhost."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_for_postgres_ready(
    container_name: str, timeout: float
) -> None:
    """Block until the Postgres container reports healthy.

    Uses ``podman exec pg_isready`` so we wait for Postgres to accept
    connections, not just for the TCP port to be open (the port comes
    up first while Postgres is still initialising).
    """
    deadline = time.monotonic() + timeout
    last_err: Exception | None = None
    while time.monotonic() < deadline:
        proc = subprocess.run(
            ["podman", "exec", container_name, "pg_isready", "-U", "postgres"],
            capture_output=True,
            text=True,
        )
        if proc.returncode == 0:
            return
        last_err = RuntimeError(
            f"pg_isready rc={proc.returncode}: {proc.stderr.strip() or proc.stdout.strip()}"
        )
        time.sleep(_POLL_INTERVAL_S)
    raise RuntimeError(
        f"Postgres in {container_name} did not become ready "
        f"within {timeout:.1f}s: {last_err}"
    )


@pytest.fixture(scope="module")
def pg_container() -> Iterator[str]:
    """Spawn a one-shot ``postgres:16-alpine`` container via ``podman run``.

    Yields the asyncpg connection URL of the form
    ``postgresql+asyncpg://postgres:test@127.0.0.1:<port>/postgres``.
    Tears the container down on module exit.
    """
    container_name = _CONTAINER_NAME_PREFIX + uuid.uuid4().hex[:12]
    port = _free_port()
    user = "postgres"
    password = "test"
    db = "postgres"

    run_cmd = [
        "podman", "run",
        "-d",
        "--rm",
        "--name", container_name,
        "-p", f"127.0.0.1:{port}:5432",
        "-e", f"POSTGRES_USER={user}",
        "-e", f"POSTGRES_PASSWORD={password}",
        "-e", f"POSTGRES_DB={db}",
        _POSTGRES_IMAGE,
    ]
    proc = subprocess.run(run_cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(
            f"podman run failed (rc={proc.returncode}): {proc.stderr.strip()}"
        )

    try:
        _wait_for_postgres_ready(container_name, _STARTUP_TIMEOUT_S)
        url = (
            f"postgresql+asyncpg://{user}:{password}"
            f"@127.0.0.1:{port}/{db}"
        )
        yield url
    finally:
        subprocess.run(
            ["podman", "rm", "-f", container_name],
            capture_output=True,
            text=True,
        )


@pytest_asyncio.fixture(scope="module")
async def engine(pg_container: str):
    """Async SQLAlchemy engine bound to the testcontainer.

    Two engines are used: a short-lived one to create the schema, then
    the runtime engine (with ``NullPool`` so each session checks out a
    fresh asyncpg connection — avoids the cross-connection
    ``another operation is in progress`` error that the default
    ``AsyncAdaptedQueuePool`` can trigger under pytest-asyncio).
    """
    # 1. Bootstrap the schema on a throwaway engine.
    bootstrap_eng = create_async_engine(pg_container, future=True)
    async with bootstrap_eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await bootstrap_eng.dispose()

    # 2. Runtime engine with NullPool — one connection per checkout.
    eng = create_async_engine(pg_container, future=True, poolclass=NullPool)
    try:
        yield eng
    finally:
        await eng.dispose()


@pytest_asyncio.fixture
async def session_factory(
    engine,
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """Per-test session factory; truncates the table before each test."""
    factory = async_sessionmaker(engine, expire_on_commit=False)

    # Truncate the table at the start of each test using a session
    # (rather than ``engine.begin()`` directly) so asyncpg's per-
    # connection state machine is not stressed by a borrowed
    # connection outside the session lifecycle.
    async with factory() as session:
        await session.execute(delete(GenerationProgress))
        await session.commit()

    yield factory

    # Final cleanup so module teardown leaves an empty table.
    async with factory() as session:
        await session.execute(delete(GenerationProgress))
        await session.commit()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _dt(base: datetime, **kwargs: int) -> datetime:
    """Return ``base + timedelta(**kwargs)`` (in UTC)."""
    return base + timedelta(**kwargs)


async def _insert(
    factory: async_sessionmaker[AsyncSession],
    *,
    operation_type: str,
    status: str,
    started_at: datetime,
    season: int | None = None,
) -> GenerationProgress:
    """Insert one :class:`GenerationProgress` row and return it."""
    row = GenerationProgress(
        operation_type=operation_type,
        season=season,
        total_items=10,
        completed_items=0 if status == "in_progress" else 10,
        status=status,
        started_at=started_at,
        completed_at=None if status == "in_progress" else started_at + timedelta(minutes=30),
    )
    async with factory() as session:
        session.add(row)
        await session.commit()
        await session.refresh(row)
        return row


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestGetByOperationInProgress:
    """Contract branch 1: an in-flight row wins over older completed rows."""

    async def test_returns_in_progress_row_when_present_alongside_completed(
        self, session_factory
    ):
        # Two completed rows first (older + newer) and one in_progress
        # started between them.  The in_progress row must be returned.
        t0 = datetime(2025, 1, 1, 0, 0, tzinfo=timezone.utc)
        await _insert(
            session_factory,
            operation_type="historic_refresh",
            status="completed",
            started_at=_dt(t0, hours=0),
        )
        await _insert(
            session_factory,
            operation_type="historic_refresh",
            status="in_progress",
            started_at=_dt(t0, hours=1),
        )
        # Newest by started_at, but still in_progress should win.
        await _insert(
            session_factory,
            operation_type="historic_refresh",
            status="in_progress",
            started_at=_dt(t0, hours=2),
        )
        await _insert(
            session_factory,
            operation_type="historic_refresh",
            status="completed",
            started_at=_dt(t0, hours=3),
        )

        async with session_factory() as session:
            result = await GenerationProgressCRUD.get_by_operation(
                session, operation_type="historic_refresh"
            )

        assert result is not None
        assert result.status == "in_progress"
        assert result.started_at == _dt(t0, hours=2), (
            "When multiple in_progress rows exist, the most-recent one "
            "by started_at must be returned"
        )

    async def test_returns_only_in_progress_row(self, session_factory):
        t0 = datetime(2025, 1, 1, 0, 0, tzinfo=timezone.utc)
        await _insert(
            session_factory,
            operation_type="historic_refresh",
            status="in_progress",
            started_at=_dt(t0, hours=0),
        )

        async with session_factory() as session:
            result = await GenerationProgressCRUD.get_by_operation(
                session, operation_type="historic_refresh"
            )

        assert result is not None
        assert result.status == "in_progress"


class TestGetByOperationCompletedFallback:
    """Contract branch 2: no in-flight → return the most-recent finished row."""

    async def test_returns_most_recent_failed_when_no_in_progress(
        self, session_factory
    ):
        t0 = datetime(2025, 1, 1, 0, 0, tzinfo=timezone.utc)
        await _insert(
            session_factory,
            operation_type="historic_refresh",
            status="completed",
            started_at=_dt(t0, hours=0),
        )
        await _insert(
            session_factory,
            operation_type="historic_refresh",
            status="completed",
            started_at=_dt(t0, hours=5),
        )
        # Newer failed row must be preferred over an older completed row.
        await _insert(
            session_factory,
            operation_type="historic_refresh",
            status="failed",
            started_at=_dt(t0, hours=8),
        )

        async with session_factory() as session:
            result = await GenerationProgressCRUD.get_by_operation(
                session, operation_type="historic_refresh"
            )

        assert result is not None
        assert result.status == "failed"
        assert result.started_at == _dt(t0, hours=8)

    async def test_returns_most_recent_completed_with_only_completed(
        self, session_factory
    ):
        t0 = datetime(2025, 1, 1, 0, 0, tzinfo=timezone.utc)
        await _insert(
            session_factory,
            operation_type="historic_refresh",
            status="completed",
            started_at=_dt(t0, hours=0),
        )
        await _insert(
            session_factory,
            operation_type="historic_refresh",
            status="completed",
            started_at=_dt(t0, hours=1),
        )
        await _insert(
            session_factory,
            operation_type="historic_refresh",
            status="completed",
            started_at=_dt(t0, hours=2),
        )

        async with session_factory() as session:
            result = await GenerationProgressCRUD.get_by_operation(
                session, operation_type="historic_refresh"
            )

        assert result is not None
        assert result.status == "completed"
        assert result.started_at == _dt(t0, hours=2)

    async def test_pending_rows_are_ignored(self, session_factory):
        """``pending`` rows are not yet started; they must be ignored."""
        t0 = datetime(2025, 1, 1, 0, 0, tzinfo=timezone.utc)
        await _insert(
            session_factory,
            operation_type="historic_refresh",
            status="pending",
            started_at=_dt(t0, hours=0),
        )
        await _insert(
            session_factory,
            operation_type="historic_refresh",
            status="completed",
            started_at=_dt(t0, hours=-1),  # older than the pending row
        )

        async with session_factory() as session:
            result = await GenerationProgressCRUD.get_by_operation(
                session, operation_type="historic_refresh"
            )

        assert result is not None
        assert result.status == "completed"


class TestGetByOperationEmpty:
    """Contract branch 3: no rows at all → ``None``."""

    async def test_returns_none_when_no_rows(self, session_factory):
        async with session_factory() as session:
            result = await GenerationProgressCRUD.get_by_operation(
                session, operation_type="historic_refresh"
            )
        assert result is None

    async def test_returns_none_when_only_pending(self, session_factory):
        """``pending`` rows are ignored; a table of only pending rows → ``None``."""
        t0 = datetime(2025, 1, 1, 0, 0, tzinfo=timezone.utc)
        await _insert(
            session_factory,
            operation_type="historic_refresh",
            status="pending",
            started_at=_dt(t0, hours=0),
        )
        await _insert(
            session_factory,
            operation_type="historic_refresh",
            status="pending",
            started_at=_dt(t0, hours=1),
        )

        async with session_factory() as session:
            result = await GenerationProgressCRUD.get_by_operation(
                session, operation_type="historic_refresh"
            )
        assert result is None


class TestGetByOperationSeasonFilter:
    """The ``season`` filter scopes the result to a single season."""

    async def test_season_filter_isolates_results(self, session_factory):
        t0 = datetime(2025, 1, 1, 0, 0, tzinfo=timezone.utc)
        await _insert(
            session_factory,
            operation_type="historic_refresh",
            status="completed",
            started_at=_dt(t0, hours=0),
            season=2024,
        )
        await _insert(
            session_factory,
            operation_type="historic_refresh",
            status="in_progress",
            started_at=_dt(t0, hours=1),
            season=2025,
        )

        async with session_factory() as session:
            r_2024 = await GenerationProgressCRUD.get_by_operation(
                session, operation_type="historic_refresh", season=2024
            )
            r_2025 = await GenerationProgressCRUD.get_by_operation(
                session, operation_type="historic_refresh", season=2025
            )
            r_none = await GenerationProgressCRUD.get_by_operation(
                session, operation_type="historic_refresh", season=None
            )

        assert r_2024 is not None and r_2024.status == "completed"
        assert r_2025 is not None and r_2025.status == "in_progress"
        # ``season=None`` matches only rows where ``season IS NULL``,
        # of which there are none here.
        assert r_none is None


class TestGetByOperationNoMultipleResultsFound:
    """Original regression pinned (commit ``103a345``).

    With the in-flight/completed distinction, we now run **two**
    single-row queries (``.scalars().first()``) instead of one
    multi-row ``.scalar_one_or_none()`` query.  Either approach must
    not raise :class:`sqlalchemy.exc.MultipleResultsFound` when the
    table has 2+ matching rows.
    """

    async def test_many_rows_do_not_raise(self, session_factory):
        t0 = datetime(2025, 1, 1, 0, 0, tzinfo=timezone.utc)
        for i in range(5):
            await _insert(
                session_factory,
                operation_type="historic_refresh",
                status="completed",
                started_at=_dt(t0, hours=i),
            )

        async with session_factory() as session:
            # Must not raise; must return the most recent row.
            result = await GenerationProgressCRUD.get_by_operation(
                session, operation_type="historic_refresh"
            )

        assert result is not None
        assert result.started_at == _dt(t0, hours=4)
