"""scikit-learn-backed ``weighted_tip`` heuristic.

This module replaces the legacy hand-tuned ``high_risk_high_reward``
heuristic (Layer 2).  Instead of ad-hoc vote tallying, it learns a
multiple linear regression over the eight underlying ML models'
predictions and predicts the **home-team-signed margin**.

The public surface here is intentionally pure and side-effect-free so
that it can be reused by two callers:

1. :class:`WeightedTipHeuristic` — the runtime heuristic applied by the
   :class:`~packages.shared.orchestrator.ModelOrchestrator` during tip
   generation.
2. The weekly retrain job (Subtask 3) — which fits the
   :class:`sklearn.linear_model.LinearRegression`, persists the
   intercept + coefficients via
   :mod:`packages.shared.crud.model_versions`, and re-uses
   :func:`build_feature_vector` to build the training ``X`` matrix.

The exact ordering of :data:`FEATURE_NAMES` is the **contract** between
training and prediction — both sides must build vectors in this order.
"""

from __future__ import annotations

import statistics
from typing import Dict, List, Mapping, Tuple

from ..models import Game
from .base import BaseHeuristic

# ---------------------------------------------------------------------------
# Canonical feature ordering (the training/prediction contract)
# ---------------------------------------------------------------------------

#: The eight underlying ML models, in fixed order.  ``get_name()`` of each
#: model in :mod:`packages.shared.models_ml` matches these strings.
MODEL_NAMES: List[str] = [
    "elo",
    "form",
    "home_advantage",
    "value",
    "weather_impact",
    "injury_impact",
    "matchup",
    "player_form",
]

#: The 16-length ordered feature list.  For each model we emit the signed
#: margin toward the home team first, then that model's confidence.
#: This is the exact order training and prediction must agree on.
FEATURE_NAMES: List[str] = []
for _n in MODEL_NAMES:
    FEATURE_NAMES.append(f"{_n}_margin_home")  # signed margin toward HOME team
    FEATURE_NAMES.append(f"{_n}_conf")  # that model's confidence 0..1


# A prediction tuple is ``(winner, confidence, margin)``.
Prediction = Tuple[str, float, int]


# ---------------------------------------------------------------------------
# A2. build_feature_vector
# ---------------------------------------------------------------------------

def build_feature_vector(
    model_predictions: Mapping[str, Prediction],
    home_team: str,
    away_team: str,
) -> List[float]:
    """Build the 16-length ordered feature vector for one game.

    For each model in :data:`MODEL_NAMES`, look up its prediction tuple.
    If present the signed margin points toward ``home_team`` (positive
    when the model picked the home team, negative for the away team, and
    ``0.0`` when the model's winner matches neither).  Missing models
    contribute ``0.0`` for both their margin and confidence features.

    Pure and side-effect-free.
    """
    features: List[float] = []
    for name in MODEL_NAMES:
        entry = model_predictions.get(name)
        if entry is None:
            features.append(0.0)  # margin
            features.append(0.0)  # confidence
            continue
        winner, confidence, margin = entry
        if winner == home_team:
            signed_margin = float(margin)
        elif winner == away_team:
            signed_margin = -float(margin)
        else:
            signed_margin = 0.0
        features.append(signed_margin)
        features.append(float(confidence))
    return features


# ---------------------------------------------------------------------------
# A3. predict_home_margin
# ---------------------------------------------------------------------------

def predict_home_margin(
    features: List[float],
    intercept: float,
    coefficients: Mapping[str, float],
) -> float:
    """Linear combination: ``intercept + sum(coef[name] * value)``.

    ``coefficients`` is keyed by :data:`FEATURE_NAMES`.  A feature with no
    coefficient entry is treated as coefficient ``0.0``.  Pure function.
    """
    total = float(intercept)
    for name, value in zip(FEATURE_NAMES, features):
        total += float(coefficients.get(name, 0.0)) * float(value)
    return total


# ---------------------------------------------------------------------------
# A4. home_margin_to_tip
# ---------------------------------------------------------------------------

