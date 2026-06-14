"""Tests for the database seed data script."""

import os
import sys

import pytest

# Ensure backend is on sys.path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

import random

from scripts.seed_data import (
    AFL_TEAMS,
    GAMES_PER_ROUND,
    HEURISTICS,
    MODEL_NAMES,
    TEAM_VENUES,
    _generate_confidence,
    _generate_deterministic_slug,
    _generate_elo_rating,
    _generate_explanation,
    _generate_game_datetime,
    _generate_margin,
    _generate_model_confidence,
    _generate_model_margin,
    _generate_realistic_score,
    _generate_round_fixtures,
    seed_backtest_results,
    seed_elo_cache,
    seed_games,
    seed_generation_progress,
    seed_job_executions,
    seed_match_analyses,
    seed_model_predictions,
    seed_tips,
)

# ---------------------------------------------------------------------------
# Fixture / helper
# ---------------------------------------------------------------------------


@pytest.fixture
def rng():
    """Deterministic random generator."""
    return random.Random(42)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


class TestConstants:
    """Verify seed data constants are correct."""

    def test_afl_teams_count(self):
        assert len(AFL_TEAMS) == 18

    def test_afl_teams_sorted(self):
        assert AFL_TEAMS == sorted(AFL_TEAMS)

    def test_afl_teams_known(self):
        expected = {
            "Adelaide", "Brisbane", "Bulldogs", "Carlton", "Collingwood",
            "Essendon", "Fremantle", "Geelong", "Giants", "GoldCoast",
            "Hawthorn", "Melbourne", "NorthMelbourne", "PortAdelaide",
            "Richmond", "StKilda", "Sydney", "WestCoast",
        }
        assert set(AFL_TEAMS) == expected

    def test_venues_for_all_teams(self):
        for team in AFL_TEAMS:
            assert team in TEAM_VENUES, f"Missing venue for {team}"

    def test_heuristics(self):
        assert set(HEURISTICS) == {"best_bet", "yolo", "high_risk_high_reward"}

    def test_model_names(self):
        assert set(MODEL_NAMES) == {"elo", "form", "home_advantage", "value"}


# ---------------------------------------------------------------------------
# Slug generation
# ---------------------------------------------------------------------------


class TestSlugGeneration:
    """Verify deterministic slug generation."""

    def test_deterministic(self):
        slug1 = _generate_deterministic_slug(2025, 1, 0)
        slug2 = _generate_deterministic_slug(2025, 1, 0)
        assert slug1 == slug2

    def test_unique_per_game(self):
        slugs = set()
        for season in [2025, 2026]:
            for rnd in range(1, 25):
                for gi in range(9):
                    slug = _generate_deterministic_slug(season, rnd, gi)
                    slugs.add(slug)
        # 2 seasons × 24 rounds × 9 games = 432 unique slugs
        assert len(slugs) == 432

    def test_length(self):
        slug = _generate_deterministic_slug(2025, 1, 0)
        assert len(slug) == 10

    def test_alphanumeric(self):
        slug = _generate_deterministic_slug(2025, 1, 0)
        assert slug.isalnum()


# ---------------------------------------------------------------------------
# Fixture generation
# ---------------------------------------------------------------------------


class TestRoundFixtures:
    """Verify round fixture generation."""

    def test_correct_count(self):
        fixtures = _generate_round_fixtures(2025, 1)
        assert len(fixtures) == GAMES_PER_ROUND

    def test_valid_teams(self):
        fixtures = _generate_round_fixtures(2025, 1)
        for home, away, venue in fixtures:
            assert home in AFL_TEAMS
            assert away in AFL_TEAMS
            assert home != away

    def test_valid_venue(self):
        fixtures = _generate_round_fixtures(2025, 1)
        for home, away, venue in fixtures:
            assert venue == TEAM_VENUES[home]

    def test_different_matchups_per_round(self):
        """Different rounds should produce different matchups."""
        f1 = _generate_round_fixtures(2025, 1)
        f2 = _generate_round_fixtures(2025, 2)
        # Matchups should differ between rounds
        assert set((h, a) for h, a, _ in f1) != set((h, a) for h, a, _ in f2)

    def test_all_teams_appear_in_round(self):
        """All 18 teams should play in each round."""
        fixtures = _generate_round_fixtures(2025, 1)
        teams_in_round = set()
        for home, away, _ in fixtures:
            teams_in_round.add(home)
            teams_in_round.add(away)
        assert teams_in_round == set(AFL_TEAMS)


