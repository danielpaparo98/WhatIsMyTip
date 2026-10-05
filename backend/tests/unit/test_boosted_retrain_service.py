"""Unit tests for ``packages.shared.services.boosted_retrain`` (BT-1).

These tests exercise the real data-access + XGBoost fit path against a live
PostgreSQL instance (same Podman ``postgres:16-alpine`` lifecycle as
``tests/unit/test_model_retrain_service.py``, whose fixtures/helpers this
module mirrors): seed completed games with their ``model_predictions``, run
:func:`run_boosted_retrain`, and assert the ``XGBRegressor`` is fitted,
explained with SHAP, and persisted as an *active* :class:`ModelVersion` with
its serialized artifact bytes (``save_raw(raw_format="json")``), 16
:class:`ModelCoefficient` rows carrying mean |SHAP| importances, the SHAP base
value, correct versioning, byte-identical determinism on identical data, a
lazy-import-pure skip path, and the ``params`` override hook.

Skips gracefully when ``podman`` is not on ``PATH``.
"""
from __future__ import annotations

import math
import shutil
import socket
import subprocess
import sys
import textwrap
import time
import uuid
from datetime import datetime
from pathlib import Path
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
    get_active_model_artifact,
    get_active_model_version,
    get_model_coefficients,
)
from packages.shared.heuristics.boosted_tip import load_boosted_model
from packages.shared.heuristics.weighted_tip import FEATURE_NAMES, MODEL_NAMES
from packages.shared.models import (
    Base,
    Game,
    ModelCoefficient,
    ModelPrediction,
    ModelVersion,
)
from packages.shared.services.boosted_retrain import (
    BOOSTED_PARAMS,
    BOOSTED_TIP_MODEL_NAME,
    MIN_TRAINING_ROWS,
    run_boosted_retrain,
)
from packages.shared.services.model_retrain import _gather_training_rows

# Mark all tests in this module with ``@pytest.mark.postgres`` so the
# standard unit-test run can be filtered on machines without Podman.
pytestmark = pytest.mark.postgres


# ---------------------------------------------------------------------------
# Lazy-import guards (mirror tests/unit/test_lazy_sklearn.py)
# ---------------------------------------------------------------------------

#: Heavy ML libraries that must NOT be loaded by the always-on import path or
#: by the insufficient-rows skip path.  BT-1: xgboost/shap/numpy may only be
#: imported INSIDE ``run_boosted_retrain``, past the skip gate.
_HEAVY_LIBS = ("sklearn", "scipy", "numpy", "xgboost", "shap")

BACKEND_DIR = Path(__file__).resolve().parents[2]  # backend/


def _assert_import_does_not_load_heavy_libs(import_stmts: str) -> None:
    """Run *import_stmts* in a fresh interpreter and assert no heavy ML lib loads."""
    code = textwrap.dedent(
        f"""
        import sys
        {import_stmts}
        offenders = sorted(lib for lib in {_HEAVY_LIBS!r} if lib in sys.modules)
        assert not offenders, (
            "Importing the always-on path loaded heavy ML libs at module "
            f"load time: {{offenders}}"
        )
        """
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        cwd=str(BACKEND_DIR),
        timeout=120,
    )
    assert result.returncode == 0, (
        "Isolated import check failed — a heavy ML lib was loaded at import "
        "time (or the import itself errored):\n"
        f"--- code ---\n{code}"
        f"--- stdout ---\n{result.stdout}"
        f"--- stderr ---\n{result.stderr}"
    )


def test_module_import_does_not_load_heavy_libs():
    """``packages.shared.services.boosted_retrain`` must not pull xgboost/shap/numpy.

    The scheduler's cron module will import this service at module top, so
    importing it must stay import-cheap: numpy/xgboost/shap/sklearn may only
    load inside ``run_boosted_retrain()`` (S3).
    """
    _assert_import_does_not_load_heavy_libs(
        "import packages.shared.services.boosted_retrain"
    )


