"""Round-trip + schema-mirror tests for migration ``0015_model_versions_competition_scope``.

Per-league model storage (first-class league heuristics, FCL-1) adds a
NULLABLE ``competition_id`` to ``model_versions``: NULL = legacy global
model (the existing AFL pipeline, unchanged), non-NULL = version scoped
to one competition.  Because the weekly retrain promotes exactly one
active version per scope, two PARTIAL UNIQUE indexes enforce "at most
one active version per ``(model_name, scope)``":

* ``uq_model_versions_global_active``      — ``(model_name)`` WHERE
  ``is_active AND competition_id IS NULL``
* ``uq_model_versions_competition_active`` — ``(model_name,
  competition_id)`` WHERE ``is_active AND competition_id IS NOT NULL``

These tests run the REAL migration chain against a real Postgres (podman
testcontainer) and assert:

* upgrade 0014 → 0015 adds the nullable FK (repo convention: no ON
  DELETE, matching ``league_tips`` / ``seasons``) plus the FK index;
* the partial unique indexes reject a second active version per scope
  while scoped actives stay independent of each other and of the global
  active (legacy NULL-scope semantics preserved);
* the pre-existing ``uq_model_versions_name_version`` uniqueness is
  untouched;
* the SQLAlchemy ``ModelVersion`` model mirrors the migration columns
  and indexes exactly (no-DB metadata tests — these run everywhere);
* downgrade 0015 → 0014 drops the indexes then the column;
* upgrade head → downgrade −1 → upgrade head round-trips.
"""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import sys
import time
import uuid
from typing import Any, AsyncIterator, Iterator

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

_BACKEND_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))

_PREVIOUS_REVISION = "0014_create_league_tips"
_TARGET_REVISION = "0015_model_versions_competition_scope"


# ---------------------------------------------------------------------------
# Pure-metadata tests: the ModelVersion model mirrors the migration (FCL-1).
# These need no database, so they are NOT gated on podman.
# ---------------------------------------------------------------------------


class TestModelVersionMirrorsMigration:
    """The ORM model must mirror migration 0015 column/index-for-index."""

    @staticmethod
    def _table() -> Any:
        from packages.shared.models import ModelVersion

        return ModelVersion.__table__

    @staticmethod
    def _index_by_name(name: str) -> Any:
        for ix in TestModelVersionMirrorsMigration._table().indexes:
            if ix.name == name:
                return ix
        raise AssertionError(f"index {name!r} not found on model_versions metadata")

    def test_model_is_exported_from_models_package(self) -> None:
        from packages.shared import models
        from packages.shared.models import ModelVersion

        assert models.ModelVersion is ModelVersion, (
            "ModelVersion must be importable from packages.shared.models so "
            "Base.metadata registers it for alembic autogenerate"
        )

    def test_competition_id_column_nullable_fk(self) -> None:
        col = self._table().c.competition_id
        assert col.nullable, "legacy global rows keep NULL scope"
        assert str(col.type) == "INTEGER"
        fks = list(col.foreign_keys)
        assert len(fks) == 1
        assert fks[0].target_fullname == "competitions.id"
        # Repo convention for competitions references (league_tips,
        # seasons): no ON DELETE clause.
        assert fks[0].ondelete is None

    def test_competition_id_fk_index_present(self) -> None:
        ix = self._index_by_name("ix_model_versions_competition_id")
        assert not ix.unique
        assert {c.name for c in ix.columns} == {"competition_id"}

    def test_global_active_partial_unique_index(self) -> None:
        ix = self._index_by_name("uq_model_versions_global_active")
        assert ix.unique
        assert {c.name for c in ix.columns} == {"model_name"}
        where = str(ix.dialect_options["postgresql"]["where"])
        assert "is_active" in where
        assert "competition_id IS NULL" in where

    def test_competition_active_partial_unique_index(self) -> None:
        ix = self._index_by_name("uq_model_versions_competition_active")
        assert ix.unique
        assert {c.name for c in ix.columns} == {"model_name", "competition_id"}
        where = str(ix.dialect_options["postgresql"]["where"])
        assert "is_active" in where
        assert "competition_id IS NOT NULL" in where

    def test_existing_name_version_uq_preserved(self) -> None:
        uqs = [
            uq
            for uq in self._table().constraints
            if uq.__class__.__name__ == "UniqueConstraint"
        ]
        named = {uq.name: set(uq.columns.keys()) for uq in uqs}
        assert named.get("uq_model_versions_name_version") == {
            "model_name",
            "version",
        }