# ---------------------------------------------------------------------------
# Datetime generation
# ---------------------------------------------------------------------------


class TestGameDatetime:
    """Verify game datetime generation."""

    def test_correct_season_year(self):
        dt = _generate_game_datetime(2025, 1, 0)
        assert dt.year == 2025

    def test_utc_timezone(self):
        dt = _generate_game_datetime(2025, 1, 0)
        assert dt.tzinfo is not None

    def test_rounds_progress(self):
        dt_r1 = _generate_game_datetime(2025, 1, 0)
        dt_r5 = _generate_game_datetime(2025, 5, 0)
        assert dt_r5 > dt_r1

    def test_games_spread_across_week(self):
        """Games within a round should have different days/times."""
        dates = [_generate_game_datetime(2025, 1, i) for i in range(4)]
        # At least 2 different days
        days = set(d.date() for d in dates)
        assert len(days) >= 2


# ---------------------------------------------------------------------------
# Score generation
# ---------------------------------------------------------------------------


class TestScoreGeneration:
    """Verify realistic AFL score generation."""

    def test_within_range(self, rng):
        for _ in range(100):
            home, away = _generate_realistic_score(rng)
            assert 40 <= home <= 150
            assert 40 <= away <= 150

    def test_home_advantage_bias(self, rng):
        """Home team should score more on average."""
        home_total = 0
        away_total = 0
        n = 1000
        for _ in range(n):
            h, a = _generate_realistic_score(rng, home_advantage=True)
            home_total += h
            away_total += a
        avg_home = home_total / n
        avg_away = away_total / n
        assert avg_home > avg_away

    def test_no_home_advantage(self, rng):
        home, away = _generate_realistic_score(rng, home_advantage=False)
        assert 40 <= home <= 150
        assert 40 <= away <= 150


# ---------------------------------------------------------------------------
# Confidence generation
# ---------------------------------------------------------------------------


class TestConfidenceGeneration:
    """Verify confidence ranges per heuristic."""

    @pytest.mark.parametrize("heuristic", HEURISTICS)
    def test_confidence_range(self, rng, heuristic):
        for _ in range(100):
            conf = _generate_confidence(rng, heuristic)
            assert 0.0 <= conf <= 1.0

    def test_best_bet_highest_confidence(self, rng):
        """best_bet should have higher confidence than yolo on average."""
        bb_confs = [_generate_confidence(rng, "best_bet") for _ in range(100)]
        yolo_confs = [_generate_confidence(rng, "yolo") for _ in range(100)]
        assert sum(bb_confs) / len(bb_confs) > sum(yolo_confs) / len(yolo_confs)


# ---------------------------------------------------------------------------
# Margin generation
# ---------------------------------------------------------------------------


class TestMarginGeneration:
    """Verify margin ranges per heuristic."""

    @pytest.mark.parametrize("heuristic", HEURISTICS)
    def test_margin_positive(self, rng, heuristic):
        for _ in range(100):
            margin = _generate_margin(rng, heuristic)
            assert margin > 0

    def test_yolo_has_wider_range(self, rng):
        """YOLO margin max should be >= best_bet margin max."""
        yolo_margins = [_generate_margin(rng, "yolo") for _ in range(200)]
        bb_margins = [_generate_margin(rng, "best_bet") for _ in range(200)]
        assert max(yolo_margins) >= max(bb_margins)


# ---------------------------------------------------------------------------
# Model helpers
# ---------------------------------------------------------------------------


class TestModelHelpers:
    """Verify model confidence and margin generation."""

    @pytest.mark.parametrize("model", MODEL_NAMES)
    def test_model_confidence_range(self, rng, model):
        for _ in range(100):
            conf = _generate_model_confidence(rng, model)
            assert 0.0 <= conf <= 1.0

    @pytest.mark.parametrize("model", MODEL_NAMES)
    def test_model_margin_positive(self, rng, model):
        for _ in range(100):
            margin = _generate_model_margin(rng, model)
            assert margin > 0


