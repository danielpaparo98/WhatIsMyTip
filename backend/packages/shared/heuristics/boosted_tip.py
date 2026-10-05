"""XGBoost-backed ``boosted_tip`` heuristic (BT-1).

This module adds a gradient-boosted companion to the scikit-learn
``weighted_tip`` heuristic (Layer 2).  It learns an
:class:`xgboost.XGBRegressor` over the *same* eight underlying ML
models' predictions and predicts the **home-team-signed margin**, then
maps that margin to a tip with the exact same rules weighted_tip uses.

Design decisions (BT-1):

* **BT-1a — one feature contract, two models.**  The 16-feature vector,
  its ordering, the tip mapping and the majority-vote fallback are all
  REUSED from :mod:`packages.shared.heuristics.weighted_tip` (imported,
  never copied).  Both models are trained and served from the identical
  contract, so the weekly job (Subtask 3) and the backtest SHAP pages
  can share ``build_feature_vector`` without translation layers.
* **BT-1b — pure-function core, lazy heavy imports (S3).**  xgboost is
  a MAIN dependency (the weekly retrain runs in-process) but the
  always-on API workers must never pay its resident-memory cost at
  import time.  Every xgboost import lives INSIDE a function body; the
  module top level imports only stdlib + lightweight project modules.
  Pinned by ``tests/unit/test_boosted_tip_heuristic.py`` and
  ``tests/unit/test_lazy_sklearn.py``.
* **BT-1c — bytes are the storage format.**  The retrain job persists
  ``model.get_booster().save_raw(raw_format="json")`` bytes via
  :mod:`packages.shared.crud.model_versions` (``ModelVersion.artifact``
  BYTEA + ``artifact_format`` tag).  This module deserializes those
  bytes with :func:`load_boosted_model` — the JSON raw format is
  XGBoost's canonical, cross-version-stable serialization, and
  ``load_model`` detects the buffer format itself, so the stored tag is
  advisory metadata kept in parity with the DB column.
* **BT-1d — graceful pre-retrain behaviour.**  Until the first weekly
  retrain activates a boosted version, :class:`BoostedTipHeuristic`
  falls back to the weighted-tip majority vote.  Tip generation always
  works; a missing model never crashes a prediction.
* **BT-1e — stateless prediction core.**  No in-module model/ explainer
  caching: :func:`predict_home_margin_boosted` is pure so the
  orchestrator (Subtask 5) owns TTL caching of loaded artifacts, and
  the SHAP service (Subtask 6/7) owns explainer caching.  Keeping the
  core stateless makes determinism testable and avoids stale-model
  bugs across versions.
* **BT-2 — deserialize once per TTL window, not per tip.**  The pure
  function accepts raw artifact bytes (deserializes, historical
  behaviour) OR a preloaded model object, and
  :meth:`BoostedTipHeuristic.set_loaded_model` lets the orchestrator's
  TTL loader push the deserialized object in.  XGBoost ``load_model``
  is ms-scale with real object churn — paying it on every ``apply``
  would dominate per-tip latency for no benefit.

The public surface here is intentionally pure and side-effect-free so
it can be reused by two callers:

1. :class:`BoostedTipHeuristic` — the runtime heuristic applied by the
   :class:`~packages.shared.orchestrator.ModelOrchestrator` during tip
   generation.
2. The weekly retrain job (Subtask 3) and the SHAP backtest service —
   which fit the regressor, persist the artifact bytes, and re-use
   :func:`build_feature_vector` for both the training ``X`` matrix and
   per-game explanation rows.
"""

from __future__ import annotations

from typing import Any, Dict, List

from ..models import Game
from ..models_ml.prediction import Prediction
from .base import BaseHeuristic
from .weighted_tip import (
    FEATURE_NAMES,
    MODEL_NAMES,
    build_feature_vector,
    feature_names_for,
    home_margin_to_tip,
    weighted_tip_fallback,
)

