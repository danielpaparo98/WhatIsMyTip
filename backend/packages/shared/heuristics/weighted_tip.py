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
:data:`FEATURE_NAMES` is derived via :func:`feature_names_for` so any
model list yields a matching name list; :func:`predict_home_margin` takes
an optional ``feature_names`` argument and raises :class:`ValueError` on
a length mismatch, so a vector and its names can never silently drift
apart (``zip`` would otherwise truncate and mis-weight the prediction).
"""

from __future__ import annotations

import statistics
from typing import Dict, List, Mapping

from ..models import Game
from ..models_ml.prediction import Prediction
from ..models_ml.registry import build_default_registry
from .base import BaseHeuristic

# ---------------------------------------------------------------------------
# Canonical feature ordering (the training/prediction contract)
# ---------------------------------------------------------------------------

#: The eight underlying ML models, in fixed order.  P2-3: derived from
#: the model registry — the single source of truth — instead of a
#: hardcoded list.  The resulting order is identical (it IS the
#: weighted-tip feature contract for the AFL bootstrap set).
MODEL_NAMES: List[str] = build_default_registry().names()


def feature_names_for(model_names: List[str]) -> List[str]:
    """Interleave per-model feature names: ``<name>_margin_home``, ``<name>_conf``.

    Single source of the naming contract so the vector built from one
    model list and the names zipped against it can always be produced
    from the *same* list (see :func:`weighted_tip_predict`).
    """
    names: List[str] = []
    for name in model_names:
        names.append(f"{name}_margin_home")  # signed margin toward HOME team
        names.append(f"{name}_conf")  # that model's confidence 0..1
    return names


#: The 16-length ordered feature list.  For each model we emit the signed
#: margin toward the home team first, then that model's confidence.
#: This is the exact order training and prediction must agree on.
FEATURE_NAMES: List[str] = feature_names_for(MODEL_NAMES)


# The typed prediction contract (P2-1).  ``Prediction`` is a NamedTuple
# whose positional order matches the legacy ``(winner, confidence, margin)``.


# ---------------------------------------------------------------------------
# A2. build_feature_vector
# ---------------------------------------------------------------------------

def build_feature_vector(
    model_predictions: Mapping[str, Prediction],
    home_team: str,
    away_team: str,
    model_names: List[str] | None = None,
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
    for name in model_names if model_names is not None else MODEL_NAMES:
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
    feature_names: List[str] | None = None,
) -> float:
    """Linear combination: ``intercept + sum(coef[name] * value)``.

    ``coefficients`` is keyed by feature name (default
    :data:`FEATURE_NAMES`).  A feature with no coefficient entry is
    treated as coefficient ``0.0``.  Pass ``feature_names`` whenever the
    vector was built from a non-default ``model_names`` list — the names
    and the vector must come from the same list.  Raises
    :class:`ValueError` on a length mismatch instead of silently
    truncating the zip and mis-weighting the prediction.  Pure function.
    """
    names = FEATURE_NAMES if feature_names is None else feature_names
    if len(features) != len(names):
        raise ValueError(
            f"features/names length mismatch: got {len(features)} features "
            f"but {len(names)} feature names — the vector and the names "
            "must come from the same model list"
        )
    total = float(intercept)
    for name, value in zip(names, features):
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
    return Prediction(winner, confidence, margin)


# ---------------------------------------------------------------------------
# A5. weighted_tip_predict (compose)
# ---------------------------------------------------------------------------

def weighted_tip_predict(
    intercept: float,
    coefficients: Mapping[str, float],
    model_predictions: Mapping[str, Prediction],
    home_team: str,
    away_team: str,
    model_names: List[str] | None = None,
) -> Prediction:
    """Convenience pure function composing A2 → A3 → A4.

    The feature vector and the coefficient-name order are ALWAYS derived
    from the same ``model_names`` list, so they can never drift apart.
    """
    resolved_names = model_names if model_names is not None else MODEL_NAMES
    features = build_feature_vector(
        model_predictions, home_team, away_team, model_names=resolved_names
    )
    y_pred = predict_home_margin(
        features,
        intercept,
        coefficients,
        feature_names=feature_names_for(resolved_names),
    )
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
    team with the most votes wins.  Ties and empty inputs resolve to the
    alphabetically first team — a deterministic, home/away-neutral rule.
    Confidence is fixed at ``0.55`` and the margin is the rounded mean of
    the absolute prediction margins (or ``6`` when there are none).
    """
    if not model_predictions:
        # Zero information must look like zero information: alphabetically
        # first team, fixed 0.55 confidence, minimum margin 6.
        return Prediction(min(home_team, away_team), 0.55, 6)

    home_votes = 0
    away_votes = 0
    margins: List[int] = []
    for winner, _confidence, margin in model_predictions.values():
        if winner == home_team:
            home_votes += 1
        elif winner == away_team:
            away_votes += 1
        margins.append(abs(margin))

    # Home/away-neutral tie-break: strictly-more-votes wins; a tie goes to
    # the alphabetically first team (deterministic, no fixture bias).
    if home_votes > away_votes:
        winner = home_team
    elif away_votes > home_votes:
        winner = away_team
    else:
        winner = min(home_team, away_team)
    confidence = 0.55
    if margins:
        margin = max(1, int(round(statistics.fmean(margins))))
    else:
        margin = 6
    return Prediction(winner, confidence, margin)


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
        # P2-3: per-instance model names derived from the injected
        # models — the registry (not a hardcoded list) decides which
        # models exist, and the feature vector follows.
        self.model_names: List[str] = (
            [m.get_name() for m in models] if models else list(MODEL_NAMES)
        )
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
                model_names=self.model_names,
            )

        # No trained model yet — majority-vote fallback.  The fallback owns
        # the empty cold-start case too (alphabetically first team, 0.55, 6),
        # so the neutral rule lives in exactly one place.
        return weighted_tip_fallback(model_predictions, home_team, away_team)