# ---------------------------------------------------------------------------
# Elo rating generation
# ---------------------------------------------------------------------------


class TestEloRating:
    """Verify Elo rating generation."""

    def test_realistic_range(self, rng):
        for team in AFL_TEAMS:
            rating = _generate_elo_rating(rng, team)
            assert 1300 <= rating <= 1750

    def test_deterministic_for_same_team(self):
        """Same team should get same base rating (with same seed)."""
        rng1 = random.Random(42)
        rng2 = random.Random(42)
        for team in AFL_TEAMS:
            assert _generate_elo_rating(rng1, team) == _generate_elo_rating(rng2, team)

    def test_top_teams_higher(self, rng):
        """Known strong teams should have higher ratings."""
        coll_rating = _generate_elo_rating(rng, "Collingwood")
        wc_rating = _generate_elo_rating(rng, "WestCoast")
        assert coll_rating > wc_rating


# ---------------------------------------------------------------------------
# Explanation generation
# ---------------------------------------------------------------------------


class TestExplanation:
    """Verify tip explanation generation."""

    @pytest.mark.parametrize("heuristic", HEURISTICS)
    def test_explanation_not_empty(self, rng, heuristic):
        explanation = _generate_explanation(rng, "Brisbane", "Carlton", 15, heuristic)
        assert len(explanation) > 0

    def test_explanation_contains_winner(self, rng):
        winner = "Sydney"
        explanation = _generate_explanation(rng, winner, "Hawthorn", 20, "best_bet")
        assert winner in explanation


# ---------------------------------------------------------------------------
# Seed functions — Games
# ---------------------------------------------------------------------------


class TestSeedGames:
    """Verify game seeding."""

    def test_full_season_count(self, rng):
        games = seed_games(rng, season=2025, rounds=24, completed_rounds=24)
        assert len(games) == 24 * GAMES_PER_ROUND

    def test_partial_season(self, rng):
        games = seed_games(rng, season=2025, rounds=24, completed_rounds=10)
        completed = [g for g in games if g.completed]
        incomplete = [g for g in games if not g.completed]
        assert len(completed) == 10 * GAMES_PER_ROUND
        assert len(incomplete) == 14 * GAMES_PER_ROUND

    def test_completed_games_have_scores(self, rng):
        games = seed_games(rng, season=2025, rounds=5, completed_rounds=5)
        for game in games:
            assert game.home_score is not None
            assert game.away_score is not None

    def test_incomplete_games_no_scores(self, rng):
        games = seed_games(rng, season=2025, rounds=5, completed_rounds=3)
        incomplete = [g for g in games if not g.completed]
        for game in incomplete:
            assert game.home_score is None
            assert game.away_score is None

    def test_unique_slugs(self, rng):
        games = seed_games(rng, season=2025, rounds=24)
        slugs = [g.slug for g in games]
        assert len(slugs) == len(set(slugs))

    def test_unique_ids(self, rng):
        games = seed_games(rng, season=2025, rounds=24)
        ids = [g.id for g in games]
        assert len(ids) == len(set(ids))

    def test_unique_squiggle_ids(self, rng):
        games = seed_games(rng, season=2025, rounds=24)
        sq_ids = [g.squiggle_id for g in games]
        assert len(sq_ids) == len(set(sq_ids))

    def test_completed_flags_set(self, rng):
        games = seed_games(rng, season=2025, rounds=5, completed_rounds=3)
        for game in games:
            if game.completed:
                assert game.predictions_generated is True
                assert game.tips_generated is True
                assert game.last_synced_at is not None
            else:
                assert game.predictions_generated is False
                assert game.tips_generated is False
                assert game.last_synced_at is None

    def test_season_attribute(self, rng):
        games = seed_games(rng, season=2026, rounds=3)
        for game in games:
            assert game.season == 2026

    def test_round_ids_range(self, rng):
        games = seed_games(rng, season=2025, rounds=10)
        round_ids = {g.round_id for g in games}
        assert round_ids == set(range(1, 11))


