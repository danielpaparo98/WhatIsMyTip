"""Unit tests for the league heuristics service (performance-per-league D3).

Three layers, mirroring established repo patterns:

* **Pure pick logic** — the three result-derived heuristics with the
  EXACT D3 semantics.  No database: the pick functions take plain
  frozen dataclasses, so every rule below is a table-driven unit test.
* **Service orchestration** — ``LeagueHeuristicsService.generate_for_competition``
  against a fake session with the thin DB fetchers patched (the
  ``test_local_competition_sync`` pattern).  Proves the two-season
  scope (current + most recent past), the draw-no-pick persistence
  shape, and IDEMPOTENCE: a re-run inserts nothing, updates nothing
  and never touches the UNIQUE (event_id, heuristic) constraint.
* **End-to-end** (``@pytest.mark.postgres``) — the real migration chain
  on a podman Postgres testcontainer (the ``test_league_tips_model``
  pattern): generate → re-generate against the real constraint.
  Skips when podman is unavailable.
"""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import sys
import time
import uuid
from datetime import datetime
from types import SimpleNamespace
from typing import Any, AsyncIterator, Iterator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from packages.shared.services.league_heuristics import (
    HEURISTIC_FORM,
    HEURISTIC_HOME_ADVANTAGE,
    HEURISTIC_LADDER,
    LEAGUE_HEURISTICS,
    CompletedEvent,
    LeagueHeuristicsService,
    SeasonRef,
    Standing,
    compute_all_picks,
    compute_event_picks,
    compute_standings,
    is_drawn,
    pick_form,
    pick_home_advantage,
    pick_ladder,
    select_tip_seasons,
    sort_chronologically,
    wins_in_last_n,
)

_BACKEND_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))

_KICKOFF = datetime(2026, 4, 4, 14, 40)


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


# ---------------------------------------------------------------------------
# Pure pick logic — EXACT D3 semantics, no database.
# ---------------------------------------------------------------------------


class TestHeuristicNames:
    def test_the_three_d3_heuristics_under_their_contract_names(self) -> None:
        assert LEAGUE_HEURISTICS == ("home_advantage", "form", "ladder")


class TestPickHomeAdvantage:
    def test_always_the_home_side(self) -> None:
        assert pick_home_advantage(_event(1)) == 101

    def test_no_home_side_makes_no_pick(self) -> None:
        assert pick_home_advantage(_event(1, home_side=None)) is None


class TestWinsInLastN:
    def test_counts_only_the_participants_decided_wins(self) -> None:
        history = [
            _event(1, home_pid=11, away_pid=21, home_score=90, away_score=70),
            _event(2, home_pid=11, away_pid=22, home_score=50, away_score=80),
            _event(3, home_pid=11, away_pid=23, home_score=80, away_score=80),
        ]
        assert wins_in_last_n(11, history) == 1

    def test_events_without_the_participant_are_ignored(self) -> None:
        history = [_event(1, home_pid=21, away_pid=22, home_score=90, away_score=70)]
        assert wins_in_last_n(11, history) == 0

    def test_window_is_the_last_five_in_chronological_order(self) -> None:
        # Rounds 1-7: the participant wins R1-R2 (stale), loses R3-R7.
        # The five-event window covers R3-R7 — zero wins survive.
        history = [
            _event(
                round_no,
                starts_at=datetime(2026, 3, 1, 12, 0),
                home_pid=11,
                away_pid=20 + round_no,
                home_score=90 if round_no <= 2 else 50,
                away_score=70 if round_no <= 2 else 80,
            )
            for round_no in range(1, 8)
        ]
        assert wins_in_last_n(11, history) == 0
        # And the mirror: wins R4-R7, all inside the window.
        recent_wins = [
            _event(
                round_no,
                starts_at=datetime(2026, 3, 1, 12, 0),
                home_pid=11,
                away_pid=20 + round_no,
                home_score=50 if round_no <= 3 else 90,
                away_score=80 if round_no <= 3 else 70,
            )
            for round_no in range(1, 8)
        ]
        assert wins_in_last_n(11, recent_wins) == 4


