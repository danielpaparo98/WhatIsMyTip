"""Unit tests for the league heuristics service (performance-per-league D3).

Four layers, mirroring established repo patterns:

* **Pure pick logic** — the three result-derived heuristics with the
  EXACT D3 semantics.  No database: the pick functions take plain
  frozen dataclasses, so every rule below is a table-driven unit test.
* **Derived-heuristic pipeline (FCL-6)** — the pure best_bet consensus
  and the weighted_tip/boosted_tip combiners over the SAME
  chronological slices the base picks read, with the majority-vote
  fallback wherever a learned model is missing or fails.
* **Service orchestration** — ``LeagueHeuristicsService.generate_for_competition``
  against a fake session with the thin DB fetchers patched (the
  ``test_local_competition_sync`` pattern).  Proves the two-season
  scope (current + most recent past), the draw-no-pick persistence
  shape, the per-competition persistence of the base models AND the
  three derived names (best_bet / weighted_tip / boosted_tip, FCL-6),
  the graceful fallback on scoped model-load failure, and
  IDEMPOTENCE: a re-run inserts nothing, updates nothing and never
  touches the UNIQUE (event_id, heuristic) constraint.
* **End-to-end** (``@pytest.mark.postgres``) — the real migration chain
  on a podman Postgres testcontainer (the ``test_league_tips_model``
  pattern): generate → re-generate against the real constraint.
  Skips when podman is unavailable.
"""

from __future__ import annotations

import contextlib
import os
import shutil
import socket
import subprocess
import sys
import time
import uuid
from contextlib import contextmanager
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
    DEFAULT_MODEL_SET,
    DERIVED_LEAGUE_HEURISTICS,
    HEURISTIC_BEST_BET,
    HEURISTIC_BOOSTED_TIP,
    HEURISTIC_ELO,
    HEURISTIC_FORM,
    HEURISTIC_HOME_ADVANTAGE,
    HEURISTIC_LADDER,
    HEURISTIC_MATCHUP,
    HEURISTIC_VALUE,
    HEURISTIC_WEIGHTED_TIP,
    HEURISTIC_YOLO,
    LEAGUE_HEURISTICS,
    LEAGUE_MODEL_SETS,
    LEAGUE_MODELS,
    PARITY_LEAGUE_MODEL_SET,
    RUGBY_LEAGUE_MODELS,
    SPORT_MODEL_SETS,
    CompletedEvent,
    LeagueHeuristicsService,
    SeasonRef,
    Standing,
    TipPick,
    _validate_registry,
    compute_all_picks,
    compute_all_picks_and_derived,
    compute_derived_picks,
    compute_event_picks,
    compute_standings,
    compute_win_rates,
    is_drawn,
    league_model_set_for_competition,
    league_model_set_for_sport,
    pick_form,
    pick_home_advantage,
    pick_ladder,
    pick_value,
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
    completed: bool = True,
) -> CompletedEvent:
    """A completed-event snapshot; decided once both scores are given.
    ``completed=False`` marks a FCL-12 scheduled snapshot (forward tips)."""
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


class TestComputeWinRates:
    def test_decided_win_rate_per_participant(self) -> None:
        history = [
            _event(1, home_pid=11, away_pid=12, home_score=90, away_score=70),
            _event(2, home_pid=12, away_pid=11, home_score=90, away_score=70),
        ]
        rates = compute_win_rates(history)
        assert rates[11] == pytest.approx(0.5)
        assert rates[12] == pytest.approx(0.5)

    def test_draws_are_excluded_from_the_denominator(self) -> None:
        """A draw is a win for neither side — it enters no denominator."""
        history = [
            _event(1, home_pid=11, away_pid=12, home_score=90, away_score=70),
            _event(2, home_pid=11, away_pid=12, home_score=44, away_score=44),
        ]
        rates = compute_win_rates(history)
        # 11: 1 win from its 1 DECIDED game (the draw doesn't inflate
        # the denominator); 12: 0 wins from the same decided game —
        # present at 0.0 (a decided loss is evidence, a draw is not).
        assert rates == {11: pytest.approx(1.0), 12: pytest.approx(0.0)}

    def test_undecided_rows_are_skipped_entirely(self) -> None:
        assert compute_win_rates([_event(1)]) == {}

    def test_order_does_not_matter(self) -> None:
        e1 = _event(1, home_pid=11, away_pid=12, home_score=90, away_score=70)
        e2 = _event(2, home_pid=12, away_pid=11, home_score=80, away_score=60)
        assert compute_win_rates([e1, e2]) == compute_win_rates([e2, e1])


class TestPickValue:
    """PARITY (2026-10-14): the AFL ``value`` model's league counterpart —
    the better all-time decided win rate; tie → home."""

    def test_side_with_the_better_all_time_win_rate_is_picked(self) -> None:
        prior = [
            _event(1, home_pid=11, away_pid=13, home_score=90, away_score=70),
            _event(2, home_pid=11, away_pid=14, home_score=90, away_score=70),
            _event(3, home_pid=12, away_pid=15, home_score=50, away_score=80),
        ]
        # 11 is 2-0 (rate 1.0); 12 is 0-1 (rate 0.0) — 11 is the HOME
        # side of the target event, so the home side is picked.
        assert pick_value(_event(4, home_pid=11, away_pid=12), prior) == 101

    def test_better_rate_side_is_picked_wherever_it_plays(self) -> None:
        prior = [_event(1, home_pid=11, away_pid=12, home_score=70, away_score=90)]
        # 12 won the only decided meeting → rate 1.0 vs 11's 0.0; 12 is
        # now the HOME side.
        assert pick_value(_event(2, home_pid=12, away_pid=11), prior) == 101

    def test_tie_goes_home(self) -> None:
        assert pick_value(_event(2), []) == 101

    def test_rates_tie_goes_home_even_with_history(self) -> None:
        prior = [
            _event(1, home_pid=11, away_pid=13, home_score=90, away_score=70),
            _event(2, home_pid=12, away_pid=14, home_score=90, away_score=70),
        ]
        # Both sides are 1-0 — the raw rates tie (home advantage is owned
        # by its own model) → home.
        assert pick_value(_event(3, home_pid=11, away_pid=12), prior) == 101

    def test_draw_history_leaves_the_winless_side_at_the_default(self) -> None:
        prior = [
            _event(1, home_pid=11, away_pid=12, home_score=90, away_score=70),
            _event(2, home_pid=11, away_pid=12, home_score=44, away_score=44),
        ]
        # 11: 1.0 from its single decided game; 12: 0.0 from the same
        # decided game (the draw enters no denominator and no win) —
        # the HOME side 11 has the better rate and is picked.
        assert pick_value(_event(3), prior) == 101

    def test_missing_sides_make_no_pick(self) -> None:
        assert pick_value(_event(1, home_pid=None), []) is None
        assert pick_value(_event(1, away_pid=None), []) is None


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
# Per-league model registry (FCL-2) — league → sport → default resolution.
# ---------------------------------------------------------------------------


