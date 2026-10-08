"""Unit tests for the league backtest query service (performance-per-league D4).

Four layers, mirroring established repo patterns:

* **Pure grading** — correctness graded at QUERY time from event
  results (a drawn game has no winner, so a tip on either side is
  incorrect — AFL ``actual_winner_name`` semantics).  No database.
* **Pure metrics** — the AFL ``calculate_backtest_from_tips``-shaped
  season stats with ``settlement.py`` profit semantics ($10 stake,
  real decimal odds where available, $1.90 fallback otherwise, draws
  push).  No database.
* **Service orchestration** — patched fetchers (the
  ``test_league_heuristics`` pattern) proving shape parity with the AFL
  ``/compare`` + ``/current-season`` responses and the well-defined
  empty structures the API layer passes through.
* **End-to-end** (``@pytest.mark.postgres``) — the test_league_heuristics
  two-season seed (incl. a draw and a scheduled event): generate tips,
  then assert the real seasons / current-season / compare payloads.
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
from contextlib import contextmanager
from datetime import datetime
from typing import Any, AsyncIterator, Iterator
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from packages.shared.services.league_backtest import (
    GradedTip,
    LeagueBacktestService,
    build_seasons_payload,
    compute_season_metrics,
    grade_tip,
    resolve_current_label,
    round_accuracy_values,
    winning_side_id,
)
from packages.shared.services.league_heuristics import (
    HEURISTIC_FORM,
    HEURISTIC_HOME_ADVANTAGE,
    HEURISTIC_LADDER,
    LEAGUE_HEURISTICS,
    LeagueHeuristicsService,
    SeasonRef,
)

_BACKEND_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))

# Fallback settlement: $10 stake at the representative $1.90 price.
_FALLBACK_WIN = 10.0 * (1.90 - 1.0)  # +$9

#: The AFL comparison-dict stat keys the league payload must reproduce.
_AFL_STAT_KEYS = {
    "total_rounds",
    "total_tips",
    "total_correct",
    "overall_accuracy",
    "total_profit",
    "avg_profit_per_round",
    "best_round_accuracy",
    "worst_round_accuracy",
    "odds_coverage",
}

#: The AFL CurrentSeasonHeuristicPerformance fields the league payload
#: must reproduce so one frontend presentation renders both paths.
_CURRENT_SEASON_ENTRY_KEYS = {
    "heuristic",
    "total_profit",
    "total_accuracy",
    "rounds_played",
    "avg_profit_per_round",
    "projected_annual_profit",
    "odds_coverage",
}


def _tip(
    round_id: int | None = 1,
    *,
    selected: int | None = 101,
    home_side: int = 101,
    away_side: int = 102,
    home_score: int | None = 90,
    away_score: int | None = 70,
    decimal_odds: float | None = None,
) -> GradedTip:
    """A graded tip; home 101 beats away 102 unless scores say otherwise."""
    return grade_tip(
        round_id=round_id,
        home_side_id=home_side,
        away_side_id=away_side,
        home_score=home_score,
        away_score=away_score,
        selected_side_id=selected,
        decimal_odds=decimal_odds,
    )


# ---------------------------------------------------------------------------
# Pure grading — query-time correctness, no database.
# ---------------------------------------------------------------------------


class TestWinningSideId:
    def test_home_higher_score_wins(self) -> None:
        assert winning_side_id(101, 102, 90, 70) == 101

    def test_away_higher_score_wins(self) -> None:
        assert winning_side_id(101, 102, 70, 90) == 102

    def test_draw_has_no_winner(self) -> None:
        assert winning_side_id(101, 102, 80, 80) is None

    def test_missing_scores_have_no_winner(self) -> None:
        assert winning_side_id(101, 102, None, None) is None


class TestGradeTip:
    def test_selected_home_side_with_home_win_is_correct(self) -> None:
        tip = _tip(1, selected=101, home_score=90, away_score=70)
        assert tip.is_correct is True
        assert tip.is_draw is False

    def test_selected_away_side_with_away_win_is_correct(self) -> None:
        tip = _tip(1, selected=102, home_score=70, away_score=90)
        assert tip.is_correct is True

    def test_selected_home_side_with_away_win_is_incorrect(self) -> None:
        tip = _tip(1, selected=101, home_score=70, away_score=90)
        assert tip.is_correct is False
        assert tip.is_draw is False

    def test_draw_makes_a_tip_on_either_side_incorrect(self) -> None:
        # A drawn game has no winner (actual_winner_name semantics) —
        # but it IS a draw, so settlement pushes.
        home_tip = _tip(1, selected=101, home_score=80, away_score=80)
        away_tip = _tip(1, selected=102, home_score=80, away_score=80)
        assert home_tip.is_correct is False and home_tip.is_draw is True
        assert away_tip.is_correct is False and away_tip.is_draw is True

    def test_draw_no_pick_is_incorrect_but_a_push(self) -> None:
        tip = _tip(1, selected=None, home_score=80, away_score=80)
        assert tip.is_correct is False
        assert tip.is_draw is True

    def test_missing_scores_grade_incorrect_and_not_a_draw(self) -> None:
        tip = _tip(1, selected=101, home_score=None, away_score=None)
        assert tip.is_correct is False
        assert tip.is_draw is False

    def test_round_id_is_carried_for_round_aggregation(self) -> None:
        assert _tip(7).round_id == 7
        assert _tip(None).round_id is None


# ---------------------------------------------------------------------------
# Pure metrics — AFL-shaped season stats with settlement semantics.
# ---------------------------------------------------------------------------


class TestRoundAccuracyValues:
    def test_accuracy_per_round_ascending(self) -> None:
        tips = [
            _tip(2, selected=101, home_score=10, away_score=20),  # wrong
            _tip(1, home_score=90, away_score=70),  # correct
            _tip(1, selected=102, home_score=50, away_score=5),  # wrong
        ]
        assert round_accuracy_values(tips) == [0.5, 0.0]

    def test_null_round_tips_are_excluded_from_rounds(self) -> None:
        tips = [_tip(None, home_score=90, away_score=70)]
        assert round_accuracy_values(tips) == []

    def test_empty_is_empty(self) -> None:
        assert round_accuracy_values([]) == []


class TestComputeSeasonMetrics:
    def test_empty_tips_return_the_zero_structure_with_afl_keys(self) -> None:
        stats = compute_season_metrics([])
        assert set(stats) == _AFL_STAT_KEYS
        assert stats["total_rounds"] == 0
        assert stats["total_tips"] == 0
        assert stats["total_correct"] == 0
        assert stats["overall_accuracy"] == 0.0
        assert stats["total_profit"] == 0.0
        assert stats["avg_profit_per_round"] == 0.0
        assert stats["best_round_accuracy"] == 0.0
        assert stats["worst_round_accuracy"] == 0.0
        assert stats["odds_coverage"] == 0.0

    def test_mixed_results_settle_at_the_fallback_price(self) -> None:
        tips = [
            _tip(1, home_score=90, away_score=70),  # correct
            _tip(1, selected=102, home_score=50, away_score=5),  # wrong
            _tip(2, home_score=30, away_score=10),  # correct
            _tip(2, selected=102, home_score=9, away_score=1),  # wrong
        ]
        stats = compute_season_metrics(tips)
        assert stats["total_tips"] == 4
        assert stats["total_correct"] == 2
        assert stats["overall_accuracy"] == pytest.approx(0.5)
        # No odds source (state leagues): 2 fallback wins (+$9) − 2 losses.
        assert stats["total_profit"] == pytest.approx(2 * _FALLBACK_WIN - 20.0)
        assert stats["odds_coverage"] == 0.0
        assert stats["total_rounds"] == 2

    def test_real_prices_count_toward_odds_coverage_and_profit(self) -> None:
        tips = [
            _tip(1, home_score=90, away_score=70, decimal_odds=1.50),  # +$5
            _tip(1, selected=102, home_score=50, away_score=5),  # −$10 (fallback)
        ]
        stats = compute_season_metrics(tips)
        assert stats["total_profit"] == pytest.approx(5.0 - 10.0)
        assert stats["odds_coverage"] == pytest.approx(0.5)

    def test_a_draw_pushes_and_counts_as_an_incorrect_tip(self) -> None:
        tips = [
            _tip(1, home_score=80, away_score=80),  # draw: $0, incorrect
            _tip(2, home_score=90, away_score=70),  # correct
        ]
        stats = compute_season_metrics(tips)
        assert stats["total_tips"] == 2
        assert stats["total_correct"] == 1
        assert stats["overall_accuracy"] == pytest.approx(0.5)
        assert stats["total_profit"] == pytest.approx(_FALLBACK_WIN)

    def test_round_accuracy_extremes_and_average_profit(self) -> None:
        tips = [
            _tip(1, home_score=90, away_score=70),  # R1: 1/1 → 1.0, +$9
            _tip(2, selected=102, home_score=50, away_score=5),  # R2: 0/1 → 0.0, −$10
            _tip(2, selected=101, home_score=3, away_score=30),  # R2
        ]
        stats = compute_season_metrics(tips)
        assert stats["total_rounds"] == 2
        assert stats["best_round_accuracy"] == pytest.approx(1.0)
        assert stats["worst_round_accuracy"] == pytest.approx(0.0)
        assert stats["avg_profit_per_round"] == pytest.approx(
            stats["total_profit"] / 2
        )


# ---------------------------------------------------------------------------
# Pure season selection — D4 seasons payload, no database.
# ---------------------------------------------------------------------------


class TestResolveCurrentLabel:
    def test_is_current_flag_beats_label_order(self) -> None:
        seasons = [
            SeasonRef(id=1, label="2024", is_current=False),
            SeasonRef(id=2, label="2026", is_current=False),
            SeasonRef(id=3, label="2025", is_current=True),
        ]
        assert resolve_current_label(seasons) == "2025"

    def test_without_a_flag_the_latest_label_is_current(self) -> None:
        seasons = [
            SeasonRef(id=1, label="2025", is_current=False),
            SeasonRef(id=2, label="2026", is_current=False),
        ]
        assert resolve_current_label(seasons) == "2026"

    def test_no_seasons_resolves_to_none(self) -> None:
        assert resolve_current_label([]) is None


class TestBuildSeasonsPayload:
    def test_available_years_are_tipped_labels_latest_first(self) -> None:
        seasons = [
            SeasonRef(id=1, label="2025", is_current=False),
            SeasonRef(id=2, label="2026", is_current=True),
        ]
        payload = build_seasons_payload(seasons, ["2025", "2026"])
        assert payload == {"available_years": ["2026", "2025"], "current_year": "2026"}

    def test_current_year_names_the_current_season_even_untipped(self) -> None:
        # Early current season: no completed events yet → no tips, but the
        # frontend's "most recent past season" logic still needs the label.
        seasons = [
            SeasonRef(id=1, label="2025", is_current=False),
            SeasonRef(id=2, label="2026", is_current=True),
        ]
        payload = build_seasons_payload(seasons, ["2025"])
        assert payload == {"available_years": ["2025"], "current_year": "2026"}

    def test_empty_competition_is_the_well_defined_empty_payload(self) -> None:
        assert build_seasons_payload([], []) == {
            "available_years": [],
            "current_year": None,
        }


# ---------------------------------------------------------------------------
# Service orchestration — fake session, patched fetchers (no database).
# ---------------------------------------------------------------------------

_DB = AsyncMock()  # the service under test never touches it directly


@contextmanager
def _patched_fetches(
    service: LeagueBacktestService,
    *,
    seasons: list[SeasonRef],
    tipped_labels: list[str],
    season_ids: dict[str, int],
    tips_by_heuristic: dict[str, list[GradedTip]],
    fixture_rounds: tuple[int, int],
) -> Iterator[dict[str, AsyncMock]]:
    """Patch the instance fetchers, exposing the mocks for assertions."""
    mocks: dict[str, AsyncMock] = {
        "seasons": AsyncMock(return_value=seasons),
        "tipped": AsyncMock(return_value=tipped_labels),
        "season_id": AsyncMock(
            side_effect=lambda db, competition_id, label: season_ids.get(label)
        ),
        "tips": AsyncMock(
            side_effect=lambda db, season_id, heuristic: list(
                tips_by_heuristic.get(heuristic, [])
            )
        ),
        "rounds": AsyncMock(return_value=fixture_rounds),
    }
    with patch.object(service, "_fetch_seasons", mocks["seasons"]), patch.object(
        service, "_fetch_tipped_season_labels", mocks["tipped"]
    ), patch.object(service, "_fetch_season_id", mocks["season_id"]), patch.object(
        service, "_fetch_graded_tips", mocks["tips"]
    ), patch.object(service, "_fetch_fixture_rounds", mocks["rounds"]):
        yield mocks


def _service() -> LeagueBacktestService:
    return LeagueBacktestService()


class TestGetAvailableSeasons:
    @pytest.mark.asyncio
    async def test_returns_the_d4_shape_for_a_seeded_competition(self) -> None:
        seasons = [
            SeasonRef(id=1, label="2025", is_current=False),
            SeasonRef(id=2, label="2026", is_current=True),
        ]
        service = _service()
        with _patched_fetches(
            service,
            seasons=seasons,
            tipped_labels=["2026", "2025"],
            season_ids={},
            tips_by_heuristic={},
            fixture_rounds=(0, 0),
        ):
            payload = await service.get_available_seasons(_DB, competition_id=42)
        assert payload == {
            "available_years": ["2026", "2025"],
            "current_year": "2026",
        }


class TestCompareSeason:
    @pytest.mark.asyncio
    async def test_explicit_label_resolves_the_season_and_mirrors_the_afl_dict(
        self,
    ) -> None:
        tips = {
            HEURISTIC_HOME_ADVANTAGE: [
                _tip(1, home_score=90, away_score=70),  # correct
                _tip(2, selected=102, home_score=50, away_score=5),  # wrong
            ],
            HEURISTIC_FORM: [],
            HEURISTIC_LADDER: [],
        }
        service = _service()
        with _patched_fetches(
            service,
            seasons=[SeasonRef(id=11, label="2025", is_current=True)],
            tipped_labels=["2025"],
            season_ids={"2025": 11},
            tips_by_heuristic=tips,
            fixture_rounds=(0, 0),
        ) as mocks:
            payload = await service.compare_season(
                _DB, competition_id=42, season_label="2025"
            )

        mocks["season_id"].assert_awaited_once_with(_DB, 42, "2025")
        assert payload["season"] == "2025"
        assert set(payload["comparison"]) == set(LEAGUE_HEURISTICS)
        home_stats = payload["comparison"][HEURISTIC_HOME_ADVANTAGE]
        assert set(home_stats) == _AFL_STAT_KEYS
        assert home_stats["total_tips"] == 2
        assert home_stats["total_correct"] == 1
        assert home_stats["overall_accuracy"] == pytest.approx(0.5)
        assert home_stats["total_profit"] == pytest.approx(_FALLBACK_WIN - 10.0)
        assert home_stats["odds_coverage"] == 0.0
        # Empty heuristics still appear with the zero structure (AFL parity).
        assert payload["comparison"][HEURISTIC_FORM]["total_tips"] == 0
        # Best overall mirrors the AFL /compare summary.
        assert payload["best_overall"] == {
            "heuristic": HEURISTIC_HOME_ADVANTAGE,
            "accuracy": pytest.approx(0.5),
            "profit": pytest.approx(_FALLBACK_WIN - 10.0),
        }

    @pytest.mark.asyncio
    async def test_unknown_label_returns_the_zero_structure(self) -> None:
        service = _service()
        with _patched_fetches(
            service,
            seasons=[],
            tipped_labels=[],
            season_ids={},
            tips_by_heuristic={},
            fixture_rounds=(0, 0),
        ):
            payload = await service.compare_season(
                _DB, competition_id=42, season_label="1999"
            )

        assert payload["season"] == "1999"
        assert set(payload["comparison"]) == set(LEAGUE_HEURISTICS)
        assert all(
            stats["total_tips"] == 0 for stats in payload["comparison"].values()
        )
        assert payload["best_overall"] == {
            "heuristic": None,
            "accuracy": 0.0,
            "profit": 0.0,
        }


class TestGetCurrentSeasonPerformance:
    @pytest.mark.asyncio
    async def test_competition_without_seasons_is_the_zero_structure(self) -> None:
        service = _service()
        with _patched_fetches(
            service,
            seasons=[],
            tipped_labels=[],
            season_ids={},
            tips_by_heuristic={},
            fixture_rounds=(0, 0),
        ):
            payload = await service.get_current_season_performance(
                _DB, competition_id=42
            )

        assert payload["season"] is None
        assert payload["rounds_completed"] == 0
        assert payload["total_rounds"] == 0
        assert {h["heuristic"] for h in payload["heuristics"]} == set(LEAGUE_HEURISTICS)
        assert all(
            set(entry) == _CURRENT_SEASON_ENTRY_KEYS for entry in payload["heuristics"]
        )
        assert all(entry["total_profit"] == 0.0 for entry in payload["heuristics"])

    @pytest.mark.asyncio
    async def test_mirrors_the_afl_current_season_shape_with_bt_round_semantics(
        self,
    ) -> None:
        tips = {
            HEURISTIC_HOME_ADVANTAGE: [
                _tip(1, home_score=90, away_score=70),
                _tip(2, home_score=60, away_score=40),
            ],
            HEURISTIC_FORM: [
                _tip(1, home_score=90, away_score=70),  # correct
                _tip(2, selected=102, home_score=50, away_score=5),  # wrong
            ],
            HEURISTIC_LADDER: [],
        }
        service = _service()
        with _patched_fetches(
            service,
            seasons=[SeasonRef(id=22, label="2026", is_current=True)],
            tipped_labels=["2026"],
            season_ids={"2026": 22},
            tips_by_heuristic=tips,
            fixture_rounds=(3, 2),
        ) as mocks:
            payload = await service.get_current_season_performance(
                _DB, competition_id=42
            )

        # BT-ROUND: fixture-derived rounds + fully-completed rounds.
        mocks["rounds"].assert_awaited_once_with(_DB, 22)
        assert payload["season"] == "2026"
        assert payload["total_rounds"] == 3
        assert payload["rounds_completed"] == 2

        by_heuristic = {entry["heuristic"]: entry for entry in payload["heuristics"]}
        assert set(by_heuristic) == set(LEAGUE_HEURISTICS)
        home = by_heuristic[HEURISTIC_HOME_ADVANTAGE]
        assert set(home) == _CURRENT_SEASON_ENTRY_KEYS
        assert home["total_profit"] == pytest.approx(2 * _FALLBACK_WIN)
        assert home["total_accuracy"] == pytest.approx(1.0)
        assert home["rounds_played"] == 2
        assert home["avg_profit_per_round"] == pytest.approx(_FALLBACK_WIN)
        # Projection: linear pace over the season's fixture rounds.
        assert home["projected_annual_profit"] == pytest.approx(
            _FALLBACK_WIN * 3
        )
        assert home["odds_coverage"] == 0.0

        form = by_heuristic[HEURISTIC_FORM]
        assert form["total_accuracy"] == pytest.approx(0.5)
        assert form["projected_annual_profit"] == pytest.approx(
            (form["total_profit"] / 2) * 3
        )

    @pytest.mark.asyncio
    async def test_current_label_prefers_the_is_current_flag(self) -> None:
        # Drift-safe mirror of select_tip_seasons: a stale newer label
        # must not hijack the current season from the flagged one.
        seasons = [
            SeasonRef(id=1, label="2026", is_current=False),
            SeasonRef(id=2, label="2025", is_current=True),
        ]
        service = _service()
        with _patched_fetches(
            service,
            seasons=seasons,
            tipped_labels=["2025"],
            season_ids={"2025": 2},
            tips_by_heuristic={},
            fixture_rounds=(0, 0),
        ) as mocks:
            payload = await service.get_current_season_performance(
                _DB, competition_id=42
            )

        assert payload["season"] == "2025"
        mocks["season_id"].assert_awaited_once_with(_DB, 42, "2025")


# ---------------------------------------------------------------------------
# End-to-end on real Postgres (podman testcontainer), mirroring the
# test_league_heuristics fixtures: real migration chain, generate → query.
# ---------------------------------------------------------------------------

_POSTGRES_IMAGE = "docker.io/library/postgres:16-alpine"
_CONTAINER_NAME_PREFIX = "wimt-pg-lb04-"
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
    """The test_league_heuristics two-season seed, mirrored.

    Season 2025: three completed events (one draw).  Season 2026: two
    completed events + one scheduled (never tipped).
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
                "VALUES ('afl', 'Backtest Test League', 'state', 'rounds') "
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
                        "slug": f"lb04e{no}",
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


