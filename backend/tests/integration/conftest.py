"""Integration test configuration for FastAPI API routes.

This conftest spins up a **one-shot Postgres container via Podman** (no
Docker daemon required), wires it into the FastAPI app via dependency
override, and provides a ``seeded_db`` fixture that idempotently inserts
the minimum rows needed to exercise the 21 routes inventoried in
``plans/integration-test-endpoint-inventory.md``.

Every test in this directory is marked ``@pytest.mark.postgres`` so the
standard unit-test run can filter it on machines without Podman.  The
module is skipped at collection time when ``podman`` is unavailable or
the daemon is not reachable.

Run from the ``backend/`` directory::

    uv run pytest tests/integration/ -v --tb=short

Or only the comprehensive API suite added by this conftest::

    uv run pytest tests/integration/test_api_*.py -v --tb=short

Notes
-----

* We do **not** use FastAPI's ``TestClient(app)`` ``with`` block — that
  would run the production lifespan, which boots the APScheduler
  (``*/15 * * * *`` etc.) and the Squiggle / OpenRouter integrations.
  Instead we instantiate ``TestClient(app)`` directly and set
  ``app.state.engine``, ``app.state.redis``, ``app.state.scheduler``
  manually.
* We override ``app.core.db_deps.get_db`` to yield sessions from the
  testcontainer-bound ``session_factory``; the global
  ``packages.shared.db.get_engine()`` singleton is never called in
  tests, so we never touch the module-level engine state.
* The slowapi limiter's in-memory storage is reset per test via
  ``Limiter.reset()`` so accumulated request counts can't bleed into
  the next test (relevant for ``POST /api/tips/generate``'s
  ``10/minute`` cap).
"""
from __future__ import annotations

import os
import shutil
import socket
import subprocess
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import AsyncIterator, Iterator

# Ensure shared packages are importable when pytest is invoked from
# the repo root (pyproject.toml's testpaths is "tests").
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

# Set env vars BEFORE importing any application modules that read
# settings at import time (pydantic-settings).
#
# DATABASE_URL is *unset* here — it is rewritten to the testcontainer's
# URL the moment ``pg_container`` is set up; the rest use ``setdefault``
# so a CI-provided value wins.
os.environ.setdefault("DATABASE_URL", "")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("ALERT_ENABLED", "false")
os.environ.setdefault("OPENROUTER_API_KEY", "test-key")
os.environ.setdefault("ADMIN_API_KEY", "integration-test-admin-key")
os.environ.setdefault("CURRENT_SEASON", "2026")
os.environ.setdefault("MAX_REQUEST_BODY_BYTES", "5242880")

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from packages.shared.models import (
    BacktestResult,
    GenerationProgress,
    JobExecution,
    ModelPrediction,
    Tip,
)
from packages.shared.models import Game


# Mark every test in this directory with @pytest.mark.postgres so the
# default unit-test run can filter it on machines without Podman.
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
        return (
            "podman daemon is not reachable: "
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
_CONTAINER_PREFIX = "wimt-pg-integ-"
_STARTUP_TIMEOUT_S = 60.0
_POLL_INTERVAL_S = 0.25


def _free_port() -> int:
    """Return a free TCP port on localhost."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_for_postgres_ready(container_name: str, timeout: float) -> None:
    """Block until Postgres in the container reports healthy.

    Uses ``podman exec pg_isready`` (not just a TCP port probe) so we
    wait for the actual daemon, not the half-open port.
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
            f"pg_isready rc={proc.returncode}: "
            f"{proc.stderr.strip() or proc.stdout.strip()}"
        )
        time.sleep(_POLL_INTERVAL_S)
    raise RuntimeError(
        f"Postgres in {container_name} did not become ready within "
        f"{timeout:.1f}s: {last_err}"
    )


