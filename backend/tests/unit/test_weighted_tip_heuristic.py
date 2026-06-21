"""Unit tests for the ``weighted_tip`` scikit-learn-backed heuristic.

These are pure-computation tests — no database, Redis, or sklearn import
required.  The pure functions (``build_feature_vector``,
``predict_home_margin``, ``home_margin_to_tip``,
``weighted_tip_predict``, ``weighted_tip_fallback``) plus the
:class:`WeightedTipHeuristic` class form the public contract that
Subtask 3's retrain job will import, so the exact ordering, sign
conventions and clamping behaviour are pinned here.
"""

from unittest.mock import MagicMock

import pytest

from packages.shared.heuristics.weighted_tip import (
    FEATURE_NAMES,
    MODEL_NAMES,
    WeightedTipHeuristic,
    build_feature_vector,
    home_margin_to_tip,
    predict_home_margin,
    weighted_tip_fallback,
    weighted_tip_predict,
)


def _make_game(home_team="Richmond", away_team="Carlton"):
    """Create a mock Game object for testing (matches sibling heuristic tests)."""
    game = MagicMock()
    game.home_team = home_team
    game.away_team = away_team
    return game


# ---------------------------------------------------------------------------
# Feature ordering contract
# ---------------------------------------------------------------------------

class TestFeatureOrdering:
    """The 16-feature ordering is the contract between training and prediction."""

    def test_model_names_are_the_eight_underlying_models(self):
        assert MODEL_NAMES == [
            "elo",
            "form",
            "home_advantage",
            "value",
            "weather_impact",
            "injury_impact",
            "matchup",
            "player_form",
        ]

    def test_feature_names_length(self):
        assert len(FEATURE_NAMES) == 16

    def test_feature_names_are_signed_margin_then_confidence(self):
        expected = []
        for name in MODEL_NAMES:
            expected.append(f"{name}_margin_home")
            expected.append(f"{name}_conf")
        assert FEATURE_NAMES == expected

    def test_feature_names_are_unique(self):
        assert len(set(FEATURE_NAMES)) == len(FEATURE_NAMES)


# ---------------------------------------------------------------------------
# build_feature_vector
# ---------------------------------------------------------------------------

class TestBuildFeatureVector:
    def test_returns_sixteen_values_in_feature_order(self):
        preds = {"elo": ("Richmond", 0.7, 10)}
        vec = build_feature_vector(preds, "Richmond", "Carlton")
        assert len(vec) == 16
        # elo is first: elo_margin_home=+10 (home winner), elo_conf=0.7
        assert vec[0] == pytest.approx(10.0)
        assert vec[1] == pytest.approx(0.7)
        # Remaining features default to 0.0
        for v in vec[2:]:
            assert v == 0.0

    def test_home_winner_positive_signed_margin(self):
        preds = {"elo": ("Richmond", 0.6, 12)}
        vec = build_feature_vector(preds, "Richmond", "Carlton")
        assert vec[0] == pytest.approx(12.0)

    def test_away_winner_negative_signed_margin(self):
        preds = {"elo": ("Carlton", 0.6, 12)}
        vec = build_feature_vector(preds, "Richmond", "Carlton")
        assert vec[0] == pytest.approx(-12.0)

    def test_winner_neither_home_nor_away_is_zero_margin(self):
        # Defensive: a winner value that matches neither team → 0.0 margin.
        preds = {"elo": ("Geelong", 0.6, 12)}
        vec = build_feature_vector(preds, "Richmond", "Carlton")
        assert vec[0] == pytest.approx(0.0)
        # Confidence is still captured.
        assert vec[1] == pytest.approx(0.6)

    def test_missing_model_defaults_to_zero(self):
        vec = build_feature_vector({}, "Richmond", "Carlton")
        assert len(vec) == 16
        for v in vec:
            assert v == 0.0

    def test_multiple_models_fill_correct_positions(self):
        # elo (index 0,1) home; form (index 2,3) away; weather_impact (index 8,9) home.
        preds = {
            "elo": ("Richmond", 0.7, 10),
            "form": ("Carlton", 0.5, 8),
            "weather_impact": ("Richmond", 0.65, 14),
        }
        vec = build_feature_vector(preds, "Richmond", "Carlton")
        assert vec[0] == pytest.approx(10.0)   # elo_margin_home
        assert vec[1] == pytest.approx(0.7)    # elo_conf
        assert vec[2] == pytest.approx(-8.0)   # form_margin_home (away winner)
        assert vec[3] == pytest.approx(0.5)    # form_conf
        # indices 4..7 (home_advantage, value) absent → 0.0
        assert vec[4] == 0.0 and vec[5] == 0.0
        assert vec[6] == 0.0 and vec[7] == 0.0
        assert vec[8] == pytest.approx(14.0)   # weather_impact_margin_home
        assert vec[9] == pytest.approx(0.65)   # weather_impact_conf

    def test_confidence_coerced_to_float(self):
        preds = {"elo": ("Richmond", "0.9", 10)}  # confidence as string
        vec = build_feature_vector(preds, "Richmond", "Carlton")
        assert vec[1] == pytest.approx(0.9)
        assert isinstance(vec[1], float)