class TestPickForm:
    def test_home_with_more_recent_wins_is_picked(self) -> None:
        prior = [_event(1, home_pid=11, away_pid=12, home_score=90, away_score=70)]
        assert pick_form(_event(2), prior) == 101

    def test_away_with_more_recent_wins_is_picked(self) -> None:
        prior = [_event(1, home_pid=11, away_pid=12, home_score=70, away_score=90)]
        assert pick_form(_event(2), prior) == 102

    def test_tie_goes_home(self) -> None:
        assert pick_form(_event(2), []) == 101

    def test_only_the_last_five_events_count(self) -> None:
        # Overall the away side has 4 wins to 3 — but only the last FIVE
        # meetings (rounds 3-7) count: home 3, away 2 → home.
        history = [
            _event(
                round_no,
                starts_at=datetime(2026, 3, 1, 12, 0),
                home_pid=11,
                away_pid=12,
                home_score=50 if round_no <= 4 else 90,
                away_score=90 if round_no <= 4 else 50,
            )
            for round_no in range(1, 8)
        ]
        assert pick_form(_event(8), history) == 101


class TestComputeStandings:
    def test_wins_and_points_accumulate_per_participant(self) -> None:
        history = [
            _event(1, home_pid=11, away_pid=12, home_score=90, away_score=70),
            _event(2, home_pid=12, away_pid=11, home_score=80, away_score=85),
        ]
        table = compute_standings(history)
        assert table[11].wins == 2
        assert table[11].points_for == 175
        assert table[11].points_against == 150
        assert table[12].wins == 0
        assert table[12].points_for == 150

    def test_percentage_is_points_for_per_hundred_against(self) -> None:
        assert Standing(1, 1, 200, 100).percentage == pytest.approx(200.0)
        assert Standing(2, 0, 0, 0).percentage == 0.0
        # Nothing conceded yet: infinitely good (AFL ladder convention).
        assert Standing(3, 0, 50, 0).percentage == float("inf")


class TestPickLadder:
    def test_more_wins_is_picked(self) -> None:
        prior = [_event(1, home_pid=11, away_pid=13, home_score=90, away_score=70)]
        target = _event(2, home_pid=11, away_pid=12)
        assert pick_ladder(target, prior) == 101  # home 1-0 vs away 0-0

    def test_percentage_breaks_a_wins_tie(self) -> None:
        prior = [
            _event(1, home_pid=11, away_pid=13, home_score=90, away_score=50),
            _event(2, home_pid=12, away_pid=14, home_score=60, away_score=59),
        ]
        target = _event(3, home_pid=11, away_pid=12)
        # Both 1-0; 11 at 180.0% beats 12 at ~101.7%.
        assert pick_ladder(target, prior) == 101

    def test_early_season_tie_goes_home(self) -> None:
        assert pick_ladder(_event(1), []) == 101

    def test_identical_records_go_home(self) -> None:
        prior = [
            _event(1, home_pid=11, away_pid=13, home_score=80, away_score=60),
            _event(2, home_pid=12, away_pid=14, home_score=80, away_score=60),
        ]
        assert pick_ladder(_event(3, home_pid=11, away_pid=12), prior) == 101

    def test_unplayed_side_is_treated_as_zero_and_zero(self) -> None:
        prior = [_event(1, home_pid=11, away_pid=13, home_score=90, away_score=70)]
        # Away (12) has no results yet — equivalent to 0 wins, 0%.
        assert pick_ladder(_event(2, home_pid=12, away_pid=11), prior) == 102


class TestIsDrawn:
    def test_equal_scores_is_a_draw(self) -> None:
        assert is_drawn(_event(1, home_score=80, away_score=80))

    def test_decided_or_unscored_is_not(self) -> None:
        assert not is_drawn(_event(2, home_score=80, away_score=70))
        assert not is_drawn(_event(3, home_score=None, away_score=None))