@pytest.fixture(scope="session")
def pg_container() -> Iterator[str]:
    """Spawn a one-shot ``postgres:16-alpine`` container, yield its URL.

    The container is destroyed on module exit.  Yields an asyncpg
    connection URL of the form
    ``postgresql+asyncpg://postgres:test@127.0.0.1:<port>/postgres``.
    """
    name = _CONTAINER_PREFIX + uuid.uuid4().hex[:12]
    port = _free_port()
    user, password, db = "postgres", "test", "postgres"

    run_cmd = [
        "podman", "run",
        "-d",
        "--rm",
        "--name", name,
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
        _wait_for_postgres_ready(name, _STARTUP_TIMEOUT_S)
        url = (
            f"postgresql+asyncpg://{user}:{password}"
            f"@127.0.0.1:{port}/{db}"
        )
        yield url
    finally:
        subprocess.run(
            ["podman", "rm", "-f", name],
            capture_output=True,
            text=True,
        )


@pytest_asyncio.fixture(scope="session")
async def engine(pg_container: str) -> AsyncIterator[AsyncEngine]:
    """Async SQLAlchemy engine bound to the testcontainer.

    Creates the schema on a one-shot bootstrap engine, then returns
    a runtime engine with ``NullPool`` (one connection per checkout).
    ``NullPool`` avoids the cross-connection ``another operation is in
    progress`` error that the default ``AsyncAdaptedQueuePool`` can
    trigger under pytest-asyncio.
    """
    from packages.shared.db import Base

    bootstrap = create_async_engine(pg_container, future=True)
    async with bootstrap.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await bootstrap.dispose()

    eng = create_async_engine(pg_container, future=True, poolclass=NullPool)
    try:
        yield eng
    finally:
        await eng.dispose()


# ---------------------------------------------------------------------------
# Function-scope seed data
# ---------------------------------------------------------------------------


# Tables truncated between tests.  The list mirrors the model graph in
# ``packages.shared.models``; we use CASCADE so foreign keys (e.g.
# match_analysis.game_id -> games.id) clear without an explicit per-
# table order.
_TRUNCATE_SQL = text(
    "TRUNCATE TABLE "
    "match_weather, match_analyses, model_predictions, tips, "
    "backtest_results, elo_cache, games, "
    "generation_progress, job_executions, job_locks, "
    "players, player_match_stats, player_advanced_stats, injuries "
    "RESTART IDENTITY CASCADE"
)

# Fixed anchor date for progress rows so tests can assert exact
# values without timestamp flakiness.
_SEED_ANCHOR = datetime(2026, 1, 15, 12, 0, tzinfo=timezone.utc)


async def _insert_seed_rows(session: AsyncSession) -> None:
    """Insert the minimum rows to exercise every route."""
    # 1 game in (2025, r1), completed with scores (needed by /api/games,
    # /api/games/{slug}, /api/games/{slug}/detail, /api/tips/{heuristic},
    # /api/tips/, /api/tips/games-with-tips, /api/backtest/{compare,
    # model-compare, table, seasons, current-season}).
    game = Game(
        id=1,
        slug="seedgame001",
        squiggle_id=9001,
        afltables_match_id="seed-2025-r1-g1",
        round_id=1,
        season=2025,
        home_team="Brisbane",
        away_team="Collingwood",
        home_score=85,
        away_score=72,
        venue="Gabba",
        # NB: ``Game.date`` is a TIMESTAMP WITHOUT TIME ZONE column, so
        # we insert a naive datetime (asyncpg raises
        # ``can't subtract offset-naive and offset-aware datetimes``
        # otherwise — see R-misc-1 in the test log).
        date=datetime(2025, 3, 20, 8, 30),
        completed=True,
        predictions_generated=True,
        tips_generated=True,
    )
    session.add(game)

    # 1 tip (best_bet) for the game.
    session.add(
        Tip(
            game_id=1,
            heuristic="best_bet",
            selected_team="Brisbane",
            margin=12,
            confidence=0.75,
            explanation="Brisbane strong at home",
        )
    )

    # 1 model_prediction (elo).
    session.add(
        ModelPrediction(
            game_id=1,
            model_name="elo",
            winner="Brisbane",
            confidence=0.70,
            margin=12,
        )
    )

    # 1 historic-refresh progress row (completed) — needed by the
    # "completed fallback" branch of GET /api/admin/historic-refresh/progress.
    # NB: ``season=None`` here is intentional.  The route handler calls
    # ``GenerationProgressCRUD.get_by_operation(operation_type="historic_refresh")``
    # with ``season=None``, which the CRUD translates to a
    # ``season IS NULL`` filter — a "global" historic-refresh query.
    session.add(
        GenerationProgress(
            operation_type="historic_refresh",
            season=None,
            total_items=10,
            completed_items=10,
            status="completed",
            started_at=_SEED_ANCHOR - timedelta(hours=1),
            completed_at=_SEED_ANCHOR - timedelta(minutes=30),
        )
    )

    # 1 backtest_result row — feeds /api/backtest/compare,
    # /api/backtest/model-compare, /api/backtest/table,
    # /api/backtest/seasons.
    session.add(
        BacktestResult(
            heuristic="best_bet",
            season=2025,
            round_id=1,
            tips_made=1,
            tips_correct=1,
            accuracy=1.0,
            profit=10.0,
        )
    )

    # 1 job_execution row — feeds /api/admin/metrics.
    session.add(
        JobExecution(
            job_name="daily-sync",
            status="completed",
            started_at=_SEED_ANCHOR - timedelta(hours=2),
            completed_at=_SEED_ANCHOR - timedelta(hours=2) + timedelta(minutes=1),
            duration_seconds=60,
            items_processed=9,
            items_failed=0,
            result_summary="Synced 9 games",
        )
    )


@pytest_asyncio.fixture
async def seeded_db(
    engine: AsyncEngine,
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """Truncate all tables, then insert the minimum seed rows.

    Yields a session factory bound to the testcontainer.  Per-test
    truncation gives each test a clean slate so assertions can rely on
    ``count == 1`` (or whatever the seed dictates).
    """
    factory = async_sessionmaker(engine, expire_on_commit=False)

    # Truncate + seed
    async with engine.begin() as conn:
        await conn.execute(_TRUNCATE_SQL)
    async with factory() as session:
        await _insert_seed_rows(session)
        await session.commit()

    yield factory

    # Final cleanup so module teardown leaves empty tables.
    async with engine.begin() as conn:
        await conn.execute(_TRUNCATE_SQL)


# ---------------------------------------------------------------------------
# FastAPI app + TestClient fixtures
# ---------------------------------------------------------------------------


ADMIN_API_KEY = "integration-test-admin-key"
ADMIN_HEADERS = {"X-API-Key": ADMIN_API_KEY}


# ---------------------------------------------------------------------------
# Cache-disabling fixture (autouse)
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _disable_redis_cache(monkeypatch):
    """Disable the Redis-backed cache for every integration test.

    The route handlers in :mod:`app.api.games` and :mod:`app.api.tips`
    are decorated with ``@long_cache`` / ``@medium_cache`` (see
    :mod:`packages.shared.cache`).  Without a real Redis container those
    decorators raise ``RuntimeError: Event loop is closed`` because
    ``redis.asyncio`` cannot reuse a connection from a previous loop.

    Patching ``RedisCache.get`` to always return ``None`` (cache miss)
    and ``RedisCache.set`` to a no-op effectively disables caching so
    every request reaches the CRUD layer.  This makes the tests
    deterministic and removes the network dependency entirely.
    """
    from packages.shared import cache as _cache

    async def _cache_miss(self, key):
        return None

    async def _cache_noop_set(self, key, value, ttl=None):
        return None

    monkeypatch.setattr(_cache.RedisCache, "get", _cache_miss)
    monkeypatch.setattr(_cache.RedisCache, "set", _cache_noop_set)
    yield


@pytest.fixture
def admin_api_key() -> str:
    """The admin X-API-Key value tests should send."""
    return ADMIN_API_KEY


@pytest.fixture
def client(
    seeded_db: async_sessionmaker[AsyncSession],
    engine: AsyncEngine,
    admin_api_key: str,
):
    """Build a FastAPI TestClient backed by the seeded Postgres testcontainer.

    No ``with`` block — we do not run the production lifespan
    (which would start the APScheduler and any network-touching
    startup).  ``app.state.engine``, ``app.state.redis``, and
    ``app.state.scheduler`` are set explicitly.
    """
    # Force the admin key into the live settings object *before*
    # the route handlers / dependency closures read it.
    from packages.shared.config import settings

    settings.admin_api_key = admin_api_key

    # Import here (not at module top) so the env-var setup above runs
    # before ``Settings()`` is constructed.
    from main import app
    from app.core import db_deps

    # Override the DB dependency to use our testcontainer session_factory.
    async def _override():
        async with seeded_db() as session:
            yield session

    app.dependency_overrides[db_deps.get_db] = _override

    # Wire app.state for the /health endpoint and the lifespan hooks
    # that may have been skipped.
    app.state.engine = engine
    app.state.redis = None  # no Redis in this suite — /health reports "degraded"
    app.state.scheduler = None

    # Reset slowapi's in-memory rate-limit storage so accumulated
    # request counts can't bleed into the next test (matters for
    # ``POST /api/tips/generate``'s 10/minute cap).
    limiter = getattr(app.state, "limiter", None)
    if limiter is not None and hasattr(limiter, "reset"):
        try:
            limiter.reset()
        except Exception:  # noqa: BLE001 — best-effort reset
            pass

    yield TestClient(app)

    app.dependency_overrides.clear()
