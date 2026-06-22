"""Real round-trip test for migration 0005 (model_versions & model_coefficients).

This is the authoritative behaviour test for the weighted-tip schema
migration.  Unlike ``tests/unit/test_alembic_migrations.py`` (which only
inspects revision ids and SQL strings statically), this test applies the
migration to a live PostgreSQL instance via the ``alembic`` CLI and
asserts the real effects:

1. ``alembic upgrade head`` creates ``model_versions`` and
   ``model_coefficients`` with their columns, unique constraints and
   indexes.
2. ``alembic downgrade 0004_canonical_team_names`` removes both tables.
3. The stored heuristic value ``high_risk_high_reward`` is renamed to
   ``weighted_tip`` in ``tips`` and ``backtest_results`` on upgrade, and
   reverted on downgrade (seed -> upgrade -> assert -> downgrade ->
   assert).

Uses the same self-contained Podman ``postgres:16-alpine`` lifecycle as
``tests/unit/test_generation_progress_crud.py`` and the same subprocess
``alembic`` invocation as
``tests/integration/test_alembic_round_trip.py``.

NOTE on ``alembic_version`` width: alembic creates its version table
with ``version_num VARCHAR(32)``, but this repo's revision ids are
longer than that (e.g. ``0003_job_executions_metrics_index`` is 34
chars, ``0005_model_versions_coefficients`` is 33).  This is now fixed
by the env.py bootstrap guard (``_ensure_alembic_version_table_width``,
added together with migration 0006), so this fixture no longer
pre-creates a widened ``alembic_version``: the from-scratch upgrade
relies on the guard, exactly like a real fresh database.

Skips gracefully when ``podman`` is not on ``PATH``.
"""
from __future__ import annotations

import os
import shutil
import socket
import subprocess
import sys
import time
import uuid
from typing import AsyncIterator, Iterator

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool


# Mark all tests in this module with ``@pytest.mark.postgres`` so the
# standard unit-test run can be filtered on machines without Podman.
pytestmark = pytest.mark.postgres


# Resolve the backend directory (where alembic.ini lives).
_BACKEND_DIR = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)


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
_CONTAINER_NAME_PREFIX = "wimt-pg-mig0005-"
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
def pg_container() -> Iterator[tuple[str, str]]:
    """Spawn a one-shot ``postgres:16-alpine`` container.

    Yields ``(sync_dsn, async_dsn)`` where ``sync_dsn`` is the
    ``postgresql://`` URL alembic/psycopg2 expects and ``async_dsn`` is
    the ``postgresql+asyncpg://`` URL the introspection engine uses.
    """
    container_name = _CONTAINER_NAME_PREFIX + uuid.uuid4().hex[:12]
    port = _free_port()
    user, password, db = "postgres", "test", "postgres"

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
        sync_dsn = f"postgresql://{user}:{password}@127.0.0.1:{port}/{db}"
        async_dsn = f"postgresql+asyncpg://{user}:{password}@127.0.0.1:{port}/{db}"
        yield sync_dsn, async_dsn
    finally:
        subprocess.run(
            ["podman", "rm", "-f", container_name],
            capture_output=True,
            text=True,
        )


def _run_alembic(dsn: str, *args: str) -> subprocess.CompletedProcess:
    """Run an ``alembic`` command in the backend dir against ``dsn``."""
    cmd = [sys.executable, "-m", "alembic", *args]
    env = {**os.environ, "DATABASE_URL": dsn}
    return subprocess.run(
        cmd,
        cwd=_BACKEND_DIR,
        capture_output=True,
        text=True,
        env=env,
        timeout=120,
    )


@pytest_asyncio.fixture
async def _fresh_db(pg_container) -> AsyncIterator[tuple[str, str]]:
    """Reset the DB to a completely empty public schema.

    Each test starts from a clean public schema.  We deliberately do NOT
    pre-create ``alembic_version``: the from-scratch upgrade now relies on
    the env.py bootstrap guard (``_ensure_alembic_version_table_width``,
    added together with migration 0006) to widen/create the version column,
    exactly as a real fresh database would.  Yields ``(sync_dsn, async_dsn)``.
    """
    _, async_dsn = pg_container
    engine = create_async_engine(async_dsn, poolclass=NullPool)
    try:
        async with engine.begin() as conn:
            await conn.execute(text("DROP SCHEMA IF EXISTS public CASCADE"))
            await conn.execute(text("CREATE SCHEMA public"))
    finally:
        await engine.dispose()
    yield pg_container


# ---------------------------------------------------------------------------
# Introspection helpers
# ---------------------------------------------------------------------------


