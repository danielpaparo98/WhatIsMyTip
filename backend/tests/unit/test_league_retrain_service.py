"""Unit tests for ``packages.shared.services.league_retrain`` (FCL-5).

Structure mirrors the repo's retrain-service suites:

* **Pure builder tests (no DB)** — :func:`build_league_training_rows`
  produces one ``(features, home-signed margin)`` row per usable
  completed event, with chronological-prefix evidence ONLY (FCL-5b):
  two explicit no-look-ahead pins — truncating the history and mutating
  a LATER result must both leave every earlier row byte-identical.
* **Lazy-import pins (FCL-5d)** — importing the service module (and the
  cron module that imports it) must not load numpy/sklearn/xgboost/shap
  or ``app.*`` (isolated subprocess, ``test_lazy_sklearn.py`` style);
  the below-gate skip path must not load heavy libs either (in-process).
* **Cron delegation tests** — :func:`run_all_league_retrains` isolates
  per-competition failures (one league's error never stops the others).
* **Postgres round-trips** — end-to-end fit + scoped persistence against
  a real Postgres (podman testcontainer, ``test_boosted_retrain_service.py``
  plumbing): scoped actives are promoted WITHOUT touching the legacy
  NULL-scope global rows, artifacts round-trip through the production
  loader, and two competitions never collide on
  ``uq_model_versions_name_version``.  Skipped when Podman is unusable.
"""

from __future__ import annotations

import subprocess
import sys
import textwrap
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio

from packages.shared.services import league_retrain
from packages.shared.services.league_consensus import (
    league_feature_names_for,
)
from packages.shared.services.league_heuristics import (
    LEAGUE_HEURISTICS,
    CompletedEvent,
)

BACKEND_DIR = Path(__file__).resolve().parents[2]  # backend/

# Heavy ML libraries that must NOT load on the always-on path (same guard
# list as tests/unit/test_lazy_sklearn.py).
_HEAVY_LIBS = ("sklearn", "scipy", "numpy", "xgboost", "shap")

_KICKOFF = datetime(2026, 4, 4, 14, 40)

#: The full five-model set (all :data:`LEAGUE_MODELS` entries).
FULL_MODEL_SET = (
    "home_advantage",
    "form",
    "ladder",
    "elo",
    "matchup",
)


def _assert_isolated_import_ok(code: str) -> None:
    """Run *code* in a fresh interpreter; assert rc=0 (mirrors test_lazy_sklearn)."""
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        cwd=str(BACKEND_DIR),
        timeout=120,
    )
    assert result.returncode == 0, (
        "Isolated import check failed:\n"
        f"--- code ---\n{code}"
        f"--- stdout ---\n{result.stdout}"
        f"--- stderr ---\n{result.stderr}"
    )


# ---------------------------------------------------------------------------
# Synthetic completed events (pure — no DB).
# ---------------------------------------------------------------------------


def _event(
    event_id: int,
    *,
    season_id: int = 1,
    starts_at: datetime | None = _KICKOFF,
    home_side: int | None = 101,
    away_side: int | None = 102,
    home_pid: int | None = 11,
    away_pid: int | None = 12,
    home_score: int | None = None,
    away_score: int | None = None,
) -> CompletedEvent:
    """A completed-event snapshot; decided once both scores are given."""
    return CompletedEvent(
        event_id=event_id,
        season_id=season_id,
        starts_at=starts_at,
        home_side_id=home_side,
        away_side_id=away_side,
        home_participant_id=home_pid,
        away_participant_id=away_pid,
        home_score=home_score,
        away_score=away_score,
    )


def _history(n: int) -> list[CompletedEvent]:
    """``n`` decided events for participants 11/12 with varying margins.

    Kick-offs increase with ``event_id`` so chronological order is the
    insertion order; margins vary so feature vectors differ per row.
    """
    events: list[CompletedEvent] = []
    for i in range(n):
        events.append(
            _event(
                i + 1,
                starts_at=_KICKOFF + timedelta(days=i),
                home_score=50 + (i * 7) % 30,
                away_score=45 + (i * 5) % 25,
            )
        )
    return events


# ---------------------------------------------------------------------------
# Lazy-import pins (FCL-5d) — isolated subprocess.
# ---------------------------------------------------------------------------