class TestComputeEventPicks:
    def test_draw_persists_draw_no_pick_for_every_heuristic(self) -> None:
        event = _event(1, home_score=80, away_score=80)
        picks = compute_event_picks(event, [], [])
        assert [p.heuristic for p in picks] == list(LEAGUE_HEURISTICS)
        assert all(p.event_id == 1 for p in picks)
        assert all(p.selected_side_id is None for p in picks)

    def test_normal_event_picks_a_side_for_every_heuristic(self) -> None:
        event = _event(1, home_score=80, away_score=70)
        picks = compute_event_picks(event, [], [])
        assert [p.heuristic for p in picks] == list(LEAGUE_HEURISTICS)
        assert all(p.selected_side_id is not None for p in picks)

    def test_missing_side_yields_no_tips(self) -> None:
        event = _event(1, home_side=None)
        assert compute_event_picks(event, [], []) == []
        assert compute_all_picks([event]) == []


class TestComputeAllPicks:
    def test_form_uses_only_prior_events_no_lookahead(self) -> None:
        r1 = _event(1, home_pid=11, away_pid=12, home_score=90, away_score=70)
        r2 = _event(2, home_pid=12, away_pid=11, home_score=90, away_score=70)
        # Deliberately out of chronological order: the pure pipeline sorts.
        picks = compute_all_picks([r2, r1])
        by_key = {(p.event_id, p.heuristic): p for p in picks}
        # Event 1: no prior history → tie → home.
        assert by_key[(1, HEURISTIC_FORM)].selected_side_id == r1.home_side_id
        # Event 2: only event 1 counts — 11 has 1 win (the away side here).
        assert by_key[(2, HEURISTIC_FORM)].selected_side_id == r2.away_side_id

    def test_ladder_standings_exclude_other_seasons(self) -> None:
        past = _event(
            1, season_id=2, home_pid=11, away_pid=12, home_score=49, away_score=50
        )  # 12 wins in the PAST season
        now = _event(
            2, season_id=1, home_pid=11, away_pid=12, home_score=51, away_score=50
        )  # decided, with no season-1 history before it
        picks = compute_all_picks([past, now])
        ladder = next(
            p for p in picks if p.event_id == 2 and p.heuristic == HEURISTIC_LADDER
        )
        # Cross-season leakage would tip 12 (away); scoped correctly → tie → home.
        assert ladder.selected_side_id == now.home_side_id

    def test_form_history_spans_seasons(self) -> None:
        past = _event(
            1, season_id=2, home_pid=11, away_pid=12, home_score=90, away_score=70
        )  # 11 wins the prior-season meeting
        now = _event(
            2, season_id=1, home_pid=12, away_pid=11, home_score=70, away_score=71
        )  # 11 is the AWAY side; no season-1 history before it
        picks = compute_all_picks([past, now])
        form = next(
            p for p in picks if p.event_id == 2 and p.heuristic == HEURISTIC_FORM
        )
        # Season-1-only evidence would tie → home; spanning seasons, 11's
        # prior win tips 11 (the away side).
        assert form.selected_side_id == now.away_side_id

    def test_chronological_ordering_handles_missing_start_times(self) -> None:
        a = _event(2, starts_at=datetime(2026, 4, 2, 13, 0))
        b = _event(1, starts_at=datetime(2026, 4, 1, 13, 0))
        c = _event(9, starts_at=None)
        d = _event(5, starts_at=datetime(2026, 4, 2, 13, 0))
        assert sort_chronologically([a, b, c, d]) == [c, b, a, d]


class TestSelectTipSeasons:
    def test_no_seasons_is_empty(self) -> None:
        assert select_tip_seasons([]) == []

    def test_is_current_flag_beats_label_order(self) -> None:
        seasons = [
            SeasonRef(id=1, label="2024", is_current=False),
            SeasonRef(id=2, label="2026", is_current=False),
            SeasonRef(id=3, label="2025", is_current=True),
        ]
        # Current = the flagged 2025 (drift-safe); past = latest label
        # strictly below it (2024) — NOT the newer 2026.
        assert select_tip_seasons(seasons) == [
            SeasonRef(id=3, label="2025", is_current=True),
            SeasonRef(id=1, label="2024", is_current=False),
        ]

    def test_without_a_flag_the_latest_label_is_current(self) -> None:
        seasons = [
            SeasonRef(id=1, label="2025", is_current=False),
            SeasonRef(id=2, label="2026", is_current=False),
        ]
        assert select_tip_seasons(seasons) == [
            SeasonRef(id=2, label="2026", is_current=False),
            SeasonRef(id=1, label="2025", is_current=False),
        ]

    def test_single_season_has_no_past(self) -> None:
        assert select_tip_seasons([SeasonRef(id=7, label="2026", is_current=True)]) == [
            SeasonRef(id=7, label="2026", is_current=True)
        ]