class TestLeagueModelSets:
    """The per-league registry layered above the per-sport one: keyed on
    the competition name (``LeagueConfig.name`` == ``competitions.name``),
    holding BASE pick models only — the derived heuristics
    (best_bet / weighted_tip / boosted_tip) are built ON the resolved set,
    never registered in it."""

    def test_values_are_non_empty_tuples_of_registered_base_models(self) -> None:
        for name, models in LEAGUE_MODEL_SETS.items():
            assert isinstance(name, str) and name, "key is the competition name"
            assert isinstance(models, tuple) and models, name
            assert all(model in LEAGUE_MODELS for model in models), name

    def test_derived_heuristics_are_not_registry_eligible(self) -> None:
        derived = {"best_bet", "weighted_tip", "boosted_tip"}
        assert not derived & set(LEAGUE_MODELS)
        for models in LEAGUE_MODEL_SETS.values():
            assert not derived & set(models)

    def test_registered_league_overrides_its_sport_default(self, monkeypatch) -> None:
        monkeypatch.setitem(LEAGUE_MODEL_SETS, "Override Test League", (HEURISTIC_ELO,))
        assert (
            league_model_set_for_competition("Override Test League", sport_id="afl")
            == (HEURISTIC_ELO,)
        )
        # The league step wins even when the sport carries its own set.
        monkeypatch.setitem(
            SPORT_MODEL_SETS, "override-test-sport", (HEURISTIC_MATCHUP,)
        )
        assert (
            league_model_set_for_competition(
                "Override Test League", sport_id="override-test-sport"
            )
            == (HEURISTIC_ELO,)
        )

    @pytest.mark.parametrize(
        ("name", "sport_id"),
        [
            ("Never Registered League", "afl"),
            ("Never Registered League", "rugby-league"),
            ("Never Registered League", "netball"),
            ("Never Registered League", None),
        ],
    )
    def test_unregistered_league_is_byte_identical_to_sport_resolution(
        self, name: str | None, sport_id: str | None
    ) -> None:
        assert league_model_set_for_competition(
            name, sport_id
        ) == league_model_set_for_sport(sport_id)

    def test_unresolvable_name_still_falls_through_to_sport(self) -> None:
        assert league_model_set_for_competition(None) == DEFAULT_MODEL_SET
        assert (
            league_model_set_for_competition(None, sport_id="rugby-league")
            == RUGBY_LEAGUE_MODELS
        )

    def test_default_model_set_is_the_parity_five_in_canonical_order(self) -> None:
        """PARITY (2026-10-14, absolute AFL/league parity): ONE canonical
        base set for EVERY league — AFL's six result/weather models minus
        the two with no non-AFL source.  Unregistered sports (state
        leagues, sport 'afl') resolve here; the D3 trio is no longer any
        league's active set."""
        assert PARITY_LEAGUE_MODEL_SET == (
            "elo",
            "form",
            "home_advantage",
            "matchup",
            "value",
        )
        assert DEFAULT_MODEL_SET == PARITY_LEAGUE_MODEL_SET

    def test_rugby_league_is_registered_as_the_parity_set(self) -> None:
        assert RUGBY_LEAGUE_MODELS == PARITY_LEAGUE_MODEL_SET
        assert SPORT_MODEL_SETS["rugby-league"] == PARITY_LEAGUE_MODEL_SET

    def test_ladder_stays_registered_but_in_no_active_set(self) -> None:
        """``ladder`` remains a pure-registry pick (pick_ladder exists),
        but no league's ACTIVE set contains it any more."""
        assert HEURISTIC_LADDER in LEAGUE_MODELS
        assert HEURISTIC_LADDER not in DEFAULT_MODEL_SET
        assert HEURISTIC_LADDER not in RUGBY_LEAGUE_MODELS
        for models in LEAGUE_MODEL_SETS.values():
            assert HEURISTIC_LADDER not in models

    def test_unknown_model_name_in_a_registration_fails_fast(
        self, monkeypatch
    ) -> None:
        monkeypatch.setitem(LEAGUE_MODEL_SETS, "Broken League", ("no_such_model",))
        with pytest.raises(ValueError, match="Broken League"):
            _validate_registry()

    def test_empty_model_set_in_a_registration_fails_fast(
        self, monkeypatch
    ) -> None:
        monkeypatch.setitem(LEAGUE_MODEL_SETS, "Empty League", ())
        with pytest.raises(ValueError, match="Empty League"):
            _validate_registry()

    def test_valid_registration_passes_validation(self, monkeypatch) -> None:
        monkeypatch.setitem(
            LEAGUE_MODEL_SETS,
            "Valid Test League",
            (HEURISTIC_ELO, HEURISTIC_FORM),
        )
        _validate_registry()  # must not raise


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
    *,
    scheduled_events: list[CompletedEvent] | None = None,
    existing_numbers: tuple[dict[tuple[int, str], float | None],
                            dict[tuple[int, str], int | None]] | None = None,
    linear_weights: tuple[float, dict[str, float]] | None = None,
    boosted_model: Any | None = None,
) -> tuple[Any, ...]:
    """Instance-level fetch/loader patches — the class's real staticmethods
    are never touched (each test builds its own service instance).  The
    learned-model loaders default to ``None`` — the majority-vote
    fallback (FCL-3d), i.e. no scoped trained model.  ``scheduled_events``
    defaults to ``()`` — no upcoming fixtures (FCL-12 forward tips).
    ``existing_numbers`` defaults to empty maps — no stored confidence/
    margin (FCL-14)."""
    return (
        patch.object(service, "_fetch_seasons", AsyncMock(return_value=seasons)),
        patch.object(
            service, "_fetch_completed_events", AsyncMock(return_value=events)
        ),
        patch.object(
            service, "_fetch_existing_tips", AsyncMock(return_value=existing)
        ),
        patch.object(
            service,
            "_load_linear_weights",
            AsyncMock(return_value=linear_weights),
        ),
        patch.object(
            service,
            "_load_boosted_artifact",
            AsyncMock(return_value=boosted_model),
        ),
        patch.object(
            service,
            "_fetch_scheduled_events",
            AsyncMock(return_value=scheduled_events or []),
        ),
        patch.object(
            service,
            "_fetch_existing_tip_numbers",
            AsyncMock(return_value=existing_numbers or ({}, {})),
        ),
    )


