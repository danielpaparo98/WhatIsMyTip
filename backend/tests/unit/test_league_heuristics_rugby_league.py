"""Rugby-league league-model registry (Phase 5.2, P2-3).

The reduced DB-only model set for rugby-league — ``elo``, ``form``,
``home_advantage``, ``matchup`` — registered against the shared
league-tips pipeline, keyed to the three national competitions
(``nrl``/``nrlw``/``origin``, all sport ``rugby-league``), and the
EXPLICIT EXCLUSION of the four AFL-scrape-sourced models
(``weather_impact``, ``injury_impact``, ``player_form``, ``value``)
which have no NRL source: requesting one for rugby-league is a clean
repo-standard ``BackendServiceError``.

Layers, mirroring ``test_league_heuristics``:

* **Per-sport registry** — the subset map and its D3 default (AFL
  behavior byte-identical: no explicit AFL entry, unknown sports fall
  back to the D3 set).
* **Exclusion contract** — the repo-standard error shape for the four
  excluded models.
* **Pure pick logic** — the new result-derived ``elo`` and ``matchup``
  rules, no database.
* **Pipeline scoping** — ``compute_event_picks``/``compute_all_picks``
  honour the per-sport set; the no-argument default stays the D3 three.
* **Service orchestration** — the competition's ``sport_id`` selects
  the set (fake session, patched fetchers, no database).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.exceptions import BackendServiceError
from packages.shared.services.league_heuristics import (
    DEFAULT_MODEL_SET,
    ELO_START_RATING,
    HEURISTIC_ELO,
    HEURISTIC_MATCHUP,
    LEAGUE_HEURISTICS,
    LEAGUE_MODELS,
    RUGBY_LEAGUE_EXCLUDED_MODELS,
    RUGBY_LEAGUE_MODELS,
    SPORT_MODEL_SETS,
    CompletedEvent,
    LeagueHeuristicsService,
    SeasonRef,
    compute_all_picks,
    compute_elo_ratings,
    compute_event_picks,
    head_to_head_wins,
    league_model_set_for_sport,
    pick_elo,
    pick_matchup,
    require_league_model,
)
from packages.shared.sport_context import RUGBY_LEAGUE

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
# Per-sport registry — the reduced rugby-league subset, D3 default intact.
# ---------------------------------------------------------------------------


class TestRugbyLeagueModelSet:
    def test_exactly_the_four_db_only_models(self) -> None:
        assert RUGBY_LEAGUE_MODELS == ("elo", "form", "home_advantage", "matchup")

    def test_registry_lookup_for_rugby_league(self) -> None:
        assert league_model_set_for_sport("rugby-league") == RUGBY_LEAGUE_MODELS

    def test_set_is_keyed_on_the_sport_context_id(self) -> None:
        assert SPORT_MODEL_SETS[RUGBY_LEAGUE.sport_id] == RUGBY_LEAGUE_MODELS

    def test_afl_keeps_the_d3_set_unchanged(self) -> None:
        assert LEAGUE_HEURISTICS == ("home_advantage", "form", "ladder")
        assert league_model_set_for_sport("afl") == LEAGUE_HEURISTICS

    def test_afl_has_no_explicit_registry_entry(self) -> None:
        """AFL is the default, not a registration — nothing to disturb."""
        assert "afl" not in SPORT_MODEL_SETS
        assert DEFAULT_MODEL_SET is LEAGUE_HEURISTICS

    def test_unknown_or_missing_sport_falls_back_to_d3(self) -> None:
        assert league_model_set_for_sport("netball") == LEAGUE_HEURISTICS
        assert league_model_set_for_sport(None) == LEAGUE_HEURISTICS

    def test_every_registered_model_has_a_pick_spec(self) -> None:
        assert all(name in LEAGUE_MODELS for name in RUGBY_LEAGUE_MODELS)
        # ...and the registry still carries the D3 trio.
        assert all(name in LEAGUE_MODELS for name in LEAGUE_HEURISTICS)

    def test_new_models_exposed_under_their_contract_names(self) -> None:
        assert HEURISTIC_ELO == "elo"
        assert HEURISTIC_MATCHUP == "matchup"


class TestKeyedToTheThreeCompetitions:
    """Every national competition syncs under sport ``rugby-league``, so
    its competition resolves to the reduced set through the same sport
    lookup the tips hook uses."""

    def test_all_three_national_competitions_resolve_to_the_subset(self) -> None:
        from packages.shared.ingestion.national_leagues import NATIONAL_LEAGUES

        assert set(NATIONAL_LEAGUES) == {"nrl", "nrlw", "origin"}
        for key, config in NATIONAL_LEAGUES.items():
            provider = config.provider_factory()
            assert provider.sport_id == "rugby-league", key
            assert league_model_set_for_sport(provider.sport_id) == (
                RUGBY_LEAGUE_MODELS
            ), key


# ---------------------------------------------------------------------------
# Exclusion contract — the four AFL-scrape models, rejected cleanly.
# ---------------------------------------------------------------------------


class TestExcludedModels:
    @pytest.mark.parametrize(
        "model_name",
        ["weather_impact", "injury_impact", "player_form", "value"],
    )
    def test_excluded_model_request_is_rejected_cleanly(
        self, model_name: str
    ) -> None:
        with pytest.raises(BackendServiceError) as excinfo:
            require_league_model(model_name, sport_id="rugby-league")
        exc = excinfo.value
        assert exc.status_code == 400
        assert exc.code == "model_unavailable_for_sport"
        assert model_name in exc.message
        assert exc.details["model"] == model_name
        assert exc.details["sport_id"] == "rugby-league"
        assert exc.details["available"] == list(RUGBY_LEAGUE_MODELS)

    def test_exclusion_is_explicit_and_complete(self) -> None:
        assert RUGBY_LEAGUE_EXCLUDED_MODELS == frozenset(
            {"weather_impact", "injury_impact", "player_form", "value"}
        )

    def test_excluded_names_are_not_league_picks(self) -> None:
        """They have no result-derived pick — that's WHY they're excluded
        (they need AFL scrapers with no NRL source)."""
        for name in RUGBY_LEAGUE_EXCLUDED_MODELS:
            assert name not in LEAGUE_MODELS
            assert name not in RUGBY_LEAGUE_MODELS

    def test_included_models_pass_through_with_the_sport_set(self) -> None:
        for name in RUGBY_LEAGUE_MODELS:
            assert require_league_model(
                name, sport_id="rugby-league"
            ) == RUGBY_LEAGUE_MODELS

    def test_d3_model_not_offered_for_rugby_league_is_rejected(self) -> None:
        """``ladder`` is AFL-only at the league-tips layer."""
        with pytest.raises(BackendServiceError) as excinfo:
            require_league_model("ladder", sport_id="rugby-league")
        assert excinfo.value.status_code == 400
        assert excinfo.value.code == "model_unavailable_for_sport"

    def test_excluded_model_for_afl_is_unknown_at_the_league_layer(self) -> None:
        """AFL's weather model lives in models_ml, not the league-tips
        pipeline — so at THIS layer the name is simply unknown."""
        with pytest.raises(ValueError):
            require_league_model("weather_impact", sport_id="afl")

    def test_unknown_model_name_is_a_value_error(self) -> None:
        with pytest.raises(ValueError):
            require_league_model("boosted_tip", sport_id="rugby-league")


# ---------------------------------------------------------------------------
# Pure pick logic — elo and matchup, result-derived, no database.
# ---------------------------------------------------------------------------


class TestComputeEloRatings:
    def test_one_decided_win_is_worth_ten_points(self) -> None:
        ratings = compute_elo_ratings(
            [_event(1, home_pid=11, away_pid=12, home_score=90, away_score=70)]
        )
        # Both start at 1500, expected 0.5, K=20: 1500 ± 20 × 0.5.
        assert ratings[11] == 1510.0
        assert ratings[12] == 1490.0

    def test_every_participant_starts_at_the_same_rating(self) -> None:
        assert ELO_START_RATING == 1500.0

    def test_a_draw_leaves_both_sides_level(self) -> None:
        ratings = compute_elo_ratings(
            [_event(1, home_pid=11, away_pid=12, home_score=44, away_score=44)]
        )
        assert ratings[11] == 1500.0
        assert ratings[12] == 1500.0

    def test_undecided_rows_are_skipped(self) -> None:
        ratings = compute_elo_ratings([_event(1)])  # no scores
        assert ratings == {}

    def test_updates_walk_in_chronological_order(self) -> None:
        # Round 1: 11 wins. Round 2: 12 wins the rematch. If the rounds
        # applied in reverse, 12's final rating would differ.
        r1 = _event(
            1,
            starts_at=datetime(2026, 3, 7),
            home_pid=11,
            away_pid=12,
            home_score=90,
            away_score=70,
        )
        r2 = _event(
            2,
            starts_at=datetime(2026, 3, 14),
            home_pid=12,
            away_pid=11,
            home_score=90,
            away_score=70,
        )
        ratings = compute_elo_ratings([r2, r1])  # deliberately shuffled
        ordered = compute_elo_ratings([r1, r2])
        assert ratings == ordered
        # 12 won the higher-rated 11 in round 2 → nets positive.
        assert ratings[12] > 1500.0 > ratings[11]


class TestPickElo:
    def test_no_history_is_a_tie_and_goes_home(self) -> None:
        assert pick_elo(_event(1), []) == 101

    def test_higher_rated_side_is_picked_away(self) -> None:
        prior = [
            _event(1, home_pid=11, away_pid=12, home_score=90, away_score=70)
        ]  # 11 → 1510, 12 → 1490
        target = _event(2, home_pid=12, away_pid=11)
        assert pick_elo(target, prior) == 102  # 11 plays away

    def test_higher_rated_side_is_picked_home(self) -> None:
        prior = [
            _event(1, home_pid=11, away_pid=12, home_score=90, away_score=70)
        ]
        target = _event(2, home_pid=11, away_pid=12)
        assert pick_elo(target, prior) == 101

    def test_unrated_opponent_defaults_to_the_start_rating(self) -> None:
        prior = [
            _event(1, home_pid=11, away_pid=12, home_score=90, away_score=70)
        ]  # 11 → 1510; 13 is unrated (1500)
        target = _event(2, home_pid=13, away_pid=11)
        assert pick_elo(target, prior) == 102

    def test_a_draw_history_stays_a_tie_and_goes_home(self) -> None:
        prior = [
            _event(1, home_pid=11, away_pid=12, home_score=44, away_score=44)
        ]
        assert pick_elo(_event(2), prior) == 101

    def test_missing_sides_make_no_pick(self) -> None:
        assert pick_elo(_event(1, home_pid=None), []) is None
        assert pick_elo(_event(1, away_pid=None), []) is None

    def test_history_order_does_not_change_the_pick(self) -> None:
        e1 = _event(
            1,
            starts_at=datetime(2026, 3, 7),
            home_pid=11,
            away_pid=12,
            home_score=90,
            away_score=70,
        )
        e2 = _event(
            2,
            starts_at=datetime(2026, 3, 14),
            home_pid=12,
            away_pid=11,
            home_score=50,
            away_score=80,
        )  # 11 wins away
        target = _event(3, home_pid=12, away_pid=11)
        assert pick_elo(target, [e2, e1]) == pick_elo(target, [e1, e2]) == 102


class TestHeadToHeadWins:
    def test_counts_decided_wins_per_side(self) -> None:
        history = [
            _event(1, home_pid=11, away_pid=12, home_score=90, away_score=70),
            _event(2, home_pid=12, away_pid=11, home_score=90, away_score=70),
            _event(3, home_pid=11, away_pid=12, home_score=44, away_score=44),
        ]  # 1-1 with a draw
        assert head_to_head_wins(11, 12, history) == (1, 1)

    def test_only_meetings_between_the_pair_count(self) -> None:
        history = [
            _event(1, home_pid=11, away_pid=13, home_score=90, away_score=70),
            _event(2, home_pid=12, away_pid=14, home_score=50, away_score=90),
        ]
        assert head_to_head_wins(11, 12, history) == (0, 0)


class TestPickMatchup:
    def test_no_meetings_goes_home(self) -> None:
        assert pick_matchup(_event(1), []) == 101

    def test_more_head_to_head_wins_is_picked_regardless_of_venue(self) -> None:
        prior = [
            _event(1, home_pid=11, away_pid=12, home_score=90, away_score=70),
            _event(2, home_pid=12, away_pid=11, home_score=60, away_score=80),
        ]  # 11 won both, one at each ground
        target = _event(3, home_pid=12, away_pid=11)
        assert pick_matchup(target, prior) == 102

    def test_equal_wins_go_home(self) -> None:
        prior = [
            _event(1, home_pid=11, away_pid=12, home_score=90, away_score=70),
            _event(2, home_pid=12, away_pid=11, home_score=90, away_score=70),
        ]
        assert pick_matchup(_event(3), prior) == 101

    def test_draws_never_count_as_wins(self) -> None:
        prior = [
            _event(1, home_pid=11, away_pid=12, home_score=44, away_score=44),
            _event(2, home_pid=11, away_pid=12, home_score=90, away_score=70),
        ]  # 11 leads 1-0 on wins
        target = _event(3, home_pid=12, away_pid=11)
        assert pick_matchup(target, prior) == 102

    def test_results_against_others_are_irrelevant(self) -> None:
        prior = [
            _event(1, home_pid=11, away_pid=13, home_score=90, away_score=60),
            _event(2, home_pid=11, away_pid=14, home_score=90, away_score=60),
            _event(3, home_pid=15, away_pid=12, home_score=90, away_score=60),
            _event(4, home_pid=16, away_pid=12, home_score=90, away_score=60),
        ]  # 11 is 2-0, 12 is 0-2 — but never against each other
        assert pick_matchup(_event(5, home_pid=11, away_pid=12), prior) == 101

    def test_missing_sides_make_no_pick(self) -> None:
        assert pick_matchup(_event(1, home_pid=None), []) is None
        assert pick_matchup(_event(1, away_pid=None), []) is None


# ---------------------------------------------------------------------------
# Pipeline scoping — the per-sport set flows through the pure pipeline;
# the D3 default stays byte-identical.
# ---------------------------------------------------------------------------


class TestComputeEventPicksRugbyLeagueSet:
    def test_exactly_the_four_models_in_registry_order(self) -> None:
        prior = [
            _event(1, home_pid=11, away_pid=12, home_score=90, away_score=70)
        ]
        event = _event(2, home_pid=11, away_pid=12)
        picks = compute_event_picks(
            event, prior, [], models=RUGBY_LEAGUE_MODELS
        )
        assert [p.heuristic for p in picks] == [
            "elo",
            "form",
            "home_advantage",
            "matchup",
        ]
        assert "ladder" not in [p.heuristic for p in picks]
        assert all(p.event_id == 2 for p in picks)
        assert all(p.selected_side_id is not None for p in picks)

    def test_draw_persists_draw_no_pick_for_all_four(self) -> None:
        event = _event(1, home_score=44, away_score=44)
        picks = compute_event_picks(
            event, [], [], models=RUGBY_LEAGUE_MODELS
        )
        assert [p.heuristic for p in picks] == list(RUGBY_LEAGUE_MODELS)
        assert all(p.selected_side_id is None for p in picks)

    def test_missing_side_yields_no_tips(self) -> None:
        event = _event(1, home_side=None)
        assert compute_event_picks(
            event, [], [], models=RUGBY_LEAGUE_MODELS
        ) == []

    def test_default_call_remains_the_d3_three(self) -> None:
        """No ``models=`` kwarg → the D3 set, exactly as before (AFL
        byte-identical)."""
        event = _event(1, home_score=80, away_score=70)
        picks = compute_event_picks(event, [], [])
        assert [p.heuristic for p in picks] == ["home_advantage", "form", "ladder"]


class TestComputeAllPicksRugbyLeagueSet:
    def test_four_tips_per_decided_event_and_none_for_missing_sides(self) -> None:
        decided = _event(1, home_score=90, away_score=70)
        no_sides = _event(2, home_side=None)
        picks = compute_all_picks(
            [decided, no_sides], models=RUGBY_LEAGUE_MODELS
        )
        assert {p.heuristic for p in picks} == set(RUGBY_LEAGUE_MODELS)
        assert all(p.event_id == 1 for p in picks)
        assert len(picks) == 4

    def test_elo_and_matchup_use_only_prior_evidence(self) -> None:
        r1 = _event(
            1, home_pid=11, away_pid=12, home_score=90, away_score=70
        )  # 11 wins
        r2 = _event(
            2, home_pid=12, away_pid=11, home_score=80, away_score=60
        )  # 12 wins — leaking this would flip both picks
        picks = compute_all_picks([r2, r1], models=RUGBY_LEAGUE_MODELS)
        by_key = {(p.event_id, p.heuristic): p.selected_side_id for p in picks}
        # Event 1: no prior evidence → ties → home.
        assert by_key[(1, HEURISTIC_ELO)] == r1.home_side_id
        assert by_key[(1, HEURISTIC_MATCHUP)] == r1.home_side_id
        # Event 2: only event 1 counts — 11 rated higher AND leads h2h 1-0,
        # and plays away.
        assert by_key[(2, HEURISTIC_ELO)] == r2.away_side_id
        assert by_key[(2, HEURISTIC_MATCHUP)] == r2.away_side_id

    def test_matchup_spans_seasons(self) -> None:
        past = _event(
            1,
            season_id=2,
            home_pid=11,
            away_pid=12,
            home_score=90,
            away_score=70,
        )  # 11 wins the prior-season meeting
        now = _event(2, season_id=1, home_pid=12, away_pid=11)
        picks = compute_all_picks([past, now], models=RUGBY_LEAGUE_MODELS)
        matchup = next(
            p for p in picks if p.event_id == 2 and p.heuristic == HEURISTIC_MATCHUP
        )
        assert matchup.selected_side_id == now.away_side_id

    def test_drawn_events_carry_null_picks_for_all_four(self) -> None:
        draw = _event(1, home_score=44, away_score=44)
        picks = compute_all_picks([draw], models=RUGBY_LEAGUE_MODELS)
        assert len(picks) == 4
        assert {p.heuristic for p in picks} == set(RUGBY_LEAGUE_MODELS)
        assert all(p.selected_side_id is None for p in picks)

    def test_default_call_remains_the_d3_three(self) -> None:
        decided = _event(1, home_score=90, away_score=70)
        picks = compute_all_picks([decided])
        assert {p.heuristic for p in picks} == set(LEAGUE_HEURISTICS)
        assert len(picks) == 3


# ---------------------------------------------------------------------------
# Service orchestration — the competition's sport selects the set
# (fake session, patched fetchers, no database).
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
    sport_id: str | None,
    seasons: list[SeasonRef],
    events: list[CompletedEvent],
    existing: dict[tuple[int, str], int | None],
) -> tuple[Any, ...]:
    """Instance-level fetch patches — the class's real staticmethods are
    never touched (each test builds its own service instance)."""
    return (
        patch.object(
            service, "_fetch_sport_id", AsyncMock(return_value=sport_id)
        ),
        patch.object(service, "_fetch_seasons", AsyncMock(return_value=seasons)),
        patch.object(
            service, "_fetch_completed_events", AsyncMock(return_value=events)
        ),
        patch.object(
            service, "_fetch_existing_tips", AsyncMock(return_value=existing)
        ),
    )


class TestGenerateForCompetitionSportScoping:
    @pytest.mark.asyncio
    async def test_rugby_league_competition_generates_the_four_model_tips(
        self,
    ) -> None:
        seasons = [SeasonRef(id=7, label="2026", is_current=True)]
        events = [
            _event(1, season_id=7, home_score=90, away_score=70),
            _event(2, season_id=7, home_score=44, away_score=44),  # draw
        ]
        db = _fake_session()
        service = LeagueHeuristicsService()
        patches = _patched_fetches(service, "rugby-league", seasons, events, {})
        with patches[0], patches[1], patches[2], patches[3]:
            summary = await service.generate_for_competition(db, competition_id=42)

        rows = [call.args[0] for call in db.add.call_args_list]
        # 1 decided event × 4 models + 1 draw × 4 draw-no-pick rows.
        assert len(rows) == 8
        assert {row.heuristic for row in rows} == set(RUGBY_LEAGUE_MODELS)
        assert "ladder" not in {row.heuristic for row in rows}
        assert all(row.competition_id == 42 for row in rows)
        assert all(row.season_id == 7 for row in rows)
        assert all(
            row.selected_participant_id is None for row in rows if row.event_id == 2
        )
        assert all(
            row.selected_participant_id in (101, 102)
            for row in rows
            if row.event_id == 1
        )
        assert summary["tips_inserted"] == 8
        db.commit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_afl_competition_still_generates_exactly_the_d3_three(
        self,
    ) -> None:
        seasons = [SeasonRef(id=7, label="2026", is_current=True)]
        events = [_event(1, season_id=7, home_score=90, away_score=70)]
        db = _fake_session()
        service = LeagueHeuristicsService()
        patches = _patched_fetches(service, "afl", seasons, events, {})
        with patches[0], patches[1], patches[2], patches[3]:
            summary = await service.generate_for_competition(db, competition_id=42)

        rows = [call.args[0] for call in db.add.call_args_list]
        assert len(rows) == 3
        assert {row.heuristic for row in rows} == set(LEAGUE_HEURISTICS)
        assert summary["tips_inserted"] == 3

    @pytest.mark.asyncio
    async def test_unresolvable_sport_falls_back_to_the_d3_set(self) -> None:
        """No competition row (sport None) → the historical behaviour."""
        seasons = [SeasonRef(id=7, label="2026", is_current=True)]
        events = [_event(1, season_id=7, home_score=90, away_score=70)]
        db = _fake_session()
        service = LeagueHeuristicsService()
        patches = _patched_fetches(service, None, seasons, events, {})
        with patches[0], patches[1], patches[2], patches[3]:
            summary = await service.generate_for_competition(db, competition_id=42)

        rows = [call.args[0] for call in db.add.call_args_list]
        assert {row.heuristic for row in rows} == set(LEAGUE_HEURISTICS)
        assert summary["tips_inserted"] == 3

    @pytest.mark.asyncio
    async def test_persisted_order_follows_the_registry_order(self) -> None:
        """Rugby-league rows persist in the canonical (registry) order."""
        seasons = [SeasonRef(id=7, label="2026", is_current=True)]
        events = [_event(1, season_id=7, home_score=90, away_score=70)]
        db = _fake_session()
        service = LeagueHeuristicsService()
        patches = _patched_fetches(service, "rugby-league", seasons, events, {})
        with patches[0], patches[1], patches[2], patches[3]:
            await service.generate_for_competition(db, competition_id=42)

        rows = [call.args[0] for call in db.add.call_args_list]
        assert [row.heuristic for row in rows] == list(RUGBY_LEAGUE_MODELS)