# ---------------------------------------------------------------------------
# Service orchestration — fake session, patched fetchers (no database).
# ---------------------------------------------------------------------------


def _fake_session() -> AsyncMock:
    """An AsyncSession double exposing just what the service touches."""
    db = AsyncMock()
    db.add = MagicMock()
    db.flush = AsyncMock()
    db.commit = AsyncMock()
    return db


def _patched_fetches(
    service: LeagueHeuristicsService,
    seasons: list[SeasonRef],
    events: list[CompletedEvent],
    existing: dict[tuple[int, str], int | None],
) -> tuple[Any, Any, Any]:
    """Instance-level fetch patches — the class's real staticmethods
    are never touched (each test builds its own service instance)."""
    return (
        patch.object(service, "_fetch_seasons", AsyncMock(return_value=seasons)),
        patch.object(
            service, "_fetch_completed_events", AsyncMock(return_value=events)
        ),
        patch.object(
            service, "_fetch_existing_tips", AsyncMock(return_value=existing)
        ),
    )


class TestGenerateForCompetition:
    @pytest.mark.asyncio
    async def test_generates_three_tips_per_completed_event_across_two_seasons(self) -> None:
        seasons = [SeasonRef(id=1, label="2025", is_current=False),
                   SeasonRef(id=2, label="2026", is_current=True)]
        events = [
            _event(1, season_id=1, home_score=90, away_score=70),
            _event(2, season_id=1, home_score=80, away_score=80),  # draw
            _event(3, season_id=2, home_score=60, away_score=65),
        ]
        db = _fake_session()
        service = LeagueHeuristicsService()
        patches = _patched_fetches(service, seasons, events, {})
        with patches[0], patches[1], patches[2]:
            summary = await service.generate_for_competition(db, competition_id=42)

        rows = [call.args[0] for call in db.add.call_args_list]
        assert len(rows) == 9  # 3 completed events × 3 heuristics
        assert {row.heuristic for row in rows} == set(LEAGUE_HEURISTICS)
        assert all(row.competition_id == 42 for row in rows)
        assert {row.season_id for row in rows} == {1, 2}
        # The drawn event persists draw-no-pick tips (NULL selection).
        assert all(
            row.selected_participant_id is None for row in rows if row.event_id == 2
        )
        # Every selection points at an event_participants row id.
        assert all(
            row.selected_participant_id in (101, 102)
            for row in rows
            if row.event_id != 2
        )
        assert summary["tips_inserted"] == 9
        assert summary["tips_updated"] == 0
        db.commit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_rerun_with_unchanged_results_inserts_and_updates_nothing(self) -> None:
        seasons = [SeasonRef(id=1, label="2025", is_current=False),
                   SeasonRef(id=2, label="2026", is_current=True)]
        events = [_event(1, season_id=1, home_score=90, away_score=70)]
        db = _fake_session()
        service = LeagueHeuristicsService()
        picks = compute_all_picks(events)
        existing = {(p.event_id, p.heuristic): p.selected_side_id for p in picks}
        first_patches = _patched_fetches(service, seasons, events, {})
        second_patches = _patched_fetches(service, seasons, events, existing)
        with first_patches[0], first_patches[1], first_patches[2]:
            first = await service.generate_for_competition(db, competition_id=42)
        db.add = MagicMock()  # fresh spy for the re-run
        with second_patches[0], second_patches[1], second_patches[2]:
            second = await service.generate_for_competition(db, competition_id=42)

        assert first["tips_inserted"] == 3
        assert second["tips_inserted"] == 0
        assert second["tips_updated"] == 0
        db.add.assert_not_called()  # no churn: unchanged rows are untouched

    @pytest.mark.asyncio
    async def test_changed_pick_updates_the_existing_row_in_place(self) -> None:
        seasons = [SeasonRef(id=2, label="2026", is_current=True)]
        events = [_event(1, season_id=2, home_score=90, away_score=70)]
        picks = compute_all_picks(events)
        existing = {(p.event_id, p.heuristic): p.selected_side_id for p in picks}
        # Upstream score revision: the stored home_advantage pick is stale.
        existing[(1, HEURISTIC_HOME_ADVANTAGE)] = 999
        tip_row = SimpleNamespace(id=7, selected_participant_id=999)
        db = _fake_session()
        # _update_tip loads the ORM row via scalar_one_or_none().
        db.execute = AsyncMock(
            return_value=SimpleNamespace(scalar_one_or_none=lambda: tip_row)
        )
        service = LeagueHeuristicsService()
        patches = _patched_fetches(service, seasons, events, existing)
        with patches[0], patches[1], patches[2]:
            summary = await service.generate_for_competition(db, competition_id=42)

        assert summary["tips_inserted"] == 0
        assert summary["tips_updated"] == 1
        # The row was pointed at the recomputed pick (the home side).
        assert tip_row.selected_participant_id == picks[0].selected_side_id == 101
        db.add.assert_not_called()
        db.commit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_competition_without_seasons_is_a_noop(self) -> None:
        db = _fake_session()
        service = LeagueHeuristicsService()
        with patch.object(
            service, "_fetch_seasons", AsyncMock(return_value=[])
        ):
            summary = await service.generate_for_competition(db, competition_id=42)

        assert summary["tips_inserted"] == 0
        db.add.assert_not_called()
        db.commit.assert_not_awaited()