__all__ = [
    "BoostedTipHeuristic",
    "load_boosted_model",
    "predict_home_margin_boosted",
]


# ---------------------------------------------------------------------------
# BT-1c. load_boosted_model (lazy deserialization helper)
# ---------------------------------------------------------------------------

def load_boosted_model(
    model_bytes: bytes, artifact_format: str = "json"
) -> Any:
    """Deserialize XGBoost model bytes into a predict-capable regressor.

    BT-1b: the ``import xgboost`` is deliberately INSIDE this function —
    importing this module must stay memory-cheap for every always-on
    API worker; only callers that actually hold an artifact pay for it.

    ``model_bytes`` is the output of ``save_raw(raw_format="json")`` as
    persisted in ``ModelVersion.artifact``.  ``artifact_format`` mirrors
    the stored ``ModelVersion.artifact_format`` tag; XGBoost's
    ``load_model`` detects the buffer format from the bytes themselves,
    so the tag is validated-for-parity metadata rather than an
    instruction (BT-1c).  Returns the loaded ``XGBRegressor`` (typed as
    ``Any`` to avoid importing xgboost for annotations).
    """
    import xgboost as xgb  # BT-1b: lazy — never at module top level

    reg = xgb.XGBRegressor()
    # xgboost's load_model accepts str/PathLike/bytearray buffers but
    # raises TypeError("Unknown file type") on plain ``bytes``.  Real
    # artifact paths hand us bytes (``set_model``'s stable copy, asyncpg
    # BYTEA reads) or memoryview, so normalise to bytearray here — the
    # one chokepoint every load goes through.
    reg.load_model(bytearray(model_bytes))
    return reg


# ---------------------------------------------------------------------------
# BT-1a. predict_home_margin_boosted (pure function core)
# ---------------------------------------------------------------------------

def predict_home_margin_boosted(
    model_or_bytes: Any,
    features: List[float],
    feature_names: List[str] | None = None,
    artifact_format: str = "json",
) -> float:
    """Predict the home-team-signed margin from a model + one feature row.

    ``model_or_bytes`` is additive (BT-2): either the raw artifact bytes
    as persisted in ``ModelVersion.artifact`` (deserialized here via
    :func:`load_boosted_model` — the historical behaviour) OR an
    already-deserialized, predict-capable model (anything that is not
    ``bytes``/``bytearray``/``memoryview``), which is used directly.
    The orchestrator passes the preloaded object so XGBoost
    deserialization happens ONCE per TTL window, not once per tip.

    ``features`` must be built with :func:`build_feature_vector` (or in
    the exact :data:`FEATURE_NAMES` order).  Pass ``feature_names``
    whenever the vector was built from a non-default ``model_names``
    list — the names and the vector must come from the same list.
    Raises :class:`ValueError` on a length mismatch instead of silently
    truncating and mis-predicting (same contract as weighted_tip's
    :func:`~packages.shared.heuristics.weighted_tip.predict_home_margin`;
    the validation happens BEFORE the xgboost import so bad input fails
    loud and cheap).  Pure and stateless (BT-1e): caching of the loaded
    model belongs to the orchestrator, not here.
    """
    names = FEATURE_NAMES if feature_names is None else feature_names
    if len(features) != len(names):
        raise ValueError(
            f"features/names length mismatch: got {len(features)} features "
            f"but {len(names)} feature names — the vector and the names "
            "must come from the same model list"
        )
    if isinstance(model_or_bytes, (bytes, bytearray, memoryview)):
        reg = load_boosted_model(bytes(model_or_bytes), artifact_format)
    else:
        # BT-2: already deserialized (orchestrator TTL path) — predict
        # with the object as-is.
        reg = model_or_bytes
    prediction = reg.predict([list(features)])
    return float(prediction[0])


# ---------------------------------------------------------------------------
# BT-1d. BoostedTipHeuristic
# ---------------------------------------------------------------------------

