"""Round-trip + schema-mirror tests for migration ``0014_create_league_tips``.

The league-generic tips pipeline (performance-per-league, design D3) needs
a ``league_tips`` table scoped to the multisport model: one row per
``(event_id, heuristic)`` with a NULLABLE ``selected_participant_id``
(draw-no-pick).  These tests run the REAL migration chain against a real
Postgres (podman testcontainer) and assert:

* upgrade 0013 → 0014 creates ``league_tips`` with exactly the D3
  columns, FKs to ``events`` / ``event_participants`` / ``competitions``
  / ``seasons``, and the UNIQUE ``(event_id, heuristic)`` constraint;
* the nullable pick works (a draw-no-pick tip inserts with
  ``selected_participant_id IS NULL``) and ``generated_at`` is
  server-defaulted;
* the SQLAlchemy ``LeagueTip`` model mirrors the migration columns
  exactly (no-DB metadata tests — these run everywhere);
* downgrade 0014 → 0013 drops the table;
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

_PREVIOUS_REVISION = "0013_add_model_artifact_columns"
_TARGET_REVISION = "0014_create_league_tips"


# ---------------------------------------------------------------------------
# Pure-metadata tests: the LeagueTip model mirrors the migration (D3 spec).
# These need no database, so they are NOT gated on podman.
# ---------------------------------------------------------------------------


class TestLeagueTipModelMirrorsMigration:
    """The ORM model must mirror migration 0014 column-for-column."""

    @staticmethod
    def _table() -> Any:
        from packages.shared.models.multisport import LeagueTip

        return LeagueTip.__table__

    def test_model_is_exported_from_models_package(self) -> None:
        from packages.shared import models
        from packages.shared.models.multisport import LeagueTip

        assert models.LeagueTip is LeagueTip, (
            "LeagueTip must be importable from packages.shared.models so "
            "Base.metadata registers it for alembic autogenerate"
        )

    def test_table_name(self) -> None:
        assert self._table().name == "league_tips"

    def test_columns_exact_set_and_types(self) -> None:
        cols = self._table().columns
        assert set(cols.keys()) == {
            "id",
            "event_id",
            "heuristic",
            "selected_participant_id",
            "competition_id",
            "season_id",
            "generated_at",
        }
        assert cols["id"].primary_key
        for name in ("event_id", "selected_participant_id", "competition_id", "season_id"):
            assert str(cols[name].type) == "INTEGER", name
        assert "VARCHAR" in str(cols["heuristic"].type).upper()
        # Type-CLASS assertion, not a render string: SQLAlchemy's generic
        # dialect renders DateTime as "DATETIME" while Postgres reports
        # "timestamp with time zone" — only the class is dialect-stable.
        import sqlalchemy as sa

        assert isinstance(cols["generated_at"].type, sa.DateTime)
        assert cols["generated_at"].type.timezone

    def test_nullability_matches_d3_spec(self) -> None:
        cols = self._table().columns
        for required in ("event_id", "heuristic", "competition_id", "season_id"):
            assert not cols[required].nullable, required
        # Draw-no-pick: the selected side may be absent.
        assert cols["selected_participant_id"].nullable
        assert cols["generated_at"].nullable

    def test_generated_at_has_server_default(self) -> None:
        assert self._table().c.generated_at.server_default is not None

    def test_foreign_keys(self) -> None:
        fks = {
            fk.parent.name: fk for fk in self._table().foreign_keys
        }
        assert set(fks) == {
            "event_id",
            "selected_participant_id",
            "competition_id",
            "season_id",
        }
        assert fks["event_id"].target_fullname == "events.id"
        assert (
            fks["selected_participant_id"].target_fullname
            == "event_participants.id"
        )
        assert fks["competition_id"].target_fullname == "competitions.id"
        assert fks["season_id"].target_fullname == "seasons.id"
        # Deleting an event removes its tips (matches event_participants).
        assert fks["event_id"].ondelete == "CASCADE"

    def test_unique_constraint_event_heuristic(self) -> None:
        uqs = [
            uq
            for uq in self._table().constraints
            if uq.__class__.__name__ == "UniqueConstraint"
        ]
        named = {uq.name: set(uq.columns.keys()) for uq in uqs}
        assert named.get("uq_league_tips_event_heuristic") == {"event_id", "heuristic"}


# ---------------------------------------------------------------------------
# Migration round-trip tests against a real Postgres (podman testcontainer),
# following test_alembic_migration_0010_multisport.py plumbing.
# ---------------------------------------------------------------------------

_POSTGRES_IMAGE = "docker.io/library/postgres:16-alpine"
_CONTAINER_NAME_PREFIX = "wimt-pg-lt0014-"
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
async def _db_at_0013(pg_container) -> AsyncIterator[tuple[str, str]]:
    """Empty public schema upgraded to 0013. Yields (sync_dsn, async_dsn)."""
    sync_dsn, async_dsn = pg_container
    engine = create_async_engine(async_dsn, poolclass=NullPool)
    try:
        async with engine.begin() as conn:
            await conn.execute(text("DROP SCHEMA public CASCADE"))
            await conn.execute(text("CREATE SCHEMA public"))
    finally:
        await engine.dispose()

    proc = _run_alembic(sync_dsn, "upgrade", _PREVIOUS_REVISION)
    assert proc.returncode == 0, f"upgrade to 0013 failed:\n{proc.stdout}\n{proc.stderr}"
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


async def _seed_competition(async_dsn: str) -> dict[str, int]:
    """Insert one sport/competition/season/event with home+away sides.

    Runs in a SINGLE transaction, capturing ids via ``RETURNING`` —
    ``currval()`` is session-scoped and would not survive per-call
    connections.  Returns the ids needed to insert valid
    ``league_tips`` rows.
    """
    engine = create_async_engine(async_dsn, poolclass=NullPool)
    try:
        async with engine.begin() as conn:
            # ON CONFLICT: migration 0010 already seeds the 'afl' sport
            # row — this seed only guarantees it exists.
            await conn.execute(
                text(
                    "INSERT INTO sports (id, display_name) "
                    "VALUES ('afl', 'AFL') ON CONFLICT (id) DO NOTHING"
                )
            )
            competition_id = (
                await conn.execute(text(
                    "INSERT INTO competitions (sport_id, name) "
                    "VALUES ('afl', 'Test League') RETURNING id"
                ))
            ).scalar_one()
            season_id = (
                await conn.execute(
                    text(
                        "INSERT INTO seasons (competition_id, label, is_current) "
                        "VALUES (:cid, '2026', true) RETURNING id"
                    ),
                    {"cid": competition_id},
                )
            ).scalar_one()
            home_id = (
                await conn.execute(text(
                    "INSERT INTO participants (sport_id, kind, name) "
                    "VALUES ('afl', 'team', 'Home Side') RETURNING id"
                ))
            ).scalar_one()
            away_id = (
                await conn.execute(text(
                    "INSERT INTO participants (sport_id, kind, name) "
                    "VALUES ('afl', 'team', 'Away Side') RETURNING id"
                ))
            ).scalar_one()
            event_id = (
                await conn.execute(
                    text(
                        "INSERT INTO events (season_id, event_type, round_id, venue, "
                        "starts_at, status, completed, slug) "
                        "VALUES (:sid, 'match', 1, 'Test Oval', '2026-04-04 14:40', "
                        "'completed', true, 'ltseed00001') RETURNING id"
                    ),
                    {"sid": season_id},
                )
            ).scalar_one()
            await conn.execute(
                text(
                    "INSERT INTO event_participants "
                    "(event_id, participant_id, side, score, is_winner) "
                    "VALUES (:eid, :hid, 'home', 80, true), (:eid, :aid, 'away', 70, false)"
                ),
                {"eid": event_id, "hid": home_id, "aid": away_id},
            )
        return {
            "competition_id": competition_id,
            "season_id": season_id,
            "event_id": event_id,
            "home_side_id": home_id,
            "away_side_id": away_id,
        }
    finally:
        await engine.dispose()


def _tip_insert_sql() -> str:
    return """
        INSERT INTO league_tips (event_id, heuristic, selected_participant_id,
                                 competition_id, season_id)
        VALUES (:event_id, :heuristic, :selected_participant_id,
                :competition_id, :season_id)
    """


def _tip_params(ids: dict[str, int], heuristic: str, pick: int | None) -> dict[str, Any]:
    """Exactly the five bound parameters of :func:`_tip_insert_sql`."""
    return {
        "event_id": ids["event_id"],
        "heuristic": heuristic,
        "selected_participant_id": pick,
        "competition_id": ids["competition_id"],
        "season_id": ids["season_id"],
    }


@pytest.mark.postgres
@pytest.mark.asyncio
async def test_upgrade_creates_league_tips_with_spec_columns(_db_at_0013):
    sync_dsn, async_dsn = _db_at_0013

    proc = _run_alembic(sync_dsn, "upgrade", _TARGET_REVISION)
    assert proc.returncode == 0, f"upgrade to 0014 failed:\n{proc.stdout}\n{proc.stderr}"

    # --- columns: exact set, types, nullability ---------------------------
    rows: list[tuple[str, str, str]] = []
    engine = create_async_engine(async_dsn, poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            result = await conn.execute(text(
                "SELECT column_name, data_type, is_nullable FROM information_schema.columns "
                "WHERE table_name = 'league_tips' ORDER BY ordinal_position"
            ))
            rows = [(r[0], r[1], r[2]) for r in result.all()]
    finally:
        await engine.dispose()

    assert [(c, t, n) for c, t, n in rows] == [
        ("id", "integer", "NO"),
        ("event_id", "integer", "NO"),
        ("heuristic", "character varying", "NO"),
        ("selected_participant_id", "integer", "YES"),  # draw-no-pick
        ("competition_id", "integer", "NO"),
        ("season_id", "integer", "NO"),
        ("generated_at", "timestamp with time zone", "YES"),
    ]

    # --- FKs to the four multisport parents -------------------------------
    fk_targets: dict[str, str] = {}
    engine = create_async_engine(async_dsn, poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            result = await conn.execute(text(
                "SELECT kcu.column_name, ccu.table_name "
                "FROM information_schema.table_constraints tc "
                "JOIN information_schema.key_column_usage kcu "
                "  ON tc.constraint_name = kcu.constraint_name "
                "JOIN information_schema.constraint_column_usage ccu "
                "  ON tc.constraint_name = ccu.constraint_name "
                "WHERE tc.table_name = 'league_tips' AND tc.constraint_type = 'FOREIGN KEY'"
            ))
            fk_targets = {r[0]: r[1] for r in result.all()}
    finally:
        await engine.dispose()

    assert fk_targets == {
        "event_id": "events",
        "selected_participant_id": "event_participants",
        "competition_id": "competitions",
        "season_id": "seasons",
    }

    # --- UNIQUE (event_id, heuristic) --------------------------------------
    assert await _scalar(
        async_dsn,
        "SELECT count(*) FROM pg_constraint "
        "WHERE conrelid = 'league_tips'::regclass "
        "AND contype = 'u' AND conname = 'uq_league_tips_event_heuristic'",
    ) == 1

    # --- indexes (FK columns are indexed, matching repo convention) --------
    for ix in ("ix_league_tips_id", "ix_league_tips_event_id",
               "ix_league_tips_selected_participant_id",
               "ix_league_tips_competition_id", "ix_league_tips_season_id"):
        assert await _scalar(
            async_dsn,
            "SELECT count(*) FROM pg_indexes "
            "WHERE tablename = 'league_tips' AND indexname = :ix",
            {"ix": ix},
        ) == 1, ix


@pytest.mark.postgres
@pytest.mark.asyncio
async def test_insert_tip_nullable_pick_and_server_default(_db_at_0013):
    sync_dsn, async_dsn = _db_at_0013
    assert _run_alembic(sync_dsn, "upgrade", _TARGET_REVISION).returncode == 0
    ids = await _seed_competition(async_dsn)

    # A picked tip.
    await _exec(async_dsn, _tip_insert_sql(),
                _tip_params(ids, "home_advantage", ids["home_side_id"]))
    # A draw-no-pick tip: NULL selected_participant_id must be accepted.
    await _exec(async_dsn, _tip_insert_sql(), _tip_params(ids, "ladder", None))

    assert await _scalar(async_dsn, "SELECT count(*) FROM league_tips") == 2
    assert await _scalar(
        async_dsn,
        "SELECT count(*) FROM league_tips "
        "WHERE heuristic = 'ladder' AND selected_participant_id IS NULL",
    ) == 1
    # generated_at is server-defaulted.
    assert await _scalar(
        async_dsn, "SELECT count(*) FROM league_tips WHERE generated_at IS NOT NULL"
    ) == 2


@pytest.mark.postgres
@pytest.mark.asyncio
async def test_unique_and_fk_enforced(_db_at_0013):
    import sqlalchemy.exc

    sync_dsn, async_dsn = _db_at_0013
    assert _run_alembic(sync_dsn, "upgrade", _TARGET_REVISION).returncode == 0
    ids = await _seed_competition(async_dsn)

    params = _tip_params(ids, "form", ids["home_side_id"])
    await _exec(async_dsn, _tip_insert_sql(), params)

    # Same (event_id, heuristic) must be rejected.
    engine = create_async_engine(async_dsn, poolclass=NullPool)
    try:
        async with engine.begin() as conn:
            with pytest.raises(sqlalchemy.exc.IntegrityError):
                await conn.execute(text(_tip_insert_sql()), params)
    finally:
        await engine.dispose()

    # A tip pointing at a nonexistent event must be rejected.
    engine = create_async_engine(async_dsn, poolclass=NullPool)
    try:
        async with engine.begin() as conn:
            with pytest.raises(sqlalchemy.exc.IntegrityError):
                await conn.execute(
                    text(_tip_insert_sql()),
                    _tip_params({**ids, "event_id": 999999}, "form2",
                                ids["home_side_id"]),
                )
    finally:
        await engine.dispose()


@pytest.mark.postgres
@pytest.mark.asyncio
async def test_downgrade_drops_league_tips(_db_at_0013):
    sync_dsn, async_dsn = _db_at_0013
    assert _run_alembic(sync_dsn, "upgrade", _TARGET_REVISION).returncode == 0

    proc = _run_alembic(sync_dsn, "downgrade", "-1")
    assert proc.returncode == 0, f"downgrade failed:\n{proc.stdout}\n{proc.stderr}"

    assert await _scalar(
        async_dsn,
        "SELECT count(*) FROM information_schema.tables WHERE table_name = 'league_tips'",
    ) == 0, "league_tips should be dropped"
    # The parent multisport tables survive.
    assert await _scalar(async_dsn, "SELECT count(*) FROM events") >= 0


@pytest.mark.postgres
@pytest.mark.asyncio
async def test_round_trip_upgrade_downgrade_upgrade(_db_at_0013):
    sync_dsn, async_dsn = _db_at_0013

    up = _run_alembic(sync_dsn, "upgrade", "head")
    assert up.returncode == 0, f"upgrade head failed:\n{up.stdout}\n{up.stderr}"
    down = _run_alembic(sync_dsn, "downgrade", "-1")
    assert down.returncode == 0, f"downgrade -1 failed:\n{down.stdout}\n{down.stderr}"
    up_again = _run_alembic(sync_dsn, "upgrade", "head")
    assert up_again.returncode == 0, (
        f"re-upgrade head failed:\n{up_again.stdout}\n{up_again.stderr}"
    )

    # The table is usable after the round-trip.
    ids = await _seed_competition(async_dsn)
    await _exec(async_dsn, _tip_insert_sql(),
                _tip_params(ids, "home_advantage", ids["home_side_id"]))
    assert await _scalar(async_dsn, "SELECT count(*) FROM league_tips") == 1