# ---------------------------------------------------------------------------
# Migration round-trip tests against a real Postgres (podman testcontainer),
# following test_league_tips_model.py plumbing.
# ---------------------------------------------------------------------------

_POSTGRES_IMAGE = "docker.io/library/postgres:16-alpine"
_CONTAINER_NAME_PREFIX = "wimt-pg-mv0015-"
_STARTUP_TIMEOUT_S = 60.0
_POLL_INTERVAL_S = 0.25


def _podman_unavailable_reason() -> str | None:
    if shutil.which("podman") is None:
        return "podman CLI not available"
    probe = subprocess.run(
        ["podman", "info", "--format", "{{.Host.Arch}}"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if probe.returncode != 0:
        return f"podman daemon unavailable: {probe.stderr.strip()[:200]}"
    return None


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_for_postgres_ready(container_name: str, timeout: float) -> None:
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
        last_err = RuntimeError(f"pg_isready rc={proc.returncode}")
        time.sleep(_POLL_INTERVAL_S)
    raise RuntimeError(f"Postgres not ready in {timeout:.1f}s: {last_err}")


@pytest.fixture(scope="module")
def pg_container() -> Iterator[tuple[str, str]]:
    reason = _podman_unavailable_reason()
    if reason is not None:
        pytest.skip(reason)

    container_name = _CONTAINER_NAME_PREFIX + uuid.uuid4().hex[:12]
    port = _free_port()
    user, password, db = "postgres", "test", "postgres"

    proc = subprocess.run(
        ["podman", "run", "-d", "--rm", "--name", container_name,
         "-p", f"127.0.0.1:{port}:5432",
         "-e", f"POSTGRES_USER={user}", "-e", f"POSTGRES_PASSWORD={password}",
         "-e", f"POSTGRES_DB={db}", _POSTGRES_IMAGE],
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"podman run failed: {proc.stderr.strip()}")

    try:
        _wait_for_postgres_ready(container_name, _STARTUP_TIMEOUT_S)
        yield (
            f"postgresql://{user}:{password}@127.0.0.1:{port}/{db}",
            f"postgresql+asyncpg://{user}:{password}@127.0.0.1:{port}/{db}",
        )
    finally:
        subprocess.run(["podman", "rm", "-f", container_name],
                       capture_output=True, text=True)


def _run_alembic(dsn: str, *args: str) -> subprocess.CompletedProcess[str]:
    cmd = [sys.executable, "-m", "alembic", *args]
    env = {**os.environ, "DATABASE_URL": dsn}
    return subprocess.run(
        cmd, cwd=_BACKEND_DIR, capture_output=True, text=True, env=env, timeout=180
    )


@pytest_asyncio.fixture
async def _db_at_0014(pg_container) -> AsyncIterator[tuple[str, str]]:
    """Empty public schema upgraded to 0014. Yields (sync_dsn, async_dsn)."""
    sync_dsn, async_dsn = pg_container
    engine = create_async_engine(async_dsn, poolclass=NullPool)
    try:
        async with engine.begin() as conn:
            await conn.execute(text("DROP SCHEMA public CASCADE"))
            await conn.execute(text("CREATE SCHEMA public"))
    finally:
        await engine.dispose()

    proc = _run_alembic(sync_dsn, "upgrade", _PREVIOUS_REVISION)
    assert proc.returncode == 0, f"upgrade to 0014 failed:\n{proc.stdout}\n{proc.stderr}"
    yield sync_dsn, async_dsn


async def _exec(async_dsn: str, sql: str, params: dict[str, Any] | None = None) -> None:
    engine = create_async_engine(async_dsn, poolclass=NullPool)
    try:
        async with engine.begin() as conn:
            await conn.execute(text(sql), params or {})
    finally:
        await engine.dispose()


async def _scalar(
    async_dsn: str, sql: str, params: dict[str, Any] | None = None
) -> Any:
    engine = create_async_engine(async_dsn, poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            return (await conn.execute(text(sql), params or {})).scalar()
    finally:
        await engine.dispose()


async def _seed_competition(async_dsn: str) -> int:
    """Insert the 'afl' sport (idempotent — 0010 seeds it too) plus one
    competition, and return its id for scoped ``model_versions`` rows."""
    engine = create_async_engine(async_dsn, poolclass=NullPool)
    try:
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "INSERT INTO sports (id, display_name) "
                    "VALUES ('afl', 'AFL') ON CONFLICT (id) DO NOTHING"
                )
            )
            competition_id = (
                await conn.execute(text(
                    "INSERT INTO competitions (sport_id, name) "
                    "VALUES ('afl', 'MV Scope League') RETURNING id"
                ))
            ).scalar_one()
        return competition_id
    finally:
        await engine.dispose()


def _version_insert_sql() -> str:
    return """
        INSERT INTO model_versions (model_name, version, is_active, competition_id)
        VALUES (:model_name, :version, :is_active, :competition_id)
    """


def _version_params(
    version: int,
    is_active: bool = True,
    competition_id: int | None = None,
    model_name: str = "weighted_tip",
) -> dict[str, Any]:
    return {
        "model_name": model_name,
        "version": version,
        "is_active": is_active,
        "competition_id": competition_id,
    }


async def _indexdef(async_dsn: str, indexname: str) -> str | None:
    return await _scalar(
        async_dsn,
        "SELECT indexdef FROM pg_indexes "
        "WHERE schemaname = 'public' AND tablename = 'model_versions' "
        "AND indexname = :ix",
        {"ix": indexname},
    )


@pytest.mark.postgres
@pytest.mark.asyncio
async def test_upgrade_adds_nullable_competition_scope_column(_db_at_0014):
    sync_dsn, async_dsn = _db_at_0014

    proc = _run_alembic(sync_dsn, "upgrade", _TARGET_REVISION)
    assert proc.returncode == 0, f"upgrade to 0015 failed:\n{proc.stdout}\n{proc.stderr}"

    # --- column: nullable integer with FK to competitions -----------------
    assert await _scalar(
        async_dsn,
        "SELECT is_nullable FROM information_schema.columns "
        "WHERE table_name = 'model_versions' AND column_name = 'competition_id'",
    ) == "YES", "competition_id must be nullable (legacy global rows)"

    assert await _scalar(
        async_dsn,
        "SELECT ccu.table_name FROM information_schema.table_constraints tc "
        "JOIN information_schema.key_column_usage kcu "
        "  ON tc.constraint_name = kcu.constraint_name "
        "JOIN information_schema.constraint_column_usage ccu "
        "  ON tc.constraint_name = ccu.constraint_name "
        "WHERE tc.table_name = 'model_versions' "
        "AND tc.constraint_type = 'FOREIGN KEY' AND kcu.column_name = 'competition_id'",
    ) == "competitions"

    # --- indexes: FK index + the two partial unique indexes ---------------
    assert await _scalar(
        async_dsn,
        "SELECT count(*) FROM pg_indexes WHERE tablename = 'model_versions' "
        "AND indexname = 'ix_model_versions_competition_id'",
    ) == 1, "FK column must be indexed (repo convention)"

    global_def = await _indexdef(async_dsn, "uq_model_versions_global_active")
    assert global_def is not None
    assert "UNIQUE" in global_def
    assert "WHERE" in global_def
    assert "is_active" in global_def
    assert "competition_id IS NULL" in global_def

    comp_def = await _indexdef(async_dsn, "uq_model_versions_competition_active")
    assert comp_def is not None
    assert "UNIQUE" in comp_def
    assert "WHERE" in comp_def
    assert "is_active" in comp_def
    assert "competition_id IS NOT NULL" in comp_def

    # --- pre-existing (model_name, version) uniqueness is untouched -------
    assert await _scalar(
        async_dsn,
        "SELECT count(*) FROM pg_constraint "
        "WHERE conrelid = 'model_versions'::regclass "
        "AND contype = 'u' AND conname = 'uq_model_versions_name_version'",
    ) == 1


@pytest.mark.postgres
@pytest.mark.asyncio
async def test_legacy_null_scope_at_most_one_active_per_model(_db_at_0014):
    """NULL-scope rows keep global semantics: one active per model_name."""
    import sqlalchemy.exc

    sync_dsn, async_dsn = _db_at_0014
    assert _run_alembic(sync_dsn, "upgrade", _TARGET_REVISION).returncode == 0

    await _exec(async_dsn, _version_insert_sql(), _version_params(1))
    assert await _scalar(async_dsn, "SELECT count(*) FROM model_versions") == 1

    # A second ACTIVE global version of the same model must be rejected.
    # The versions differ (1 vs 2), so uq_model_versions_name_version is
    # NOT the blocker — only the partial unique index can reject this.
    engine = create_async_engine(async_dsn, poolclass=NullPool)
    try:
        async with engine.begin() as conn:
            with pytest.raises(sqlalchemy.exc.IntegrityError):
                await conn.execute(
                    text(_version_insert_sql()), _version_params(2)
                )
    finally:
        await engine.dispose()

    # A second active GLOBAL version of a DIFFERENT model is fine.
    await _exec(
        async_dsn, _version_insert_sql(),
        _version_params(1, model_name="boosted_tip"),
    )

    # Multiple INACTIVE global versions of the same model are fine.
    await _exec(async_dsn, _version_insert_sql(), _version_params(3, is_active=False))
    await _exec(async_dsn, _version_insert_sql(), _version_params(4, is_active=False))

    assert await _scalar(async_dsn, "SELECT count(*) FROM model_versions") == 4


@pytest.mark.postgres
@pytest.mark.asyncio
async def test_scoped_actives_independent_per_competition(_db_at_0014):
    """Per-competition actives are independent of each other AND of the
    legacy global active (FCL-1 scope semantics)."""
    import sqlalchemy.exc

    sync_dsn, async_dsn = _db_at_0014
    assert _run_alembic(sync_dsn, "upgrade", _TARGET_REVISION).returncode == 0
    comp_a = await _seed_competition(async_dsn)

    engine = create_async_engine(async_dsn, poolclass=NullPool)
    try:
        async with engine.begin() as conn:
            comp_b = (
                await conn.execute(text(
                    "INSERT INTO competitions (sport_id, name) "
                    "VALUES ('afl', 'MV Scope League B') RETURNING id"
                ))
            ).scalar_one()
    finally:
        await engine.dispose()

    # Global active + one active per competition, same model: all coexist.
    await _exec(async_dsn, _version_insert_sql(), _version_params(1))
    await _exec(
        async_dsn, _version_insert_sql(), _version_params(2, competition_id=comp_a)
    )
    await _exec(
        async_dsn, _version_insert_sql(), _version_params(3, competition_id=comp_b)
    )
    assert await _scalar(async_dsn, "SELECT count(*) FROM model_versions") == 3
    assert await _scalar(
        async_dsn,
        "SELECT count(*) FROM model_versions WHERE is_active",
    ) == 3

    # A SECOND active version scoped to comp_a must be rejected.
    engine = create_async_engine(async_dsn, poolclass=NullPool)
    try:
        async with engine.begin() as conn:
            with pytest.raises(sqlalchemy.exc.IntegrityError):
                await conn.execute(
                    text(_version_insert_sql()),
                    _version_params(4, competition_id=comp_a),
                )
    finally:
        await engine.dispose()

    # But a second SCOPED model (different model_name, same competition)
    # is independent — scoping is per (model_name, competition).
    await _exec(
        async_dsn, _version_insert_sql(),
        _version_params(1, model_name="boosted_tip", competition_id=comp_a),
    )
    assert await _scalar(async_dsn, "SELECT count(*) FROM model_versions") == 4


@pytest.mark.postgres
@pytest.mark.asyncio
async def test_scoped_rows_require_existing_competition(_db_at_0014):
    """The FK must hold: a scoped version pointing at a nonexistent
    competition is rejected."""
    import sqlalchemy.exc

    sync_dsn, async_dsn = _db_at_0014
    assert _run_alembic(sync_dsn, "upgrade", _TARGET_REVISION).returncode == 0

    engine = create_async_engine(async_dsn, poolclass=NullPool)
    try:
        async with engine.begin() as conn:
            with pytest.raises(sqlalchemy.exc.IntegrityError):
                await conn.execute(
                    text(_version_insert_sql()), _version_params(1, competition_id=999999)
                )
    finally:
        await engine.dispose()


@pytest.mark.postgres
@pytest.mark.asyncio
async def test_downgrade_drops_indexes_then_column(_db_at_0014):
    sync_dsn, async_dsn = _db_at_0014
    assert _run_alembic(sync_dsn, "upgrade", _TARGET_REVISION).returncode == 0

    proc = _run_alembic(sync_dsn, "downgrade", "-1")
    assert proc.returncode == 0, f"downgrade failed:\n{proc.stdout}\n{proc.stderr}"

    assert await _scalar(
        async_dsn,
        "SELECT count(*) FROM information_schema.columns "
        "WHERE table_name = 'model_versions' AND column_name = 'competition_id'",
    ) == 0, "competition_id should be dropped"
    for ix in (
        "ix_model_versions_competition_id",
        "uq_model_versions_global_active",
        "uq_model_versions_competition_active",
    ):
        assert await _scalar(
            async_dsn,
            "SELECT count(*) FROM pg_indexes WHERE tablename = 'model_versions' "
            "AND indexname = :ix",
            {"ix": ix},
        ) == 0, f"{ix} should be dropped"
    # The table itself (and its original uniqueness) survive.
    assert await _scalar(
        async_dsn,
        "SELECT count(*) FROM pg_constraint "
        "WHERE conrelid = 'model_versions'::regclass "
        "AND contype = 'u' AND conname = 'uq_model_versions_name_version'",
    ) == 1


@pytest.mark.postgres
@pytest.mark.asyncio
async def test_round_trip_upgrade_downgrade_upgrade(_db_at_0014):
    sync_dsn, async_dsn = _db_at_0014

    up = _run_alembic(sync_dsn, "upgrade", "head")
    assert up.returncode == 0, f"upgrade head failed:\n{up.stdout}\n{up.stderr}"
    down = _run_alembic(sync_dsn, "downgrade", "-1")
    assert down.returncode == 0, f"downgrade -1 failed:\n{down.stdout}\n{down.stderr}"
    up_again = _run_alembic(sync_dsn, "upgrade", "head")
    assert up_again.returncode == 0, (
        f"re-upgrade head failed:\n{up_again.stdout}\n{up_again.stderr}"
    )

    # The scoped storage is usable after the round-trip.
    comp = await _seed_competition(async_dsn)
    await _exec(async_dsn, _version_insert_sql(), _version_params(1))
    await _exec(
        async_dsn, _version_insert_sql(), _version_params(2, competition_id=comp)
    )
    assert await _scalar(async_dsn, "SELECT count(*) FROM model_versions") == 2
