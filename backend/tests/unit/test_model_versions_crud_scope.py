"""Scoped CRUD for ``model_versions`` — competition_id filters + scoped promote (FCL-4).

First-class league heuristics (FCL-1, migration 0015) gave
:class:`~packages.shared.models.ModelVersion` a nullable ``competition_id``
scope column: ``NULL`` = legacy global AFL model, non-NULL = scoped to one
competition.  The CRUD layer (``packages.shared.crud.model_versions``) must
now be scope-aware so a league's trained model can be promoted, read and
versioned WITHOUT touching the legacy global rows (and vice versa):

* ``create_model_version`` persists ``competition_id`` on the row and, when
  promoting (``set_active=True``), deactivates ONLY same-scope versions —
  same ``model_name`` AND same ``competition_id``, where the NULL scope is
  its own scope (SQL ``competition_id IS NULL`` matching, never ``== None``).
  The deactivation necessarily runs BEFORE the new row's INSERT: migration
  0015's partial unique indexes allow only one ACTIVE row per scope, so an
  insert-then-deactivate sequence would fail the index at flush time (two
  active rows in flight).  The observable end state is identical to the
  pre-FCL sequence — exactly one active version per scope, one transaction.
* ``get_active_model_version`` / ``get_active_coefficients`` /
  ``get_active_model_artifact`` take an optional ``competition_id``; a
  NULL-scope query matches only NULL-scope rows, so a scoped read returns
  ``None`` when only a different-scope version is active (no cross-scope
  leakage).
* ``next_version_number`` takes ``max(version)`` ACROSS ALL scopes
  (post-review C-1 revision): ``uq_model_versions_name_version`` keeps
  ``(model_name, version)`` unique table-wide, so any per-scope counter
  would re-emit a number another scope already owns — the weekly global
  AFL retrain would collide with a league row one week after the first
  league trains.  Cross-scope max is the only collision-free allocation;
  the postgres tier pins exactly that scenario.
* Omitting ``competition_id`` (the default) keeps the legacy global-path
  statement shape: on any pre-FCL database every row is NULL-scope, so the
  appended ``IS NULL`` predicate selects exactly the rows the pre-FCL
  statements selected — byte-identical observable behaviour for the weekly
  AFL retrain and boosted retrain callers, which stay untouched.

Two test tiers in this module:

* Statement-shape unit tests (no DB): an ``AsyncMock`` session captures the
  statements the CRUD hands to ``session.execute()``; we compile them and
  pin the scope predicates (bind-param names normalised away).
* Postgres-marked promote-semantics tests (podman testcontainer, gated and
  skipping cleanly exactly like ``test_league_tips_model.py``): global vs
  scoped active rows COEXISTING, no cross-scope leakage, scoped promote
  rotation, and independent version counters against real Postgres NULL
  semantics.
"""

from __future__ import annotations

import inspect
import re
import shutil
import socket
import subprocess
import time
import uuid
from typing import AsyncIterator, Iterator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from packages.shared.crud import model_versions as crud_module
from packages.shared.crud.model_versions import (
    create_model_version,
    get_active_coefficients,
    get_active_model_artifact,
    get_active_model_version,
    next_version_number,
)
from packages.shared.models import Base, Competition, ModelVersion, Sport

#: The legacy weighted_tip model name — same table, same CRUD as production.
_MODEL = "weighted_tip"


# ---------------------------------------------------------------------------
# Part A — statement-shape unit tests (no database; run everywhere)
# ---------------------------------------------------------------------------

_BIND_PARAM_RE = re.compile(r":(\w+)")


def _mock_session(result: object | None = None, scalar: object | None = None) -> AsyncMock:
    """AsyncMock(spec=AsyncSession) whose ``execute`` returns a canned result.

    ``result`` feeds ``scalar_one_or_none()`` (read helpers); ``scalar`` feeds
    ``scalar()`` (``next_version_number``).  Every statement passed to
    ``execute`` stays captured on ``await_args_list`` for inspection.
    """
    session = AsyncMock(spec=AsyncSession)
    executed = MagicMock()
    executed.scalar_one_or_none = MagicMock(return_value=result)
    executed.scalar = MagicMock(return_value=scalar)
    session.execute = AsyncMock(return_value=executed)
    return session


def _captured_statement(session: AsyncMock):
    """The most recent statement the CRUD handed to ``session.execute``."""
    assert session.execute.await_args_list, "expected at least one execute() call"
    return session.execute.await_args_list[-1].args[0]