# ---------------------------------------------------------------------------
# Seed functions — Model Predictions
# ---------------------------------------------------------------------------


class TestSeedModelPredictions:
    """Verify model prediction seeding."""

    def test_count(self, rng):
        games = seed_games(rng, season=2025, rounds=3)
        predictions = seed_model_predictions(rng, games)
        # 3 rounds × 9 games × 4 models = 108
        assert len(predictions) == 3 * 9 * 4

    def test_model_names_valid(self, rng):
        games = seed_games(rng, season=2025, rounds=2)
        predictions = seed_model_predictions(rng, games)
        for pred in predictions:
            assert pred.model_name in MODEL_NAMES

    def test_winner_is_team_in_game(self, rng):
        games = seed_games(rng, season=2025, rounds=2)
        game_lookup = {g.id: g for g in games}
        predictions = seed_model_predictions(rng, games)
        for pred in predictions:
            game = game_lookup[pred.game_id]
            assert pred.winner in (game.home_team, game.away_team)

    def test_unique_game_model_pairs(self, rng):
        games = seed_games(rng, season=2025, rounds=2)
        predictions = seed_model_predictions(rng, games)
        pairs = [(p.game_id, p.model_name) for p in predictions]
        assert len(pairs) == len(set(pairs))

    def test_confidence_range(self, rng):
        games = seed_games(rng, season=2025, rounds=2)
        predictions = seed_model_predictions(rng, games)
        for pred in predictions:
            assert 0.0 <= pred.confidence <= 1.0


# ---------------------------------------------------------------------------
# Seed functions — Tips
# ---------------------------------------------------------------------------


class TestSeedTips:
    """Verify tip seeding."""

    def test_count(self, rng):
        games = seed_games(rng, season=2025, rounds=3)
        tips = seed_tips(rng, games)
        # 3 rounds × 9 games × 3 heuristics = 81
        assert len(tips) == 3 * 9 * 3

    def test_heuristics_valid(self, rng):
        games = seed_games(rng, season=2025, rounds=2)
        tips = seed_tips(rng, games)
        for tip in tips:
            assert tip.heuristic in HEURISTICS

    def test_selected_team_is_in_game(self, rng):
        games = seed_games(rng, season=2025, rounds=2)
        game_lookup = {g.id: g for g in games}
        tips = seed_tips(rng, games)
        for tip in tips:
            game = game_lookup[tip.game_id]
            assert tip.selected_team in (game.home_team, game.away_team)

    def test_unique_game_heuristic_pairs(self, rng):
        games = seed_games(rng, season=2025, rounds=2)
        tips = seed_tips(rng, games)
        pairs = [(t.game_id, t.heuristic) for t in tips]
        assert len(pairs) == len(set(pairs))

    def test_explanation_not_empty(self, rng):
        games = seed_games(rng, season=2025, rounds=2)
        tips = seed_tips(rng, games)
        for tip in tips:
            assert len(tip.explanation) > 0

    def test_confidence_range(self, rng):
        games = seed_games(rng, season=2025, rounds=2)
        tips = seed_tips(rng, games)
        for tip in tips:
            assert 0.0 <= tip.confidence <= 1.0


# ---------------------------------------------------------------------------
# Seed functions — Elo Cache
# ---------------------------------------------------------------------------


class TestSeedEloCache:
    """Verify Elo cache seeding."""

    def test_count(self, rng):
        entries = seed_elo_cache(rng, season=2025)
        assert len(entries) == 18

    def test_all_teams_covered(self, rng):
        entries = seed_elo_cache(rng, season=2025)
        teams = {e.team_name for e in entries}
        assert teams == set(AFL_TEAMS)

    def test_season_set(self, rng):
        entries = seed_elo_cache(rng, season=2026)
        for entry in entries:
            assert entry.season == 2026

    def test_ratings_realistic(self, rng):
        entries = seed_elo_cache(rng, season=2025)
        for entry in entries:
            assert 1300 <= entry.rating <= 1750

    def test_games_played_realistic(self, rng):
        entries = seed_elo_cache(rng, season=2025)
        for entry in entries:
            assert 18 <= entry.games_played <= 24

    def test_unique_team_names(self, rng):
        entries = seed_elo_cache(rng, season=2025)
        names = [e.team_name for e in entries]
        assert len(names) == len(set(names))


