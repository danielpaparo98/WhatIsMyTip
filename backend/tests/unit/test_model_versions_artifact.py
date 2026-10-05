"""Unit tests for boosted-tip artifact persistence (BT-1).

These tests pin the NEW artifact CRUD contract added to
``packages.shared.crud.model_versions`` for the ``boosted_tip`` XGBoost
heuristic, against a real PostgreSQL instance (the ``artifact`` column
is a Postgres ``BYTEA``).  They mirror the lifecycle of
``tests/unit/test_model_versions_crud.py`` (Podman ``postgres:16-alpine``
+ ``Base.metadata.create_all``) so both suites exercise the same schema.

Contract (BT-1):

1. ``create_model_version`` accepts optional ``artifact`` bytes (the
   XGBoost ``save_raw(raw_format="json")`` blob), an ``artifact_format``
   tag and a ``shap_base_value`` float, and persists all three.
2. ``get_active_model_artifact`` returns
   ``(bytes, format, version_row)`` for the active version, or ``None``
   when no version is active *or* the active version stores no artifact
   (e.g. legacy ``weighted_tip`` linear versions) — the runtime treats
   ``None`` as "no learned model, use the fallback".
3. SHAP global importances (mean |SHAP| per feature) are stored as
   regular ``model_coefficients`` rows via the same ``coefficients``
   mapping — an intentional overload documented in the CRUD docstrings.
4. Existing ``weighted_tip`` calls (no artifact kwargs) keep working and
   leave the new columns NULL — full backward compatibility.
5. ``next_version_number("boosted_tip")`` numbers the new model
   independently of ``weighted_tip``.

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
    get_active_model_artifact,
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
_CONTAINER_NAME_PREFIX = "wimt-pg-test-mva-"
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
        raise RuntimeError(f"podman run failed (rc={proc.returncode}): {proc.stderr.strip()}")

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

# A stand-in for the XGBoost ``save_raw(raw_format="json")`` bytes — the
# CRUD layer treats the artifact as opaque bytes, so a real trained
# booster is unnecessary here (keeping xgboost/shap OUT of the unit-test
# import graph honours the lazy-import convention, BT-1).
_FAKE_MODEL_JSON = b'{"learner": {"learner_model_param": {"num_feature": "16"}}}'
_FAKE_MODEL_JSON_V2 = b'{"learner": {"version": 2}}'


class TestCreateModelVersionArtifact:
    """``create_model_version`` persists artifact bytes + format + base."""

    async def test_artifact_roundtrip_via_crud(self, session_factory):
        async with session_factory() as session:
            version = await create_model_version(
                session,
                model_name="boosted_tip",
                version=1,
                intercept=0.0,
                training_rows=500,
                metrics={"r2": 0.31, "mae": 22.4, "shap_base_value": -1.25},
                coefficients={"elo_margin_home": 2.5, "form_conf": 0.75},
                set_active=True,
                artifact=_FAKE_MODEL_JSON,
                artifact_format="json",
                shap_base_value=-1.25,
            )

        assert version.id is not None
        assert version.artifact == _FAKE_MODEL_JSON
        assert version.artifact_format == "json"
        assert version.shap_base_value == pytest.approx(-1.25)
        assert version.metrics is not None
        assert version.metrics["shap_base_value"] == pytest.approx(-1.25)

        # Read back through a fresh session to prove it left the connection.
        async with session_factory() as session:
            active = await get_active_model_version(session, "boosted_tip")
        assert active is not None
        assert active.artifact == _FAKE_MODEL_JSON
        assert active.artifact_format == "json"

    async def test_artifact_format_defaults_to_json_when_unset(self, session_factory):
        """``artifact_format=None`` reads back as the canonical ``"json"`` tag.

        The retrain job always serialises with ``raw_format="json"``, so
        the reader may safely default the tag instead of failing on a
        legacy row that stored bytes without a format marker.
        """
        async with session_factory() as session:
            await create_model_version(
                session,
                model_name="boosted_tip",
                version=1,
                intercept=0.0,
                training_rows=10,
                metrics={},
                coefficients={},
                set_active=True,
                artifact=_FAKE_MODEL_JSON,
            )

        async with session_factory() as session:
            result = await get_active_model_artifact(session, "boosted_tip")

        assert result is not None
        artifact, artifact_format, _version_row = result
        assert artifact == _FAKE_MODEL_JSON
        assert artifact_format == "json"

    async def test_backward_compat_weighted_tip_call_without_artifact(
        self, session_factory
    ):
        """Existing weighted_tip callers omit the new kwargs — columns stay NULL."""
        async with session_factory() as session:
            version = await create_model_version(
                session,
                model_name="weighted_tip",
                version=1,
                intercept=0.5,
                training_rows=1000,
                metrics={"r2": 0.65, "mae": 1.2},
                coefficients={"elo": 0.3, "form": 0.4},
                set_active=True,
            )

        assert version.artifact is None
        assert version.artifact_format is None
        assert version.shap_base_value is None

        async with session_factory() as session:
            active = await get_active_model_version(session, "weighted_tip")
        assert active is not None
        assert active.artifact is None


class TestGetActiveModelArtifact:
    """``get_active_model_artifact`` returns (bytes, format, row) or None."""

    async def test_returns_none_when_no_active_version(self, session_factory):
        async with session_factory() as session:
            result = await get_active_model_artifact(session, "boosted_tip")
        assert result is None

    async def test_returns_none_when_active_version_has_no_artifact(
        self, session_factory
    ):
        """A linear (weighted_tip) active version must read as "no artifact".

        The runtime heuristic treats ``None`` as "fall back to majority
        vote" — a stored-coefficients model must never be mistaken for a
        predict-capable blob.
        """
        async with session_factory() as session:
            await create_model_version(
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
            result = await get_active_model_artifact(session, "weighted_tip")
        assert result is None

    async def test_returns_bytes_format_and_version_row(self, session_factory):
        async with session_factory() as session:
            created = await create_model_version(
                session,
                model_name="boosted_tip",
                version=3,
                intercept=0.0,
                training_rows=800,
                metrics={"r2": 0.4},
                coefficients={"elo_margin_home": 1.0},
                set_active=True,
                artifact=_FAKE_MODEL_JSON,
                artifact_format="json",
                shap_base_value=0.5,
            )

        async with session_factory() as session:
            result = await get_active_model_artifact(session, "boosted_tip")

        assert result is not None
        artifact, artifact_format, version_row = result
        assert artifact == _FAKE_MODEL_JSON
        assert artifact_format == "json"
        assert version_row.id == created.id
        assert version_row.version == 3
        assert version_row.shap_base_value == pytest.approx(0.5)


class TestShapImportancesAsCoefficients:
    """SHAP global importances persist as ordinary coefficient rows (BT-1)."""

    async def test_shap_importances_persisted_as_coefficient_rows(
        self, session_factory
    ):
        importances = {
            "elo_margin_home": 12.34,
            "elo_conf": 0.56,
            "form_margin_home": 8.9,
            "form_conf": 0.12,
        }
        async with session_factory() as session:
            version = await create_model_version(
                session,
                model_name="boosted_tip",
                version=1,
                intercept=0.0,
                training_rows=400,
                metrics={"r2": 0.3, "mae": 23.0},
                coefficients=importances,  # mean |SHAP| per feature
                set_active=True,
                artifact=_FAKE_MODEL_JSON,
                artifact_format="json",
                shap_base_value=-0.75,
            )

        async with session_factory() as session:
            coeffs = await get_model_coefficients(session, version.id)

        assert {c.feature_name: c.coefficient for c in coeffs} == importances
        for c in coeffs:
            assert c.model_version_id == version.id


class TestArtifactVersionAtomicity:
    """Activate/deactivate atomicity holds for artifact-bearing versions."""

    async def test_promotion_swaps_active_artifact_and_deactivates_previous(
        self, session_factory
    ):
        async with session_factory() as session:
            v1 = await create_model_version(
                session,
                model_name="boosted_tip",
                version=1,
                intercept=0.0,
                training_rows=100,
                metrics={},
                coefficients={"elo_margin_home": 1.0},
                set_active=True,
                artifact=_FAKE_MODEL_JSON,
                artifact_format="json",
                shap_base_value=0.1,
            )

        async with session_factory() as session:
            v2 = await create_model_version(
                session,
                model_name="boosted_tip",
                version=2,
                intercept=0.0,
                training_rows=200,
                metrics={},
                coefficients={"elo_margin_home": 1.5},
                set_active=True,
                artifact=_FAKE_MODEL_JSON_V2,
                artifact_format="json",
                shap_base_value=0.2,
            )

        # The active artifact is v2's, and only v2 is active.
        async with session_factory() as session:
            result = await get_active_model_artifact(session, "boosted_tip")
        assert result is not None
        artifact, _fmt, active_row = result
        assert artifact == _FAKE_MODEL_JSON_V2
        assert active_row.id == v2.id

        async with session_factory() as session:
            v1_row = (
                await session.execute(
                    select(ModelVersion).where(ModelVersion.id == v1.id)
                )
            ).scalar_one()
        assert v1_row.is_active is False
        # v1 keeps its own artifact (history stays inspectable).
        assert v1_row.artifact == _FAKE_MODEL_JSON

    async def test_set_active_false_keeps_previous_artifact_served(
        self, session_factory
    ):
        async with session_factory() as session:
            await create_model_version(
                session,
                model_name="boosted_tip",
                version=1,
                intercept=0.0,
                training_rows=100,
                metrics={},
                coefficients={},
                set_active=True,
                artifact=_FAKE_MODEL_JSON,
                artifact_format="json",
                shap_base_value=0.1,
            )

        async with session_factory() as session:
            await create_model_version(
                session,
                model_name="boosted_tip",
                version=2,
                intercept=0.0,
                training_rows=200,
                metrics={},
                coefficients={},
                set_active=False,
                artifact=_FAKE_MODEL_JSON_V2,
                artifact_format="json",
                shap_base_value=0.2,
            )

        async with session_factory() as session:
            result = await get_active_model_artifact(session, "boosted_tip")
        assert result is not None
        artifact, _fmt, active_row = result
        assert artifact == _FAKE_MODEL_JSON
        assert active_row.version == 1


class TestNextVersionNumberBoostedTip:
    """``next_version_number`` is independent per model name (BT-1)."""

    async def test_boosted_tip_starts_at_one(self, session_factory):
        async with session_factory() as session:
            n = await next_version_number(session, "boosted_tip")
        assert n == 1

    async def test_boosted_tip_numbering_ignores_weighted_tip(self, session_factory):
        async with session_factory() as session:
            await create_model_version(
                session,
                model_name="weighted_tip",
                version=7,
                intercept=0.0,
                training_rows=0,
                metrics={},
                coefficients={},
                set_active=False,
            )

        async with session_factory() as session:
            n = await next_version_number(session, "boosted_tip")
        assert n == 1

    async def test_boosted_tip_increments_own_versions(self, session_factory):
        async with session_factory() as session:
            await create_model_version(
                session,
                model_name="boosted_tip",
                version=1,
                intercept=0.0,
                training_rows=0,
                metrics={},
                coefficients={},
                set_active=False,
                artifact=_FAKE_MODEL_JSON,
                artifact_format="json",
            )
        async with session_factory() as session:
            n = await next_version_number(session, "boosted_tip")
        assert n == 2