# ---------------------------------------------------------------------------
# End-to-end on real Postgres (podman testcontainer), following the
# test_league_tips_model.py plumbing: real migration chain, real UNIQUE
# (event_id, heuristic) constraint, generate → re-generate.
# ---------------------------------------------------------------------------

_POSTGRES_IMAGE = "docker.io/library/postgres:16-alpine"
_CONTAINER_NAME_PREFIX = "wimt-pg-lh02-"
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
    """Empty public schema upgraded to head. Yields (sync_dsn, async_dsn)."""
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


async def _seed_competition(async_dsn: str) -> dict[str, int]:
    """Two seasons (2025 past, 2026 current) of a four-team competition.

    Season 2025: three completed events (one draw).
    Season 2026: two completed events + one scheduled (never tipped).
    """
    engine = create_async_engine(async_dsn, poolclass=NullPool)

    async def one(conn: Any, sql: str, params: dict[str, Any] | None = None) -> int:
        return (await conn.execute(text(sql), params or {})).scalar_one()

    try:
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "INSERT INTO sports (id, display_name) "
                    "VALUES ('afl', 'AFL') ON CONFLICT (id) DO NOTHING"
                )
            )
            cid = await one(
                conn,
                "INSERT INTO competitions (sport_id, name, tier, format) "
                "VALUES ('afl', 'Heuristics Test League', 'state', 'rounds') "
                "RETURNING id",
            )
            s2025 = await one(
                conn,
                "INSERT INTO seasons (competition_id, label, is_current) "
                "VALUES (:cid, '2025', false) RETURNING id",
                {"cid": cid},
            )
            s2026 = await one(
                conn,
                "INSERT INTO seasons (competition_id, label, is_current) "
                "VALUES (:cid, '2026', true) RETURNING id",
                {"cid": cid},
            )
            pids = {
                name: await one(
                    conn,
                    "INSERT INTO participants (sport_id, kind, name) "
                    "VALUES ('afl', 'team', :name) RETURNING id",
                    {"name": name},
                )
                for name in ("Reds", "Blues", "Greens", "Yellows")
            }

            # (event_no, year, season_id, round, home, away, hs, as_, completed)
            fixtures: list[tuple[int, int, int, int, str, str, int | None, int | None, bool]] = [
                (1, 2025, s2025, 1, "Reds", "Blues", 90, 70, True),
                (2, 2025, s2025, 2, "Blues", "Greens", 80, 80, True),  # draw
                (3, 2025, s2025, 3, "Greens", "Reds", 100, 60, True),
                (4, 2026, s2026, 1, "Reds", "Blues", 95, 75, True),
                (5, 2026, s2026, 2, "Blues", "Greens", 85, 65, True),
                (6, 2026, s2026, 3, "Greens", "Yellows", None, None, False),
            ]
            for no, year, sid, rnd, home, away, hs, as_, completed in fixtures:
                event_id = await one(
                    conn,
                    "INSERT INTO events (season_id, event_type, round_id, venue, "
                    "starts_at, status, completed, slug) "
                    "VALUES (:sid, 'match', :rnd, 'Test Oval', "
                    ":starts_at, :status, :completed, :slug) RETURNING id",
                    {
                        "sid": sid,
                        "rnd": rnd,
                        "starts_at": datetime(year, 4, rnd, 14, 40),
                        "status": "completed" if completed else "scheduled",
                        "completed": completed,
                        "slug": f"lh02e{no}",
                    },
                )
                decided = hs is not None and as_ is not None and hs != as_
                winner_side = ("home" if (hs or 0) > (as_ or 0) else "away") if decided else None
                for side, pid, score in (
                    ("home", pids[home], hs),
                    ("away", pids[away], as_),
                ):
                    await conn.execute(
                        text(
                            "INSERT INTO event_participants "
                            "(event_id, participant_id, side, score, is_winner) "
                            "VALUES (:eid, :pid, :side, :score, :winner)"
                        ),
                        {
                            "eid": event_id,
                            "pid": pid,
                            "side": side,
                            "score": score,
                            "winner": (side == winner_side) if decided else None,
                        },
                    )
        return {"competition_id": cid, "season_2025": s2025, "season_2026": s2026}
    finally:
        await engine.dispose()