def _normalized_where(stmt) -> str:
    """Compile the statement's WHERE clause with table + bind params anonymised.

    Stripping ``model_versions.`` and normalising ``:model_name_1`` to ``:?``
    pins the predicate SHAPE (the byte-identical-semantics contract) without
    coupling the tests to SQLAlchemy's table-qualification mode or bind-param
    numbering, which differ across render contexts (UPDATE vs SELECT).
    """
    whereclause = stmt.whereclause
    assert whereclause is not None, "statement must carry a WHERE clause"
    sql = str(whereclause).replace("model_versions.", "")
    return _BIND_PARAM_RE.sub(":?", sql)


class TestCreateModelVersionScope:
    """``create_model_version`` persists the scope and deactivates in-scope only."""

    async def test_no_argument_persists_null_scope(self):
        session = _mock_session()
        mv = await create_model_version(
            session,
            model_name=_MODEL,
            version=1,
            intercept=0.0,
            training_rows=10,
            metrics=None,
            coefficients={"feat": 1.0},
            set_active=True,
        )
        # No argument -> legacy NULL scope on the persisted row.
        assert mv.competition_id is None

    async def test_scoped_create_persists_competition_id(self):
        session = _mock_session()
        mv = await create_model_version(
            session,
            model_name=_MODEL,
            version=1,
            intercept=0.0,
            training_rows=10,
            metrics=None,
            coefficients={"feat": 1.0},
            set_active=True,
            competition_id=42,
        )
        assert mv.competition_id == 42

    async def test_no_argument_deactivate_is_legacy_shape_plus_null_scope(self):
        """Global promote (no argument): legacy predicate + IS NULL scope.

        The legacy (pre-FCL) deactivate statement filtered on
        ``model_name == X`` (plus ``id != self``, which existed only to
        spare the just-inserted row — under deactivate-before-insert the
        new row is not in the database yet, so no row needs sparing).
        FCL-4 appends the NULL-scope predicate: on any legacy database
        every row is NULL-scope, so the statement deactivates precisely
        the rows the legacy one did — and the weekly AFL retrain callers,
        which pass no argument, are unchanged.
        """
        session = _mock_session()
        await create_model_version(
            session,
            model_name=_MODEL,
            version=2,
            intercept=0.0,
            training_rows=10,
            metrics=None,
            coefficients={"feat": 1.0},
            set_active=True,
        )
        stmt = _captured_statement(session)
        assert _normalized_where(stmt) == (
            "model_name = :? AND competition_id IS NULL"
        )

    async def test_scoped_deactivate_targets_only_that_competition(self):
        """Scoped promote deactivates ONLY same-model same-competition rows."""
        session = _mock_session()
        await create_model_version(
            session,
            model_name=_MODEL,
            version=1,
            intercept=0.0,
            training_rows=10,
            metrics=None,
            coefficients={"feat": 1.0},
            set_active=True,
            competition_id=42,
        )
        stmt = _captured_statement(session)
        assert _normalized_where(stmt) == "model_name = :? AND competition_id = :?"
        # The bound value is the competition id itself (never a NULL bug).
        assert 42 in stmt.compile().params.values()

    async def test_deactivate_runs_before_the_new_row_is_inserted(self):
        """Promote deactivates same-scope actives BEFORE the INSERT flush.

        Migration 0015's partial unique indexes allow only ONE active row
        per ``(model_name, scope)``, so an insert-then-deactivate sequence
        fails the index at flush time (two active rows in flight).  The
        deactivating UPDATE must be awaited before the flush that persists
        the new active row — this ordering is load-bearing for the weekly
        retrain against any migrated (0015) database.
        """
        session = _mock_session()
        await create_model_version(
            session,
            model_name=_MODEL,
            version=1,
            intercept=0.0,
            training_rows=10,
            metrics=None,
            coefficients={"feat": 1.0},
            set_active=True,
            competition_id=42,
        )
        names = [c[0] for c in session.mock_calls]
        assert names.index("execute") < names.index("flush"), (
            "deactivating UPDATE must run before the INSERT flush, or the "
            "partial unique active index (migration 0015) rejects the insert"
        )

    async def test_set_active_false_runs_no_deactivation(self):
        session = _mock_session()
        await create_model_version(
            session,
            model_name=_MODEL,
            version=1,
            intercept=0.0,
            training_rows=10,
            metrics=None,
            coefficients={"feat": 1.0},
            set_active=False,
            competition_id=42,
        )
        session.execute.assert_not_awaited()

    def test_competition_id_is_keyword_only_defaulting_to_none(self):
        """New parameters default to the legacy behaviour when omitted."""
        param = inspect.signature(create_model_version).parameters["competition_id"]
        assert param.kind is inspect.Parameter.KEYWORD_ONLY
        assert param.default is None