async def _table_columns(async_dsn: str, table: str) -> dict[str, str]:
    """Return ``{column_name: data_type}`` for ``table``."""
    engine = create_async_engine(async_dsn, poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            result = await conn.execute(
                text(
                    "SELECT column_name, data_type FROM information_schema.columns "
                    "WHERE table_schema = 'public' AND table_name = :t"
                ),
                {"t": table},
            )
            return {row[0]: row[1] for row in result.fetchall()}
    finally:
        await engine.dispose()


async def _existing_tables(async_dsn: str) -> set[str]:
    """Return the set of table names in the public schema."""
    engine = create_async_engine(async_dsn, poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            result = await conn.execute(
                text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
            )
            return {row[0] for row in result.fetchall()}
    finally:
        await engine.dispose()


async def _index_names(async_dsn: str, table: str) -> set[str]:
    """Return the set of index names on ``table``."""
    engine = create_async_engine(async_dsn, poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            result = await conn.execute(
                text(
                    "SELECT indexname FROM pg_indexes "
                    "WHERE schemaname = 'public' AND tablename = :t"
                ),
                {"t": table},
            )
            return {row[0] for row in result.fetchall()}
    finally:
        await engine.dispose()


async def _constraint_names(async_dsn: str, table: str) -> set[str]:
    """Return the set of constraint names on ``table``.

    Uses a sub-select on ``pg_class.oid`` rather than a ``::regclass``
    cast because asyncpg's bind-parameter lexer chokes on the ``::`` of
    ``:t::regclass``.
    """
    engine = create_async_engine(async_dsn, poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            result = await conn.execute(
                text(
                    "SELECT conname FROM pg_constraint WHERE conrelid = "
                    "(SELECT oid FROM pg_class WHERE relname = :t)"
                ),
                {"t": table},
            )
            return {row[0] for row in result.fetchall()}
    finally:
        await engine.dispose()


async def _exec(async_dsn: str, sql: str, params: dict | None = None) -> None:
    """Execute a raw SQL statement."""
    engine = create_async_engine(async_dsn, poolclass=NullPool)
    try:
        async with engine.begin() as conn:
            await conn.execute(text(sql), params or {})
    finally:
        await engine.dispose()


async def _scalar(async_dsn: str, sql: str, params: dict | None = None):
    """Return the first column of the first row of ``sql``."""
    engine = create_async_engine(async_dsn, poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            result = await conn.execute(text(sql), params or {})
            return result.scalar()
    finally:
        await engine.dispose()


# ---------------------------------------------------------------------------
# Tests: schema
# ---------------------------------------------------------------------------


class TestUpgradeCreatesTables:
    """``upgrade head`` creates the two tables with the right shape."""

    async def test_creates_model_versions_table(self, _fresh_db):
        sync_dsn, async_dsn = _fresh_db
        result = _run_alembic(sync_dsn, "upgrade", "head")
        assert result.returncode == 0, (
            f"upgrade head failed:\n{result.stdout}\n{result.stderr}"
        )

        tables = await _existing_tables(async_dsn)
        assert "model_versions" in tables

        cols = await _table_columns(async_dsn, "model_versions")
        for expected in [
            "id",
            "model_name",
            "version",
            "intercept",
            "trained_at",
            "training_rows",
            "metrics",
            "is_active",
            "created_at",
        ]:
            assert expected in cols, f"model_versions missing column {expected}"

        # intercept/training_rows are double precision; metrics is JSON.
        assert cols["intercept"] == "double precision"
        assert cols["training_rows"] == "integer"
        assert cols["metrics"] == "jsonb"
        assert cols["is_active"] == "boolean"

    async def test_creates_model_coefficients_table(self, _fresh_db):
        sync_dsn, async_dsn = _fresh_db
        assert _run_alembic(sync_dsn, "upgrade", "head").returncode == 0

        tables = await _existing_tables(async_dsn)
        assert "model_coefficients" in tables

        cols = await _table_columns(async_dsn, "model_coefficients")
        for expected in [
            "id",
            "model_version_id",
            "feature_name",
            "coefficient",
            "created_at",
        ]:
            assert expected in cols, f"model_coefficients missing column {expected}"
        assert cols["coefficient"] == "double precision"

    async def test_indexes_and_constraints_exist(self, _fresh_db):
        sync_dsn, async_dsn = _fresh_db
        assert _run_alembic(sync_dsn, "upgrade", "head").returncode == 0

        mv_indexes = await _index_names(async_dsn, "model_versions")
        assert "ix_model_versions_model_active" in mv_indexes

        mv_constraints = await _constraint_names(async_dsn, "model_versions")
        assert "uq_model_versions_name_version" in mv_constraints

        mc_indexes = await _index_names(async_dsn, "model_coefficients")
        assert "ix_model_coefficients_version" in mc_indexes

        mc_constraints = await _constraint_names(async_dsn, "model_coefficients")
        assert "uq_model_coefficients_version_feature" in mc_constraints
        # The FK constraint (ON DELETE CASCADE) is present.
        fk_constraints = {
            c for c in mc_constraints if c.startswith("model_coefficients_")
        }
        assert len(fk_constraints) >= 1, (
            "expected FK on model_coefficients.model_version_id"
        )


class TestDowngradeDropsTables:
    """``downgrade 0004`` removes both tables."""

    async def test_downgrade_to_0004_removes_both_tables(self, _fresh_db):
        sync_dsn, async_dsn = _fresh_db
        assert _run_alembic(sync_dsn, "upgrade", "head").returncode == 0

        result = _run_alembic(sync_dsn, "downgrade", "0004_canonical_team_names")
        assert result.returncode == 0, (
            f"downgrade to 0004 failed:\n{result.stdout}\n{result.stderr}"
        )

        tables = await _existing_tables(async_dsn)
        assert "model_versions" not in tables
        assert "model_coefficients" not in tables


# ---------------------------------------------------------------------------
# Tests: heuristic data rename
# ---------------------------------------------------------------------------


class TestHeuristicDataRename:
    """The stored heuristic value is renamed on upgrade and reverted on
    downgrade, in both ``tips`` and ``backtest_results``."""

    async def test_upgrade_renames_high_risk_high_reward_to_weighted_tip(
        self, _fresh_db
    ):
        sync_dsn, async_dsn = _fresh_db
        # Stop at 0004 (pre-0005) so tips/backtest exist but are unaffected.
        assert (
            _run_alembic(sync_dsn, "upgrade", "0004_canonical_team_names").returncode == 0
        )

        # Seed rows with the OLD heuristic value.
        await _exec(
            async_dsn,
            "INSERT INTO tips (game_id, heuristic, selected_team) "
            "VALUES (NULL, 'high_risk_high_reward', 'Brisbane')",
        )
        await _exec(
            async_dsn,
            "INSERT INTO backtest_results (heuristic, season, round_id, "
            "tips_made, tips_correct, accuracy, profit) "
            "VALUES ('high_risk_high_reward', 2024, 1, 10, 6, 0.6, 5.0)",
        )

        # Apply 0005.
        assert _run_alembic(sync_dsn, "upgrade", "head").returncode == 0

        tip_heuristic = await _scalar(
            async_dsn, "SELECT heuristic FROM tips WHERE selected_team = 'Brisbane'"
        )
        backtest_heuristic = await _scalar(
            async_dsn,
            "SELECT heuristic FROM backtest_results WHERE season = 2024 AND round_id = 1",
        )
        assert tip_heuristic == "weighted_tip"
        assert backtest_heuristic == "weighted_tip"

    async def test_downgrade_reverts_weighted_tip_to_high_risk_high_reward(
        self, _fresh_db
    ):
        sync_dsn, async_dsn = _fresh_db
        # Seed at 0004, then upgrade to head so the value is renamed.
        assert (
            _run_alembic(sync_dsn, "upgrade", "0004_canonical_team_names").returncode == 0
        )
        await _exec(
            async_dsn,
            "INSERT INTO tips (game_id, heuristic, selected_team) "
            "VALUES (NULL, 'high_risk_high_reward', 'Collingwood')",
        )
        await _exec(
            async_dsn,
            "INSERT INTO backtest_results (heuristic, season, round_id, "
            "tips_made, tips_correct, accuracy, profit) "
            "VALUES ('high_risk_high_reward', 2023, 2, 9, 5, 0.55, 3.0)",
        )
        assert _run_alembic(sync_dsn, "upgrade", "head").returncode == 0

        # Rolling 0005 back must revert the rename.
        assert (
            _run_alembic(sync_dsn, "downgrade", "0004_canonical_team_names").returncode == 0
        )

        tip_heuristic = await _scalar(
            async_dsn, "SELECT heuristic FROM tips WHERE selected_team = 'Collingwood'"
        )
        backtest_heuristic = await _scalar(
            async_dsn,
            "SELECT heuristic FROM backtest_results WHERE season = 2023 AND round_id = 2",
        )
        assert tip_heuristic == "high_risk_high_reward"
        assert backtest_heuristic == "high_risk_high_reward"