@contextmanager
def _applied(patches: tuple[Any, ...]) -> Iterator[None]:
    """Apply every patch from :func:`_patched_fetches` (any tuple length)."""
    with contextlib.ExitStack() as stack:
        for p in patches:
            stack.enter_context(p)
        yield


class TestGenerateForCompetition:
    @pytest.mark.asyncio
    async def test_generates_base_and_derived_tips_per_completed_event(
        self,
    ) -> None:
        """FCL-6 + PARITY: every completed event persists the base
        models' tips AND the four derived heuristics (best_bet /
        weighted_tip / boosted_tip / yolo) — drawn events carry NULL
        selections across all nine names."""
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
        with _applied(patches):
            summary = await service.generate_for_competition(db, competition_id=42)

        rows = [call.args[0] for call in db.add.call_args_list]
        assert len(rows) == 27  # 3 completed events × (5 base + 4 derived)
        assert {row.heuristic for row in rows} == (
            set(DEFAULT_MODEL_SET) | set(DERIVED_LEAGUE_HEURISTICS)
        )
        assert all(row.competition_id == 42 for row in rows)
        assert {row.season_id for row in rows} == {1, 2}
        # The drawn event persists draw-no-pick tips (NULL selection)
        # for every name, derived ones included.
        assert all(
            row.selected_participant_id is None for row in rows if row.event_id == 2
        )
        # Every other selection points at an event_participants row id.
        assert all(
            row.selected_participant_id in (101, 102)
            for row in rows
            if row.event_id != 2
        )
        assert summary["tips_inserted"] == 27
        assert summary["tips_updated"] == 0
        assert summary["derived_tips"] == {
            "best_bet": 3,
            "weighted_tip": 3,
            "boosted_tip": 3,
            "yolo": 3,
        }
        db.commit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_rerun_with_unchanged_results_inserts_and_updates_nothing(self) -> None:
        seasons = [SeasonRef(id=1, label="2025", is_current=False),
                   SeasonRef(id=2, label="2026", is_current=True)]
        events = [_event(1, season_id=1, home_score=90, away_score=70)]
        db = _fake_session()
        service = LeagueHeuristicsService()
        # PARITY: the pass resolves the DEFAULT (parity) set, so the
        # stored rows must be computed from the SAME model set.
        picks = compute_all_picks_and_derived(events, models=DEFAULT_MODEL_SET)
        existing = {(p.event_id, p.heuristic): p.selected_side_id for p in picks}
        numbers = (
            {(p.event_id, p.heuristic): p.confidence for p in picks},
            {(p.event_id, p.heuristic): p.margin for p in picks},
        )
        first_patches = _patched_fetches(service, seasons, events, {})
        second_patches = _patched_fetches(
            service, seasons, events, existing, existing_numbers=numbers
        )
        with _applied(first_patches):
            first = await service.generate_for_competition(db, competition_id=42)
        db.add = MagicMock()  # fresh spy for the re-run
        with _applied(second_patches):
            second = await service.generate_for_competition(db, competition_id=42)

        assert first["tips_inserted"] == 9  # 5 base + 4 derived
        assert second["tips_inserted"] == 0
        assert second["tips_updated"] == 0
        assert second["derived_tips"] == {
            name: 0 for name in DERIVED_LEAGUE_HEURISTICS
        }
        db.add.assert_not_called()  # no churn: unchanged rows are untouched

    @pytest.mark.asyncio
    async def test_changed_pick_updates_the_existing_row_in_place(self) -> None:
        seasons = [SeasonRef(id=2, label="2026", is_current=True)]
        events = [_event(1, season_id=2, home_score=90, away_score=70)]
        # PARITY: the pass resolves the DEFAULT (parity) set, so the
        # stored rows must be computed from the SAME model set.
        picks = compute_all_picks_and_derived(events, models=DEFAULT_MODEL_SET)
        existing = {(p.event_id, p.heuristic): p.selected_side_id for p in picks}
        # Upstream score revision: the stored home_advantage pick is stale.
        existing[(1, HEURISTIC_HOME_ADVANTAGE)] = 999
        numbers = (
            {(p.event_id, p.heuristic): p.confidence for p in picks},
            {(p.event_id, p.heuristic): p.margin for p in picks},
        )
        tip_row = SimpleNamespace(id=7, selected_participant_id=999)
        db = _fake_session()
        # _update_tip loads the ORM row via scalar_one_or_none().
        db.execute = AsyncMock(
            return_value=SimpleNamespace(scalar_one_or_none=lambda: tip_row)
        )
        service = LeagueHeuristicsService()
        patches = _patched_fetches(
            service, seasons, events, existing, existing_numbers=numbers
        )
        with _applied(patches):
            summary = await service.generate_for_competition(db, competition_id=42)

        assert summary["tips_inserted"] == 0
        assert summary["tips_updated"] == 1
        # The row was pointed at the recomputed pick (the home side).
        home_advantage_pick = next(
            p for p in picks if p.heuristic == HEURISTIC_HOME_ADVANTAGE
        )
        assert (
            tip_row.selected_participant_id
            == home_advantage_pick.selected_side_id
            == 101
        )
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
        assert summary["derived_tips"] == {
            name: 0 for name in DERIVED_LEAGUE_HEURISTICS
        }
        db.add.assert_not_called()
        db.commit.assert_not_awaited()


# ---------------------------------------------------------------------------
# Derived-heuristic pipeline (FCL-6) — pure consensus + learned combiners
# over the SAME chronological slices the base picks read.
# ---------------------------------------------------------------------------


def _d3_linear_weights(form_wins_diff: float) -> tuple[float, dict[str, float]]:
    """Linear weights over the D3 feature contract, isolating the form
    feature so tests can steer the learned margin deterministically."""
    return (
        0.0,
        {
            "home_advantage_home_constant": 0.0,
            "form_wins_diff": form_wins_diff,
            "ladder_wins_diff": 0.0,
            "ladder_pct_diff": 0.0,
        },
    )