def test_reuses_linear_data_access_and_constants():
    """Pin the BT-1 reuse contract: no copies, one source of truth.

    ``_gather_training_rows`` is pure data access owned by the weighted-tip
    service — the boosted service must import the very same function object
    (not a copy) so both models always train on an identical dataset.
    ``MIN_TRAINING_ROWS`` is likewise imported, not redefined.
    """
    from packages.shared.services import boosted_retrain, model_retrain

    assert boosted_retrain._gather_training_rows is model_retrain._gather_training_rows
    assert boosted_retrain.MIN_TRAINING_ROWS is model_retrain.MIN_TRAINING_ROWS
    assert MIN_TRAINING_ROWS == 100
    assert BOOSTED_TIP_MODEL_NAME == "boosted_tip"


def test_boosted_baseline_params_pinned():
    """Pin the deterministic baseline XGBRegressor parameters (BT-1).

    These are the production defaults; the notebook is the tuning surface and
    reaches production via the ``run_boosted_retrain(params=...)`` hook.
    """
    assert BOOSTED_PARAMS == {
        "n_estimators": 400,
        "max_depth": 3,
        "learning_rate": 0.08,
        "subsample": 0.8,
        "colsample_bytree": 0.8,
        "tree_method": "hist",
        "eval_metric": "rmse",
        "random_state": 42,
        "n_jobs": 4,
    }


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
_CONTAINER_NAME_PREFIX = "wimt-pg-test-boostretrain-"
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
# Helpers (mirroring tests/unit/test_model_retrain_service.py)
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