class TestLazyImports:
    def test_module_import_does_not_load_heavy_libs(self):
        """The service module must not pull numpy/sklearn/xgboost/shap at import.

        The cron module imports this service at module top (so the scheduler
        transitively imports it on every API worker) — heavy ML imports may
        only live inside ``run_league_retrain``, past the skip gate (FCL-5d).
        """
        code = textwrap.dedent(
            f"""
            import sys
            import packages.shared.services.league_retrain
            offenders = sorted(lib for lib in {_HEAVY_LIBS!r} if lib in sys.modules)
            assert not offenders, (
                "Importing packages.shared.services.league_retrain loaded heavy "
                f"ML libs at module load time: {{offenders}}"
            )
            """
        )
        _assert_isolated_import_ok(code)

    def test_module_import_does_not_import_app(self):
        """FCL-5: ``packages.shared`` must not import ``app.*`` at module load.

        The app imports shared, never the reverse (repo convention, deferred
        import pattern of ``league_heuristics._raise_model_unavailable``).
        """
        code = textwrap.dedent(
            """
            import sys
            import packages.shared.services.league_retrain
            offenders = sorted(
                m for m in sys.modules if m == "app" or m.startswith("app.")
            )
            assert not offenders, (
                "packages.shared.services.league_retrain imported app.* at "
                f"module load time: {offenders}"
            )
            """
        )
        _assert_isolated_import_ok(code)


# ---------------------------------------------------------------------------
# Constants — the documented league-scale skip gate (FCL-5c).
# ---------------------------------------------------------------------------


class TestConstants:
    def test_min_league_training_rows_below_system_a_gate(self):
        """The league gate is a documented, league-scale constant.

        League histories are one to two orders of magnitude smaller than
        the AFL ``games`` corpus, so the gate must sit well below System
        A's 100-row gate while still giving the small feature matrix
        enough observations per feature.
        """
        from packages.shared.services.model_retrain import MIN_TRAINING_ROWS

        assert league_retrain.MIN_LEAGUE_TRAINING_ROWS == 30
        assert league_retrain.MIN_LEAGUE_TRAINING_ROWS < MIN_TRAINING_ROWS

    def test_model_names_reuse_legacy_names_scoped(self):
        """League combiners persist under the System A names, scoped by FK."""
        assert league_retrain.WEIGHTED_TIP_MODEL_NAME == "weighted_tip"
        assert league_retrain.BOOSTED_TIP_MODEL_NAME == "boosted_tip"

    def test_league_boosted_params_are_deterministic_and_league_scale(self):
        """Baseline XGB params: pinned seed, fast hist method, small ensemble."""
        params = league_retrain.LEAGUE_BOOSTED_PARAMS
        assert params["random_state"] == 42  # deterministic fits
        assert params["tree_method"] == "hist"
        assert params["n_jobs"] == 4
        # League datasets are 1-2 orders of magnitude smaller than the AFL
        # corpus — the ensemble must be proportionally smaller than the
        # System A baseline (400 trees) to avoid memorising tiny histories.
        assert params["n_estimators"] < 400


# ---------------------------------------------------------------------------
# build_league_training_rows — pure builder, no look-ahead (FCL-5a/5b).
# ---------------------------------------------------------------------------