class _ExplodingPredictor:
    """A 'loaded' boosted model whose predict always fails — stands in
    for a corrupt artifact / stale feature contract at prediction time."""

    def predict(self, features: Any) -> list[float]:
        raise RuntimeError("booster exploded")


class TestDerivedHeuristicContract:
    def test_derived_names_under_their_contract_names(self) -> None:
        assert HEURISTIC_BEST_BET == "best_bet"
        assert HEURISTIC_WEIGHTED_TIP == "weighted_tip"
        assert HEURISTIC_BOOSTED_TIP == "boosted_tip"

    def test_derived_set_is_exactly_the_four_in_canonical_order(self) -> None:
        """FCL-14 (AFL parity): the derived set is the AFL heuristic set —
        best_bet, weighted_tip, boosted_tip AND yolo — so the selectable
        heuristic list is IDENTICAL across AFL and every league."""
        assert DERIVED_LEAGUE_HEURISTICS == (
            "best_bet",
            "weighted_tip",
            "boosted_tip",
            "yolo",
        )

    def test_best_bet_name_matches_the_consensus_module(self) -> None:
        """league_consensus imports THIS module, so the constant lives here
        and the mirror is pinned — the two can never silently drift."""
        from packages.shared.services.league_consensus import (
            HEURISTIC_BEST_BET as CONSENSUS_BEST_BET,
        )

        assert HEURISTIC_BEST_BET == CONSENSUS_BEST_BET

    def test_derived_names_are_not_base_models(self) -> None:
        for name in DERIVED_LEAGUE_HEURISTICS:
            assert name not in LEAGUE_MODELS


class TestComputeDerivedPicks:
    """Per-event derived picks: pure, fed the SAME prior/same-season
    slices the base picks read (the caller owns the no-look-ahead
    slicing, exactly as for ``compute_event_picks``)."""

    def test_missing_sides_yield_no_derived_tips(self) -> None:
        assert compute_derived_picks(_event(1, home_side=None), [], [], []) == []
        assert compute_derived_picks(_event(2, away_side=None), [], [], []) == []

    def test_drawn_event_yields_draw_no_pick_for_all_three(self) -> None:
        event = _event(1, home_score=44, away_score=44)
        picks = compute_derived_picks(event, [], [], [])
        assert [p.heuristic for p in picks] == list(DERIVED_LEAGUE_HEURISTICS)
        assert all(p.event_id == 1 for p in picks)
        assert all(p.selected_side_id is None for p in picks)

    def test_learned_inputs_do_not_disturb_the_draw_discipline(self) -> None:
        event = _event(1, home_score=44, away_score=44)
        picks = compute_derived_picks(
            event,
            [],
            [],
            [],
            linear_weights=_d3_linear_weights(-100.0),
            boosted_model=SimpleNamespace(predict=lambda features: [-100.0]),
        )
        assert all(p.selected_side_id is None for p in picks)

    def test_best_bet_is_the_majority_vote_over_the_base_picks(self) -> None:
        event = _event(5, home_score=80, away_score=70)
        base = [
            TipPick(5, HEURISTIC_HOME_ADVANTAGE, 101),
            TipPick(5, HEURISTIC_FORM, 102),
            TipPick(5, HEURISTIC_LADDER, 101),
        ]
        by_name = {
            p.heuristic: p.selected_side_id
            for p in compute_derived_picks(event, [], [], base)
        }
        assert by_name[HEURISTIC_BEST_BET] == 101  # 2 home votes vs 1 away

    def test_vote_tie_and_zero_info_go_home(self) -> None:
        event = _event(5, home_score=80, away_score=70)
        split = [
            TipPick(5, HEURISTIC_HOME_ADVANTAGE, 101),
            TipPick(5, HEURISTIC_FORM, 102),
        ]
        by_name = {
            p.heuristic: p.selected_side_id
            for p in compute_derived_picks(event, [], [], split)
        }
        assert by_name[HEURISTIC_BEST_BET] == 101  # FCL-3a tie → home
        empty = {
            p.heuristic: p.selected_side_id
            for p in compute_derived_picks(event, [], [], [])
        }
        assert empty[HEURISTIC_BEST_BET] == 101  # zero-info → home default

    def test_weighted_tip_without_weights_is_the_majority_vote(self) -> None:
        event = _event(5, home_score=80, away_score=70)
        base = [
            TipPick(5, HEURISTIC_HOME_ADVANTAGE, 102),
            TipPick(5, HEURISTIC_FORM, 102),
        ]
        by_name = {
            p.heuristic: p.selected_side_id
            for p in compute_derived_picks(event, [], [], base)
        }
        assert by_name[HEURISTIC_WEIGHTED_TIP] == 102
        assert by_name[HEURISTIC_WEIGHTED_TIP] == by_name[HEURISTIC_BEST_BET]

    def test_weighted_tip_uses_the_learned_linear_combiner(self) -> None:
        # 11 won the prior meeting, so form_wins_diff = +1 for the home
        # side; the -100 coefficient flips the learned margin to -100 →
        # away — DIVERGING from the unanimous-home base consensus.
        prior = [_event(1, home_pid=11, away_pid=12, home_score=90, away_score=70)]
        event = _event(2)
        base = [TipPick(2, name, 101) for name in LEAGUE_HEURISTICS]
        by_name = {
            p.heuristic: p.selected_side_id
            for p in compute_derived_picks(
                event,
                prior,
                prior,
                base,
                linear_weights=_d3_linear_weights(-100.0),
            )
        }
        assert by_name[HEURISTIC_WEIGHTED_TIP] == 102
        assert by_name[HEURISTIC_BEST_BET] == 101

    def test_boosted_tip_without_a_model_is_the_majority_vote(self) -> None:
        event = _event(5, home_score=80, away_score=70)
        base = [
            TipPick(5, HEURISTIC_HOME_ADVANTAGE, 102),
            TipPick(5, HEURISTIC_FORM, 102),
        ]
        by_name = {
            p.heuristic: p.selected_side_id
            for p in compute_derived_picks(event, [], [], base)
        }
        assert by_name[HEURISTIC_BOOSTED_TIP] == 102
        assert by_name[HEURISTIC_BOOSTED_TIP] == by_name[HEURISTIC_BEST_BET]

    def test_boosted_tip_uses_the_learned_model(self) -> None:
        model = SimpleNamespace(predict=lambda features: [-100.0])
        prior = [_event(1, home_pid=11, away_pid=12, home_score=90, away_score=70)]
        event = _event(2)
        base = [TipPick(2, name, 101) for name in LEAGUE_HEURISTICS]
        by_name = {
            p.heuristic: p.selected_side_id
            for p in compute_derived_picks(
                event, prior, prior, base, boosted_model=model
            )
        }
        assert by_name[HEURISTIC_BOOSTED_TIP] == 102
        assert by_name[HEURISTIC_BEST_BET] == 101

    def test_learned_linear_failure_falls_back_to_the_majority_vote(self) -> None:
        # A non-numeric intercept fails float() inside the linear
        # prediction — the pick must degrade to the fallback, not raise.
        event = _event(2)
        base = [TipPick(2, name, 101) for name in LEAGUE_HEURISTICS]
        by_name = {
            p.heuristic: p.selected_side_id
            for p in compute_derived_picks(
                event, [], [], base, linear_weights=("not-a-number", {})
            )
        }
        assert by_name[HEURISTIC_WEIGHTED_TIP] == 101

    def test_learned_boosted_failure_falls_back_to_the_majority_vote(self) -> None:
        event = _event(2)
        base = [TipPick(2, name, 101) for name in LEAGUE_HEURISTICS]
        by_name = {
            p.heuristic: p.selected_side_id
            for p in compute_derived_picks(
                event, [], [], base, boosted_model=_ExplodingPredictor()
            )
        }
        assert by_name[HEURISTIC_BOOSTED_TIP] == 101