async def _generate_tips(async_dsn: str, competition_id: int) -> None:
    """Populate league_tips exactly as the sync hook would."""
    engine = create_async_engine(async_dsn, poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            summary = await LeagueHeuristicsService().generate_for_competition(
                session, competition_id=competition_id
            )
    finally:
        await engine.dispose()
    assert summary["tips_inserted"] == 15  # 5 completed events × 3 heuristics


@pytest.mark.postgres
@pytest.mark.asyncio
async def test_seasons_and_current_season_end_to_end(_db_at_head) -> None:
    sync_dsn, async_dsn = _db_at_head
    ids = await _seed_competition(async_dsn)
    await _generate_tips(async_dsn, ids["competition_id"])

    engine = create_async_engine(async_dsn, poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            service = LeagueBacktestService()
            seasons = await service.get_available_seasons(
                session, competition_id=ids["competition_id"]
            )
            current = await service.get_current_season_performance(
                session, competition_id=ids["competition_id"]
            )
    finally:
        await engine.dispose()

    assert seasons == {
        "available_years": ["2026", "2025"],
        "current_year": "2026",
    }

    # Expected from the seed (see test_league_heuristics): 2026 has
    # home_advantage and ladder at 2/2, form at 1/2; the scheduled
    # round-3 event makes exactly two of three rounds complete.
    assert current["season"] == "2026"
    assert current["total_rounds"] == 3
    assert current["rounds_completed"] == 2

    by_heuristic = {entry["heuristic"]: entry for entry in current["heuristics"]}
    assert set(by_heuristic) == set(LEAGUE_HEURISTICS)

    home = by_heuristic[HEURISTIC_HOME_ADVANTAGE]
    assert home["total_profit"] == pytest.approx(2 * _FALLBACK_WIN)
    assert home["total_accuracy"] == pytest.approx(1.0)
    assert home["rounds_played"] == 2
    assert home["avg_profit_per_round"] == pytest.approx(_FALLBACK_WIN)
    assert home["projected_annual_profit"] == pytest.approx(3 * _FALLBACK_WIN)
    assert home["odds_coverage"] == 0.0  # state leagues: no odds source

    form = by_heuristic[HEURISTIC_FORM]
    assert form["total_profit"] == pytest.approx(_FALLBACK_WIN - 10.0)
    assert form["total_accuracy"] == pytest.approx(0.5)
    assert form["rounds_played"] == 2
    assert form["projected_annual_profit"] == pytest.approx(-1.5)

    ladder = by_heuristic[HEURISTIC_LADDER]
    assert ladder["total_profit"] == pytest.approx(2 * _FALLBACK_WIN)
    assert ladder["total_accuracy"] == pytest.approx(1.0)


@pytest.mark.postgres
@pytest.mark.asyncio
async def test_compare_season_end_to_end(_db_at_head) -> None:
    sync_dsn, async_dsn = _db_at_head
    ids = await _seed_competition(async_dsn)
    await _generate_tips(async_dsn, ids["competition_id"])

    engine = create_async_engine(async_dsn, poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            service = LeagueBacktestService()
            past = await service.compare_season(
                session, competition_id=ids["competition_id"], season_label="2025"
            )
            present = await service.compare_season(
                session, competition_id=ids["competition_id"], season_label="2026"
            )
    finally:
        await engine.dispose()

    # Past season: home_advantage 2/3 (draw round pushes), form and
    # ladder 1/3 (the form/ladder picks follow prior results).
    assert past["season"] == "2025"
    comparison = past["comparison"]
    assert set(comparison) == set(LEAGUE_HEURISTICS)

    home = comparison[HEURISTIC_HOME_ADVANTAGE]
    assert home["total_tips"] == 3
    assert home["total_correct"] == 2
    assert home["overall_accuracy"] == pytest.approx(2 / 3)
    # 2 home wins settle at the fallback price (+$9 each); the draw
    # round pushes ($0) — NOT a loss, per the settlement contract.
    assert home["total_profit"] == pytest.approx(2 * _FALLBACK_WIN)
    assert home["total_rounds"] == 3
    assert home["avg_profit_per_round"] == pytest.approx(
        home["total_profit"] / 3
    )
    assert home["best_round_accuracy"] == pytest.approx(1.0)  # rounds 1 and 3
    assert home["worst_round_accuracy"] == pytest.approx(0.0)  # the draw round
    assert home["odds_coverage"] == 0.0

    assert comparison[HEURISTIC_FORM]["total_correct"] == 1
    assert comparison[HEURISTIC_FORM]["overall_accuracy"] == pytest.approx(1 / 3)
    assert comparison[HEURISTIC_LADDER]["total_correct"] == 1
    assert comparison[HEURISTIC_LADDER]["overall_accuracy"] == pytest.approx(1 / 3)

    assert past["best_overall"]["heuristic"] == HEURISTIC_HOME_ADVANTAGE
    assert past["best_overall"]["accuracy"] == pytest.approx(2 / 3)

    # Current season via the explicit-label comparison path.
    assert present["season"] == "2026"
    assert present["comparison"][HEURISTIC_HOME_ADVANTAGE]["total_correct"] == 2
    assert present["comparison"][HEURISTIC_FORM]["total_correct"] == 1
    assert present["best_overall"]["heuristic"] in (
        HEURISTIC_HOME_ADVANTAGE,
        HEURISTIC_LADDER,
    )
