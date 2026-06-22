"""Unit tests for ``packages.shared.crud.model_versions`` (weighted-tip model).

These tests pin the CRUD-level contract of the model-version store that
backs the new ``weighted_tip`` scikit-learn heuristic, against a real
PostgreSQL instance.  They share the same lifecycle model as
``tests/unit/test_generation_progress_crud.py`` and
``tests/unit/test_tips_crud.py`` (Podman ``postgres:16-alpine`` +
``Base.metadata.create_all``), so they are the fast-regression net for
the persistence layer before the retrain job (Subtask 3) lands.

Contract:

1. ``create_model_version`` inserts the version row *and* its coefficient
   rows in one transaction, returning the new :class:`ModelVersion`.
2. With ``set_active=True`` it atomically deactivates every other row
   with the same ``model_name`` and marks the new one active.
3. ``get_active_model_version`` / ``get_active_coefficients`` read the
   currently-active version (or ``None`` when there is none).
4. ``next_version_number`` returns ``max(version) + 1`` for a model name,
   or ``1`` when no versions exist yet.

Skips gracefully when ``podman`` is not on ``PATH``.
"""
from __future__ import annotations

import shutil
import socket
import subprocess
import time
import uuid
from typing import AsyncIterator, Iterator

import pytest
import pytest_asyncio
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from packages.shared.crud.model_versions import (
    create_model_version,
    get_active_coefficients,
    get_active_model_version,
    get_model_coefficients,
    next_version_number,
)
from packages.shared.models import Base, ModelCoefficient, ModelVersion


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
_CONTAINER_NAME_PREFIX = "wimt-pg-test-mv-"
_STARTUP_TIMEOUT_S = 60.0
_POLL_INTERVAL_S = 0.25


def _free_port() -> int:
    """Return a free TCP port on localhost."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_for_postgres_ready(container_name: str, timeout: float) -> None:
    """Block until the Postgres container reports healthy."""
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

    A throwaway engine bootstraps the schema via
    ``Base.metadata.create_all``; the runtime engine uses ``NullPool``
    so each session checks out a fresh asyncpg connection (avoids the
    cross-connection ``another operation is in progress`` error that the
    default pool can trigger under pytest-asyncio).
    """
    bootstrap_eng = create_async_engine(pg_container, future=True)
    async with bootstrap_eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await bootstrap_eng.dispose()

    eng = create_async_engine(pg_container, future=True, poolclass=NullPool)
    try:
        yield eng
    finally:
        await eng.dispose()


