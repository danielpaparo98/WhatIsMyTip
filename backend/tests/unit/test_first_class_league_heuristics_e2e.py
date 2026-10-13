"""End-to-end verification of first-class league heuristics (FCL-8).

The final gate of the FCL feature (registration, not branch): the
Subtasks 01-07 units are individually pinned — this module proves the
WHOLE chain against a real Postgres (podman testcontainer, the EXACT
``test_league_heuristics`` e2e plumbing: real migration chain to head,
real ``league_tips`` UNIQUE constraint, real scoped ``model_versions``
indexes) and SKIPS CLEANLY when podman is unavailable:

1. **Registration → sync → derived tips** — a league registered in
   :data:`packages.shared.services.league_heuristics.LEAGUE_MODEL_SETS`
   is seeded exactly the way ``LocalCompetitionSyncService.sync`` writes
   (sport/competition/season/participants/events), then the sync hook's
   own entry point (:func:`generate_league_tips`) runs.  ``league_tips``
   must carry rows for the registered BASE models AND all three derived
   heuristics (best_bet / weighted_tip / boosted_tip) with FALLBACK
   picks — no trained model exists yet, so both combiners ride the
   majority vote (FCL-3d) and zero ``model_versions`` rows exist.
2. **Retrain → learned picks** — enough completed events are seeded to
   clear :data:`MIN_LEAGUE_TRAINING_ROWS` (30) with REAL rows — no gate
   patching — the league retrain runs, and a re-generation flips the
   combiners from the fallback to the scoped learned model.  The data is
   constructed so the flip is DETERMINISTIC: the away side wins every
   match by exactly 40, so every training target is −40 and the learned
   combiners (both of them: the OLS residual is zero and the boosted
   ensemble collapses to the mean) predict −40 for EVERY event — while
   the season-opener's fallback is the tie→home majority vote (FCL-3a).
   The stored tips are also diffed against the picks recomputed from the
   scoped active weights/artifact read back through the FCL-4 CRUD —
   the tips must be exactly what the scoped model says, and the global
   (NULL-scope) actives must stay untouched (FCL-5e).
3. **Byte-identical regression** — an unregistered (state-registry)
   competition keeps its EXACT pre-FCL base model set: the persisted
   base rows equal :func:`compute_all_picks`'s output (the unchanged
   pre-FCL pipeline) name-for-name and pick-for-pick, while the three
   derived names are still gained (FCL-6: every league is first-class).
4. **Cron end-to-end** — :class:`app.cron.model_retrain.ModelRetrainJob`
   (real session factory, real AFL linear/boosted skips on the empty
   legacy ``games`` tables) nests per-league outcomes under its
   ``"leagues"`` key, trains the league above the gate, skips the one
   below it — and STILL succeeds when one league's retrain raises (the
   error is reported in that league's result dict, the other league's
   committed versions survive: FCL-5f partial-success semantics).

Every podman-gated test is marked ``@pytest.mark.postgres`` (skip when
podman is unusable); the ML-dependent ones importorskip the heavy stack.
The ``app.cron`` import is deferred inside the cron test bodies —
``packages.shared`` tests never import ``app.*`` at module load.
"""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import sys
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from typing import Any, AsyncIterator, Callable, Iterator

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from packages.shared.services.league_heuristics import (
    DEFAULT_MODEL_SET,
    DERIVED_LEAGUE_HEURISTICS,
    HEURISTIC_BEST_BET,
    HEURISTIC_BOOSTED_TIP,
    HEURISTIC_ELO,
    HEURISTIC_WEIGHTED_TIP,
    LEAGUE_HEURISTICS,
    LEAGUE_MODEL_SETS,
    compute_all_picks,
    compute_all_picks_and_derived,
    generate_league_tips,
    league_model_set_for_competition,
)

_BACKEND_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))

#: A STATE_LEAGUES identity (the WAFL registration name) that is
#: deliberately absent from :data:`LEAGUE_MODEL_SETS` — the
#: byte-identical regression subject (FCL-2: unregistered → sport →
#: default resolution).
_UNREGISTERED_STATE_LEAGUE = "West Australian Football League"


# ---------------------------------------------------------------------------
# Podman testcontainer plumbing — the EXACT test_league_heuristics.py
# e2e layer: one postgres:16-alpine per module, real migration chain to
# head per test.  Skips cleanly when podman is unavailable.
# ---------------------------------------------------------------------------