# ---------------------------------------------------------------------------
# predict_home_margin
# ---------------------------------------------------------------------------

class TestPredictHomeMargin:
    def test_linear_combination(self):
        features = [1.0] * 16
        coeffs = {name: 0.5 for name in FEATURE_NAMES}
        # intercept 2.0 + 16 * (0.5 * 1.0) = 2.0 + 8.0 = 10.0
        assert predict_home_margin(features, 2.0, coeffs) == pytest.approx(10.0)

    def test_missing_feature_treated_as_zero_coefficient(self):
        features = [3.0] * 16
        # Only elo_margin_home has a coefficient; everything else must be treated as 0.
        coeffs = {"elo_margin_home": 2.0}
        # 5.0 intercept + 2.0 * 3.0 = 11.0
        assert predict_home_margin(features, 5.0, coeffs) == pytest.approx(11.0)

    def test_zero_intercept_zero_coefficients_is_zero(self):
        features = [9.0] * 16
        assert predict_home_margin(features, 0.0, {}) == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# home_margin_to_tip
# ---------------------------------------------------------------------------

class TestHomeMarginToTip:
    def test_positive_margin_picks_home(self):
        winner, conf, margin = home_margin_to_tip(12.0, "Richmond", "Carlton")
        assert winner == "Richmond"

    def test_zero_margin_picks_home(self):
        winner, conf, margin = home_margin_to_tip(0.0, "Richmond", "Carlton")
        assert winner == "Richmond"

    def test_negative_margin_picks_away(self):
        winner, conf, margin = home_margin_to_tip(-12.0, "Richmond", "Carlton")
        assert winner == "Carlton"

    def test_margin_is_at_least_one(self):
        _, _, margin = home_margin_to_tip(0.1, "Richmond", "Carlton")
        assert margin >= 1

    def test_margin_rounds_absolute_value(self):
        _, _, margin = home_margin_to_tip(10.4, "Richmond", "Carlton")
        assert margin == 10
        _, _, margin = home_margin_to_tip(-10.6, "Richmond", "Carlton")
        assert margin == 11

    def test_confidence_clamps_to_lower_bound(self):
        _, conf, _ = home_margin_to_tip(0.0, "Richmond", "Carlton")
        assert conf == pytest.approx(0.50)

    def test_confidence_clamps_to_upper_bound_at_thirty(self):
        _, conf, _ = home_margin_to_tip(30.0, "Richmond", "Carlton")
        assert conf == pytest.approx(0.95)

    def test_confidence_clamps_when_margin_very_large(self):
        _, conf, _ = home_margin_to_tip(1000.0, "Richmond", "Carlton")
        assert conf == pytest.approx(0.95)

    def test_confidence_value_at_ten(self):
        # 0.50 + 10 * 0.015 = 0.65
        _, conf, _ = home_margin_to_tip(10.0, "Richmond", "Carlton")
        assert conf == pytest.approx(0.65)

    def test_confidence_uses_absolute_value(self):
        _, conf_pos, _ = home_margin_to_tip(15.0, "Richmond", "Carlton")
        _, conf_neg, _ = home_margin_to_tip(-15.0, "Richmond", "Carlton")
        assert conf_pos == pytest.approx(conf_neg)


# ---------------------------------------------------------------------------
# weighted_tip_predict (end-to-end compose)
# ---------------------------------------------------------------------------

class TestWeightedTipPredict:
    def test_composes_feature_build_predict_and_map(self):
        preds = {"elo": ("Richmond", 0.7, 10)}
        # A coefficient that turns the elo margin into a positive home margin.
        coeffs = {"elo_margin_home": 1.0}
        winner, conf, margin = weighted_tip_predict(
            1.0, coeffs, preds, "Richmond", "Carlton"
        )
        # y = 1.0 + 1.0 * 10.0 = 11.0 → home wins, margin 11
        assert winner == "Richmond"
        assert margin == 11
        assert 0.50 <= conf <= 0.95

    def test_away_winner_when_predicted_margin_negative(self):
        preds = {"elo": ("Carlton", 0.7, 20)}
        coeffs = {"elo_margin_home": 1.0}
        winner, conf, margin = weighted_tip_predict(
            0.0, coeffs, preds, "Richmond", "Carlton"
        )
        # y = 0 + 1.0 * (-20) = -20 → away (Carlton)
        assert winner == "Carlton"
        assert margin == 20


# ---------------------------------------------------------------------------
# weighted_tip_fallback (majority vote, pre-retrain behaviour)
# ---------------------------------------------------------------------------