@pytest.mark.postgres
@pytest.mark.asyncio
async def test_generation_round_trip_is_idempotent_against_unique_constraint(
    _db_at_head,
) -> None:
    sync_dsn, async_dsn = _db_at_head
    ids = await _seed_competition(async_dsn)

    engine = create_async_engine(async_dsn, poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            service = LeagueHeuristicsService()
            first = await service.generate_for_competition(
                session, competition_id=ids["competition_id"]
            )
            second = await service.generate_for_competition(
                session, competition_id=ids["competition_id"]
            )
        async with engine.connect() as conn:
            total = (
                await conn.execute(text("SELECT count(*) FROM league_tips"))
            ).scalar_one()
            null_picks = (
                await conn.execute(
                    text("SELECT count(*) FROM league_tips "
                         "WHERE selected_participant_id IS NULL")
                )
            ).scalar_one()
            heuristics = {
                row[0]
                for row in (
                    await conn.execute(
                        text("SELECT DISTINCT heuristic FROM league_tips")
                    )
                ).all()
            }
            season_ids = {
                row[0]
                for row in (
                    await conn.execute(
                        text("SELECT DISTINCT season_id FROM league_tips")
                    )
                ).all()
            }
    finally:
        await engine.dispose()

    # 5 completed events × 3 heuristics; the scheduled event got nothing.
    assert first["tips_inserted"] == 15
    assert total == 15
    assert heuristics == set(LEAGUE_HEURISTICS)
    assert season_ids == {ids["season_2025"], ids["season_2026"]}
    # Exactly the three draw tips carry a NULL selection.
    assert null_picks == 3
    # Re-running must not duplicate or crash — and must not churn.
    assert second["tips_inserted"] == 0
    assert second["tips_updated"] == 0


@pytest.mark.postgres
@pytest.mark.asyncio
async def test_module_level_entry_point_matches_service(_db_at_head) -> None:
    from packages.shared.services.league_heuristics import generate_league_tips

    sync_dsn, async_dsn = _db_at_head
    ids = await _seed_competition(async_dsn)

    engine = create_async_engine(async_dsn, poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            summary = await generate_league_tips(
                session, competition_id=ids["competition_id"]
            )
    finally:
        await engine.dispose()

    assert summary["tips_inserted"] == 15