class TestComputeAllPicksAndDerived:
    """The single-walk pipeline: base + derived per event, no re-walk,
    no look-ahead — derived picks only ever see PRIOR evidence."""

    def test_emits_base_then_derived_for_every_event_in_one_walk(self) -> None:
        e1 = _event(1, home_pid=11, away_pid=12, home_score=90, away_score=70)
        e2 = _event(2, home_pid=12, away_pid=11, home_score=80, away_score=60)
        picks = compute_all_picks_and_derived([e1, e2])
        per_event = list(LEAGUE_HEURISTICS) + list(DERIVED_LEAGUE_HEURISTICS)
        assert [p.heuristic for p in picks] == per_event + per_event

    def test_drawn_event_carries_null_base_and_derived_picks(self) -> None:
        draw = _event(1, home_score=44, away_score=44)
        picks = compute_all_picks_and_derived([draw])
        assert len(picks) == 7
        assert {p.heuristic for p in picks} == (
            set(LEAGUE_HEURISTICS) | set(DERIVED_LEAGUE_HEURISTICS)
        )
        assert all(p.selected_side_id is None for p in picks)
        # FCL-14: draw-no-pick rows carry NO card numbers either.
        assert all(p.confidence is None and p.margin is None for p in picks)

    def test_no_lookahead_a_later_event_never_changes_an_earlier_pick(self) -> None:
        early = _event(
            1,
            starts_at=datetime(2026, 4, 2),
            home_pid=11,
            away_pid=12,
            home_score=90,
            away_score=70,
        )
        later = _event(
            2,
            starts_at=datetime(2026, 5, 2),
            home_pid=11,
            away_pid=12,
            home_score=50,
            away_score=90,
        )
        # The later event is in the input (deliberately shuffled) — the
        # early event's base AND derived picks must not see it.
        shuffled = compute_all_picks_and_derived([later, early])
        alone = compute_all_picks_and_derived([early])
        assert [p for p in shuffled if p.event_id == 1] == alone

    def test_learned_weights_reach_the_derived_picks_through_the_walk(self) -> None:
        e1 = _event(1, home_pid=11, away_pid=12, home_score=90, away_score=70)
        e2 = _event(2, home_pid=11, away_pid=12, home_score=70, away_score=50)
        picks = compute_all_picks_and_derived(
            [e1, e2], linear_weights=_d3_linear_weights(-100.0)
        )
        by_key = {(p.event_id, p.heuristic): p.selected_side_id for p in picks}
        # Event 1: empty prior → all-zero features → y = 0 → home.
        assert by_key[(1, HEURISTIC_WEIGHTED_TIP)] == 101
        # Event 2: form_wins_diff = +1 × (-100) → away, while the
        # majority-vote base consensus stays home.
        assert by_key[(2, HEURISTIC_WEIGHTED_TIP)] == 102
        assert by_key[(2, HEURISTIC_BEST_BET)] == 101

    def test_base_picks_match_the_standalone_base_pipeline(self) -> None:
        e1 = _event(1, home_pid=11, away_pid=12, home_score=90, away_score=70)
        e2 = _event(2, home_pid=12, away_pid=11, home_score=80, away_score=60)
        picks = compute_all_picks_and_derived([e1, e2])
        base = compute_all_picks([e1, e2])
        assert [p for p in picks if p.heuristic in LEAGUE_MODELS] == base