class TestReadScopeFilters:
    """Reads filter on competition_id; NULL-scope queries match NULL rows only."""

    async def test_get_active_model_version_default_is_null_scope(self):
        session = _mock_session(result=None)
        await get_active_model_version(session, _MODEL)
        where = _normalized_where(_captured_statement(session))
        assert where.endswith("AND competition_id IS NULL")
        assert "competition_id = " not in where

    async def test_get_active_model_version_scoped(self):
        session = _mock_session(result=None)
        await get_active_model_version(session, _MODEL, competition_id=7)
        stmt = _captured_statement(session)
        where = _normalized_where(stmt)
        assert "competition_id = :?" in where
        assert "competition_id IS NULL" not in where
        assert 7 in stmt.compile().params.values()

    async def test_get_active_model_version_scope_is_positional_or_keyword(self):
        session = _mock_session(result=None)
        # Both call shapes reach the same scope predicate.
        await get_active_model_version(session, _MODEL, 7)
        assert "competition_id = :?" in _normalized_where(_captured_statement(session))

    async def test_next_version_number_is_cross_scope(self):
        """C-1 regression pin: the counter spans ALL scopes.

        The max must cover NULL-scope and league rows alike — a per-scope
        counter would let the weekly global AFL retrain re-emit a version
        number a league row already owns and fail the table-wide
        ``uq_model_versions_name_version`` on INSERT.
        """
        session = _mock_session(scalar=4)
        assert await next_version_number(session, _MODEL) == 5
        # No scope predicate at all: model_name is the only filter.
        assert _normalized_where(_captured_statement(session)) == "model_name = :?"

    async def test_next_version_number_takes_no_scope_argument(self):
        """The allocation rule is global by construction — no scope knob."""
        import inspect

        assert "competition_id" not in inspect.signature(
            next_version_number
        ).parameters


class TestReadWrappersPassScopeThrough:
    """``get_active_coefficients`` / ``get_active_model_artifact`` forward scope."""

    async def test_get_active_coefficients_passes_competition_id(self):
        session = _mock_session(result=None)
        with (
            patch.object(
                crud_module, "get_active_model_version", new=AsyncMock(return_value=None)
            ) as inner,
        ):
            got = await crud_module.get_active_coefficients(
                session, _MODEL, competition_id=9
            )
        assert got is None
        inner.assert_awaited_once_with(session, _MODEL, competition_id=9)

    async def test_get_active_coefficients_default_has_no_scope_argument(self):
        session = _mock_session(result=None)
        with (
            patch.object(
                crud_module, "get_active_model_version", new=AsyncMock(return_value=None)
            ) as inner,
        ):
            await crud_module.get_active_coefficients(session, _MODEL)
        inner.assert_awaited_once_with(session, _MODEL, competition_id=None)

    async def test_get_active_model_artifact_passes_competition_id(self):
        session = _mock_session(result=None)
        with (
            patch.object(
                crud_module, "get_active_model_version", new=AsyncMock(return_value=None)
            ) as inner,
        ):
            got = await crud_module.get_active_model_artifact(
                session, _MODEL, competition_id=3
            )
        assert got is None
        inner.assert_awaited_once_with(session, _MODEL, competition_id=3)


# ---------------------------------------------------------------------------
# Part B — Postgres-marked promote-semantics tests (podman testcontainer)
# Mirrors test_league_tips_model.py gating: the container fixture skips
# cleanly (per-test) when podman is unavailable; unmarked tests above are
# unaffected.
# ---------------------------------------------------------------------------

_POSTGRES_IMAGE = "docker.io/library/postgres:16-alpine"
_CONTAINER_NAME_PREFIX = "wimt-pg-mvcrud-"
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
def pg_container() -> Iterator[str]:
    """One-shot ``postgres:16-alpine`` container; yields the asyncpg URL.

    Skips the requesting test (and only the tests requesting this fixture)
    when podman is unusable — the standard repo gating.
    """
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
        yield f"postgresql+asyncpg://{user}:{password}@127.0.0.1:{port}/{db}"
    finally:
        subprocess.run(["podman", "rm", "-f", container_name],
                       capture_output=True, text=True)


@pytest_asyncio.fixture(scope="module")
async def engine(pg_container: str):
    """Engine against the testcontainer; schema bootstrapped via metadata."""
    bootstrap = create_async_engine(pg_container, future=True)
    async with bootstrap.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await bootstrap.dispose()

    eng = create_async_engine(pg_container, future=True, poolclass=NullPool)
    try:
        yield eng
    finally:
        await eng.dispose()


