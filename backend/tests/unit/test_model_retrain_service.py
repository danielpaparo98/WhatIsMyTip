"""Unit tests for ``packages.shared.services.model_retrain`` (weighted-tip retrain).

These tests exercise the real data-access + scikit-learn fit path against a
live PostgreSQL instance (same Podman ``postgres:16-alpine`` lifecycle as
``tests/unit/test_model_versions_crud.py``): seed completed games with their
``model_predictions``, run :func:`run_model_retrain`, and assert the
``LinearRegression`` is fitted, persisted as an active :class:`ModelVersion`
with its 16 :class:`ModelCoefficient` rows, versioned correctly, deterministic,
skips safely on insufficient data, and honours the season lookback window.

Skips gracefully when ``podman`` is not on ``PATH``.
"""
from __future__ import annotations

import math
import shutil
import socket
import subprocess
import time
import uuid
from datetime import datetime
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
)
from packages.shared.heuristics.weighted_tip import FEATURE_NAMES, MODEL_NAMES
from packages.shared.models import (
    Base,
    Game,
    ModelCoefficient,
    ModelPrediction,
    ModelVersion,
)
from packages.shared.services.model_retrain import (
    MIN_MODELS_PER_GAME,
    MIN_TRAINING_ROWS,
    TRAINING_LOOKBACK_SEASONS,
    WEIGHTED_TIP_MODEL_NAME,
    _gather_training_rows,
    run_model_retrain,
)


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
_CONTAINER_NAME_PREFIX = "wimt-pg-test-retrain-"
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

    Yields the asyncpg connection URL and tears the container down on exit.
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
        url = f"postgresql+asyncpg://{user}:{password}@127.0.0.1:{port}/{db}"
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

    A throwaway engine bootstraps the schema via ``Base.metadata.create_all``;
    the runtime engine uses ``NullPool`` so each session checks out a fresh
    asyncpg connection (avoids the cross-connection ``another operation is in
    progress`` error under pytest-asyncio).
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
async def session_factory(engine) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """Per-test session factory; truncates the four relevant tables first.

    Children are deleted before parents to respect any FK relationships.
    """
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async with factory() as session:
        await session.execute(delete(ModelPrediction))
        await session.execute(delete(ModelCoefficient))
        await session.execute(delete(Game))
        await session.execute(delete(ModelVersion))
        await session.commit()

    yield factory

    async with factory() as session:
        await session.execute(delete(ModelPrediction))
        await session.execute(delete(ModelCoefficient))
        await session.execute(delete(Game))
        await session.execute(delete(ModelVersion))
        await session.commit()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_HOME = "Brisbane"
_AWAY = "Collingwood"


def _make_preds(
    i: int, home_team: str = _HOME, away_team: str = _AWAY
) -> dict[str, tuple[str, float, int]]:
    """Deterministic, varied prediction set for game index ``i``.

    Uses all eight :data:`MODEL_NAMES` so every seeded game comfortably exceeds
    ``MIN_MODELS_PER_GAME``.  Margins/winner/confidence vary with ``i`` so the
    resulting 16-feature vectors are distinct across games (full-rank ``X``).
    """
    preds: dict[str, tuple[str, float, int]] = {}
    for j, name in enumerate(MODEL_NAMES):
        winner = home_team if (i + j) % 2 == 0 else away_team
        margin = (i * 7 + j * 3) % 40 + 1
        confidence = round(0.50 + (j / 16.0) + (i % 3) * 0.01, 3)
        preds[name] = (winner, confidence, margin)
    return preds


async def _seed_game(
    session: AsyncSession,
    *,
    idx: int,
    season: int,
    home_team: str = _HOME,
    away_team: str = _AWAY,
    home_score: int,
    away_score: int,
    preds: dict[str, tuple[str, float, int]] | None = None,
) -> Game:
    """Seed one completed game with final scores and its model predictions."""
    if preds is None:
        preds = _make_preds(idx, home_team, away_team)
    game = Game(
        slug=f"t{idx:07d}",
        season=season,
        home_team=home_team,
        away_team=away_team,
        home_score=home_score,
        away_score=away_score,
        completed=True,
        round_id=idx,
        date=datetime(2025, 1, 1),  # Game.date is a naive DateTime column
    )
    session.add(game)
    await session.flush()  # populate game.id
    for model_name, (winner, confidence, margin) in preds.items():
        session.add(
            ModelPrediction(
                game_id=game.id,
                model_name=model_name,
                winner=winner,
                confidence=confidence,
                margin=margin,
            )
        )
    await session.commit()
    return game