class TestGenerateForCompetitionDerived:
    """FCL-6 service wiring: derived names persist per competition, the
    learned combiners load scoped, and every failure mode degrades to
    the majority-vote fallback — generation never crashes on a model."""

    @pytest.mark.asyncio
    async def test_persists_base_and_derived_rows_per_completed_event(
        self,
    ) -> None:
        seasons = [SeasonRef(id=7, label="2026", is_current=True)]
        events = [_event(1, season_id=7, home_score=90, away_score=70)]
        db = _fake_session()
        service = LeagueHeuristicsService()
        patches = _patched_fetches(service, seasons, events, {})
        with _applied(patches):
            summary = await service.generate_for_competition(db, competition_id=42)

        rows = [call.args[0] for call in db.add.call_args_list]
        assert len(rows) == 9  # 1 event × (5 base + 4 derived)
        assert {row.heuristic for row in rows} == (
            set(DEFAULT_MODEL_SET) | set(DERIVED_LEAGUE_HEURISTICS)
        )
        assert all(row.competition_id == 42 for row in rows)
        assert all(row.season_id == 7 for row in rows)
        assert summary["tips_inserted"] == 9
        assert summary["derived_tips"] == {
            "best_bet": 1,
            "weighted_tip": 1,
            "boosted_tip": 1,
            "yolo": 1,
        }
        db.commit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_drawn_event_derives_null_tips_too(self) -> None:
        seasons = [SeasonRef(id=7, label="2026", is_current=True)]
        events = [_event(1, season_id=7, home_score=44, away_score=44)]
        db = _fake_session()
        service = LeagueHeuristicsService()
        patches = _patched_fetches(service, seasons, events, {})
        with _applied(patches):
            summary = await service.generate_for_competition(db, competition_id=42)

        rows = [call.args[0] for call in db.add.call_args_list]
        assert len(rows) == 9
        assert all(row.selected_participant_id is None for row in rows)
        assert summary["tips_inserted"] == 9

    @pytest.mark.asyncio
    async def test_best_bet_is_the_majority_vote_over_disagreeing_base_models(
        self,
    ) -> None:
        seasons = [SeasonRef(id=7, label="2026", is_current=True)]
        events = [
            # 11 wins big → Elo 1510, rate 1.0.
            _event(
                1,
                season_id=7,
                home_pid=11,
                away_pid=41,
                home_score=100,
                away_score=50,
            ),
            # 12 wins big → Elo 1510 too, rate 1.0.
            _event(
                2,
                season_id=7,
                home_pid=12,
                away_pid=42,
                home_score=100,
                away_score=50,
            ),
            # 11 loses → Elo drops below 12 (~1499.7 vs 1510), rate 0.5.
            _event(
                3,
                season_id=7,
                home_pid=11,
                away_pid=21,
                home_score=50,
                away_score=100,
            ),
            # Target: home_advantage → home, form ties 1-1 → home,
            # matchup (no meetings) → home — but elo (12 rated higher)
            # AND value (12's rate 1.0 vs 11's 0.5) vote away: a 3-2
            # split the majority vote resolves to home.
            _event(
                4,
                season_id=7,
                home_pid=11,
                away_pid=12,
                home_score=70,
                away_score=50,
            ),
        ]
        db = _fake_session()
        service = LeagueHeuristicsService()
        patches = _patched_fetches(service, seasons, events, {})
        with _applied(patches):
            await service.generate_for_competition(db, competition_id=42)

        rows = [call.args[0] for call in db.add.call_args_list]
        by_key = {
            (row.event_id, row.heuristic): row.selected_participant_id
            for row in rows
            if row.event_id == 4
        }
        # The two dissenting base votes (PARITY set).
        assert by_key[(4, HEURISTIC_ELO)] == 102
        assert by_key[(4, HEURISTIC_VALUE)] == 102
        # ...against the three home votes.
        assert by_key[(4, HEURISTIC_HOME_ADVANTAGE)] == 101
        assert by_key[(4, HEURISTIC_FORM)] == 101
        assert by_key[(4, HEURISTIC_MATCHUP)] == 101
        assert by_key[(4, HEURISTIC_BEST_BET)] == 101  # majority of the split
        # Before training both combiners fall back to the SAME majority.
        assert by_key[(4, HEURISTIC_WEIGHTED_TIP)] == 101
        assert by_key[(4, HEURISTIC_BOOSTED_TIP)] == 101

    @pytest.mark.asyncio
    async def test_loaded_learned_combiners_override_the_fallback(self) -> None:
        seasons = [SeasonRef(id=7, label="2026", is_current=True)]
        events = [
            _event(
                1, season_id=7, home_pid=11, away_pid=12,
                home_score=90, away_score=70,
            ),
            _event(
                2, season_id=7, home_pid=11, away_pid=12,
                home_score=70, away_score=50,
            ),
        ]
        linear = _d3_linear_weights(-100.0)
        boosted = SimpleNamespace(predict=lambda features: [-100.0])
        db = _fake_session()
        service = LeagueHeuristicsService()
        patches = _patched_fetches(
            service,
            seasons,
            events,
            {},
            linear_weights=linear,
            boosted_model=boosted,
        )
        with _applied(patches):
            await service.generate_for_competition(db, competition_id=42)

        rows = [call.args[0] for call in db.add.call_args_list]
        by_key = {
            (row.event_id, row.heuristic): row.selected_participant_id
            for row in rows
        }
        # Event 2: form_wins_diff +1 × (-100) → away for BOTH combiners,
        # while the base consensus stays home.
        assert by_key[(2, HEURISTIC_WEIGHTED_TIP)] == 102
        assert by_key[(2, HEURISTIC_BOOSTED_TIP)] == 102
        assert by_key[(2, HEURISTIC_BEST_BET)] == 101
        # Event 1: zero features → linear y = 0 → home; the boosted
        # model still says -100 → away (the two paths are independent).
        assert by_key[(1, HEURISTIC_WEIGHTED_TIP)] == 101
        assert by_key[(1, HEURISTIC_BOOSTED_TIP)] == 102

    @pytest.mark.asyncio
    async def test_scoped_model_load_failure_falls_back_and_never_crashes(
        self,
    ) -> None:
        """The CRUD reads raising (DB outage) must not fail generation —
        the REAL loaders degrade to ``None`` (their guard is under test
        here, so they are NOT patched out) and both combiners ride the
        majority-vote fallback."""
        seasons = [SeasonRef(id=7, label="2026", is_current=True)]
        events = [_event(1, season_id=7, home_score=90, away_score=70)]
        db = _fake_session()
        service = LeagueHeuristicsService()
        patches = (
            patch.object(service, "_fetch_seasons", AsyncMock(return_value=seasons)),
            patch.object(
                service, "_fetch_completed_events", AsyncMock(return_value=events)
            ),
            patch.object(
                service, "_fetch_existing_tips", AsyncMock(return_value={})
            ),
            patch.object(
                service, "_fetch_scheduled_events", AsyncMock(return_value=[])
            ),
            patch.object(
                service,
                "_fetch_existing_tip_numbers",
                AsyncMock(return_value=({}, {})),
            ),
            patch(
                "packages.shared.crud.model_versions.get_active_coefficients",
                new=AsyncMock(side_effect=RuntimeError("db down")),
            ),
            patch(
                "packages.shared.crud.model_versions.get_active_model_artifact",
                new=AsyncMock(side_effect=RuntimeError("db down")),
            ),
        )
        with _applied(patches):
            summary = await service.generate_for_competition(db, competition_id=42)

        rows = [call.args[0] for call in db.add.call_args_list]
        by_key = {
            (row.event_id, row.heuristic): row.selected_participant_id
            for row in rows
        }
        assert by_key[(1, HEURISTIC_WEIGHTED_TIP)] == by_key[
            (1, HEURISTIC_BEST_BET)
        ]
        assert by_key[(1, HEURISTIC_BOOSTED_TIP)] == by_key[
            (1, HEURISTIC_BEST_BET)
        ]
        assert summary["tips_inserted"] == 9
        db.commit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_learned_prediction_failure_falls_back_per_combiner(
        self,
    ) -> None:
        """A loaded model that fails at PREDICT time (corrupt artifact /
        stale feature contract) degrades that combiner to the fallback —
        the pass and the other combiner are unaffected."""
        seasons = [SeasonRef(id=7, label="2026", is_current=True)]
        events = [_event(1, season_id=7, home_score=90, away_score=70)]
        db = _fake_session()
        service = LeagueHeuristicsService()
        patches = _patched_fetches(
            service,
            seasons,
            events,
            {},
            linear_weights=("not-a-number", {}),
            boosted_model=_ExplodingPredictor(),
        )
        with _applied(patches):
            summary = await service.generate_for_competition(db, competition_id=42)

        rows = [call.args[0] for call in db.add.call_args_list]
        by_key = {
            (row.event_id, row.heuristic): row.selected_participant_id
            for row in rows
        }
        assert by_key[(1, HEURISTIC_WEIGHTED_TIP)] == by_key[
            (1, HEURISTIC_BEST_BET)
        ]
        assert by_key[(1, HEURISTIC_BOOSTED_TIP)] == by_key[
            (1, HEURISTIC_BEST_BET)
        ]
        assert summary["tips_inserted"] == 9
        db.commit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_registered_competition_resolves_its_set_through_its_name(
        self,
        monkeypatch,
    ) -> None:
        """FCL-2 resolution flows through generation: the league-registered
        BASE set is honoured, and the three derived names are appended."""
        monkeypatch.setitem(LEAGUE_MODEL_SETS, "Named Test League", (HEURISTIC_ELO,))
        seasons = [SeasonRef(id=7, label="2026", is_current=True)]
        events = [_event(1, season_id=7, home_score=90, away_score=70)]
        db = _fake_session()
        service = LeagueHeuristicsService()
        patches = _patched_fetches(service, seasons, events, {})
        with _applied(
            (
                *patches,
                patch.object(
                    service, "_fetch_sport_id", AsyncMock(return_value="afl")
                ),
            )
        ), patch.object(
            service,
            "_fetch_competition_name",
            AsyncMock(return_value="Named Test League"),
        ):
            summary = await service.generate_for_competition(db, competition_id=42)

        rows = [call.args[0] for call in db.add.call_args_list]
        assert {row.heuristic for row in rows} == (
            {HEURISTIC_ELO} | set(DERIVED_LEAGUE_HEURISTICS)
        )
        assert summary["tips_inserted"] == 5

    @pytest.mark.asyncio
    async def test_unregistered_competition_falls_through_to_sport_plus_derived(
        self,
    ) -> None:
        """All leagues are first-class (FCL-6): an unregistered name keeps
        its sport's base set byte-identically AND gains the derived names.
        PARITY: rugby-league's set IS the default parity five."""
        seasons = [SeasonRef(id=7, label="2026", is_current=True)]
        events = [_event(1, season_id=7, home_score=90, away_score=70)]
        db = _fake_session()
        service = LeagueHeuristicsService()
        patches = _patched_fetches(service, seasons, events, {})
        with _applied(
            (
                *patches,
                patch.object(
                    service,
                    "_fetch_sport_id",
                    AsyncMock(return_value="rugby-league"),
                ),
            )
        ), patch.object(
            service,
            "_fetch_competition_name",
            AsyncMock(return_value="Mystery League"),
        ):
            summary = await service.generate_for_competition(db, competition_id=42)

        rows = [call.args[0] for call in db.add.call_args_list]
        assert {row.heuristic for row in rows} == (
            set(RUGBY_LEAGUE_MODELS) | set(DERIVED_LEAGUE_HEURISTICS)
        )
        assert summary["tips_inserted"] == 9