async def _wipe(engine) -> None:
    """Truncate the scope-relevant tables (children before parents)."""
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        await session.execute(delete(ModelVersion))
        await session.execute(delete(Competition))
        await session.execute(delete(Sport))
        await session.commit()


@pytest_asyncio.fixture
async def db(engine) -> AsyncIterator[AsyncSession]:
    """Fresh session per test with a clean ``model_versions`` slate."""
    await _wipe(engine)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session
    await _wipe(engine)


async def _seed_competition(db: AsyncSession, name: str) -> Competition:
    """Insert (idempotently) the ``afl`` sport and one named competition."""
    if await db.get(Sport, "afl") is None:
        db.add(Sport(id="afl", display_name="AFL"))
        await db.flush()
    comp = Competition(sport_id="afl", name=name)
    db.add(comp)
    await db.commit()
    await db.refresh(comp)
    return comp


def _version_kwargs(version: int) -> dict:
    """Deterministic payload for one model-version row."""
    return {
        "model_name": _MODEL,
        "version": version,
        "intercept": 0.25,
        "training_rows": 10,
        "metrics": {"r2": 0.1, "mae": 2.0},
        "coefficients": {"feat_a": 1.5, "feat_b": -0.5},
    }


@pytest.mark.postgres
@pytest.mark.asyncio
class TestScopedPromoteSemantics:
    """Promote/read semantics on real Postgres (real SQL NULL behaviour)."""

    async def test_global_and_scoped_active_rows_coexist(self, db):
        """One active version per scope — promotes never cross scopes.

        This is the FCL-4 core contract: the weekly GLOBAL retrain and a
        league's retrain can each promote without destroying the other's
        active version (the two partial unique indexes from migration 0015
        guarantee the schema allows it; the CRUD must keep it that way).
        """
        comp_a = await _seed_competition(db, "Scoped CRUD League A")
        comp_b = await _seed_competition(db, "Scoped CRUD League B")

        # Distinct version numbers across scopes: uq_model_versions_name_version
        # (model_name, version) is global, so callers sharing a model_name must
        # not reuse version numbers — semantics under test here are the SCOPE
        # predicates, not that constraint.
        g1 = await create_model_version(db, **_version_kwargs(1), set_active=True)
        a2 = await create_model_version(
            db, **_version_kwargs(2), set_active=True, competition_id=comp_a.id
        )
        b4 = await create_model_version(
            db, **_version_kwargs(4), set_active=True, competition_id=comp_b.id
        )

        # All three scopes read their own active row.
        assert (await get_active_model_version(db, _MODEL)).id == g1.id
        assert (
            await get_active_model_version(db, _MODEL, competition_id=comp_a.id)
        ).id == a2.id
        assert (
            await get_active_model_version(db, _MODEL, competition_id=comp_b.id)
        ).id == b4.id

        # A new GLOBAL promote deactivates only the global row...
        g3 = await create_model_version(db, **_version_kwargs(3), set_active=True)
        assert (await get_active_model_version(db, _MODEL)).id == g3.id
        await db.refresh(g1)
        await db.refresh(a2)
        await db.refresh(b4)
        assert g1.is_active is False
        assert a2.is_active is True, "global promote leaked into league scope A"
        assert b4.is_active is True, "global promote leaked into league scope B"

        # ...and a scoped promote deactivates only that competition's row.
        a5 = await create_model_version(
            db, **_version_kwargs(5), set_active=True, competition_id=comp_a.id
        )
        assert (
            await get_active_model_version(db, _MODEL, competition_id=comp_a.id)
        ).id == a5.id
        await db.refresh(g3)
        await db.refresh(a2)
        await db.refresh(b4)
        assert g3.is_active is True, "scoped promote leaked into the global scope"
        assert a2.is_active is False, "same-scope rotation must deactivate the old row"
        assert b4.is_active is True, "scoped promote leaked into league scope B"

    async def test_null_scope_is_its_own_scope_on_promote(self, db):
        """A scoped promote never deactivates the NULL-scope active row."""
        comp_a = await _seed_competition(db, "Scoped CRUD League C")

        g1 = await create_model_version(db, **_version_kwargs(10), set_active=True)
        a1 = await create_model_version(
            db, **_version_kwargs(11), set_active=True, competition_id=comp_a.id
        )
        a2 = await create_model_version(
            db, **_version_kwargs(12), set_active=True, competition_id=comp_a.id
        )

        assert (await get_active_model_version(db, _MODEL)).id == g1.id
        assert (
            await get_active_model_version(db, _MODEL, competition_id=comp_a.id)
        ).id == a2.id
        await db.refresh(g1)
        await db.refresh(a1)
        assert g1.is_active is True
        assert a1.is_active is False

    async def test_create_persists_scope_on_the_row(self, db):
        comp_a = await _seed_competition(db, "Scoped CRUD League D")

        scoped = await create_model_version(
            db, **_version_kwargs(1), set_active=True, competition_id=comp_a.id
        )
        assert scoped.competition_id == comp_a.id
        # Read back through a scoped read — round-trips from the database.
        fresh = await get_active_model_version(db, _MODEL, competition_id=comp_a.id)
        assert fresh is not None
        assert fresh.competition_id == comp_a.id

        legacy = await create_model_version(db, **_version_kwargs(2), set_active=True)
        assert legacy.competition_id is None

    async def test_no_cross_scope_leakage_reads_return_none(self, db):
        """A scoped read returns None when only a DIFFERENT scope is active."""
        comp_a = await _seed_competition(db, "Scoped CRUD League E")
        comp_b = await _seed_competition(db, "Scoped CRUD League F")

        await create_model_version(
            db,
            **_version_kwargs(1),
            artifact=b"\x89scoped-artifact-bytes",
            artifact_format="json",
            set_active=True,
            competition_id=comp_a.id,
        )

        # League B has no version: every scoped read must come back empty...
        assert await get_active_model_version(db, _MODEL, competition_id=comp_b.id) is None
        assert await get_active_coefficients(db, _MODEL, competition_id=comp_b.id) is None
        assert await get_active_model_artifact(db, _MODEL, competition_id=comp_b.id) is None
        # ...and so must the global (NULL-scope) read — League A's row must
        # never leak into the legacy AFL serving path.
        assert await get_active_model_version(db, _MODEL) is None
        assert await get_active_coefficients(db, _MODEL) is None
        assert await get_active_model_artifact(db, _MODEL) is None

        # Positive controls: League A reads its own row, weights and artifact.
        active = await get_active_model_version(db, _MODEL, competition_id=comp_a.id)
        assert active is not None
        coeffs = await get_active_coefficients(db, _MODEL, competition_id=comp_a.id)
        assert coeffs == (0.25, {"feat_a": 1.5, "feat_b": -0.5})
        bundle = await get_active_model_artifact(db, _MODEL, competition_id=comp_a.id)
        assert bundle is not None
        artifact_bytes, artifact_format, row = bundle
        assert artifact_bytes == b"\x89scoped-artifact-bytes"
        assert artifact_format == "json"
        assert row.id == active.id

    async def test_next_version_number_spans_global_and_league_rows(self, db):
        """C-1 end-to-end: global allocation sees league-owned versions.

        The exact post-review collision sequence: NULL-scope rows up to
        v1, a league row at v2 — the GLOBAL retrain's counter must hand
        back v3 (not v2, which the league row owns and the table-wide
        ``uq_model_versions_name_version`` would reject on INSERT).
        """
        comp_a = await _seed_competition(db, "Scoped CRUD League G")
        comp_b = await _seed_competition(db, "Scoped CRUD League H")

        await create_model_version(db, **_version_kwargs(1), set_active=False)
        await create_model_version(
            db, **_version_kwargs(2), set_active=False, competition_id=comp_a.id
        )

        # The global (weekly AFL retrain) counter skips the league row.
        assert await next_version_number(db, _MODEL) == 3
        # A second league with its own scoped row also allocates past it.
        await create_model_version(
            db, **_version_kwargs(3), set_active=False, competition_id=comp_b.id
        )
        assert await next_version_number(db, _MODEL) == 4

    async def test_legacy_global_path_reads_active_global_row(self, db):
        """No-argument reads keep the legacy semantics end to end."""
        comp_a = await _seed_competition(db, "Scoped CRUD League I")
        created = await create_model_version(db, **_version_kwargs(1), set_active=True)

        # Even with a scoped row present, the unscoped path serves the
        # NULL-scope row only.
        await create_model_version(
            db, **_version_kwargs(2), set_active=True, competition_id=comp_a.id
        )
        active = await get_active_model_version(db, _MODEL)
        assert active is not None
        assert active.id == created.id
        assert active.competition_id is None
        assert await get_active_coefficients(db, _MODEL) == (
            0.25,
            {"feat_a": 1.5, "feat_b": -0.5},
        )