_POSTGRES_IMAGE = "docker.io/library/postgres:16-alpine"
_CONTAINER_NAME_PREFIX = "wimt-pg-fcl08-"
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
async def _db_at_head(pg_container: tuple[str, str]) -> AsyncIterator[tuple[str, str]]:
    """Empty public schema upgraded to head (0015). Yields (sync, async) DSNs."""
    sync_dsn, async_dsn = pg_container
    engine = create_async_engine(async_dsn, poolclass=NullPool)
    try:
        async with engine.begin() as conn:
            await conn.execute(text("DROP SCHEMA public CASCADE"))
            await conn.execute(text("CREATE SCHEMA public"))
    finally:
        await engine.dispose()

    proc = _run_alembic(sync_dsn, "upgrade", "head")
    assert proc.returncode == 0, f"upgrade to head failed:\n{proc.stdout}\n{proc.stderr}"
    yield sync_dsn, async_dsn


@pytest.fixture
def _ml_or_skip() -> None:
    """Skip gracefully when the (main-dependency) ML stack is absent."""
    pytest.importorskip("numpy")
    pytest.importorskip("sklearn")
    pytest.importorskip("xgboost")
    pytest.importorskip("shap")


def _job_session_factory(engine: Any) -> Callable[[], Any]:
    """A zero-arg factory yielding an async context manager over one
    session — the ``BaseJob`` session-factory contract, built on the
    testcontainer engine."""
    factory = async_sessionmaker(engine, expire_on_commit=False)

    @asynccontextmanager
    async def _ctx() -> AsyncIterator[Any]:
        async with factory() as session:
            yield session

    return _ctx


# ---------------------------------------------------------------------------
# Seeding — the exact rows LocalCompetitionSyncService.sync writes
# (CompetitionCRUD.ensure_competition/ensure_season + ParticipantResolver
# + EventCRUD.upsert_fixture), expressed as the same raw INSERTs the
# mirrored e2e layer uses.  Single transaction, RETURNING-captured ids.
# ---------------------------------------------------------------------------


