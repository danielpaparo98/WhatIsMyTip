"""Real round-trip test for migration 0007 (elo_cache dedup).

This is the authoritative behavioural test for the elo_cache cleanup
migration.  It seeds ``elo_cache`` with the production-shaped dirty state
(an alias row alongside its canonical row, an alias whose canonical has
more data, a canonical-only team, and a ``NULL`` ``team_name`` row),
applies migration 0007, and asserts:

* alias rows are removed;
* the ``NULL`` row is removed;
* a canonical-only team is untouched;
* the canonical row that had a *more-complete* alias now carries the
  alias's rating/games_played (data preserved); and
* a canonical row whose alias was *less* complete is NOT overwritten.

Running ``upgrade head`` a second time must leave the state unchanged
(idempotency).

Uses the same self-contained Podman ``postgres:16-alpine`` lifecycle as
``test_alembic_migration_0005_round_trip.py``.  Skips gracefully when
``podman`` is not on ``PATH``.
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
_CONTAINER_NAME_PREFIX = "wimt-pg-mig0007-"
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
async def _db_at_0006(pg_container) -> AsyncIterator[tuple[str, str]]:
    """Reset to an empty schema and upgrade to 0006 (elo_cache exists,
    dedup not yet applied).  Does NOT pre-create a wide alembic_version --
    the env.py guard handles from-scratch.
    """
    _, async_dsn = pg_container
    engine = create_async_engine(async_dsn, poolclass=NullPool)
    try:
        async with engine.begin() as conn:
            await conn.execute(text("DROP SCHEMA IF EXISTS public CASCADE"))
            await conn.execute(text("CREATE SCHEMA public"))
    finally:
        await engine.dispose()

    sync_dsn, _ = pg_container
    assert _run_alembic(sync_dsn, "upgrade", "0006_model_version_num_width").returncode == 0
    yield pg_container


# ---------------------------------------------------------------------------
# Seed + introspection helpers
# ---------------------------------------------------------------------------


async def _seed_row(
    async_dsn: str, team_name: str | None, rating: float, games: int
) -> None:
    """Insert one elo_cache row with fixed season/last_updated."""
    engine = create_async_engine(async_dsn, poolclass=NullPool)
    try:
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "INSERT INTO elo_cache "
                    "(team_name, rating, games_played, last_updated, season) "
                    "VALUES (:name, :rating, :games, "
                    "TIMESTAMP WITH TIME ZONE '2024-01-01 00:00:00+00', 2024)"
                ),
                {"name": team_name, "rating": rating, "games": games},
            )
    finally:
        await engine.dispose()


async def _row(async_dsn: str, team_name: str) -> tuple[float, int] | None:
    """Return ``(rating, games_played)`` for ``team_name`` or ``None``."""
    engine = create_async_engine(async_dsn, poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            result = await conn.execute(
                text(
                    "SELECT rating, games_played FROM elo_cache "
                    "WHERE team_name = :name"
                ),
                {"name": team_name},
            )
            row = result.first()
            return (row[0], row[1]) if row is not None else None
    finally:
        await engine.dispose()


async def _null_row_count(async_dsn: str) -> int:
    """Return the count of rows with a NULL team_name."""
    engine = create_async_engine(async_dsn, poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            result = await conn.execute(
                text(
                    "SELECT COUNT(*) FROM elo_cache WHERE team_name IS NULL"
                )
            )
            return int(result.scalar() or 0)
    finally:
        await engine.dispose()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestEloCacheDedup:
    """Migration 0007 removes alias + NULL rows, preserving the best data."""

    async def test_alias_with_more_data_is_merged_then_removed(self, _db_at_0006):
        """A more-complete alias row (more games_played) is merged into the
        canonical row, then the alias is deleted."""
        sync_dsn, async_dsn = _db_at_0006
        # Canonical 'Giants' has less data than its alias 'GWS'.
        await _seed_row(async_dsn, "Giants", rating=1500.0, games=10)
        await _seed_row(async_dsn, "GWS", rating=1600.0, games=50)

        assert _run_alembic(sync_dsn, "upgrade", "head").returncode == 0

        assert await _row(async_dsn, "GWS") is None, "alias 'GWS' must be removed"
        giants = await _row(async_dsn, "Giants")
        assert giants is not None, "canonical 'Giants' must remain"
        assert giants == (1600.0, 50), (
            f"'Giants' must carry the merged alias data (1600, 50), got {giants}"
        )

    async def test_alias_with_less_data_does_not_overwrite(self, _db_at_0006):
        """A less-complete alias must NOT overwrite the canonical row; the
        alias is still removed (a canonical row exists)."""
        sync_dsn, async_dsn = _db_at_0006
        await _seed_row(async_dsn, "Bulldogs", rating=1700.0, games=30)
        await _seed_row(async_dsn, "Footscray", rating=1300.0, games=3)

        assert _run_alembic(sync_dsn, "upgrade", "head").returncode == 0

        assert await _row(async_dsn, "Footscray") is None
        bulldogs = await _row(async_dsn, "Bulldogs")
        assert bulldogs == (1700.0, 30), (
            f"'Bulldogs' must keep its own data (1700, 30), got {bulldogs}"
        )

    async def test_canonical_only_team_untouched(self, _db_at_0006):
        """A team with no alias row is left exactly as-is."""
        sync_dsn, async_dsn = _db_at_0006
        await _seed_row(async_dsn, "Richmond", rating=1550.0, games=20)

        assert _run_alembic(sync_dsn, "upgrade", "head").returncode == 0

        richmond = await _row(async_dsn, "Richmond")
        assert richmond == (1550.0, 20), (
            f"canonical-only 'Richmond' must be untouched, got {richmond}"
        )

    async def test_null_team_name_row_removed(self, _db_at_0006):
        """The NULL team_name row is removed unconditionally."""
        sync_dsn, async_dsn = _db_at_0006
        await _seed_row(async_dsn, None, rating=1400.0, games=5)
        assert await _null_row_count(async_dsn) == 1

        assert _run_alembic(sync_dsn, "upgrade", "head").returncode == 0

        assert await _null_row_count(async_dsn) == 0, "NULL row must be removed"

    async def test_dedup_is_idempotent(self, _db_at_0006):
        """Running the migration's effect twice (a second upgrade head is a
        no-op) leaves the state identical."""
        sync_dsn, async_dsn = _db_at_0006
        await _seed_row(async_dsn, "Giants", rating=1500.0, games=10)
        await _seed_row(async_dsn, "GWS", rating=1600.0, games=50)
        await _seed_row(async_dsn, "Richmond", rating=1550.0, games=20)
        await _seed_row(async_dsn, None, rating=1400.0, games=5)

        assert _run_alembic(sync_dsn, "upgrade", "head").returncode == 0
        state_after_first = {
            "Giants": await _row(async_dsn, "Giants"),
            "GWS": await _row(async_dsn, "GWS"),
            "Richmond": await _row(async_dsn, "Richmond"),
            "null_rows": await _null_row_count(async_dsn),
        }

        # Second run: alembic is already at head, so this is a no-op; the
        # guard also runs again (idempotently).
        second = _run_alembic(sync_dsn, "upgrade", "head")
        assert second.returncode == 0, (
            f"second upgrade head failed:\n{second.stdout}\n{second.stderr}"
        )

        state_after_second = {
            "Giants": await _row(async_dsn, "Giants"),
            "GWS": await _row(async_dsn, "GWS"),
            "Richmond": await _row(async_dsn, "Richmond"),
            "null_rows": await _null_row_count(async_dsn),
        }
        assert state_after_first == state_after_second, (
            f"dedup not idempotent:\nfirst={state_after_first}\n"
            f"second={state_after_second}"
        )
