"""Unit tests for the league numeric model predictions (PARITY-1).

The league counterpart of the AFL orchestrator's ``model_predictions``
pass: six models (elo, form, home_advantage, value, weather_impact,
matchup) compute per-event ``(winner, confidence, margin)`` numbers
from the SAME chronological slices the tip picks read, abstain (no
row) without usable evidence, and persist diff-based (idempotent).

Layers, mirroring test_league_heuristics.py:

* **Pure prediction functions** — table-driven, no database: the elo
  confidence/margin formula, the AFL ``value`` constants (clamp
  0.5–0.9 / 1–80), and the abstention contracts (matchup without
  meetings, weather without a row/sample, home_advantage without a
  usable sample).
* **Pipeline** — the one-walk chronological pass: a scheduled fixture's
  numbers read only PRIOR completed evidence (no look-ahead).
* **Service orchestration** — fake session, patched fetchers: the
  diff-based upsert is IDEMPOTENT (insert once, update only on change).
"""

from __future__ import annotations

import contextlib
from contextlib import ExitStack
from datetime import datetime
from types import SimpleNamespace
from typing import Any, Iterator, Sequence
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from packages.shared.services.league_heuristics import (
    ELO_START_RATING,
    CompletedEvent,
    LeagueHeuristicsService,
    SeasonRef,
)
from packages.shared.services.league_model_predictions import (
    LeagueModelPredictionPick,
    LeagueModelPredictionService,
    compute_all_model_predictions,
    predict_elo_numbers,
    predict_home_advantage_numbers,
    predict_matchup_numbers,
    predict_value_numbers,
    predict_weather_impact_numbers,
)

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
    completed: bool = True,
    venue: str | None = "Test Oval",
) -> CompletedEvent:
    """A snapshot; decided once both scores are given."""
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
        completed=completed,
        venue=venue,
    )


_NAMES = {101: "Alpha", 102: "Beta"}


# ---------------------------------------------------------------------------
# Pure prediction functions.
# ---------------------------------------------------------------------------


class TestPredictEloNumbers:
    def test_elo_confidence_and_margin_follow_the_rating_gap(self) -> None:
        """One decided prior: 11 → 1510, 12 → 1490.  Target has 11 AWAY:
        diff = −20 → away wins at 1 − expected_home, margin |diff|/10."""
        prior = [_event(1, home_pid=11, away_pid=12, home_score=90, away_score=70)]
        target = _event(2, home_pid=12, away_pid=11)
        pick = predict_elo_numbers(
            target, prior, home_name="Beta", away_name="Alpha"
        )
        assert pick is not None
        assert pick.model_name == "elo"
        assert pick.winner == "Alpha"
        expected_home = 1.0 / (1.0 + 10.0 ** (20.0 / 400.0))
        assert pick.confidence == pytest.approx(1.0 - expected_home)
        assert pick.margin == 2

    def test_no_history_is_a_home_coin_flip_at_the_margin_floor(self) -> None:
        pick = predict_elo_numbers(
            _event(1), [], home_name="Alpha", away_name="Beta"
        )
        assert pick is not None
        assert pick.winner == "Alpha"
        assert pick.confidence == pytest.approx(0.5)
        assert pick.margin == 1

    def test_ratings_start_at_the_documented_level(self) -> None:
        assert ELO_START_RATING == 1500.0


class TestPredictValueNumbers:
    def test_afl_constants_clamp_confidence_and_margin(self) -> None:
        """A 1.0-vs-0.0 rate gap is a +1.0 differential: the AFL
        ValueModel constants clamp confidence to 0.9 and margin to 80
        (rate × 150 would otherwise say 1.5 / 150)."""
        prior = [
            _event(1, home_pid=11, away_pid=21, home_score=90, away_score=70),
            _event(2, home_pid=12, away_pid=22, home_score=50, away_score=80),
        ]  # 11 is 1-0 (rate 1.0); 12 is 0-1 (rate 0.0)
        pick = predict_value_numbers(
            _event(3, home_pid=11, away_pid=12),
            prior,
            home_name="Alpha",
            away_name="Beta",
        )
        assert pick is not None
        assert pick.winner == "Alpha"
        assert pick.confidence == 0.9  # clamp(0.5 + 1.0, 0.5, 0.9)
        assert pick.margin == 80  # clamp(int(1.0 × 150), 1, 80)

    def test_tie_resolves_away_at_the_floor(self) -> None:
        """Raw rates only: a 0.5-vs-0.5 tie (no home bonus — that is the
        home_advantage model's job) resolves to the away side at the
        0.5/1 floor."""
        pick = predict_value_numbers(
            _event(1), [], home_name="Alpha", away_name="Beta"
        )
        assert pick is not None
        assert pick.winner == "Beta"
        assert pick.confidence == pytest.approx(0.5)
        assert pick.margin == 1