class TestRunBoostedRetrain:
    @pytest.mark.asyncio
    async def test_insufficient_rows_skips_without_heavy_imports_and_preserves_existing(
        self, session_factory
    ):
        """<100 rows -> skip dict, no lazy imports triggered, active model untouched."""
        # Seed a pre-existing active boosted version that must be preserved.
        async with session_factory() as session:
            existing = await create_model_version(
                session,
                model_name=BOOSTED_TIP_MODEL_NAME,
                version=1,
                intercept=0.0,
                training_rows=999,
                metrics={"r2": 0.0, "mae": 0.0, "shap_base_value": 0.5},
                coefficients={name: 0.0 for name in FEATURE_NAMES},
                artifact=b'{"stub": true}',
                artifact_format="json",
                shap_base_value=0.5,
                set_active=True,
            )

        # 99 usable games: below the 100-row gate, above the old 20-row gate.
        small_n = 99
        await _seed_rows(session_factory, small_n, season=2025)

        heavy_before = sorted(lib for lib in _HEAVY_LIBS if lib in sys.modules)
        async with session_factory() as session:
            result = await run_boosted_retrain(session)
        heavy_after = sorted(lib for lib in _HEAVY_LIBS if lib in sys.modules)

        assert result["status"] == "skipped"
        assert result["reason"] == "insufficient_training_rows"
        assert result["rows"] == small_n
        assert result["min_required"] == 100

        # The skip path sits BEFORE the lazy imports: it must not load any
        # heavy ML lib (mirrors the S3 convention pinned by
        # test_lazy_sklearn.py for the weighted-tip retrain).
        assert not set(heavy_after) - set(heavy_before), (
            f"skip path loaded heavy ML libs: {sorted(set(heavy_after) - set(heavy_before))}"
        )
        if "xgboost" not in heavy_before:
            assert "xgboost" not in sys.modules

        # Existing active model is unchanged; no new version was created.
        async with session_factory() as session:
            active = await get_active_model_version(session, BOOSTED_TIP_MODEL_NAME)
            all_versions = list(
                (
                    await session.execute(
                        select(ModelVersion).where(
                            ModelVersion.model_name == BOOSTED_TIP_MODEL_NAME
                        )
                    )
                ).scalars().all()
            )
        assert active is not None
        assert active.id == existing.id
        assert active.version == 1
        assert active.is_active is True
        assert len(all_versions) == 1  # no new version created

    @pytest.mark.asyncio
    async def test_trains_and_persists_active_version_with_artifact(
        self, session_factory
    ):
        """End-to-end: fit, SHAP-explain, persist artifact + importances + metrics."""
        n = MIN_TRAINING_ROWS + 5
        await _seed_rows(session_factory, n, season=2025)

        async with session_factory() as session:
            result = await run_boosted_retrain(session)

        assert result["status"] == "trained"
        assert result["model_name"] == BOOSTED_TIP_MODEL_NAME
        assert result["version"] == 1
        assert result["training_rows"] == n
        assert isinstance(result["params_used"], dict)
        assert result["params_used"] == BOOSTED_PARAMS

        metrics = result["metrics"]
        assert set(metrics.keys()) == {"r2", "mae", "shap_base_value"}
        assert all(math.isfinite(v) for v in metrics.values())

        shap_importance = result["shap_importance"]
        assert set(shap_importance.keys()) == set(FEATURE_NAMES)
        assert len(shap_importance) == len(FEATURE_NAMES)
        # Global importance is mean |SHAP| — never negative, always finite.
        assert all(math.isfinite(v) and v >= 0.0 for v in shap_importance.values())

        # Active version row carries the artifact + base value.
        async with session_factory() as session:
            active = await get_active_model_version(session, BOOSTED_TIP_MODEL_NAME)
        assert active is not None
        assert active.is_active is True
        assert active.version == 1
        assert active.id == result["model_version_id"]
        assert active.training_rows == n
        assert active.intercept == 0.0  # ensemble has no intercept (BT-1)
        assert active.artifact is not None
        assert active.artifact_format == "json"
        assert active.shap_base_value == pytest.approx(metrics["shap_base_value"])

        # SHAP importances are stored as coefficient rows (BT-1 overload).
        async with session_factory() as session:
            coeffs = await get_model_coefficients(session, result["model_version_id"])
        assert {c.feature_name for c in coeffs} == set(FEATURE_NAMES)
        for c in coeffs:
            assert c.coefficient == pytest.approx(shap_importance[c.feature_name])

        # Artifact roundtrip: bytes -> loadable, predict-capable regressor.
        np = pytest.importorskip("numpy")
        pytest.importorskip("xgboost")
        shap = pytest.importorskip("shap")

        async with session_factory() as session:
            bundle = await get_active_model_artifact(session, BOOSTED_TIP_MODEL_NAME)
        assert bundle is not None
        artifact_bytes, artifact_format, mv_row = bundle
        assert len(artifact_bytes) > 0
        assert artifact_format == "json"
        assert mv_row.id == result["model_version_id"]

        # Deserialize through the PRODUCTION loader (heuristics.boosted_tip.
        # load_boosted_model), not a bare reg.load_model: xgboost 3.4.1
        # rejects plain bytes buffers ("Unknown file type") and the loader
        # is the one chokepoint that normalises bytes/bytearray/memoryview —
        # exactly what runtime serving and the SHAP service both use.
        reg = load_boosted_model(artifact_bytes)

        # Same rows the service trained on -> additivity sanity (Tree SHAP is
        # exact for gbtree): base + sum(shap) == prediction.  Tolerance 1e-4
        # (not machine epsilon): shap_values are float32 accumulated over 16
        # features, so ~1e-5 deviations are expected.
        async with session_factory() as session:
            rows = await _gather_training_rows(session)
        assert len(rows) == n
        X = np.array([features for features, _ in rows], dtype=float)  # noqa: N806 — ML design-matrix convention
        preds = reg.predict(X)
        assert bool(np.all(np.isfinite(preds)))

        explainer = shap.TreeExplainer(reg)
        sv = explainer.shap_values(X)
        assert sv.shape == (n, len(FEATURE_NAMES))
        base = float(np.asarray(explainer.expected_value).ravel()[0])
        max_dev = float(np.abs(base + sv.sum(axis=1) - preds).max())
        assert max_dev <= 1e-4, f"SHAP additivity violated: max |dev| = {max_dev:.3e}"

        # Persisted base value and importances describe THIS artifact.
        assert base == pytest.approx(metrics["shap_base_value"], abs=1e-9)
        reloaded_importance = np.abs(sv).mean(axis=0)
        for name, value in zip(FEATURE_NAMES, reloaded_importance):
            assert shap_importance[name] == pytest.approx(float(value), abs=1e-9)

    @pytest.mark.asyncio
    async def test_determinism_identical_model_bytes_and_versioning(
        self, session_factory
    ):
        """Same rows -> identical fit: same metrics, importances, and model bytes.

        xgboost with a fixed ``random_state`` on identical data is
        deterministic on the same machine, so two retrains must produce
        byte-identical JSON artifacts while the version number still
        advances (and only the newest version stays active).
        """
        n = MIN_TRAINING_ROWS + 3
        await _seed_rows(session_factory, n, season=2025)

        async with session_factory() as session:
            r1 = await run_boosted_retrain(session)
        async with session_factory() as session:
            r2 = await run_boosted_retrain(session)

        # Versions advance monotonically.
        assert r1["version"] == 1
        assert r2["version"] == 2

        # Identical data -> identical deterministic fit.
        assert r1["metrics"] == r2["metrics"]
        assert r1["shap_importance"] == r2["shap_importance"]

        async with session_factory() as session:
            all_versions = list(
                (
                    await session.execute(
                        select(ModelVersion).where(
                            ModelVersion.model_name == BOOSTED_TIP_MODEL_NAME
                        )
                    )
                ).scalars().all()
            )
        assert len(all_versions) == 2
        artifacts = {v.version: v.artifact for v in all_versions}
        assert artifacts[1] and artifacts[2]  # both non-empty
        assert artifacts[1] == artifacts[2], (
            "xgboost should be deterministic with a fixed random_state on "
            "identical data; byte-identical save_raw output expected"
        )

        # Exactly one active version — the newest.
        actives = [v for v in all_versions if v.is_active]
        assert len(actives) == 1
        assert actives[0].version == 2

    @pytest.mark.asyncio
    async def test_params_override_hook(self, session_factory):
        """``run_boosted_retrain(params=...)`` replaces the baseline params.

        The notebook / future tuning surfaces pass a full param dict; the
        summary must echo exactly what was used (``params_used``).
        """
        n = MIN_TRAINING_ROWS + 1
        await _seed_rows(session_factory, n, season=2025)

        small_params: dict[str, object] = {
            "n_estimators": 5,
            "max_depth": 2,
            "learning_rate": 0.3,
            "subsample": 1.0,
            "colsample_bytree": 1.0,
            "tree_method": "hist",
            "eval_metric": "rmse",
            "random_state": 7,
            "n_jobs": 1,
        }
        async with session_factory() as session:
            result = await run_boosted_retrain(session, params=small_params)

        assert result["status"] == "trained"
        assert result["params_used"] == small_params
        assert result["params_used"] != BOOSTED_PARAMS
        assert result["training_rows"] == n
        assert set(result["shap_importance"].keys()) == set(FEATURE_NAMES)

        # The tiny fit still persists a complete, active version.
        async with session_factory() as session:
            active = await get_active_model_version(session, BOOSTED_TIP_MODEL_NAME)
        assert active is not None
        assert active.is_active is True
        assert active.version == 1
        assert active.artifact is not None
        assert len(active.artifact) > 0

        # Negative space: the boosted service never writes ``weighted_tip`` rows.
        async with session_factory() as session:
            weighted_versions = list(
                (
                    await session.execute(
                        select(ModelVersion).where(
                            ModelVersion.model_name == "weighted_tip"
                        )
                    )
                ).scalars().all()
            )
        assert weighted_versions == []

    @pytest.mark.asyncio
    async def test_empty_db_skips_with_zero_rows(self, session_factory):
        """No games at all -> empty row list -> skip path (never an empty fit)."""
        async with session_factory() as session:
            rows = await _gather_training_rows(session)
        assert rows == []

        async with session_factory() as session:
            result = await run_boosted_retrain(session)

        assert result["status"] == "skipped"
        assert result["reason"] == "insufficient_training_rows"
        assert result["rows"] == 0
        assert result["min_required"] == 100

        async with session_factory() as session:
            versions = list(
                (
                    await session.execute(
                        select(ModelVersion).where(
                            ModelVersion.model_name == BOOSTED_TIP_MODEL_NAME
                        )
                    )
                ).scalars().all()
            )
        assert versions == []  # no version created on the skip path