@pytest_asyncio.fixture
async def session_factory(
    engine,
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """Per-test session factory; truncates both tables before each test."""
    factory = async_sessionmaker(engine, expire_on_commit=False)

    # Delete children first (FK dependency), then parents.
    async with factory() as session:
        await session.execute(delete(ModelCoefficient))
        await session.execute(delete(ModelVersion))
        await session.commit()

    yield factory

    async with factory() as session:
        await session.execute(delete(ModelCoefficient))
        await session.execute(delete(ModelVersion))
        await session.commit()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestCreateModelVersion:
    """``create_model_version`` persists the version and its coefficients."""

    async def test_persists_version_and_coefficients(self, session_factory):
        async with session_factory() as session:
            version = await create_model_version(
                session,
                model_name="weighted_tip",
                version=1,
                intercept=0.5,
                training_rows=1000,
                metrics={"r2": 0.65, "mae": 1.2},
                coefficients={"elo": 0.3, "form": 0.4, "home_advantage": 0.2},
                set_active=False,
            )

        assert version.id is not None
        assert version.model_name == "weighted_tip"
        assert version.version == 1
        assert version.intercept == 0.5
        assert version.training_rows == 1000
        assert version.metrics == {"r2": 0.65, "mae": 1.2}
        assert version.is_active is False

        # Coefficients persisted against the new version id.
        async with session_factory() as session:
            coeffs = await get_model_coefficients(session, version.id)

        assert len(coeffs) == 3
        feature_to_coef = {c.feature_name: c.coefficient for c in coeffs}
        assert feature_to_coef == {
            "elo": 0.3,
            "form": 0.4,
            "home_advantage": 0.2,
        }
        for c in coeffs:
            assert c.model_version_id == version.id

    async def test_empty_coefficients_is_allowed(self, session_factory):
        async with session_factory() as session:
            version = await create_model_version(
                session,
                model_name="weighted_tip",
                version=1,
                intercept=0.0,
                training_rows=0,
                metrics=None,
                coefficients={},
                set_active=False,
            )

        async with session_factory() as session:
            coeffs = await get_model_coefficients(session, version.id)
        assert coeffs == []

    async def test_set_active_true_marks_new_version_active(self, session_factory):
        async with session_factory() as session:
            version = await create_model_version(
                session,
                model_name="weighted_tip",
                version=1,
                intercept=0.0,
                training_rows=0,
                metrics={},
                coefficients={"elo": 1.0},
                set_active=True,
            )

        async with session_factory() as session:
            active = await get_active_model_version(session, "weighted_tip")
        assert active is not None
        assert active.id == version.id

    async def test_set_active_deactivates_previous_active_of_same_name(
        self, session_factory
    ):
        # v1 promoted to active.
        async with session_factory() as session:
            v1 = await create_model_version(
                session,
                model_name="weighted_tip",
                version=1,
                intercept=0.1,
                training_rows=100,
                metrics={},
                coefficients={"elo": 0.5},
                set_active=True,
            )

        # v2 promoted -> v1 must be deactivated.
        async with session_factory() as session:
            v2 = await create_model_version(
                session,
                model_name="weighted_tip",
                version=2,
                intercept=0.2,
                training_rows=200,
                metrics={},
                coefficients={"elo": 0.6},
                set_active=True,
            )

        async with session_factory() as session:
            active = await get_active_model_version(session, "weighted_tip")
        assert active is not None
        assert active.id == v2.id
        assert active.version == 2

        # v1 is no longer active.
        async with session_factory() as session:
            v1_row = (
                await session.execute(
                    select(ModelVersion).where(ModelVersion.id == v1.id)
                )
            ).scalar_one()
        assert v1_row.is_active is False

    async def test_set_active_false_does_not_disturb_existing_active(
        self, session_factory
    ):
        async with session_factory() as session:
            v1 = await create_model_version(
                session,
                model_name="weighted_tip",
                version=1,
                intercept=0.1,
                training_rows=100,
                metrics={},
                coefficients={"elo": 0.5},
                set_active=True,
            )

        async with session_factory() as session:
            await create_model_version(
                session,
                model_name="weighted_tip",
                version=2,
                intercept=0.2,
                training_rows=200,
                metrics={},
                coefficients={"elo": 0.6},
                set_active=False,
            )

        # v1 is still the active one.
        async with session_factory() as session:
            active = await get_active_model_version(session, "weighted_tip")
        assert active is not None
        assert active.id == v1.id

    async def test_activation_is_isolated_per_model_name(self, session_factory):
        async with session_factory() as session:
            await create_model_version(
                session,
                model_name="other_model",
                version=1,
                intercept=0.0,
                training_rows=0,
                metrics={},
                coefficients={"a": 1.0},
                set_active=True,
            )
        async with session_factory() as session:
            await create_model_version(
                session,
                model_name="weighted_tip",
                version=1,
                intercept=0.0,
                training_rows=0,
                metrics={},
                coefficients={"a": 1.0},
                set_active=True,
            )

        async with session_factory() as session:
            other = await get_active_model_version(session, "other_model")
            weighted = await get_active_model_version(session, "weighted_tip")
        assert other is not None and other.model_name == "other_model"
        assert weighted is not None and weighted.model_name == "weighted_tip"


class TestGetActive:
    """``get_active_*`` read the active version or ``None``."""

    async def test_get_active_model_version_returns_none_when_no_active(
        self, session_factory
    ):
        async with session_factory() as session:
            active = await get_active_model_version(session, "weighted_tip")
        assert active is None

    async def test_get_active_coefficients_returns_intercept_and_map(
        self, session_factory
    ):
        async with session_factory() as session:
            await create_model_version(
                session,
                model_name="weighted_tip",
                version=1,
                intercept=1.5,
                training_rows=10,
                metrics={"r2": 0.5},
                coefficients={"elo": 0.3, "form": 0.7},
                set_active=True,
            )

        async with session_factory() as session:
            result = await get_active_coefficients(session, "weighted_tip")

        assert result is not None
        intercept, coeffs = result
        assert intercept == 1.5
        assert coeffs == {"elo": 0.3, "form": 0.7}

    async def test_get_active_coefficients_returns_none_when_no_active(
        self, session_factory
    ):
        async with session_factory() as session:
            result = await get_active_coefficients(session, "weighted_tip")
        assert result is None


class TestNextVersionNumber:
    """``next_version_number`` returns ``max(version) + 1`` (or 1)."""

    async def test_returns_one_when_no_versions(self, session_factory):
        async with session_factory() as session:
            n = await next_version_number(session, "weighted_tip")
        assert n == 1

    async def test_increments_max_version(self, session_factory):
        async with session_factory() as session:
            await create_model_version(
                session,
                model_name="weighted_tip",
                version=1,
                intercept=0.0,
                training_rows=0,
                metrics={},
                coefficients={"a": 1.0},
                set_active=False,
            )
        async with session_factory() as session:
            await create_model_version(
                session,
                model_name="weighted_tip",
                version=3,
                intercept=0.0,
                training_rows=0,
                metrics={},
                coefficients={"a": 1.0},
                set_active=False,
            )

        async with session_factory() as session:
            n = await next_version_number(session, "weighted_tip")
        assert n == 4

    async def test_isolated_per_model_name(self, session_factory):
        async with session_factory() as session:
            await create_model_version(
                session,
                model_name="other_model",
                version=5,
                intercept=0.0,
                training_rows=0,
                metrics={},
                coefficients={"a": 1.0},
                set_active=False,
            )

        async with session_factory() as session:
            n = await next_version_number(session, "weighted_tip")
        assert n == 1