class TestBuildLeagueTrainingRows:
    def test_targets_are_home_signed_margins(self):
        """Target = home_score - away_score, mirroring System A."""
        events = [
            _event(1, home_score=80, away_score=65),
            _event(2, home_score=55, away_score=70),
            _event(3, home_score=50, away_score=50),  # draw -> target 0.0
        ]
        rows = league_retrain.build_league_training_rows(events)
        assert [target for _, target in rows] == [15.0, -15.0, 0.0]

    def test_rows_are_in_chronological_order(self):
        """Input row order is irrelevant; output follows kick-off order."""
        events = list(reversed(_history(4)))
        rows = league_retrain.build_league_training_rows(events)
        # Margin sequence of _history: identifiable via targets.
        assert [target for _, target in rows] == [
            float(50 + (i * 7) % 30 - (45 + (i * 5) % 25)) for i in range(4)
        ]

    def test_undecided_rows_are_skipped_but_stay_evidence(self):
        """Missing scores -> no training row; decided rows still read them.

        An undecided completed event occupies a chronological slot (the
        pick functions treat it as non-win evidence) but contributes no
        (features, target) pair.
        """
        events = [
            _event(1, home_score=60, away_score=50),
            _event(2, home_score=None, away_score=40),  # undecided
            _event(3, home_score=70, away_score=60),
        ]
        rows = league_retrain.build_league_training_rows(events)
        assert [target for _, target in rows] == [10.0, 10.0]

    def test_sideless_rows_are_skipped(self):
        """Events without both participants can never be training rows."""
        events = [
            _event(1, home_score=60, away_score=50),
            _event(2, home_pid=None, home_score=60, away_score=50),
            _event(3, away_pid=None, home_score=60, away_score=50),
        ]
        rows = league_retrain.build_league_training_rows(events)
        assert [target for _, target in rows] == [10.0]

    def test_feature_width_matches_the_contract(self):
        """Features and their names derive from the SAME model list (FCL-3b)."""
        for model_names in (LEAGUE_HEURISTICS, FULL_MODEL_SET):
            rows = league_retrain.build_league_training_rows(
                _history(6), model_names=model_names
            )
            expected_width = len(league_feature_names_for(model_names))
            assert rows, "history must produce rows"
            assert all(len(features) == expected_width for features, _ in rows)

    def test_no_look_ahead_truncated_history_is_byte_identical(self):
        """THE no-look-ahead pin (FCL-5b): rows depend only on the past.

        Building rows from a history TRUNCATED after event k must yield
        exactly the same first k rows as building from the full history —
        a future result can never leak into an earlier row's features.
        """
        full = _history(6)
        rows_full = league_retrain.build_league_training_rows(full)
        rows_prefix = league_retrain.build_league_training_rows(full[:3])
        assert rows_prefix == rows_full[:3]

    def test_no_look_ahead_later_result_cannot_change_earlier_rows(self):
        """THE no-look-ahead pin (FCL-5b): a later result is invisible.

        Reversing the FINAL event's margin must change that row's target
        and features but leave every EARLIER row byte-identical.
        """
        full = _history(5)
        mutated = full[:-1] + [
            replace(full[-1], home_score=1, away_score=99)
        ]
        rows_before = league_retrain.build_league_training_rows(full)
        rows_after = league_retrain.build_league_training_rows(mutated)
        assert len(rows_before) == len(rows_after) == 5
        assert rows_after[:-1] == rows_before[:-1]
        assert rows_after[-1][1] == 1.0 - 99.0

    def test_ladder_features_are_same_season_scoped(self):
        """Ladder reads ONLY the same-season prefix (standings never cross).

        With a full previous season in the evidence, a new season's first
        event must still see an empty ladder (0 wins diff, 0 pct diff)
        while spanning features (form/elo/matchup) DO read the previous
        season — exactly the slicing ``compute_all_picks`` applies.
        """
        season1 = [
            _event(
                1,
                season_id=1,
                starts_at=_KICKOFF,
                home_score=100,
                away_score=40,
            )
        ]
        season2_opener = _event(
            2,
            season_id=2,
            starts_at=_KICKOFF + timedelta(days=200),
            home_score=55,
            away_score=50,
        )
        rows = league_retrain.build_league_training_rows(
            season1 + [season2_opener], model_names=FULL_MODEL_SET
        )
        assert len(rows) == 2
        # Feature order for FULL_MODEL_SET:
        # home_advantage_home_constant, form_wins_diff, ladder_wins_diff,
        # ladder_pct_diff, elo_rating_diff, matchup_h2h_wins_diff.
        opener_features = rows[1][0]
        assert opener_features[0] == 1.0  # home constant
        # Spanning models read the cross-season prefix:
        assert opener_features[1] == 1.0  # form: 11 won the season-1 meeting
        assert opener_features[2] == 0.0  # ladder wins diff: no season-2 games
        assert opener_features[3] == 0.0  # ladder pct diff: no season-2 games
        assert opener_features[4] == pytest.approx(20.0)  # elo: 1510 vs 1490
        assert opener_features[5] == 1.0  # h2h: 11 leads 1-0

    def test_empty_history_yields_no_rows(self):
        assert league_retrain.build_league_training_rows([]) == []


# ---------------------------------------------------------------------------
# run_league_retrain — skip gate + lazy imports (fetchers monkeypatched).
# ---------------------------------------------------------------------------