class TestWeightedTipFallback:
    def test_majority_vote_picks_home(self):
        preds = {
            "elo": ("Richmond", 0.7, 10),
            "form": ("Richmond", 0.6, 12),
            "value": ("Richmond", 0.55, 8),
            "weather_impact": ("Carlton", 0.5, 5),
        }
        winner, conf, margin = weighted_tip_fallback(preds, "Richmond", "Carlton")
        assert winner == "Richmond"
        assert conf == pytest.approx(0.55)

    def test_majority_vote_picks_away(self):
        preds = {
            "elo": ("Carlton", 0.7, 10),
            "form": ("Carlton", 0.6, 12),
            "value": ("Richmond", 0.55, 8),
        }
        winner, conf, margin = weighted_tip_fallback(preds, "Richmond", "Carlton")
        assert winner == "Carlton"

    def test_tie_breaks_to_home(self):
        # Non-empty tie (1-1) → home.
        preds = {
            "elo": ("Richmond", 0.7, 10),
            "form": ("Carlton", 0.7, 12),
        }
        winner, conf, margin = weighted_tip_fallback(preds, "Richmond", "Carlton")
        assert winner == "Richmond"
        assert conf == pytest.approx(0.55)

    def test_margin_is_mean_of_absolute_margins(self):
        preds = {
            "elo": ("Richmond", 0.7, 10),
            "form": ("Richmond", 0.6, 12),
            "weather_impact": ("Carlton", 0.5, 5),
        }
        # mean(|10|, |12|, |5|) = 27/3 = 9.0 → round 9 → max(1,9) = 9
        _, _, margin = weighted_tip_fallback(preds, "Richmond", "Carlton")
        assert margin == 9

    def test_empty_predictions_returns_away_cold_start(self):
        winner, conf, margin = weighted_tip_fallback({}, "Richmond", "Carlton")
        assert winner == "Carlton"
        assert conf == pytest.approx(0.55)
        assert margin == 6


# ---------------------------------------------------------------------------
# WeightedTipHeuristic class
# ---------------------------------------------------------------------------

class TestWeightedTipHeuristic:
    def setup_method(self):
        self.heuristic = WeightedTipHeuristic(models=[])

    def test_get_name(self):
        assert self.heuristic.get_name() == "weighted_tip"

    @pytest.mark.asyncio
    async def test_apply_without_coefficients_uses_fallback(self):
        game = _make_game()
        preds = {
            "elo": ("Richmond", 0.7, 10),
            "form": ("Richmond", 0.6, 12),
            "value": ("Carlton", 0.5, 5),
        }
        winner, conf, margin = await self.heuristic.apply(game, preds)
        # Majority vote → Richmond; confidence is the fallback 0.55.
        assert winner == "Richmond"
        assert conf == pytest.approx(0.55)

    @pytest.mark.asyncio
    async def test_apply_with_coefficients_uses_linear_path(self):
        game = _make_game()
        preds = {"elo": ("Richmond", 0.7, 20)}
        coeffs = {"elo_margin_home": 1.0}
        self.heuristic.set_coefficients(1.0, coeffs)
        winner, conf, margin = await self.heuristic.apply(game, preds)
        # y = 1.0 + 1.0*20 = 21 → home, margin 21
        assert winner == "Richmond"
        assert margin == 21

    @pytest.mark.asyncio
    async def test_set_coefficients_then_apply_reflects_change(self):
        game = _make_game()
        preds = {"elo": ("Carlton", 0.7, 30)}
        # First, no coefficients → fallback majority → away (Carlton wins vote).
        winner_before, _, margin_before = await self.heuristic.apply(game, preds)
        assert winner_before == "Carlton"

        # Now inject coefficients that strongly favour home margin.
        self.heuristic.set_coefficients(0.0, {"elo_margin_home": 1.0})
        winner_after, _, margin_after = await self.heuristic.apply(game, preds)
        # y = 0 + 1.0 * (-30) = -30 → still Carlton (away winner) here, but the
        # margin now reflects the linear path (round(|-30|) = 30).
        assert winner_after == "Carlton"
        assert margin_after == 30

    @pytest.mark.asyncio
    async def test_clear_coefficients_reverts_to_fallback(self):
        game = _make_game()
        preds = {"elo": ("Richmond", 0.7, 20)}
        self.heuristic.set_coefficients(1.0, {"elo_margin_home": 1.0})
        # Linear path: y = 1 + 1*20 = 21 → margin 21
        _, _, margin_linear = await self.heuristic.apply(game, preds)
        assert margin_linear == 21

        self.heuristic.clear_coefficients()
        # Fallback: margin = round(mean(|20|)) = 20, confidence 0.55
        winner, conf, margin = await self.heuristic.apply(game, preds)
        assert winner == "Richmond"
        assert conf == pytest.approx(0.55)
        assert margin == 20

    @pytest.mark.asyncio
    async def test_empty_predictions_returns_away_cold_start(self):
        game = _make_game()
        winner, conf, margin = await self.heuristic.apply(game, {})
        assert winner == "Carlton"
        assert conf == pytest.approx(0.55)
        assert margin == 6