class TestPredictMatchupNumbers:
    def test_no_decided_meetings_abstains(self) -> None:
        prior = [
            _event(1, home_pid=11, away_pid=21, home_score=90, away_score=70),
            _event(2, home_pid=11, away_pid=12, home_score=44, away_score=44),
        ]  # an other-opponent win + a DRAWN direct meeting — no decided h2h
        assert (
            predict_matchup_numbers(
                _event(3), prior, home_name="Alpha", away_name="Beta"
            )
            is None
        )

    def test_a_decided_meeting_produces_numbers(self) -> None:
        prior = [_event(1, home_pid=11, away_pid=12, home_score=90, away_score=70)]
        pick = predict_matchup_numbers(
            _event(2), prior, home_name="Alpha", away_name="Beta"
        )
        assert pick is not None
        assert pick.winner == "Alpha"  # 11 leads the h2h 1-0
        assert pick.margin >= 1


class TestPredictHomeAdvantageNumbers:
    def test_below_the_sample_floor_abstains(self) -> None:
        prior = [_event(1, home_pid=11, away_pid=12, home_score=90, away_score=70)]
        assert (
            predict_home_advantage_numbers(
                _event(2), prior, home_name="Alpha", away_name="Beta"
            )
            is None
        )

    def test_three_decided_events_produce_numbers(self) -> None:
        prior = [
            _event(1, home_pid=11, away_pid=21, home_score=90, away_score=70),
            _event(2, home_pid=12, away_pid=22, home_score=80, away_score=60),
            _event(3, home_pid=13, away_pid=23, home_score=70, away_score=50),
        ]  # homes 3/3 → share 1.0
        pick = predict_home_advantage_numbers(
            _event(4), prior, home_name="Alpha", away_name="Beta"
        )
        assert pick is not None
        assert pick.winner == "Alpha"
        assert pick.confidence == pytest.approx(0.85)  # clamp(1.0, 0.5, 0.85)
        assert pick.margin == 60  # clamp(int((1.0 − 0.5) × 200), 1, 60)


class TestPredictWeatherImpactNumbers:
    def test_no_weather_row_abstains(self) -> None:
        assert (
            predict_weather_impact_numbers(
                _event(1),
                [],
                home_name="Alpha",
                away_name="Beta",
                weather=None,
                weather_by_event={},
            )
            is None
        )

    def test_weather_row_without_a_usable_sample_abstains(self) -> None:
        """A forecast exists but no prior same-venue similar-tier event
        clears the 3-event sample floor — both sides' rates are None →
        the model abstains (never invents numbers)."""
        weather = {
            "venue": "Test Oval",
            "precipitation": 0.0,
            "wind_gusts": 10.0,
            "temperature": 20.0,
        }
        pick = predict_weather_impact_numbers(
            _event(1),
            [_event(2, home_pid=11, away_pid=12, home_score=90, away_score=70)],
            home_name="Alpha",
            away_name="Beta",
            weather=weather,
            weather_by_event={},  # no historical weather snapshots
        )
        assert pick is None


# ---------------------------------------------------------------------------
# Pipeline — chronological, no look-ahead for scheduled fixtures.
# ---------------------------------------------------------------------------


class TestComputeAllModelPredictions:
    def test_scheduled_numbers_read_only_prior_completed_evidence(self) -> None:
        """A later-started COMPLETED event must be invisible to a
        scheduled fixture's numbers: E1 (11 beats 12 big) puts 11 two
        rating bands above 12 — elo's E2 row is a 2-point home margin.
        Had the later E3 (12's 100-point rout) leaked in, 12 would edge
        ahead and the row would flip to a 1-point away margin."""
        e1 = _event(
            1,
            starts_at=datetime(2026, 4, 1),
            home_pid=11,
            away_pid=12,
            home_score=90,
            away_score=70,
        )
        e3 = _event(
            3,
            starts_at=datetime(2026, 4, 15),
            home_pid=12,
            away_pid=11,
            home_score=110,
            away_score=10,
        )
        e2 = _event(
            2,
            starts_at=datetime(2026, 4, 8),
            home_side=201,
            away_side=202,
            home_pid=11,
            away_pid=12,
            completed=False,
        )
        side_names = {
            101: "Alpha",
            102: "Beta",
            201: "Gamma",
            202: "Delta",
            301: "Epsilon",
            302: "Zeta",
        }
        predictions = compute_all_model_predictions(
            [e3, e1, e2],  # deliberately shuffled — the pipeline sorts
            side_names=side_names,
            weather_by_event={},
        )
        by_key = {(p.event_id, p.model_name): p for p in predictions}
        elo_e2 = by_key[(2, "elo")]
        assert elo_e2.winner == "Gamma"  # the home side (participant 11)
        assert elo_e2.margin == 2  # (1510 − 1490) / 10 — E1 evidence only
        # The later completed event still gets its own rows (the model
        # breakdown covers completed events too).
        assert (3, "elo") in by_key

    def test_drawn_and_unnamed_events_yield_no_rows(self) -> None:
        draw = _event(1, home_score=44, away_score=44)
        unknown_sides = _event(2, home_side=999, away_side=998)
        predictions = compute_all_model_predictions(
            [draw, unknown_sides],
            side_names=_NAMES,
            weather_by_event={},
        )
        assert predictions == []