# ---------------------------------------------------------------------------
# Seed functions — Backtest Results
# ---------------------------------------------------------------------------


class TestSeedBacktestResults:
    """Verify backtest result seeding."""

    def test_count(self, rng):
        results = seed_backtest_results(rng, season=2025, rounds=24)
        # 24 rounds × 3 heuristics = 72
        assert len(results) == 24 * 3

    def test_heuristics_valid(self, rng):
        results = seed_backtest_results(rng, season=2025, rounds=5)
        for r in results:
            assert r.heuristic in HEURISTICS

    def test_accuracy_between_0_and_1(self, rng):
        results = seed_backtest_results(rng, season=2025, rounds=5)
        for r in results:
            assert 0.0 <= r.accuracy <= 1.0

    def test_tips_correct_leq_tips_made(self, rng):
        results = seed_backtest_results(rng, season=2025, rounds=5)
        for r in results:
            assert r.tips_correct <= r.tips_made

    def test_season_set(self, rng):
        results = seed_backtest_results(rng, season=2025, rounds=5)
        for r in results:
            assert r.season == 2025

    def test_unique_season_round_heuristic(self, rng):
        results = seed_backtest_results(rng, season=2025, rounds=10)
        keys = [(r.season, r.round_id, r.heuristic) for r in results]
        assert len(keys) == len(set(keys))


# ---------------------------------------------------------------------------
# Seed functions — Match Analyses
# ---------------------------------------------------------------------------


class TestSeedMatchAnalyses:
    """Verify match analysis seeding."""

    def test_count(self, rng):
        games = seed_games(rng, season=2025, rounds=3)
        analyses = seed_match_analyses(rng, games)
        assert len(analyses) == len(games)

    def test_analysis_text_not_empty(self, rng):
        games = seed_games(rng, season=2025, rounds=2)
        analyses = seed_match_analyses(rng, games)
        for a in analyses:
            assert len(a.analysis_text) > 0

    def test_game_id_mapping(self, rng):
        games = seed_games(rng, season=2025, rounds=2)
        analyses = seed_match_analyses(rng, games)
        game_ids = {g.id for g in games}
        analysis_game_ids = {a.game_id for a in analyses}
        assert analysis_game_ids == game_ids

    def test_analysis_mentions_teams(self, rng):
        games = seed_games(rng, season=2025, rounds=1)
        game_lookup = {g.id: g for g in games}
        analyses = seed_match_analyses(rng, games)
        for a in analyses:
            game = game_lookup[a.game_id]
            # Analysis should mention at least one of the teams or venue
            mentions_team = game.home_team in a.analysis_text or game.away_team in a.analysis_text
            mentions_venue = game.venue in a.analysis_text
            assert mentions_team or mentions_venue


# ---------------------------------------------------------------------------
# Seed functions — Generation Progress
# ---------------------------------------------------------------------------


class TestSeedGenerationProgress:
    """Verify generation progress seeding."""

    def test_count(self):
        entries = seed_generation_progress([2025, 2026])
        # 2 seasons × 3 operations = 6
        assert len(entries) == 2 * 3

    def test_single_season(self):
        entries = seed_generation_progress([2025])
        assert len(entries) == 3

    def test_completed_items_for_completed_status(self):
        entries = seed_generation_progress([2025])
        for entry in entries:
            if entry.status == "completed":
                assert entry.completed_items == entry.total_items
                assert entry.completed_at is not None

    def test_valid_operation_types(self):
        entries = seed_generation_progress([2025])
        valid_ops = {"historical_generation", "season_sync", "tip_generation"}
        for entry in entries:
            assert entry.operation_type in valid_ops


# ---------------------------------------------------------------------------
# Seed functions — Job Executions
# ---------------------------------------------------------------------------


