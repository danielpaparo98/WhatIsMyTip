"""Real from-scratch ``alembic upgrade head`` test for the version_num guard.

This is the authoritative behavioural proof that the
``backend/alembic/env.py`` bootstrap guard (``_ensure_alembic_version_table_width``)
fixes from-scratch upgrades.

Background: alembic creates its ``alembic_version`` table with
``version_num VARCHAR(32)``, but this repo's revision ids exceed 32 chars
(``0003_job_executions_metrics_index`` is 34).  A from-scratch upgrade
therefore used to fail the moment revision ``0003`` was stamped -- before
migration 0006 could widen the column.  The guard now creates/widens the
table to ``VARCHAR(128)`` *before* ``context.run_migrations()`` runs.

Unlike ``test_alembic_migration_0005_round_trip.py``, the fresh-DB fixture
here deliberately does **not** pre-create ``alembic_version`` with a wide
column: the upgrade must succeed with no such workaround, proving the guard
does the work.

Uses the same self-contained Podman ``postgres:16-alpine`` lifecycle as the
other migration round-trip tests.  Skips gracefully when ``podman`` is not
on ``PATH``.
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
        return (
            f"podman daemon is not reachable: "
            f"{result.stderr.strip() or 'unknown error'}"
        )
    return None


_SKIP_REASON = _podman_unavailable_reason()
if _SKIP_REASON is not None:
    pytest.skip(_SKIP_REASON, allow_module_level=True)


# ---------------------------------------------------------------------------
# Module-scope Postgres container (Podman)
# ---------------------------------------------------------------------------


_POSTGRES_IMAGE = "docker.io/library/postgres:16-alpine"
_CONTAINER_NAME_PREFIX = "wimt-pg-mig0006-"
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
            f"pg_isready rc={proc.returncode}: "
            f"{proc.stderr.strip() or proc.stdout.strip()}"
        )
        time.sleep(_POLL_INTERVAL_S)
    raise RuntimeError(
        f"Postgres in {container_name} did not become ready "
        f"within {timeout:.1f}s: {last_err}"
    )


@pytest.fixture(scope="module")
def pg_container() -> Iterator[tuple[str, str]]:
    """Spawn a one-shot ``postgres:16-alpine`` container.

    Yields ``(sync_dsn, async_dsn)``.
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
async def _truly_fresh_db(pg_container) -> AsyncIterator[tuple[str, str]]:
    """Reset the DB to a **completely empty** public schema.

    Crucially this does NOT pre-create ``alembic_version`` with a widened
    ``VARCHAR(128)`` column (the old workaround).  The from-scratch upgrade
    must succeed relying solely on the env.py bootstrap guard.
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


async def _version_num_width(async_dsn: str) -> int | None:
    """Return ``character_maximum_length`` of ``alembic_version.version_num``."""
    engine = create_async_engine(async_dsn, poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            result = await conn.execute(
                text(
                    "SELECT character_maximum_length FROM information_schema.columns "
                    "WHERE table_schema = 'public' "
                    "AND table_name = 'alembic_version' "
                    "AND column_name = 'version_num'"
                ),
            )
            return result.scalar()
    finally:
        await engine.dispose()


async def _current_revision(async_dsn: str) -> str | None:
    """Return the single stamped revision id in ``alembic_version``."""
    engine = create_async_engine(async_dsn, poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            result = await conn.execute(
                text("SELECT version_num FROM alembic_version LIMIT 1")
            )
            return result.scalar()
    finally:
        await engine.dispose()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestFromScratchUpgrade:
    """A from-scratch ``alembic upgrade head`` succeeds and ends wide."""

    async def test_upgrade_head_succeeds_without_workaround(self, _truly_fresh_db):
        """The upgrade must succeed with NO pre-created wide version table --
        the env.py guard is what makes it work."""
        sync_dsn, _ = _truly_fresh_db
        result = _run_alembic(sync_dsn, "upgrade", "head")
        assert result.returncode == 0, (
            f"from-scratch upgrade head failed (the guard should have fixed "
            f"this):\n{result.stdout}\n{result.stderr}"
        )

    async def test_version_num_column_is_varchar_128(self, _truly_fresh_db):
        """After upgrade, ``alembic_version.version_num`` must be VARCHAR(128)."""
        sync_dsn, async_dsn = _truly_fresh_db
        assert _run_alembic(sync_dsn, "upgrade", "head").returncode == 0

        width = await _version_num_width(async_dsn)
        assert width == 128, (
            f"expected version_num VARCHAR(128), got character_maximum_length={width}"
        )

    async def test_reaches_head_revision(self, _truly_fresh_db):
        """The upgrade must reach the current migration head, proving every
        long revision id (incl. 0003 = 34 chars, 0005 = 33 chars) was
        stamped successfully via the guard.

        The head is resolved dynamically (``alembic heads``) rather than
        pinned to a literal so the test stays correct as the chain grows.
        """
        sync_dsn, async_dsn = _truly_fresh_db
        assert _run_alembic(sync_dsn, "upgrade", "head").returncode == 0

        heads = _run_alembic(sync_dsn, "heads")
        assert heads.returncode == 0, (
            f"alembic heads failed:\n{heads.stdout}\n{heads.stderr}"
        )
        head_lines = [ln for ln in heads.stdout.splitlines() if ln.strip()]
        assert head_lines, (
            f"alembic heads produced no head id:\n{heads.stdout!r}"
        )
        head_id = head_lines[-1].split()[0]

        rev = await _current_revision(async_dsn)
        assert rev == head_id, f"expected head {head_id!r}, got {rev!r}"

    async def test_guard_is_idempotent_across_runs(self, _truly_fresh_db):
        """Running ``upgrade head`` twice must both succeed (the guard runs
        on every invocation and is idempotent)."""
        sync_dsn, _ = _truly_fresh_db
        assert _run_alembic(sync_dsn, "upgrade", "head").returncode == 0
        second = _run_alembic(sync_dsn, "upgrade", "head")
        assert second.returncode == 0, (
            f"second upgrade head failed:\n{second.stdout}\n{second.stderr}"
        )