def _patch_fetchers(monkeypatch, *, competition, events) -> None:
    """Patch the service's DB fetchers so run_league_retrain needs no DB."""
    monkeypatch.setattr(
        league_retrain,
        "_fetch_competition",
        AsyncMock(return_value=competition),
    )
    monkeypatch.setattr(
        league_retrain,
        "_fetch_completed_events",
        AsyncMock(return_value=events),
    )


class TestSkipGate:
    @pytest.mark.asyncio
    async def test_below_gate_skips_and_never_loads_heavy_libs(self, monkeypatch):
        """<30 rows -> skipped dict; the currently-active versions are
        untouched (nothing persisted) and NO heavy ML lib is loaded —
        the gate sits strictly BEFORE the lazy imports (FCL-5d)."""
        small = _history(league_retrain.MIN_LEAGUE_TRAINING_ROWS - 1)
        _patch_fetchers(
            monkeypatch, competition=("test-league", "afl"), events=small
        )

        heavy_before = {lib for lib in _HEAVY_LIBS if lib in sys.modules}
        result = await league_retrain.run_league_retrain(
            MagicMock(), competition_id=1
        )
        heavy_after = {lib for lib in _HEAVY_LIBS if lib in sys.modules}

        assert result == {
            "status": "skipped",
            "reason": "insufficient_training_rows",
            "competition_id": 1,
            "rows": league_retrain.MIN_LEAGUE_TRAINING_ROWS - 1,
            "min_required": league_retrain.MIN_LEAGUE_TRAINING_ROWS,
        }
        assert not heavy_after - heavy_before, (
            f"skip path loaded heavy ML libs: "
            f"{sorted(heavy_after - heavy_before)}"
        )

    @pytest.mark.asyncio
    async def test_empty_history_skips_with_zero_rows(self, monkeypatch):
        _patch_fetchers(monkeypatch, competition=("test-league", "afl"), events=[])
        result = await league_retrain.run_league_retrain(
            MagicMock(), competition_id=7
        )
        assert result["status"] == "skipped"
        assert result["rows"] == 0
        assert result["competition_id"] == 7

    @pytest.mark.asyncio
    async def test_unknown_competition_still_resolves_default_set(
        self, monkeypatch
    ):
        """A missing competition row falls through to the default set
        (byte-identical resolution) rather than failing the pass."""
        _patch_fetchers(monkeypatch, competition=None, events=[])
        result = await league_retrain.run_league_retrain(
            MagicMock(), competition_id=999
        )
        assert result["status"] == "skipped"  # no events -> no rows


class TestRunAllLeagueRetrains:
    @pytest.mark.asyncio
    async def test_per_league_failure_never_stops_the_pass(self, monkeypatch):
        """FCL-5f: one league's failure is logged + reported; the remaining
        leagues still train (the pass itself never raises)."""
        monkeypatch.setattr(
            league_retrain,
            "_competitions_with_history",
            AsyncMock(return_value=[1, 2, 3]),
        )
        attempted: list[int] = []

        async def fake_retrain(_session, *, competition_id: int):
            attempted.append(competition_id)
            if competition_id == 2:
                raise RuntimeError("xgb exploded")
            return {"status": "trained", "competition_id": competition_id}

        monkeypatch.setattr(league_retrain, "run_league_retrain", fake_retrain)

        # AsyncMock: the H-1 rollback in the failure path awaits
        # session.rollback(), so the fake session must be async-capable
        # (a plain MagicMock would raise TypeError inside the handler).
        session = AsyncMock()
        summary = await league_retrain.run_all_league_retrains(session)
        session.rollback.assert_awaited_once()  # the failed league's transaction

        assert attempted == [1, 2, 3]  # every league attempted, in order
        assert summary["competitions"] == 3
        assert summary["results"]["1"] == {
            "status": "trained",
            "competition_id": 1,
        }
        assert summary["results"]["2"] == {
            "status": "error",
            "reason": "xgb exploded",
        }
        assert summary["results"]["3"] == {
            "status": "trained",
            "competition_id": 3,
        }

    @pytest.mark.asyncio
    async def test_no_history_yields_empty_results(self, monkeypatch):
        monkeypatch.setattr(
            league_retrain,
            "_competitions_with_history",
            AsyncMock(return_value=[]),
        )
        summary = await league_retrain.run_all_league_retrains(MagicMock())
        assert summary == {"competitions": 0, "results": {}}