async def _seed_synced_league(
    async_dsn: str,
    *,
    competition_name: str,
    rounds: int,
    scores: Callable[[int, int], tuple[int | None, int | None]] | None = None,
    scheduled_events: int = 0,
    season_label: str = "2026",
) -> dict[str, Any]:
    """Seed one synced league and return its ids.

    Four teams, two matches per round (team0 v team1, team2 v team3 —
    fixed home/away pairings), ``rounds`` rounds of COMPLETED events
    scored by ``scores(round, match) -> (home, away)`` — defaulting to
    the away side winning every match by exactly 40 (the deterministic
    learned-vs-fallback construction of the retrain scenario).  Any
    ``scheduled_events`` are inserted as not-completed fixtures (never
    tipped).  ``events`` maps ``"r<round>m<match>"`` (and
    ``"scheduled<n>"``) to the event id and both side ids
    (``event_participants.id`` — the tip-FK targets).
    """
    score_fn = scores or (lambda _round, _match: (20, 60))
    engine = create_async_engine(async_dsn, poolclass=NullPool)

    async def one(conn: Any, sql: str, params: dict[str, Any] | None = None) -> int:
        return (await conn.execute(text(sql), params or {})).scalar_one()

    try:
        async with engine.begin() as conn:
            # Migration 0010 already seeds the 'afl' sport row — the
            # ON CONFLICT mirrors the mirrored e2e seeder's guarantee.
            await conn.execute(
                text(
                    "INSERT INTO sports (id, display_name) "
                    "VALUES ('afl', 'AFL') ON CONFLICT (id) DO NOTHING"
                )
            )
            cid = await one(
                conn,
                "INSERT INTO competitions (sport_id, name, tier, format) "
                "VALUES ('afl', :name, 'state', 'rounds') RETURNING id",
                {"name": competition_name},
            )
            sid = await one(
                conn,
                "INSERT INTO seasons (competition_id, label, is_current) "
                "VALUES (:cid, :label, true) RETURNING id",
                {"cid": cid, "label": season_label},
            )
            team_names = [
                f"{competition_name} United",
                f"{competition_name} Rovers",
                f"{competition_name} Districts",
                f"{competition_name} Central",
            ]
            teams = {
                name: await one(
                    conn,
                    "INSERT INTO participants (sport_id, kind, name) "
                    "VALUES ('afl', 'team', :name) RETURNING id",
                    {"name": name},
                )
                for name in team_names
            }
            pairings = ((0, 1), (2, 3))
            events: dict[str, dict[str, int]] = {}
            counter = 0
            for rnd in range(rounds):
                for match, (hi, ai) in enumerate(pairings):
                    counter += 1
                    starts_at = datetime(2026, 4, 1) + timedelta(
                        days=rnd, hours=3 * match
                    )
                    eid = await one(
                        conn,
                        "INSERT INTO events (season_id, event_type, round_id, venue, "
                        "starts_at, status, completed, slug) "
                        "VALUES (:sid, 'match', :rnd, 'Test Oval', :starts_at, "
                        "'completed', true, :slug) RETURNING id",
                        {"sid": sid, "rnd": rnd, "starts_at": starts_at,
                         "slug": f"fcl08{cid:04d}{counter:04d}"},
                    )
                    hs, as_ = score_fn(rnd, match)
                    decided = hs is not None and as_ is not None and hs != as_
                    winner = ("home" if hs > as_ else "away") if decided else None
                    home_ep = away_ep = None
                    for side, team_name, score in (
                        ("home", team_names[hi], hs),
                        ("away", team_names[ai], as_),
                    ):
                        ep_id = await one(
                            conn,
                            "INSERT INTO event_participants "
                            "(event_id, participant_id, side, score, is_winner) "
                            "VALUES (:eid, :pid, :side, :score, :winner) "
                            "RETURNING id",
                            {
                                "eid": eid,
                                "pid": teams[team_name],
                                "side": side,
                                "score": score,
                                "winner": (side == winner) if decided else None,
                            },
                        )
                        if side == "home":
                            home_ep = ep_id
                        else:
                            away_ep = ep_id
                    events[f"r{rnd}m{match}"] = {
                        "event_id": int(eid),
                        "home_side_id": int(home_ep),
                        "away_side_id": int(away_ep),
                    }
            for sched in range(scheduled_events):
                starts_at = datetime(2026, 8, 1) + timedelta(days=sched)
                eid = await one(
                    conn,
                    "INSERT INTO events (season_id, event_type, round_id, venue, "
                    "starts_at, status, completed, slug) "
                    "VALUES (:sid, 'match', 99, 'Test Oval', :starts_at, "
                    "'scheduled', false, :slug) RETURNING id",
                    {"sid": sid, "starts_at": starts_at,
                     "slug": f"fcl08{cid:04d}s{sched:03d}"},
                )
                # A real fixture always carries BOTH side rows — FCL-12
                # forward tips target them exactly like completed picks.
                home_ep = await one(
                    conn,
                    "INSERT INTO event_participants (event_id, participant_id, "
                    "side) VALUES (:eid, :pid, 'home') RETURNING id",
                    {"eid": eid, "pid": teams[f"{competition_name} United"]},
                )
                away_ep = await one(
                    conn,
                    "INSERT INTO event_participants (event_id, participant_id, "
                    "side) VALUES (:eid, :pid, 'away') RETURNING id",
                    {"eid": eid, "pid": teams[f"{competition_name} Rovers"]},
                )
                events[f"scheduled{sched}"] = {
                    "event_id": int(eid),
                    "home_side_id": int(home_ep),
                    "away_side_id": int(away_ep),
                }
        return {
            "competition_id": int(cid),
            "season_id": int(sid),
            "events": events,
        }
    finally:
        await engine.dispose()