class TestSeedJobExecutions:
    """Verify job execution seeding."""

    def test_count(self):
        executions = seed_job_executions()
        assert len(executions) > 0

    def test_valid_status(self):
        executions = seed_job_executions()
        for ex in executions:
            assert ex.status in ("completed", "failed", "running")

    def test_duration_positive(self):
        executions = seed_job_executions()
        for ex in executions:
            assert ex.duration_seconds > 0

    def test_completed_at_after_started_at(self):
        executions = seed_job_executions()
        for ex in executions:
            assert ex.completed_at >= ex.started_at

    def test_items_processed_non_negative(self):
        executions = seed_job_executions()
        for ex in executions:
            assert ex.items_processed >= 0
            assert ex.items_failed >= 0


# ---------------------------------------------------------------------------
# Integration — Cross-function relationships
# ---------------------------------------------------------------------------


class TestCrossFunctionRelationships:
    """Verify that seeded data maintains referential integrity."""

    def test_tips_reference_valid_games(self, rng):
        games = seed_games(rng, season=2025, rounds=3)
        tips = seed_tips(rng, games)
        game_ids = {g.id for g in games}
        for tip in tips:
            assert tip.game_id in game_ids

    def test_predictions_reference_valid_games(self, rng):
        games = seed_games(rng, season=2025, rounds=3)
        predictions = seed_model_predictions(rng, games)
        game_ids = {g.id for g in games}
        for pred in predictions:
            assert pred.game_id in game_ids

    def test_analyses_reference_valid_games(self, rng):
        games = seed_games(rng, season=2025, rounds=3)
        analyses = seed_match_analyses(rng, games)
        game_ids = {g.id for g in games}
        for a in analyses:
            assert a.game_id in game_ids

    def test_two_seasons_no_id_collision(self, rng):
        """Games across two seasons should have unique IDs."""
        games_2025 = seed_games(rng, season=2025, rounds=5, squiggle_id_start=10000)
        games_2026 = seed_games(rng, season=2026, rounds=5, squiggle_id_start=11000)
        all_ids = [g.id for g in games_2025 + games_2026]
        assert len(all_ids) == len(set(all_ids))

        all_sq_ids = [g.squiggle_id for g in games_2025 + games_2026]
        assert len(all_sq_ids) == len(set(all_sq_ids))

        all_slugs = [g.slug for g in games_2025 + games_2026]
        assert len(all_slugs) == len(set(all_slugs))


# ---------------------------------------------------------------------------
# Reproducibility
# ---------------------------------------------------------------------------


class TestReproducibility:
    """Verify that seeding with the same seed produces identical data."""

    def test_games_reproducible(self):
        rng1 = random.Random(42)
        rng2 = random.Random(42)
        games1 = seed_games(rng1, season=2025, rounds=3)
        games2 = seed_games(rng2, season=2025, rounds=3)

        for g1, g2 in zip(games1, games2):
            assert g1.id == g2.id
            assert g1.slug == g2.slug
            assert g1.home_team == g2.home_team
            assert g1.away_team == g2.away_team
            assert g1.home_score == g2.home_score
            assert g1.away_score == g2.away_score

    def test_tips_reproducible(self):
        rng1 = random.Random(42)
        rng2 = random.Random(42)
        games1 = seed_games(rng1, season=2025, rounds=2)
        games2 = seed_games(rng2, season=2025, rounds=2)
        tips1 = seed_tips(rng1, games1)
        tips2 = seed_tips(rng2, games2)

        for t1, t2 in zip(tips1, tips2):
            assert t1.selected_team == t2.selected_team
            assert t1.margin == t2.margin
            assert t1.confidence == t2.confidence
            assert t1.heuristic == t2.heuristic

    def test_predictions_reproducible(self):
        rng1 = random.Random(42)
        rng2 = random.Random(42)
        games1 = seed_games(rng1, season=2025, rounds=2)
        games2 = seed_games(rng2, season=2025, rounds=2)
        preds1 = seed_model_predictions(rng1, games1)
        preds2 = seed_model_predictions(rng2, games2)

        for p1, p2 in zip(preds1, preds2):
            assert p1.winner == p2.winner
            assert p1.confidence == p2.confidence
            assert p1.margin == p2.margin