# ---------------------------------------------------------------------------
# Postgres round-trips (podman testcontainer) — real fit + scoped storage.
# Mirrors tests/unit/test_boosted_retrain_service.py +
# tests/unit/test_model_versions_competition_scope.py plumbing.
# The podman check lives in the fixture (not module level) so the pure
# tests above still run on machines without Podman.
# ---------------------------------------------------------------------------

_POSTGRES_IMAGE = "docker.io/library/postgres:16-alpine"
_CONTAINER_NAME_PREFIX = "wimt-pg-league-retrain-"
_STARTUP_TIMEOUT_S = 60.0
_POLL_INTERVAL_S = 0.25


def _podman_unavailable_reason() -> str | None:
    import shutil

    if shutil.which("podman") is None:
        return "podman is not on PATH"
    try:
        import subprocess as _subprocess

        result = _subprocess.run(
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


def _free_port() -> int:
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_for_postgres_ready(container_name: str, timeout: float) -> None:
    import subprocess as _subprocess
    import time as _time

    deadline = _time.monotonic() + timeout
    last_err: Exception | None = None
    while _time.monotonic() < deadline:
        proc = _subprocess.run(
            ["podman", "exec", container_name, "pg_isready", "-U", "postgres"],
            capture_output=True,
            text=True,
        )
        if proc.returncode == 0:
            return
        last_err = RuntimeError(
            f"pg_isready rc={proc.returncode}: {proc.stderr.strip() or proc.stdout.strip()}"
        )
        _time.sleep(_POLL_INTERVAL_S)
    raise RuntimeError(
        f"Postgres in {container_name} did not become ready "
        f"within {timeout:.1f}s: {last_err}"
    )


@pytest.fixture(scope="module")
def pg_container():
    """One-shot ``postgres:16-alpine`` container; yields the asyncpg URL."""
    import subprocess as _subprocess
    import uuid as _uuid

    reason = _podman_unavailable_reason()
    if reason is not None:
        pytest.skip(reason)

    container_name = _CONTAINER_NAME_PREFIX + _uuid.uuid4().hex[:12]
    port = _free_port()
    user, password, db = "postgres", "test", "postgres"

    proc = _subprocess.run(
        [
            "podman", "run", "-d", "--rm", "--name", container_name,
            "-p", f"127.0.0.1:{port}:5432",
            "-e", f"POSTGRES_USER={user}",
            "-e", f"POSTGRES_PASSWORD={password}",
            "-e", f"POSTGRES_DB={db}",
            _POSTGRES_IMAGE,
        ],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"podman run failed (rc={proc.returncode}): {proc.stderr.strip()}")

    try:
        _wait_for_postgres_ready(container_name, _STARTUP_TIMEOUT_S)
        yield f"postgresql+asyncpg://{user}:{password}@127.0.0.1:{port}/{db}"
    finally:
        _subprocess.run(
            ["podman", "rm", "-f", container_name],
            capture_output=True,
            text=True,
        )


@pytest_asyncio.fixture
async def session_factory(pg_container):
    """Per-test session factory; truncates every table the suite touches."""
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from sqlalchemy.pool import NullPool

    from packages.shared.models import (
        Base,
        Competition,
        Event,
        EventParticipant,
        LeagueTip,
        ModelCoefficient,
        ModelVersion,
        Participant,
        Season,
        Sport,
    )

    bootstrap = create_async_engine(pg_container, future=True)
    async with bootstrap.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await bootstrap.dispose()

    tables = (
        ModelCoefficient,
        ModelVersion,
        LeagueTip,
        EventParticipant,
        Event,
        Season,
        Participant,
        Competition,
        Sport,
    )
    engine = create_async_engine(pg_container, future=True, poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async def _truncate() -> None:
        async with factory() as session:
            for table in tables:
                await session.execute(table.__table__.delete())
            await session.commit()

    await _truncate()
    yield factory
    await _truncate()
    await engine.dispose()


async def _seed_league_history(
    session_factory, *, competition_name: str, n_rounds: int
) -> int:
    """Seed sport + competition + season + 4 teams + ``n_rounds`` rounds.

    Each round hosts two decided matches (0v1 and 2v3) with deterministic
    varying scores, so ``n_rounds`` rounds yield ``2 * n_rounds`` usable
    training rows.  Returns the competition id.
    """
    from packages.shared.models import (
        Competition,
        Event,
        EventParticipant,
        Participant,
        Season,
        Sport,
    )

    async with session_factory() as session:
        # Idempotent sport seeding, async-safe: sync ``session.merge``
        # on an AsyncSession defers IO into a greenlet-less context and
        # the sport row never lands before the competition flush (FK
        # violation).  get-or-add keeps the two-league double-seed safe.
        if await session.get(Sport, "afl") is None:
            session.add(Sport(id="afl", display_name="AFL"))
            await session.flush()
        competition = Competition(
            sport_id="afl", name=competition_name, tier="state", format="rounds"
        )
        session.add(competition)
        await session.flush()
        season = Season(competition_id=competition.id, label="2026", is_current=True)
        session.add(season)
        await session.flush()
        teams = [
            Participant(
                sport_id="afl", kind="team", name=f"{competition_name} Team {i}"
            )
            for i in range(4)
        ]
        session.add_all(teams)
        await session.flush()

        for r in range(n_rounds):
            day = datetime(2026, 4, 1) + timedelta(days=r)
            for m, (hi, ai) in enumerate(((0, 1), (2, 3))):
                event = Event(
                    season_id=season.id,
                    event_type="match",
                    round_id=r,
                    venue=f"Ground {m}",
                    starts_at=day + timedelta(hours=3 * m),
                    status="completed",
                    completed=True,
                    # events.slug is UNIQUE table-wide — include the
                    # competition id so two seeded leagues never collide.
                    slug=f"lr{competition.id}r{r:03d}m{m}",
                )
                session.add(event)
                await session.flush()
                session.add(
                    EventParticipant(
                        event_id=event.id,
                        participant_id=teams[hi].id,
                        side="home",
                        score=60 + (r * 7 + m * 5) % 30,
                    )
                )
                session.add(
                    EventParticipant(
                        event_id=event.id,
                        participant_id=teams[ai].id,
                        side="away",
                        score=55 + (r * 11 + m * 3) % 25,
                    )
                )
        await session.commit()
        return competition.id


class TestRunLeagueRetrainPostgres:
    @pytest.fixture
    def _ml_or_skip(self):
        """Skip gracefully when the (main) ML stack is somehow absent."""
        pytest.importorskip("numpy")
        pytest.importorskip("sklearn")
        pytest.importorskip("xgboost")
        pytest.importorskip("shap")

    @pytest.mark.postgres
    @pytest.mark.asyncio
    async def test_trained_persists_scoped_active_versions(
        self, _ml_or_skip, session_factory
    ):
        """End-to-end: 40 rows -> both fits -> scoped active versions with
        competition_id set, contract-named coefficients and a loadable
        boosted artifact."""
        from sqlalchemy import select

        from packages.shared.crud.model_versions import (
            get_active_model_version,
            get_model_coefficients,
        )
        from packages.shared.models import ModelVersion
        from packages.shared.services.league_consensus import (
            league_feature_names_for,
            load_league_boosted_model,
        )
        from packages.shared.services.league_heuristics import (
            league_model_set_for_sport,
        )

        competition_id = await _seed_league_history(
            session_factory, competition_name="LR Trained", n_rounds=20
        )

        async with session_factory() as session:
            result = await league_retrain.run_league_retrain(
                session, competition_id=competition_id
            )

        assert result["status"] == "trained"
        assert result["competition_id"] == competition_id
        assert result["training_rows"] == 40
        assert result["model_names"] == list(league_model_set_for_sport("afl"))

        # --- persisted rows: scoped, active, contract-named -------------
        async with session_factory() as session:
            versions = list(
                (
                    await session.execute(
                        select(ModelVersion).where(
                            ModelVersion.competition_id == competition_id
                        )
                    )
                ).scalars().all()
            )
        by_name = {v.model_name: v for v in versions}
        assert set(by_name) == {"weighted_tip", "boosted_tip"}

        linear_row = by_name["weighted_tip"]
        assert linear_row.is_active is True
        assert linear_row.training_rows == 40
        assert linear_row.version == result["weighted_tip"]["version"]
        assert linear_row.artifact is None  # linear keeps weights in rows

        boosted_row = by_name["boosted_tip"]
        assert boosted_row.is_active is True
        assert boosted_row.artifact is not None
        assert boosted_row.artifact_format == "json"
        assert boosted_row.shap_base_value == pytest.approx(
            result["boosted_tip"]["metrics"]["shap_base_value"]
        )

        expected_features = league_feature_names_for(
            league_model_set_for_sport("afl")
        )
        async with session_factory() as session:
            linear_coeffs = await get_model_coefficients(session, linear_row.id)
            boosted_coeffs = await get_model_coefficients(session, boosted_row.id)
        assert {c.feature_name for c in linear_coeffs} == set(expected_features)
        # Boosted coefficients carry mean |SHAP| — never negative.
        assert all(c.coefficient >= 0.0 for c in boosted_coeffs)

        # Scoped reads resolve the new versions (FCL-4 CRUD).
        async with session_factory() as session:
            active_linear = await get_active_model_version(
                session, "weighted_tip", competition_id=competition_id
            )
            assert active_linear is not None
            assert active_linear.id == linear_row.id
        async with session_factory() as session:
            from packages.shared.crud.model_versions import (
                get_active_model_artifact,
            )

            bundle = await get_active_model_artifact(
                session, "boosted_tip", competition_id=competition_id
            )
        assert bundle is not None
        artifact_bytes, artifact_format, _row = bundle
        assert artifact_format == "json"

        # Artifact round-trips through the production loader and predicts.
        reg = load_league_boosted_model(artifact_bytes)
        np = pytest.importorskip("numpy")
        async with session_factory() as session:
            events = await league_retrain._fetch_completed_events(
                session, competition_id
            )
        rows = league_retrain.build_league_training_rows(events)
        X = np.array([features for features, _ in rows], dtype=float)  # noqa: N806 — ML design-matrix convention
        preds = reg.predict(X)
        assert bool(np.all(np.isfinite(preds)))

    @pytest.mark.postgres
    @pytest.mark.asyncio
    async def test_scoped_promotion_never_touches_global_rows(
        self, _ml_or_skip, session_factory
    ):
        """THE FCL-5 storage pin: a league's promotion deactivates ONLY that
        competition's rows — the legacy NULL-scope AFL actives stay active
        and unchanged."""
        from packages.shared.crud.model_versions import (
            create_model_version,
            get_active_model_version,
        )

        competition_id = await _seed_league_history(
            session_factory, competition_name="LR Scoped", n_rounds=20
        )
        async with session_factory() as session:
            global_linear = await create_model_version(
                session,
                model_name="weighted_tip",
                version=100,
                intercept=0.5,
                training_rows=9999,
                metrics={"r2": 0.4, "mae": 1.0},
                coefficients={"legacy": 1.0},
                set_active=True,
            )
            global_boosted = await create_model_version(
                session,
                model_name="boosted_tip",
                version=100,
                intercept=0.0,
                training_rows=9999,
                metrics={"r2": 0.4, "mae": 1.0},
                coefficients={"legacy": 1.0},
                artifact=b'{"legacy": true}',
                artifact_format="json",
                set_active=True,
            )

        async with session_factory() as session:
            result = await league_retrain.run_league_retrain(
                session, competition_id=competition_id
            )
        assert result["status"] == "trained"

        async with session_factory() as session:
            still_global_linear = await get_active_model_version(
                session, "weighted_tip"
            )
            still_global_boosted = await get_active_model_version(
                session, "boosted_tip"
            )
        assert still_global_linear is not None
        assert still_global_linear.id == global_linear.id
        assert still_global_linear.is_active is True
        assert still_global_linear.version == 100
        assert still_global_boosted is not None
        assert still_global_boosted.id == global_boosted.id
        assert still_global_boosted.is_active is True

    @pytest.mark.postgres
    @pytest.mark.asyncio
    async def test_below_gate_preserves_scoped_active_and_creates_nothing(
        self, session_factory
    ):
        """FCL-5c: below the gate the currently-active scoped versions stay
        untouched and no new version rows appear."""
        from sqlalchemy import select

        from packages.shared.crud.model_versions import (
            create_model_version,
            get_active_model_version,
        )
        from packages.shared.models import ModelVersion

        competition_id = await _seed_league_history(
            session_factory, competition_name="LR Skip", n_rounds=5
        )  # 10 rows < 30
        async with session_factory() as session:
            existing = await create_model_version(
                session,
                model_name="weighted_tip",
                version=1,
                intercept=0.25,
                training_rows=10,
                metrics={"r2": 0.0, "mae": 2.0},
                coefficients={"x": 1.0},
                set_active=True,
                competition_id=competition_id,
            )

        async with session_factory() as session:
            result = await league_retrain.run_league_retrain(
                session, competition_id=competition_id
            )
        assert result["status"] == "skipped"
        assert result["rows"] == 10

        async with session_factory() as session:
            active = await get_active_model_version(
                session, "weighted_tip", competition_id=competition_id
            )
            scoped_rows = list(
                (
                    await session.execute(
                        select(ModelVersion).where(
                            ModelVersion.competition_id == competition_id
                        )
                    )
                ).scalars().all()
            )
        assert active is not None
        assert active.id == existing.id
        assert active.intercept == 0.25
        assert len(scoped_rows) == 1  # nothing new created

    @pytest.mark.postgres
    @pytest.mark.asyncio
    async def test_deterministic_fit_and_monotonic_versions(
        self, _ml_or_skip, session_factory
    ):
        """Identical data -> identical metrics; versions advance per retrain."""
        competition_id = await _seed_league_history(
            session_factory, competition_name="LR Determinism", n_rounds=20
        )
        async with session_factory() as session:
            first = await league_retrain.run_league_retrain(
                session, competition_id=competition_id
            )
        async with session_factory() as session:
            second = await league_retrain.run_league_retrain(
                session, competition_id=competition_id
            )
        assert first["status"] == second["status"] == "trained"
        assert first["weighted_tip"]["metrics"] == second["weighted_tip"]["metrics"]
        assert (
            first["boosted_tip"]["shap_importance"]
            == second["boosted_tip"]["shap_importance"]
        )
        # Versions advance within the competition's scope (cross-scope counter).
        assert second["weighted_tip"]["version"] > first["weighted_tip"]["version"]
        assert second["boosted_tip"]["version"] > first["boosted_tip"]["version"]
        from packages.shared.crud.model_versions import get_active_model_version

        async with session_factory() as session:
            active = await get_active_model_version(
                session, "weighted_tip", competition_id=competition_id
            )
        assert active is not None
        assert active.version == second["weighted_tip"]["version"]

    @pytest.mark.postgres
    @pytest.mark.asyncio
    async def test_two_competitions_never_collide_on_name_version(
        self, _ml_or_skip, session_factory
    ):
        """THE version-allocation pin: ``uq_model_versions_name_version`` is
        unique across ALL scopes, so two leagues retrains must allocate
        DISTINCT (model_name, version) pairs — the cross-scope counter,
        not a per-scope counter, hands out numbers."""
        from sqlalchemy import select

        from packages.shared.models import ModelVersion
        from packages.shared.services.league_retrain import run_all_league_retrains

        comp_a = await _seed_league_history(
            session_factory, competition_name="LR League A", n_rounds=20
        )
        comp_b = await _seed_league_history(
            session_factory, competition_name="LR League B", n_rounds=20
        )

        async with session_factory() as session:
            summary = await run_all_league_retrains(session)
        assert summary["competitions"] == 2
        assert all(
            r["status"] == "trained" for r in summary["results"].values()
        )

        async with session_factory() as session:
            rows = list(
                (
                    await session.execute(select(ModelVersion))
                ).scalars().all()
            )
        pairs = {(v.model_name, v.version) for v in rows}
        assert len(pairs) == len(rows), (
            "uq_model_versions_name_version violated: duplicate "
            "(model_name, version) across scopes"
        )


@pytest.mark.postgres
@pytest.mark.asyncio
async def test_competitions_with_history_finds_only_usable_competitions(
    session_factory,
):
    """The cron's competition discovery: only competitions with completed
    events that have both sides appear (and every such competition does —
    no LEAGUE_MODEL_SETS restriction; all leagues are first-class)."""
    from packages.shared.models import EventParticipant

    with_history = await _seed_league_history(
        session_factory, competition_name="LR Discovery A", n_rounds=2
    )
    without_history = await _seed_league_history(
        session_factory, competition_name="LR Discovery B", n_rounds=0
    )

    async with session_factory() as session:
        found = await league_retrain._competitions_with_history(session)
    assert found == [with_history]
    assert without_history not in found

    # An event whose sides were retracted (no participants) is not history.
    async with session_factory() as session:
        await session.execute(
            EventParticipant.__table__.delete().where(
                EventParticipant.event_id.is_not(None)
            )
        )
        await session.commit()
    async with session_factory() as session:
        found_after = await league_retrain._competitions_with_history(session)
    assert found_after == []
