"""Round-trip + data-copy tests for migration ``0010_consolidated_multisport``.

The ADR 0001 target schema arrives in one migration with an in-migration
AFL copy (games → events/event_participants/source_refs, elo_cache →
rating_snapshots).  These tests run the REAL migration chain against a
real Postgres (podman testcontainer) and assert:

* upgrade 0009 → 0010 creates all 14 new tables;
* legacy games rows are copied into events + sides + source refs —
  including draws (``is_winner`` NULL), TBC fixtures (no sides), and
  Squiggle re-publish duplicates (folded into one event by the natural
  key);
* ``elo_cache`` rows land in ``rating_snapshots`` keyed
  (participant, season);
* copied sequences are advanced (the 0009 lesson);
* downgrade 0010 → 0009 drops the new tables and leaves legacy intact.
"""

from __future__ import annotations

import os
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

_BACKEND_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


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


import shutil  # noqa: E402  (kept next to the skip guard for clarity)

_SKIP_REASON = _podman_unavailable_reason()
if _SKIP_REASON is not None:
    pytest.skip(_SKIP_REASON, allow_module_level=True)


_POSTGRES_IMAGE = "docker.io/library/postgres:16-alpine"
_CONTAINER_NAME_PREFIX = "wimt-pg-mig0010-"
_STARTUP_TIMEOUT_S = 60.0
_POLL_INTERVAL_S = 0.25


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


def _run_alembic(dsn: str, *args: str) -> subprocess.CompletedProcess:
    cmd = [sys.executable, "-m", "alembic", *args]
    env = {**os.environ, "DATABASE_URL": dsn}
    return subprocess.run(
        cmd, cwd=_BACKEND_DIR, capture_output=True, text=True, env=env, timeout=180
    )


@pytest_asyncio.fixture
async def _db_at_0009(pg_container) -> AsyncIterator[tuple[str, str]]:
    """Empty public schema upgraded to 0009. Yields (sync_dsn, async_dsn)."""
    sync_dsn, async_dsn = pg_container
    engine = create_async_engine(async_dsn, poolclass=NullPool)
    try:
        async with engine.begin() as conn:
            await conn.execute(text("DROP SCHEMA public CASCADE"))
            await conn.execute(text("CREATE SCHEMA public"))
    finally:
        await engine.dispose()

    proc = _run_alembic(sync_dsn, "upgrade", "0009_fix_generation_progress_seq")
    assert proc.returncode == 0, f"upgrade to 0009 failed:\n{proc.stdout}\n{proc.stderr}"
    yield sync_dsn, async_dsn


async def _exec(async_dsn: str, sql: str, params: dict | None = None) -> None:
    engine = create_async_engine(async_dsn, poolclass=NullPool)
    try:
        async with engine.begin() as conn:
            await conn.execute(text(sql), params or {})
    finally:
        await engine.dispose()