# ---------------------------------------------------------------------------
# Service orchestration — fake session, patched fetchers (no database).
# ---------------------------------------------------------------------------


def _stored_rows(picks: Sequence[LeagueModelPredictionPick]) -> list[tuple]:
    """Shape ``_upsert_predictions`` reads back from ``db.execute``."""
    return [
        (p.event_id, p.model_name, p.winner, p.confidence, p.margin)
        for p in picks
    ]


class _RowsResult:
    """``execute()`` result: ``.all()`` for the diff SELECT and a
    mutable ORM-row stand-in for the update path's scalar lookup."""

    def __init__(self, rows: list[tuple]) -> None:
        self._rows = rows

    def all(self) -> list[tuple]:
        return list(self._rows)

    def scalar_one_or_none(self) -> Any:
        for row in self._rows:
            return SimpleNamespace(
                winner=row[2], confidence=row[3], margin=row[4]
            )
        return None


def _fake_session(rows: list[tuple]) -> AsyncMock:
    """AsyncSession double whose ``execute`` resolves the stored rows."""
    db = AsyncMock()
    db.add = MagicMock()
    db.flush = AsyncMock()
    db.commit = AsyncMock()
    db.execute = AsyncMock(return_value=_RowsResult(rows))
    return db


def _patched_fetches(
    service: LeagueModelPredictionService,
    seasons: list[SeasonRef],
    events: list[CompletedEvent],
) -> tuple[Any, ...]:
    """Class-level heuristics fetchers (the predictions service calls
    them via the class) + instance-level name/weather fetchers."""
    return (
        patch.object(
            LeagueHeuristicsService,
            "_fetch_seasons",
            AsyncMock(return_value=seasons),
        ),
        patch.object(
            LeagueHeuristicsService,
            "_fetch_completed_events",
            AsyncMock(return_value=events),
        ),
        patch.object(
            LeagueHeuristicsService,
            "_fetch_scheduled_events",
            AsyncMock(return_value=[]),
        ),
        patch.object(
            service, "_fetch_side_names", AsyncMock(return_value=_NAMES)
        ),
        patch.object(service, "_fetch_weather", AsyncMock(return_value={})),
    )


@contextlib.contextmanager
def _applied(patchers: tuple[Any, ...]) -> Iterator[None]:
    with ExitStack() as stack:
        for patcher in patchers:
            stack.enter_context(patcher)
        yield


class TestServiceUpsertIdempotency:
    @pytest.mark.asyncio
    async def test_insert_then_update_only_when_changed(self) -> None:
        seasons = [SeasonRef(id=7, label="2026", is_current=True)]
        events = [
            _event(1, season_id=7, home_score=90, away_score=70)
        ]  # no prior evidence: only elo + value produce numbers
        picks = compute_all_model_predictions(
            events, side_names=_NAMES, weather_by_event={}
        )
        assert [p.model_name for p in picks] == ["elo", "value"]

        service = LeagueModelPredictionService()

        # --- first pass: no stored rows → every pick inserts ----------
        db = _fake_session([])
        with _applied(_patched_fetches(service, seasons, events)):
            first = await service.generate_for_competition(db, competition_id=42)

        assert first["predictions_inserted"] == 2
        assert first["predictions_updated"] == 0
        rows = [call.args[0] for call in db.add.call_args_list]
        assert {row.model_name for row in rows} == {"elo", "value"}
        assert all(row.event_id == 1 for row in rows)
        assert all(row.winner in ("Alpha", "Beta") for row in rows)
        db.flush.assert_awaited_once()

        # --- second pass: identical numbers → zero churn ---------------
        db = _fake_session(_stored_rows(picks))
        with _applied(_patched_fetches(service, seasons, events)):
            second = await service.generate_for_competition(db, competition_id=42)

        assert second["predictions_inserted"] == 0
        assert second["predictions_updated"] == 0
        db.add.assert_not_called()

        # --- third pass: one stored margin changed → one update --------
        drifted = [
            row if row[1] != "elo" else (*row[:4], row[4] + 5)
            for row in _stored_rows(picks)
        ]
        db = _fake_session(drifted)
        with _applied(_patched_fetches(service, seasons, events)):
            third = await service.generate_for_competition(db, competition_id=42)

        assert third["predictions_inserted"] == 0
        assert third["predictions_updated"] == 1
        db.add.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_seasons_is_a_zero_noop(self) -> None:
        service = LeagueModelPredictionService()
        db = _fake_session([])
        with _applied(_patched_fetches(service, [], [])):
            summary = await service.generate_for_competition(
                db, competition_id=42
            )
        assert summary["predictions_inserted"] == 0
        assert summary["predictions_updated"] == 0
        db.add.assert_not_called()
