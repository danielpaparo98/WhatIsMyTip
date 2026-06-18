"""Unit tests for ``TipCRUD.upsert`` (HI-003).

The CRUD-level contract for the upsert path used by
``regenerate_tips_for_round``:

1. Calling upsert for a ``(game_id, heuristic)`` pair that does not yet
   exist inserts a new row and returns the new :class:`Tip`.
2. Calling upsert again for the same ``(game_id, heuristic)`` pair
   updates the existing row in place (no duplicate, no
   :class:`IntegrityError`).
3. Concurrent upserts for the same ``(game_id, heuristic)`` pair all
   succeed: the underlying constraint is ``UNIQUE (game_id,
   heuristic)`` and we expect Postgres to serialise the upserts
   cleanly (this is what ``INSERT ... ON CONFLICT DO UPDATE`` gives
   us).

These tests pin the fast-regression net for HI-003 against a real
PostgreSQL instance (the same lifecycle as
``tests/unit/test_generation_progress_crud.py``).
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
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from packages.shared.crud.tips import TipCRUD
from packages.shared.models import Base, Game, Tip


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
_CONTAINER_NAME_PREFIX = "wimt-pg-test-tips-"
_STARTUP_TIMEOUT_S = 60.0
_POLL_INTERVAL_S = 0.25


def _free_port() -> int:
    """Return a free TCP port on localhost."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_for_postgres_ready(
    container_name: str, timeout: float
) -> None:
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
    """Spawn a one-shot ``postgres:16-alpine`` container via ``podman run``."""
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
    """Async SQLAlchemy engine bound to the testcontainer."""
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
    monkeypatch,
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """Per-test session factory; truncates tables before each test.

    Also stubs out ``invalidate_cache_pattern`` (and the cache module's
    client/pool accessors) for the duration of the test so the
    module-scoped Redis pool — which pytest-asyncio tears down
    between event loops — never gets touched.  The CRUD contract is
    what we want to pin here, not the cache layer.
    """
    from unittest.mock import AsyncMock

    import packages.shared.cache as _cache_mod
    import packages.shared.crud.tips as _tips_mod

    noop = AsyncMock(return_value=None)
    monkeypatch.setattr(_cache_mod, "invalidate_cache_pattern", noop)
    monkeypatch.setattr(_tips_mod, "invalidate_cache_pattern", noop)
    monkeypatch.setattr(_cache_mod, "_get_client", AsyncMock(return_value=None))

    factory = async_sessionmaker(engine, expire_on_commit=False)

    async with factory() as session:
        await session.execute(delete(Tip))
        await session.execute(delete(Game))
        await session.commit()

    yield factory

    async with factory() as session:
        await session.execute(delete(Tip))
        await session.execute(delete(Game))
        await session.commit()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _insert_game(factory: async_sessionmaker[AsyncSession]) -> int:
    """Insert one minimal :class:`Game` row and return its id."""
    import uuid as _uuid
    row = Game(
        slug=_uuid.uuid4().hex[:12],
        round_id=1,
        season=2025,
        home_team="Brisbane",
        away_team="Collingwood",
        venue="Gabba",
        completed=False,
    )
    async with factory() as session:
        session.add(row)
        await session.commit()
        await session.refresh(row)
        return int(row.id)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestUpsertCreates:
    """Contract branch 1: ``upsert`` inserts when no row exists."""

    async def test_inserts_when_no_existing_row(self, session_factory):
        game_id = await _insert_game(session_factory)

        async with session_factory() as session:
            tip = await TipCRUD.upsert(
                db=session,
                game_id=game_id,
                heuristic="best_bet",
                selected_team="Brisbane",
                margin=12,
                confidence=0.75,
                explanation="home ground advantage",
            )

        assert tip.id is not None
        assert tip.game_id == game_id
        assert tip.heuristic == "best_bet"
        assert tip.selected_team == "Brisbane"
        assert tip.margin == 12
        assert tip.confidence == 0.75
        assert tip.explanation == "home ground advantage"


class TestUpsertUpdatesExisting:
    """Contract branch 2: ``upsert`` updates in place (no IntegrityError)."""

    async def test_updates_existing_row_without_integrity_error(
        self, session_factory
    ):
        game_id = await _insert_game(session_factory)

        async with session_factory() as session:
            first = await TipCRUD.upsert(
                db=session,
                game_id=game_id,
                heuristic="best_bet",
                selected_team="Brisbane",
                margin=12,
                confidence=0.6,
                explanation="v1",
            )

        async with session_factory() as session:
            second = await TipCRUD.upsert(
                db=session,
                game_id=game_id,
                heuristic="best_bet",
                selected_team="Collingwood",
                margin=20,
                confidence=0.85,
                explanation="v2",
            )

        assert first.id == second.id, (
            "upsert must update the same row, not insert a new one"
        )
        assert second.selected_team == "Collingwood"
        assert second.margin == 20
        assert second.confidence == 0.85
        assert second.explanation == "v2"

        # Sanity: exactly one row in the table for that (game_id,
        # heuristic) pair.
        async with session_factory() as session:
            tips = await TipCRUD.get_by_game(session, game_id)
        assert len(tips) == 1


class TestUpsertConcurrentSafe:
    """Contract branch 3: concurrent upserts don't blow up.

    The legacy delete-then-insert pattern in
    ``regenerate_tips_for_round`` could raise :class:`IntegrityError`
    on the unique constraint ``uq_game_heuristic`` when two requests
    raced each other.  The upsert path must instead serialise the
    upserts cleanly via ``INSERT ... ON CONFLICT DO UPDATE``.
    """

    async def test_concurrent_upserts_all_succeed(self, session_factory):
        import asyncio

        game_id = await _insert_game(session_factory)

        async def do_upsert(team: str) -> None:
            async with session_factory() as session:
                await TipCRUD.upsert(
                    db=session,
                    game_id=game_id,
                    heuristic="best_bet",
                    selected_team=team,
                    margin=10,
                    confidence=0.7,
                    explanation=f"by {team}",
                )

        # Fire 5 concurrent upserts for the same (game_id, heuristic).
        await asyncio.gather(
            *(do_upsert(team) for team in ("A", "B", "C", "D", "E"))
        )

        async with session_factory() as session:
            tips = await TipCRUD.get_by_game(session, game_id)

        assert len(tips) == 1, (
            "Concurrent upserts must collapse to a single row"
        )