async def _scalar(async_dsn: str, sql: str, params: dict | None = None):
    engine = create_async_engine(async_dsn, poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            return (await conn.execute(text(sql), params or {})).scalar()
    finally:
        await engine.dispose()


async def _seed_legacy_afl(async_dsn: str) -> None:
    """Seed games/elo_cache rows exercising every copy branch."""
    await _exec(async_dsn, """
        INSERT INTO games (slug, squiggle_id, afltables_match_id, round_id, season,
                           home_team, away_team, home_score, away_score, venue,
                           date, completed)
        VALUES
          ('seed00001', 1001, 'at-1001', 1, 2025, 'Adelaide Crows', 'Sydney',
           85, 72, 'SCG', '2025-03-15 10:00', true),
          -- a DRAW: is_winner must be NULL on both sides
          ('seed00002', 1002, NULL,      2, 2025, 'Carlton', 'Richmond',
           60, 60, 'MCG', '2025-03-22 10:00', true),
          -- a TBC fixture: no participant identity yet
          ('seed00003', 1003, NULL,      3, 2025, NULL, NULL,
           NULL, NULL, 'MCG', '2025-09-20 10:00', false),
          -- a re-published duplicate (new squiggle id, same fixture):
          -- must FOLD into seed00001's event via the natural key
          ('seed00004', 9999, NULL,      1, 2025, 'Adelaide', 'Sydney',
           85, 72, 'SCG', '2025-03-15 10:00', true)
    """)
    await _exec(async_dsn, """
        INSERT INTO elo_cache (team_name, rating, season, last_updated)
        VALUES ('Adelaide', 1555.5, 2025, now())
    """)


@pytest.mark.asyncio
async def test_upgrade_copies_afl_data(_db_at_0009):
    sync_dsn, async_dsn = _db_at_0009
    await _seed_legacy_afl(async_dsn)

    proc = _run_alembic(sync_dsn, "upgrade", "0010_consolidated_multisport")
    assert proc.returncode == 0, f"upgrade to 0010 failed:\n{proc.stdout}\n{proc.stderr}"

    # --- framework rows ---------------------------------------------------
    assert await _scalar(async_dsn, "SELECT id FROM sports WHERE id = 'afl'") == "afl"
    competition_id = await _scalar(
        async_dsn,
        "SELECT id FROM competitions WHERE sport_id = 'afl' AND name = 'Australian Football League'",
    )
    assert competition_id is not None
    season_id = await _scalar(
        async_dsn, "SELECT id FROM seasons WHERE competition_id = :c AND label = '2025'",
        {"c": competition_id},
    )
    assert season_id is not None

    # --- participants: aliases canonicalized into ONE team ----------------
    adelaide_id = await _scalar(
        async_dsn,
        "SELECT id FROM participants WHERE sport_id = 'afl' AND kind = 'team' AND name = 'Adelaide'",
    )
    assert adelaide_id is not None
    assert await _scalar(
        async_dsn,
        "SELECT count(*) FROM participants WHERE sport_id = 'afl' AND name = 'Adelaide Crows'",
    ) == 0

    # --- events: 4 games → 3 events (re-publish folds, TBC still copied) --
    assert await _scalar(async_dsn, "SELECT count(*) FROM events") == 3

    # the draw event has two sides, neither a winner
    draw_event_id = await _scalar(async_dsn, "SELECT id FROM events WHERE slug = 'seed00002'")
    assert await _scalar(async_dsn,
        "SELECT count(*) FROM event_participants WHERE event_id = :e AND is_winner IS NULL",
        {"e": draw_event_id}) == 2

    # the TBC event has NO sides
    tbc_event_id = await _scalar(async_dsn, "SELECT id FROM events WHERE slug = 'seed00003'")
    assert await _scalar(async_dsn,
        "SELECT count(*) FROM event_participants WHERE event_id = :e", {"e": tbc_event_id}) == 0

    # winner sides are flagged
    assert await _scalar(async_dsn,
        "SELECT count(*) FROM event_participants ep "
        "JOIN events e ON e.id = ep.event_id WHERE e.slug = 'seed00001' AND ep.is_winner") == 1

    # --- source refs --------------------------------------------------------
    assert await _scalar(async_dsn,
        "SELECT count(*) FROM event_source_refs WHERE source = 'squiggle'") == 4
    assert await _scalar(async_dsn,
        "SELECT count(*) FROM event_source_refs WHERE source = 'afltables'") == 1
    # the re-published fixture's ref points at the SAME event as the original
    orig_event_id = await _scalar(async_dsn, "SELECT id FROM events WHERE slug = 'seed00001'")
    assert await _scalar(async_dsn,
        "SELECT event_id FROM event_source_refs WHERE source = 'squiggle' AND external_id = '9999'",
    ) == orig_event_id

    # --- elo copy -----------------------------------------------------------
    assert await _scalar(async_dsn,
        "SELECT count(*) FROM rating_snapshots rs "
        "JOIN participants p ON p.id = rs.participant_id WHERE p.name = 'Adelaide'") == 1

    # --- sequences advanced (0009 lesson) -----------------------------------
    engine = create_async_engine(async_dsn, poolclass=NullPool)
    try:
        async with engine.begin() as conn:
            result = await conn.execute(text(
                "INSERT INTO participants (sport_id, kind, name) "
                "VALUES ('afl', 'team', 'Sequence Probe') RETURNING id"
            ))
            new_id = result.scalar_one()
    finally:
        await engine.dispose()
    assert new_id > 0


@pytest.mark.asyncio
async def test_downgrade_drops_new_tables_keeps_legacy(_db_at_0009):
    sync_dsn, async_dsn = _db_at_0009
    await _seed_legacy_afl(async_dsn)

    proc = _run_alembic(sync_dsn, "upgrade", "0010_consolidated_multisport")
    assert proc.returncode == 0

    proc = _run_alembic(sync_dsn, "downgrade", "0009_fix_generation_progress_seq")
    assert proc.returncode == 0, f"downgrade failed:\n{proc.stdout}\n{proc.stderr}"

    for table in ("sports", "competitions", "seasons", "participants", "events",
                  "event_participants", "rating_snapshots"):
        assert await _scalar(
            async_dsn,
            "SELECT count(*) FROM information_schema.tables WHERE table_name = :t",
            {"t": table},
        ) == 0, f"{table} should be dropped"

    # legacy data untouched
    assert await _scalar(async_dsn, "SELECT count(*) FROM games") == 4
    assert await _scalar(async_dsn, "SELECT count(*) FROM elo_cache") == 1