class BoostedTipHeuristic(BaseHeuristic):
    """Gradient-boosted margin predictor over the eight underlying models.

    When a trained artifact is available (injected from the orchestrator
    via :meth:`set_model`) the heuristic builds the shared feature
    vector, predicts the home-team-signed margin with
    :func:`predict_home_margin_boosted`, and maps it to a tip with the
    shared :func:`home_margin_to_tip` rules.  Otherwise it falls back to
    the majority-vote :func:`weighted_tip_fallback`, so tip generation
    always works even before the first weekly retrain (BT-1d).

    Note: the ``apply`` signature intentionally takes only
    ``(game, model_predictions)`` — artifact loading happens in the
    orchestrator (which owns the db session) and is pushed in here.
    """

    def __init__(self, models: List[Any]) -> None:
        self.models = models
        # BT-1a: per-instance model names derived from the injected
        # models — the registry (not a hardcoded list) decides which
        # models exist, and the feature vector follows.  Mirrors
        # WeightedTipHeuristic exactly so both models always agree on
        # the feature contract for a given registry.
        self.model_names: List[str] = (
            [m.get_name() for m in models] if models else list(MODEL_NAMES)
        )
        self._model_bytes: bytes | None = None
        self._artifact_format: str = "json"
        # BT-2: an ALREADY-deserialized model pushed in by the
        # orchestrator's TTL loader.  When set, ``apply`` predicts with
        # this object directly instead of re-deserializing bytes per tip.
        self._loaded_model: Any | None = None

    def get_name(self) -> str:
        return "boosted_tip"

    def set_model(self, model_bytes: bytes, artifact_format: str = "json") -> None:
        """Activate the learned-boosted path with the given artifact bytes."""
        # Coerce to bytes defensively: DB drivers may hand back
        # memoryview, and the heuristic should hold a stable copy.
        self._model_bytes = bytes(model_bytes)
        self._artifact_format = artifact_format
        # Bytes replace any preloaded object so the two sources of truth
        # can never disagree about which model is serving.
        self._loaded_model = None

    def set_loaded_model(self, model: Any) -> None:
        """Activate the learned path with an ALREADY-deserialized model.

        BT-2 (orchestrator contract): ``_ensure_boosted_tip_model``
        deserializes the active artifact ONCE per TTL window and pushes
        the loaded object here, so ``apply`` never pays XGBoost's
        ms-scale ``load_model`` cost (plus object churn) per tip.
        """
        self._loaded_model = model
        self._model_bytes = None

    def clear_model(self) -> None:
        """Revert to the majority-vote fallback (e.g. when no model is active)."""
        self._model_bytes = None
        self._loaded_model = None

    async def apply(
        self, game: Game, model_predictions: Dict[str, Prediction]
    ) -> Prediction:
        """Apply the Boosted Tip heuristic to model predictions."""
        home_team = game.home_team
        away_team = game.away_team

        if self._model_bytes is not None or self._loaded_model is not None:
            features = build_feature_vector(
                model_predictions, home_team, away_team, model_names=self.model_names
            )
            # BT-2: prefer the preloaded object (orchestrator TTL path —
            # no per-tip deserialization); fall back to raw bytes (per-tip
            # deserialize) for direct set_model callers.
            model_or_bytes: Any = (
                self._loaded_model
                if self._loaded_model is not None
                else self._model_bytes
            )
            y_pred = predict_home_margin_boosted(
                model_or_bytes,
                features,
                feature_names=feature_names_for(self.model_names),
                artifact_format=self._artifact_format,
            )
            return home_margin_to_tip(y_pred, home_team, away_team)

        # No trained model yet — majority-vote fallback.  The fallback owns
        # the empty cold-start case too (alphabetically first team, 0.55, 6),
        # so the neutral rule lives in exactly one place.
        return weighted_tip_fallback(model_predictions, home_team, away_team)