class TestScheduledEventTips:
    """FCL-12: forward tips — scheduled fixtures get the same nine names.

    A tip is a tip: the league pages show upcoming fixtures, and every
    model in a league's set is causal (prior completed evidence only),
    so generation now also covers SCHEDULED events with both sides.
    Grading is untouched — league_backtest grades ``completed`` events
    only, so a forward pick cannot pollute accuracy until played."""

    @pytest.mark.asyncio
    async def test_scheduled_event_persists_base_and_derived_tips(self) -> None:
        """One completed event + one scheduled: the scheduled fixture gets
        all nine names with picks from PRIOR evidence only (no NULLs — a
        fixture without scores is not a draw)."""
        seasons = [SeasonRef(id=7, label="2026", is_current=True)]
        completed = [_event(1, season_id=7, home_score=90, away_score=70)]
        scheduled = [
            _event(
                2,
                season_id=7,
                starts_at=datetime(2026, 4, 8),
                home_score=None,
                away_score=None,
                completed=False,
            )
        ]
        db = _fake_session()
        service = LeagueHeuristicsService()
        patches = _patched_fetches(
            service, seasons, completed, {}, scheduled_events=scheduled
        )
        with _applied(patches):
            summary = await service.generate_for_competition(db, competition_id=42)

        rows = [call.args[0] for call in db.add.call_args_list]
        scheduled_rows = [row for row in rows if row.event_id == 2]
        assert {row.heuristic for row in scheduled_rows} == (
            set(DEFAULT_MODEL_SET) | set(DERIVED_LEAGUE_HEURISTICS)
        )
        # A fixture without scores is NOT a draw — every pick points at a side.
        assert all(
            row.selected_participant_id in (101, 102) for row in scheduled_rows
        )
        assert summary["tips_inserted"] == 18  # 2 events × 9 names
        assert summary["scheduled_considered"] == 1
        assert summary["events_considered"] == 1  # completed only, unchanged

    @pytest.mark.asyncio
    async def test_scheduled_picks_follow_prior_evidence(self) -> None:
        """The scheduled pick is causal: home side wins the prior round
        convincingly, so every base model (home_advantage, form's 1-0
        window, Elo's +20, matchup's 1-0 h2h, value's 1.0-vs-0.0 rate)
        votes home — best_bet and the fallback combiners echo the
        unanimous vote."""
        seasons = [SeasonRef(id=7, label="2026", is_current=True)]
        completed = [_event(1, season_id=7, home_score=90, away_score=70)]
        scheduled = [
            _event(
                2,
                season_id=7,
                starts_at=datetime(2026, 4, 8),
                home_score=None,
                away_score=None,
                completed=False,
            )
        ]
        db = _fake_session()
        service = LeagueHeuristicsService()
        patches = _patched_fetches(
            service, seasons, completed, {}, scheduled_events=scheduled
        )
        with _applied(patches):
            await service.generate_for_competition(db, competition_id=42)

        rows = [call.args[0] for call in db.add.call_args_list]
        scheduled_rows = [row for row in rows if row.event_id == 2]
        assert {row.heuristic: row.selected_participant_id
                for row in scheduled_rows} == {
            HEURISTIC_ELO: 101,
            HEURISTIC_FORM: 101,
            HEURISTIC_HOME_ADVANTAGE: 101,
            HEURISTIC_MATCHUP: 101,
            HEURISTIC_VALUE: 101,
            HEURISTIC_BEST_BET: 101,
            HEURISTIC_WEIGHTED_TIP: 101,
            HEURISTIC_BOOSTED_TIP: 101,
            HEURISTIC_YOLO: 101,
        }

    @pytest.mark.asyncio
    async def test_scheduled_event_without_sides_gets_no_tips(self) -> None:
        """A scheduled fixture missing a side row can never carry a tip
        (the pick FK targets the side row) — same discipline as D3."""
        seasons = [SeasonRef(id=7, label="2026", is_current=True)]
        completed = [_event(1, season_id=7, home_score=90, away_score=70)]
        scheduled = [
            _event(
                2,
                season_id=7,
                starts_at=datetime(2026, 4, 8),
                home_side=None,
                away_side=None,
                home_pid=None,
                away_pid=None,
                home_score=None,
                away_score=None,
                completed=False,
            )
        ]
        db = _fake_session()
        service = LeagueHeuristicsService()
        patches = _patched_fetches(
            service, seasons, completed, {}, scheduled_events=scheduled
        )
        with _applied(patches):
            summary = await service.generate_for_competition(db, competition_id=42)

        rows = [call.args[0] for call in db.add.call_args_list]
        assert [row for row in rows if row.event_id == 2] == []
        assert summary["tips_inserted"] == 9
        assert summary["scheduled_considered"] == 1

    @pytest.mark.asyncio
    async def test_scheduled_zero_zero_placeholders_get_real_picks(self) -> None:
        """FCL-12 live bug (AFLW round 10): feeds ship pre-match 0-0
        score placeholders on scheduled fixtures.  Without the completion
        guard in :func:`is_drawn` every forward tip degenerated to
        draw-no-pick NULLs; now the fixture gets REAL side picks."""
        seasons = [SeasonRef(id=7, label="2026", is_current=True)]
        completed = [_event(1, season_id=7, home_score=90, away_score=70)]
        scheduled = [
            _event(
                2,
                season_id=7,
                starts_at=datetime(2026, 4, 8),
                home_score=0,
                away_score=0,  # the feed's pre-match placeholder pair
                completed=False,
            )
        ]
        db = _fake_session()
        service = LeagueHeuristicsService()
        patches = _patched_fetches(
            service, seasons, completed, {}, scheduled_events=scheduled
        )
        with _applied(patches):
            summary = await service.generate_for_competition(db, competition_id=42)

        rows = [call.args[0] for call in db.add.call_args_list]
        scheduled_rows = [row for row in rows if row.event_id == 2]
        assert len(scheduled_rows) == 9
        assert all(
            row.selected_participant_id == 101  # the unanimous home vote
            for row in scheduled_rows
        )
        assert summary["tips_inserted"] == 18

    @pytest.mark.asyncio
    async def test_scheduled_picks_exclude_later_completed_evidence(self) -> None:
        """No look-ahead for forward tips: a scheduled fixture's evidence
        slice contains only completed events that start no later than
        the fixture — a LATER-started completed event (e.g. a round that
        concluded while this fixture awaited its result) is invisible."""
        seasons = [SeasonRef(id=7, label="2026", is_current=True)]
        completed = [
            _event(
                1,
                season_id=7,
                starts_at=datetime(2026, 4, 1),
                home_score=90,
                away_score=70,
            ),
            # Starts AFTER the scheduled fixture yet is already completed:
            # participant 12 (the AWAY side of E2) thrashes 11 — if this
            # leaked into the fixture's evidence, ELO would flip to the
            # away side (12 would be rated above 11 off this rout).
            _event(
                3,
                season_id=7,
                starts_at=datetime(2026, 4, 15),
                home_pid=12,
                away_pid=11,
                home_score=110,
                away_score=10,
            ),
        ]
        scheduled = [
            _event(
                2,
                season_id=7,
                starts_at=datetime(2026, 4, 8),
                home_score=None,
                away_score=None,
                completed=False,
            )
        ]
        db = _fake_session()
        service = LeagueHeuristicsService()
        patches = _patched_fetches(
            service, seasons, completed, {}, scheduled_events=scheduled
        )
        with _applied(patches):
            await service.generate_for_competition(db, competition_id=42)

        rows = [call.args[0] for call in db.add.call_args_list]
        scheduled_rows = {r.heuristic: r.selected_participant_id
                          for r in rows if r.event_id == 2}
        # Evidence = E1 only (E3 starts after the fixture): form sees one
        # 90-70 home win and the home side leads/ties on every signal —
        # home across the board.  Had E3 leaked in, the away side's
        # 110-point rout would lift its Elo above the home side's and
        # ELO (and only Elo) would flip to 102.
        assert scheduled_rows == {
            HEURISTIC_ELO: 101,
            HEURISTIC_FORM: 101,
            HEURISTIC_HOME_ADVANTAGE: 101,
            HEURISTIC_MATCHUP: 101,
            HEURISTIC_VALUE: 101,
            HEURISTIC_BEST_BET: 101,
            HEURISTIC_WEIGHTED_TIP: 101,
            HEURISTIC_BOOSTED_TIP: 101,
            HEURISTIC_YOLO: 101,
        }


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

    # 6 events (5 completed + 1 scheduled) × (5 base + 4 derived)
    # heuristics — FCL-12 forward tips cover the scheduled fixture too.
    # No scoped trained models exist, so both combiners ride the
    # majority-vote fallback — rows still exist.
    assert first["tips_inserted"] == 54
    assert total == 54
    assert heuristics == set(DEFAULT_MODEL_SET) | set(DERIVED_LEAGUE_HEURISTICS)
    assert season_ids == {ids["season_2025"], ids["season_2026"]}
    # Exactly the nine draw tips (one drawn event × 9 names) carry NULL.
    assert null_picks == 9
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

    # 6 events (5 completed + 1 scheduled) × (5 base + 4 derived)
    # heuristics — FCL-12 forward tips cover the scheduled fixture too.
    assert summary["tips_inserted"] == 54