async def _seed_rows(
    session_factory: async_sessionmaker[AsyncSession],
    n: int,
    season: int = 2025,
    start_idx: int = 0,
) -> list[int]:
    """Seed ``n`` completed games; return their indices."""
    indices: list[int] = []
    async with session_factory() as session:
        for k in range(n):
            i = start_idx + k
            home_score = 80 + (i % 6) * 4
            away_score = 70 + (i % 5) * 3
            await _seed_game(
                session,
                idx=i,
                season=season,
                home_score=home_score,
                away_score=away_score,
            )
            indices.append(i)
    return indices


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestRunModelRetrain:
    @pytest.mark.asyncio
    async def test_trains_and_persists_active_version(self, session_factory):
        n = MIN_TRAINING_ROWS + 5
        await _seed_rows(session_factory, n, season=2025)

        async with session_factory() as session:
            result = await run_model_retrain(session)

        assert result["status"] == "trained"
        assert result["model_name"] == WEIGHTED_TIP_MODEL_NAME
        assert result["version"] == 1
        assert result["training_rows"] == n
        assert isinstance(result["metrics"], dict)
        assert math.isfinite(result["metrics"]["r2"])
        assert math.isfinite(result["metrics"]["mae"])
        assert set(result["coefficients"].keys()) == set(FEATURE_NAMES)
        assert all(math.isfinite(v) for v in result["coefficients"].values())

        async with session_factory() as session:
            active = await get_active_model_version(session, WEIGHTED_TIP_MODEL_NAME)
        assert active is not None
        assert active.is_active is True
        assert active.version == 1
        assert active.training_rows == n

        async with session_factory() as session:
            got = await get_active_coefficients(session, WEIGHTED_TIP_MODEL_NAME)
        assert got is not None
        intercept, coeffs = got
        assert math.isfinite(intercept)
        assert set(coeffs.keys()) == set(FEATURE_NAMES)
        assert len(coeffs) == len(FEATURE_NAMES)
        assert all(math.isfinite(v) for v in coeffs.values())

    @pytest.mark.asyncio
    async def test_second_run_deactivates_previous(self, session_factory):
        n = MIN_TRAINING_ROWS + 2
        await _seed_rows(session_factory, n, season=2025)

        async with session_factory() as session:
            r1 = await run_model_retrain(session)
        async with session_factory() as session:
            r2 = await run_model_retrain(session)

        assert r1["version"] == 1
        assert r2["version"] == 2

        async with session_factory() as session:
            active = await get_active_model_version(session, WEIGHTED_TIP_MODEL_NAME)
            assert active is not None
            assert active.is_active is True
            assert active.version == 2
            all_versions = list(
                (
                    await session.execute(
                        select(ModelVersion).where(
                            ModelVersion.model_name == WEIGHTED_TIP_MODEL_NAME
                        )
                    )
                ).scalars().all()
            )
        actives = [v for v in all_versions if v.is_active]
        assert len(actives) == 1
        assert actives[0].version == 2
        # The first version still exists but is no longer active.
        versions_by_number = {v.version: v.is_active for v in all_versions}
        assert versions_by_number == {1: False, 2: True}

    @pytest.mark.asyncio
    async def test_determinism_identical_coefficients(self, session_factory):
        n = MIN_TRAINING_ROWS + 3
        await _seed_rows(session_factory, n, season=2025)

        async with session_factory() as session:
            r1 = await run_model_retrain(session)
        async with session_factory() as session:
            r2 = await run_model_retrain(session)

        # Identical data -> identical deterministic fit.
        assert r1["intercept"] == r2["intercept"]
        assert r1["coefficients"] == r2["coefficients"]
        # Versions still advance.
        assert r1["version"] == 1
        assert r2["version"] == 2

    @pytest.mark.asyncio
    async def test_insufficient_rows_skips_and_preserves_existing(self, session_factory):
        # Seed a pre-existing active version that must be preserved.
        async with session_factory() as session:
            existing = await create_model_version(
                session,
                model_name=WEIGHTED_TIP_MODEL_NAME,
                version=1,
                intercept=0.1,
                training_rows=999,
                metrics={"r2": 0.0, "mae": 0.0},
                coefficients={name: 0.0 for name in FEATURE_NAMES},
                set_active=True,
            )

        # Fewer than MIN_TRAINING_ROWS usable games (each is usable: 8 preds).
        small_n = MIN_TRAINING_ROWS - 1
        await _seed_rows(session_factory, small_n, season=2025)

        async with session_factory() as session:
            result = await run_model_retrain(session)

        assert result["status"] == "skipped"
        assert result["reason"] == "insufficient_training_rows"
        assert result["rows"] == small_n
        assert result["min_required"] == MIN_TRAINING_ROWS

        # Existing active model is unchanged.
        async with session_factory() as session:
            active = await get_active_model_version(session, WEIGHTED_TIP_MODEL_NAME)
            all_versions = list(
                (
                    await session.execute(
                        select(ModelVersion).where(
                            ModelVersion.model_name == WEIGHTED_TIP_MODEL_NAME
                        )
                    )
                ).scalars().all()
            )
        assert active is not None
        assert active.id == existing.id
        assert active.version == 1
        assert len(all_versions) == 1  # no new version created

    @pytest.mark.asyncio
    async def test_lookback_excludes_old_seasons(self, session_factory):
        # The lookback window is the latest ``TRAINING_LOOKBACK_SEASONS``
        # seasons present in the DB.  With 2025 as the latest season, a 2020
        # game falls outside the window and must be excluded.
        n_latest = MIN_TRAINING_ROWS + 5
        await _seed_rows(session_factory, n_latest, season=2025, start_idx=0)
        await _seed_rows(session_factory, 3, season=2020, start_idx=1000)

        async with session_factory() as session:
            rows = await _gather_training_rows(session)
        assert len(rows) == n_latest

        async with session_factory() as session:
            result = await run_model_retrain(session)
        assert result["status"] == "trained"
        assert result["training_rows"] == n_latest
        # Sanity: each feature vector is the expected 16-length.
        assert len(rows[0][0]) == len(FEATURE_NAMES)