def home_margin_to_tip(
    y_pred: float,
    home_team: str,
    away_team: str,
) -> Prediction:
    """Map a predicted home-team-signed margin to a ``(winner, conf, margin)`` tip.

    * ``winner = home_team`` when ``y_pred >= 0`` else ``away_team``
    * ``margin = max(1, round(abs(y_pred)))``
    * ``confidence = clamp(0.50 + abs(y_pred) * 0.015, 0.50, 0.95)`` — so
      ``|y_pred|=10`` gives ``0.65`` and ``|y_pred|>=30`` saturates at
      ``0.95``.

    Deterministic and pure.
    """
    winner = home_team if y_pred >= 0 else away_team
    margin = max(1, int(round(abs(y_pred))))
    confidence = round(min(0.95, max(0.50, 0.50 + abs(y_pred) * 0.015)), 3)
    return winner, confidence, margin


# ---------------------------------------------------------------------------
# A5. weighted_tip_predict (compose)
# ---------------------------------------------------------------------------

def weighted_tip_predict(
    intercept: float,
    coefficients: Mapping[str, float],
    model_predictions: Mapping[str, Prediction],
    home_team: str,
    away_team: str,
) -> Prediction:
    """Convenience pure function composing A2 → A3 → A4."""
    features = build_feature_vector(model_predictions, home_team, away_team)
    y_pred = predict_home_margin(features, intercept, coefficients)
    return home_margin_to_tip(y_pred, home_team, away_team)


# ---------------------------------------------------------------------------
# A6. weighted_tip_fallback (pre-retrain graceful behaviour)
# ---------------------------------------------------------------------------

def weighted_tip_fallback(
    model_predictions: Mapping[str, Prediction],
    home_team: str,
    away_team: str,
) -> Prediction:
    """Majority-vote fallback used before the first weekly retrain runs.

    Tally votes across ``model_predictions`` (home vs away winner); the
    team with the most votes wins, with ties broken toward the home team.
    Confidence is fixed at ``0.55`` and the margin is the rounded mean of
    the absolute prediction margins (or ``6`` when there are none).

    An empty ``model_predictions`` short-circuits to the cold-start tip
    ``(away_team, 0.55, 6)`` so callers always get a sane answer.
    """
    if not model_predictions:
        return away_team, 0.55, 6

    home_votes = 0
    away_votes = 0
    margins: List[int] = []
    for winner, _confidence, margin in model_predictions.values():
        if winner == home_team:
            home_votes += 1
        elif winner == away_team:
            away_votes += 1
        margins.append(abs(margin))

    winner = home_team if home_votes >= away_votes else away_team
    confidence = 0.55
    if margins:
        margin = max(1, int(round(statistics.fmean(margins))))
    else:
        margin = 6
    return winner, confidence, margin


# ---------------------------------------------------------------------------
# A7. WeightedTipHeuristic
# ---------------------------------------------------------------------------

class WeightedTipHeuristic(BaseHeuristic):
    """Learned-weight combiner over the eight underlying ML models.

    When trained coefficients are available (injected from the
    orchestrator via :meth:`set_coefficients`) the heuristic predicts the
    home-team-signed margin with :func:`weighted_tip_predict`.  Otherwise
    it falls back to the majority-vote :func:`weighted_tip_fallback`, so
    tip generation always works even before the first weekly retrain.

    Note: the ``apply`` signature intentionally takes only
    ``(game, model_predictions)`` — coefficient loading happens in the
    orchestrator (which owns the db session) and is pushed in here.
    """

    def __init__(self, models):
        self.models = models
        self._intercept: float | None = None
        self._coefficients: Dict[str, float] | None = None

    def get_name(self) -> str:
        return "weighted_tip"

    def set_coefficients(self, intercept: float, coefficients: Mapping[str, float]) -> None:
        """Activate the learned-linear path with the given weights."""
        self._intercept = float(intercept)
        self._coefficients = dict(coefficients)

    def clear_coefficients(self) -> None:
        """Revert to the majority-vote fallback (e.g. when no model is active)."""
        self._intercept = None
        self._coefficients = None

    async def apply(
        self, game: Game, model_predictions: Dict[str, Prediction]
    ) -> Prediction:
        """Apply the Weighted Tip heuristic to model predictions."""
        home_team = game.home_team
        away_team = game.away_team

        if self._coefficients is not None and self._intercept is not None:
            return weighted_tip_predict(
                self._intercept,
                self._coefficients,
                model_predictions,
                home_team,
                away_team,
            )

        # No trained model yet — majority-vote fallback.  Guard the empty
        # cold-start case explicitly to match the old behaviour.
        if not model_predictions:
            return away_team, 0.55, 6

        return weighted_tip_fallback(model_predictions, home_team, away_team)