async def _stored_tips(
    async_dsn: str, competition_id: int
) -> dict[tuple[int, str], int | None]:
    """Persisted league_tips picks keyed (event_id, heuristic)."""
    engine = create_async_engine(async_dsn, poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            rows = (
                await conn.execute(
                    text(
                        "SELECT event_id, heuristic, selected_participant_id "
                        "FROM league_tips WHERE competition_id = :cid"
                    ),
                    {"cid": competition_id},
                )
            ).all()
    finally:
        await engine.dispose()
    return {(int(r[0]), str(r[1])): r[2] for r in rows}


async def _scalar(async_dsn: str, sql: str, params: dict[str, Any] | None = None) -> Any:
    engine = create_async_engine(async_dsn, poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            return (await conn.execute(text(sql), params or {})).scalar()
    finally:
        await engine.dispose()


# ---------------------------------------------------------------------------
# Non-postgres premise pin — runs everywhere (no podman needed).
# ---------------------------------------------------------------------------


class TestByteIdenticalPremise:
    def test_state_league_identity_is_unregistered(self) -> None:
        """FCL-8 premise: the state-registry WAFL name has NO entry in
        :data:`LEAGUE_MODEL_SETS`, so the byte-identical scenario below
        genuinely exercises the unregistered (sport → default) path."""
        assert _UNREGISTERED_STATE_LEAGUE not in LEAGUE_MODEL_SETS
        assert (
            league_model_set_for_competition(_UNREGISTERED_STATE_LEAGUE, "afl")
            == DEFAULT_MODEL_SET
        )


# ---------------------------------------------------------------------------
# E2E 1 — registration → sync → derived tips (fallback picks, FCL-8).
# ---------------------------------------------------------------------------


@pytest.mark.postgres
@pytest.mark.asyncio
async def test_registered_league_sync_persists_base_and_fallback_derived_tips(
    _db_at_head, monkeypatch
) -> None:
    """A registered league's sync-hook generation persists its base
    models' tips AND all three derived heuristics with fallback picks —
    before any training run (no ``model_versions`` rows exist, so both
    combiners and best_bet all ride the majority vote, FCL-3d)."""
    _sync_dsn, async_dsn = _db_at_head
    monkeypatch.setitem(
        LEAGUE_MODEL_SETS, "FCL E2E Registered League", (HEURISTIC_ELO,)
    )
    # The registry (league → sport → default) honours the registration.
    assert league_model_set_for_competition(
        "FCL E2E Registered League", "afl"
    ) == (HEURISTIC_ELO,)

    def scores(round_no: int, match_no: int) -> tuple[int | None, int | None]:
        # r1m0 is a DRAW (draw-no-pick discipline across all names);
        # everything else is decided.
        if (round_no, match_no) == (1, 0):
            return (44, 44)
        return [(90, 70), (60, 55), (50, 90), (65, 60), (55, 80)][
            (round_no * 2 + match_no) % 5
        ]

    ids = await _seed_synced_league(
        async_dsn,
        competition_name="FCL E2E Registered League",
        rounds=3,
        scores=scores,
    )
    cid = ids["competition_id"]

    # The EXACT call the sync hook makes
    # (LocalCompetitionSyncService._refresh_league_tips → generate_league_tips).
    engine = create_async_engine(async_dsn, poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            summary = await generate_league_tips(session, competition_id=cid)
    finally:
        await engine.dispose()

    # 6 completed events × (1 base [elo] + 3 derived).
    assert summary["tips_inserted"] == 24
    assert summary["tips_updated"] == 0
    assert summary["derived_tips"] == {
        "best_bet": 6,
        "weighted_tip": 6,
        "boosted_tip": 6,
    }

    stored = await _stored_tips(async_dsn, cid)
    assert len(stored) == 24
    assert {h for _, h in stored} == {HEURISTIC_ELO} | set(DERIVED_LEAGUE_HEURISTICS)

    # Every row is scoped to the competition and its season.
    assert await _scalar(
        async_dsn,
        "SELECT count(*) FROM league_tips "
        "WHERE competition_id = :cid AND season_id = :sid",
        {"cid": cid, "sid": ids["season_id"]},
    ) == 24

    # No trained models exist yet — the fallback is absence-driven.
    assert await _scalar(async_dsn, "SELECT count(*) FROM model_versions") == 0

    # Decided events: the single-vote majority IS the elo pick, so all
    # four names agree per event.  The draw event: NULL across all four.
    for key, entry in ids["events"].items():
        picks = {
            h: stored[(entry["event_id"], h)]
            for h in (HEURISTIC_ELO,) + DERIVED_LEAGUE_HEURISTICS
        }
        if key == "r1m0":  # the draw
            assert all(pick is None for pick in picks.values()), key
            continue
        sides = set(picks.values())
        assert len(sides) == 1, (key, picks)
        assert sides <= {entry["home_side_id"], entry["away_side_id"]}, key
        assert picks[HEURISTIC_WEIGHTED_TIP] == picks[HEURISTIC_ELO], key
        assert picks[HEURISTIC_BOOSTED_TIP] == picks[HEURISTIC_ELO], key
        assert picks[HEURISTIC_BEST_BET] == picks[HEURISTIC_ELO], key


# ---------------------------------------------------------------------------
# E2E 1b — forward tips for SCHEDULED fixtures (FCL-12).
# ---------------------------------------------------------------------------


@pytest.mark.postgres
@pytest.mark.asyncio
async def test_scheduled_fixture_gets_forward_tips_grading_stays_clean(
    _db_at_head,
) -> None:
    """FCL-12: a scheduled fixture with both sides persists the same six
    names as a completed event (picks from prior completed evidence
    only), and the league backtest grades COMPLETED events only — the
    forward tips cannot pollute accuracy before the fixture is played."""
    _sync_dsn, async_dsn = _db_at_head
    ids = await _seed_synced_league(
        async_dsn,
        competition_name="FCL E2E Forward League",
        rounds=2,
        scheduled_events=1,
    )
    cid = ids["competition_id"]
    scheduled_id = ids["events"]["scheduled0"]["event_id"]

    engine = create_async_engine(async_dsn, poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            summary = await generate_league_tips(session, competition_id=cid)
    finally:
        await engine.dispose()

    # 4 completed + 1 scheduled, × 6 names (unregistered → D3 trio + derived).
    assert summary["tips_inserted"] == 30
    assert summary["scheduled_considered"] == 1

    stored = await _stored_tips(async_dsn, cid)
    scheduled_names = {h for (eid, h) in stored if eid == scheduled_id}
    assert scheduled_names == set(LEAGUE_HEURISTICS) | set(
        DERIVED_LEAGUE_HEURISTICS
    )
    # The scheduled fixture's picks point at real sides (not draw-NULL).
    assert all(
        stored[(scheduled_id, h)] is not None
        for h in scheduled_names
    )

    # Grading cleanliness: the season comparison counts only completed
    # rounds' tips — 4 completed × 6 names, the scheduled fixture's six
    # rows are invisible to accuracy until the fixture completes.
    from packages.shared.services.league_backtest import LeagueBacktestService

    engine = create_async_engine(async_dsn, poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            payload = await LeagueBacktestService().compare_season(
                session,
                competition_id=cid,
                season_label="2026",
                models=LEAGUE_HEURISTICS + DERIVED_LEAGUE_HEURISTICS,
            )
    finally:
        await engine.dispose()
    for name, stats in payload["comparison"].items():
        assert stats["total_tips"] == 4, name

    # FCL-12 round lookup: the bulk round endpoint resolves picks to TEAM
    # NAMES via the side row (selected_participant_id targets
    # event_participants.id, NOT participants.id — pinned after this
    # exact join bug shipped NULL picks on the AFLW round payload).
    from packages.shared.services.league_heuristics import league_round_tips

    engine = create_async_engine(async_dsn, poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            round_payload = await league_round_tips(
                session, competition_id=cid, round_id=99
            )
            sides = (
                await session.execute(
                    text(
                        "SELECT p.name FROM event_participants ep "
                        "JOIN participants p ON p.id = ep.participant_id "
                        "WHERE ep.event_id = :eid"
                    ),
                    {"eid": round_payload["fixtures"][0]["event_id"]},
                )
            ).all()
    finally:
        await engine.dispose()
    team_names = {str(r[0]) for r in sides}
    assert round_payload["season"] == "2026"
    assert len(round_payload["fixtures"]) == 1
    fixture = round_payload["fixtures"][0]
    assert fixture["slug"].startswith("fcl08")
    named = [v for v in fixture["picks"].values() if v is not None]
    assert len(named) == len(LEAGUE_HEURISTICS) + len(
        DERIVED_LEAGUE_HEURISTICS
    ), fixture["picks"]
    assert set(named) <= team_names, (named, team_names)


# ---------------------------------------------------------------------------
# E2E 2 — retrain → learned picks replace the fallback (FCL-8).
# ---------------------------------------------------------------------------


@pytest.mark.postgres
@pytest.mark.asyncio
async def test_retrain_switches_combiners_from_fallback_to_scoped_learned_model(
    _db_at_head, _ml_or_skip
) -> None:
    """Above the real training gate (40 rows > MIN_LEAGUE_TRAINING_ROWS
    — no patching), the league retrain trains scoped combiners and a
    re-generation replaces the fallback picks with the learned ones.

    Deterministic construction: the away side wins every match by 40,
    so every training target is −40 and BOTH learned combiners predict
    −40 for every event — flipping the season opener from the
    fallback's tie→home vote to AWAY, while best_bet (pure, untrained)
    stays home.  The stored tips must exactly match the picks recomputed
    from the scoped active model (FCL-4 read-back), and the global
    NULL-scope actives must remain untouched (FCL-5e)."""
    from packages.shared.crud.model_versions import (
        get_active_coefficients,
        get_active_model_artifact,
        get_active_model_version,
    )
    from packages.shared.services import league_retrain
    from packages.shared.services.league_consensus import load_league_boosted_model

    _sync_dsn, async_dsn = _db_at_head
    name = "FCL E2E Learned League"
    ids = await _seed_synced_league(
        async_dsn, competition_name=name, rounds=20
    )  # 20 rounds × 2 matches = 40 usable training rows
    cid = ids["competition_id"]
    opener = ids["events"]["r0m0"]
    models = league_model_set_for_competition(name, "afl")

    engine = create_async_engine(async_dsn, poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            events = await league_retrain._fetch_completed_events(session, cid)

        # --- generation BEFORE training: everything is the fallback ----
        async with factory() as session:
            first = await generate_league_tips(session, competition_id=cid)
        stored = await _stored_tips(async_dsn, cid)
        fallback = {
            (p.event_id, p.heuristic): p.selected_side_id
            for p in compute_all_picks_and_derived(events, models=models)
        }
        assert first["tips_inserted"] == 240  # 40 events × (3 base + 3 derived)
        assert stored == fallback
        # The opener's fallback is the tie→home majority vote (FCL-3a).
        assert fallback[(opener["event_id"], HEURISTIC_WEIGHTED_TIP)] == (
            opener["home_side_id"]
        )
        assert fallback[(opener["event_id"], HEURISTIC_BEST_BET)] == (
            opener["home_side_id"]
        )

        # --- the league retrain (real fits, real scoped persistence) ---
        async with factory() as session:
            result = await league_retrain.run_league_retrain(
                session, competition_id=cid
            )
        assert result["status"] == "trained"
        assert result["training_rows"] == 40
        assert result["model_names"] == list(models)

        # --- generation AFTER training: the scoped model is served -----
        async with factory() as session:
            regen = await generate_league_tips(session, competition_id=cid)
        stored_after = await _stored_tips(async_dsn, cid)

        # The opener flipped to the learned −40 margin → AWAY, diverging
        # from best_bet (still the untrained home majority vote).
        assert regen["tips_updated"] >= 2  # weighted_tip + boosted_tip flipped
        assert stored_after[(opener["event_id"], HEURISTIC_WEIGHTED_TIP)] == (
            opener["away_side_id"]
        )
        assert stored_after[(opener["event_id"], HEURISTIC_BOOSTED_TIP)] == (
            opener["away_side_id"]
        )
        assert stored_after[(opener["event_id"], HEURISTIC_BEST_BET)] == (
            opener["home_side_id"]
        )

        # The stored tips are EXACTLY what the scoped active model
        # predicts — read the weights/artifact back through the FCL-4
        # CRUD and recompute the whole payload.
        async with factory() as session:
            weights = await get_active_coefficients(
                session, HEURISTIC_WEIGHTED_TIP, competition_id=cid
            )
            bundle = await get_active_model_artifact(
                session, HEURISTIC_BOOSTED_TIP, competition_id=cid
            )
            global_active = await get_active_model_version(
                session, HEURISTIC_WEIGHTED_TIP
            )
        assert weights is not None, "scoped active linear weights must exist"
        assert bundle is not None, "scoped active boosted artifact must exist"
        assert global_active is None, (
            "the league retrain must never promote a global (NULL-scope) "
            "version (FCL-5e)"
        )
        boosted_model = load_league_boosted_model(bundle[0], bundle[1])
        expected = {
            (p.event_id, p.heuristic): p.selected_side_id
            for p in compute_all_picks_and_derived(
                events,
                models=models,
                linear_weights=weights,
                boosted_model=boosted_model,
            )
        }
        assert stored_after == expected

        # The re-generation inserted nothing new (idempotent upsert: the
        # flips were in-place updates against the UNIQUE constraint).
        assert regen["tips_inserted"] == 0
    finally:
        await engine.dispose()


# ---------------------------------------------------------------------------
# E2E 3 — byte-identical regression for an unregistered (state) league.
# ---------------------------------------------------------------------------


@pytest.mark.postgres
@pytest.mark.asyncio
async def test_unregistered_state_league_keeps_pre_fcl_base_set_and_gains_derived(
    _db_at_head,
) -> None:
    """FCL-8 regression pin: an unregistered competition's generation
    payload keeps its EXACT pre-FCL base model set — the persisted base
    rows equal ``compute_all_picks`` (the unchanged pre-FCL pipeline)
    name-for-name and pick-for-pick — while still gaining the three
    derived names (every league is first-class, FCL-6)."""
    from packages.shared.services.league_retrain import _fetch_completed_events

    _sync_dsn, async_dsn = _db_at_head

    def scores(round_no: int, match_no: int) -> tuple[int, int]:
        home = 45 + ((round_no * 13 + match_no * 7) % 28)
        away = 40 + ((round_no * 9 + match_no * 11) % 24)
        return (home, away)

    ids = await _seed_synced_league(
        async_dsn,
        competition_name=_UNREGISTERED_STATE_LEAGUE,
        rounds=3,
        scores=scores,
        scheduled_events=1,
    )
    cid = ids["competition_id"]
    scheduled_id = ids["events"]["scheduled0"]["event_id"]

    engine = create_async_engine(async_dsn, poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            summary = await generate_league_tips(session, competition_id=cid)
            # Idempotent AND byte-stable: a re-run changes nothing.
            second = await generate_league_tips(session, competition_id=cid)
            events = await _fetch_completed_events(session, cid)
    finally:
        await engine.dispose()

    stored = await _stored_tips(async_dsn, cid)

    # Base names are EXACTLY the pre-FCL D3 trio (no sport registration
    # for 'afl' → DEFAULT_MODEL_SET), plus the three derived names.
    base_names = {h for _, h in stored} - set(DERIVED_LEAGUE_HEURISTICS)
    assert base_names == set(DEFAULT_MODEL_SET)
    assert set(DERIVED_LEAGUE_HEURISTICS) <= {h for _, h in stored}
    # 6 completed events + 1 scheduled fixture (FCL-12 forward tip) ×
    # (3 base + 3 derived) = 42.
    assert summary["tips_inserted"] == 42
    assert len(stored) == 42
    assert await _scalar(
        async_dsn,
        "SELECT count(*) FROM league_tips WHERE event_id = :eid",
        {"eid": scheduled_id},
    ) == 6

    # THE byte-identical pin: every base pick equals the pre-FCL
    # pipeline's pick for the same event, exactly.
    pre_fcl = {
        (p.event_id, p.heuristic): p.selected_side_id
        for p in compute_all_picks(events, models=DEFAULT_MODEL_SET)
    }
    for key, pick in pre_fcl.items():
        assert stored[key] == pick, key

    # Re-run: no churn at all.
    assert second["tips_inserted"] == 0
    assert second["tips_updated"] == 0


# ---------------------------------------------------------------------------
# E2E 4 — the weekly cron job end-to-end (FCL-5/FCL-8).
# ---------------------------------------------------------------------------


@pytest.mark.postgres
@pytest.mark.asyncio
async def test_cron_run_summary_includes_per_league_outcomes(
    _db_at_head, _ml_or_skip
) -> None:
    """ModelRetrainJob.run() nests per-league retrain outcomes under
    ``"leagues"``: the league above the training gate reports
    ``trained`` (with its row count and scoped persisted versions), the
    league below it reports ``skipped`` — and the job itself succeeds
    (the empty legacy ``games`` tables make the AFL linear/boosted
    steps skip, leaving the top-level summary untouched)."""
    from packages.shared.crud.model_versions import get_active_model_version

    _sync_dsn, async_dsn = _db_at_head
    trained_ids = await _seed_synced_league(
        async_dsn, competition_name="FCL Cron Trained League", rounds=20
    )
    tiny_ids = await _seed_synced_league(
        async_dsn, competition_name="FCL Cron Tiny League", rounds=1
    )  # 2 rows < MIN_LEAGUE_TRAINING_ROWS

    from app.cron.model_retrain import ModelRetrainJob

    engine = create_async_engine(async_dsn, poolclass=NullPool)
    try:
        job = ModelRetrainJob(_job_session_factory(engine))
        summary = await job.run()  # must not raise
    finally:
        await engine.dispose()

    # The AFL top-level summary is untouched by the league pass (empty
    # games → the linear retrain skips, its keys stay top-level).
    assert summary["status"] == "skipped"

    # The per-league outcomes nest under "leagues".
    leagues = summary["leagues"]
    assert leagues["competitions"] == 2
    assert set(leagues["results"]) == {
        str(trained_ids["competition_id"]),
        str(tiny_ids["competition_id"]),
    }
    trained_result = leagues["results"][str(trained_ids["competition_id"])]
    tiny_result = leagues["results"][str(tiny_ids["competition_id"])]
    assert trained_result["status"] == "trained"
    assert trained_result["training_rows"] == 40
    assert tiny_result["status"] == "skipped"
    assert tiny_result["reason"] == "insufficient_training_rows"

    # The cron's league pass really persisted the scoped active model.
    engine = create_async_engine(async_dsn, poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            active = await get_active_model_version(
                session,
                HEURISTIC_WEIGHTED_TIP,
                competition_id=trained_ids["competition_id"],
            )
    finally:
        await engine.dispose()
    assert active is not None
    assert active.is_active is True
    assert active.competition_id == trained_ids["competition_id"]
    assert active.model_name == HEURISTIC_WEIGHTED_TIP


@pytest.mark.postgres
@pytest.mark.asyncio
async def test_cron_run_survives_one_leagues_retrain_failing(
    _db_at_head, _ml_or_skip, monkeypatch
) -> None:
    """One league's retrain RAISING never fails the job (FCL-5f): the
    failed league's outcome is reported as ``{"status": "error",
    "reason": ...}`` in the ``leagues`` dict, the other league still
    processes, and the job's own top-level summary stays intact."""
    from packages.shared.services import league_retrain

    _sync_dsn, async_dsn = _db_at_head
    dominant = await _seed_synced_league(
        async_dsn, competition_name="FCL Cron Dominant League", rounds=20
    )
    tiny = await _seed_synced_league(
        async_dsn, competition_name="FCL Cron Tiny League", rounds=1
    )
    dominant_cid = dominant["competition_id"]

    from app.cron.model_retrain import ModelRetrainJob

    engine = create_async_engine(async_dsn, poolclass=NullPool)
    try:
        # First run: both leagues process normally.
        job = ModelRetrainJob(_job_session_factory(engine))
        first = await job.run()
        assert (
            first["leagues"]["results"][str(dominant_cid)]["status"] == "trained"
        )

        # Second run: the dominant league's retrain explodes (patched on
        # the service module — the pass's own call site), the tiny one
        # still processes.
        real = league_retrain.run_league_retrain

        async def _flaky(session: Any, *, competition_id: int) -> dict[str, Any]:
            if competition_id == dominant_cid:
                raise RuntimeError("boom: dominant league exploded")
            return await real(session, competition_id=competition_id)

        monkeypatch.setattr(league_retrain, "run_league_retrain", _flaky)

        second_job = ModelRetrainJob(_job_session_factory(engine))
        second = await second_job.run()  # must not raise
    finally:
        await engine.dispose()

    results = second["leagues"]["results"]
    assert results[str(dominant_cid)]["status"] == "error"
    assert "dominant league exploded" in results[str(dominant_cid)]["reason"]
    assert results[str(tiny["competition_id"])]["status"] == "skipped"
    # The job's own top-level summary is untouched by a league failure.
    assert second["status"] == "skipped"


@pytest.mark.postgres
@pytest.mark.asyncio
async def test_model_load_failure_does_not_poison_the_generation_pass(
    _db_at_head, monkeypatch
) -> None:
    """FCL-11: a failed scoped model-version read must not abort the pass.

    Real operational fault (found syncing AFLW against a pre-0015
    database): the scoped ``model_versions`` SELECT fails with
    UndefinedColumnError, asyncpg ABORTS the transaction, and — without
    a savepoint — every later statement of the generation pass (and the
    enclosing sync's committed-together fixture writes) dies with
    ``InFailedSQLTransactionError``.  The loaders now wrap the read in a
    SAVEPOINT: this test patches the CRUD read to raise, runs the
    sync-hook generation on REAL Postgres, and asserts the tips still
    persist (fallback picks) AND the same session stays usable."""
    _sync_dsn, async_dsn = _db_at_head
    ids = await _seed_synced_league(
        async_dsn,
        competition_name="FCL E2E Savepoint League",
        rounds=2,
    )
    cid = ids["competition_id"]

    async def _boom(*_args: Any, **_kwargs: Any) -> Any:
        raise RuntimeError("simulated scoped model-version fault")

    monkeypatch.setattr(
        "packages.shared.crud.model_versions.get_active_coefficients", _boom
    )
    monkeypatch.setattr(
        "packages.shared.crud.model_versions.get_active_model_artifact", _boom
    )

    engine = create_async_engine(async_dsn, poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            summary = await generate_league_tips(session, competition_id=cid)

            # The SAME session must still be usable — the transaction is
            # alive (this is the statement that died pre-FCL-11).
            alive = (
                await session.execute(
                    text("SELECT count(*) FROM league_tips WHERE competition_id = :cid"),
                    {"cid": cid},
                )
            ).scalar_one()
    finally:
        await engine.dispose()

    # 4 completed events × (3 D3 base + 3 derived) — fallback picks.
    assert summary["tips_inserted"] == 24
    assert summary["derived_tips"] == {
        "best_bet": 4,
        "weighted_tip": 4,
        "boosted_tip": 4,
    }
    assert alive == 24
