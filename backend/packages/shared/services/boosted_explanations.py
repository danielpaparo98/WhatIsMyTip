"""SHAP explanation services for the ``boosted_tip`` XGBoost model (BT-1).

Two read paths back the backtest page's new "Active Boosted Model" section:

1. :func:`get_active_boosted_model` — metadata for the currently-active
   ``boosted_tip`` :class:`~packages.shared.models.ModelVersion` plus its
   global SHAP feature importances.  BT-1 intentionally overloads
   ``model_coefficients`` to carry ``mean |SHAP value|`` instead of linear
   weights (see the CRUD docstrings), so this function reads the *same*
   rows the ``weighted_tip`` coefficient chart reads and enriches them
   with ``model``/``type`` derived from the feature-name convention —
   mirroring :meth:`BacktestService.get_active_weighted_model`
   (``services/backtest.py``) so the frontend can render both charts with
   one shape.

2. :func:`get_game_shap_explanation` — per-game *local* explanation:
   rebuild the game's 16-feature vector via
   :func:`heuristics.weighted_tip.build_feature_vector` (the one and only
   feature contract — never copied), run ``shap.TreeExplainer`` over the
   single-row matrix, and return ``base_value``, the raw ``prediction``,
   per-feature signed ``contributions``, the ``winner`` (sign of the
   prediction) and a :func:`home_margin_to_tip`-derived ``pick`` so the
   UI can show "SHAP says: base value → prediction → tip" in one card.

Design rationale:

* **Lazy heavy imports (S3).**  ``shap`` and ``numpy`` are imported
  *inside function bodies only*, and xgboost loads only behind
  :func:`heuristics.boosted_tip.load_boosted_model` (itself lazy).
  ``app.api.backtest`` imports this module eagerly at worker start; a
  top-level ML import would make every always-on API worker pay the
  full resident-memory cost even though SHAP is only computed on
  explicit page requests.  Pinned by
  ``test_importing_boosted_explanations_module_does_not_load_heavy_libs``
  (and the repo-wide ``tests/unit/test_lazy_sklearn.py`` guard).
* **Explainer caching.**  ``TreeExplainer`` construction is cheap relative
  to process startup but not free, and every per-request
  ``shap_values`` call is sub-millisecond once built — so the loaded
  model + explainer are cached in-process, keyed by
  ``model_version_id``.  The cache is invalidated when the active
  version id changes (weekly retrain promotes a new row) or the stored
  artifact bytes drift for the same id; stale entries for other ids are
  pruned so the cache never pins more than the one served model.
  ``_reset_cache()`` exists for tests.
* **None, never raise, for "no explanation available".**  No active
  version, no artifact bytes, or a game without (any) ``ModelPrediction``
  rows all return ``None`` — the API layer maps that to 404.  A missing
  explanation must never crash a backtest page render.
"""

from __future__ import annotations

from typing import Any, Dict, Tuple

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..heuristics.boosted_tip import load_boosted_model
from ..heuristics.weighted_tip import (
    FEATURE_NAMES,
    build_feature_vector,
    home_margin_to_tip,
)
from ..logger import get_logger
from ..models import Game, ModelPrediction

logger = get_logger(__name__)

#: Name under which boosted-tip versions are stored in ``model_versions``.
BOOSTED_TIP_MODEL_NAME: str = "boosted_tip"

#: In-process explainer cache: ``model_version_id -> (artifact_bytes,
#: loaded_model, explainer)``.  Values are typed ``Any`` (not ``object``)
#: because the concrete ``xgboost.XGBRegressor`` / ``shap.TreeExplainer``
#: types only exist behind the lazy imports — and mypy strict would reject
#: method calls on plain ``object``.  Invalidation: id change, byte drift,
#: or :func:`_reset_cache`.
_cache: Dict[int, Tuple[bytes, Any, Any]] = {}


def _reset_cache() -> None:
    """Drop every cached model/explainer (tests and ops escapes only)."""
    _cache.clear()


def _get_loaded(version_id: int, artifact_bytes: bytes) -> Tuple[Any, Any]:
    """Return ``(model, explainer)`` for the artifact, (re)building on drift.

    Cache hit requires BOTH the same version id AND byte-identical
    artifact; anything else rebuilds.  Rebuilding also prunes entries for
    other version ids — only the active version is ever served, so
    keeping them would just pin resident memory.

    Lazy-import rationale (S3): ``xgboost`` (via
    :func:`heuristics.boosted_tip.load_boosted_model`, which keeps its
    import inside the function) and ``shap`` (here) are main deps — the
    weekly cron trains in-process — but must never load at module import
    time, only when a SHAP request actually arrives.
    """
    cached = _cache.get(version_id)
    if cached is not None and cached[0] == artifact_bytes:
        return cached[1], cached[2]

    import shap

    model = load_boosted_model(artifact_bytes)
    explainer = shap.TreeExplainer(model)

    _cache.clear()  # prune stale ids: single-served-model policy
    _cache[version_id] = (artifact_bytes, model, explainer)
    logger.debug("rebuilt boosted_tip explainer cache for version_id=%d", version_id)
    return model, explainer


def _enrich_importance(feature_name: str, shap_value: float) -> Dict[str, Any]:
    """Derive ``model``/``type`` from the feature-name convention.

    Convention (set by :func:`heuristics.weighted_tip.feature_names_for`):
    ``"<model>_margin_home"`` is the signed home margin of ``<model>``,
    ``"<model>_conf"`` its confidence; anything else is not tied to a
    known model.  Mirrors ``get_active_weighted_model`` exactly so both
    charts speak the same enrichment shape.
    """
    if feature_name.endswith("_margin_home"):
        model_name = feature_name[: -len("_margin_home")]
        ctype = "margin"
    elif feature_name.endswith("_conf"):
        model_name = feature_name[: -len("_conf")]
        ctype = "confidence"
    else:
        model_name = feature_name
        ctype = "other"
    return {
        "feature_name": feature_name,
        "shap_value": shap_value,
        "model": model_name,
        "type": ctype,
    }


async def get_active_boosted_model(session: AsyncSession) -> dict[str, Any] | None:
    """Return the active ``boosted_tip`` version meta + global SHAP importances.

    The importance list is sorted by ``|shap_value|`` descending (the
    chart's natural order); ``shap_value`` comes from
    ``ModelCoefficient.coefficient``, which the weekly retrain overloads
    to store ``mean |SHAP value|`` (BT-1).  Returns ``None`` when no
    active version exists (e.g. before the first retrain, or while the
    boosted step is disabled via ``BOOSTED_RETRAIN_ENABLED=false``).
    """
    # Local import keeps this module's import graph free of CRUD churn and
    # matches the lazy-resolution style of services/backtest.py (also what
    # lets tests stub the reads at the source module).
    from ..crud.model_versions import get_active_model_version, get_model_coefficients

    model_version = await get_active_model_version(session, BOOSTED_TIP_MODEL_NAME)
    if model_version is None:
        return None

    coefficient_rows = await get_model_coefficients(session, model_version.id)

    importances = [
        _enrich_importance(row.feature_name, float(row.coefficient))
        for row in coefficient_rows
    ]
    # Deterministic order: |SHAP| descending, feature name as tie-break.
    importances.sort(key=lambda item: (-abs(item["shap_value"]), item["feature_name"]))

    return {
        "model_name": BOOSTED_TIP_MODEL_NAME,
        "version": model_version.version,
        "trained_at": model_version.trained_at.isoformat()
        if model_version.trained_at
        else None,
        "training_rows": model_version.training_rows,
        "is_active": bool(model_version.is_active),
        "metrics": model_version.metrics or {},
        "importances": importances,
    }


async def get_game_shap_explanation(
    session: AsyncSession, game_id: int
) -> dict[str, Any] | None:
    """Explain the boosted model's margin prediction for one game via SHAP.

    Loads the active artifact, gathers the game's stored
    ``ModelPrediction`` rows into the canonical
    ``{model_name: (winner, confidence, margin)}`` mapping (same shape
    ``_gather_training_rows`` builds, so the feature vector is bit-for-bit
    the contract both training and prediction use), then runs
    ``TreeExplainer.shap_values`` on the single-row matrix.

    Returns ``None`` when there is no active artifact, the game does not
    exist, or the game has no prediction rows (the join yields nothing) —
    callers map ``None`` to 404.  On success: ``base_value +
    sum(contributions) == prediction`` holds to float tolerance (asserted
    in tests; keeping the payload clean of derived flags on purpose).
    """
    from ..crud.model_versions import get_active_model_artifact

    artifact = await get_active_model_artifact(session, BOOSTED_TIP_MODEL_NAME)
    if artifact is None:
        return None
    artifact_bytes, _artifact_format, version_row = artifact

    result = await session.execute(
        select(Game, ModelPrediction)
        .join(ModelPrediction, ModelPrediction.game_id == Game.id)
        .where(Game.id == game_id)
        .order_by(ModelPrediction.model_name)
    )
    rows = result.all()
    if not rows:
        # Covers both "game missing" and "game without predictions" —
        # the inner join cannot yield a game row with zero predictions.
        return None

    game = rows[0][0]
    preds: Dict[str, Tuple[str, float, int]] = {}
    for _game, pred in rows:
        preds[pred.model_name] = (pred.winner, float(pred.confidence), int(pred.margin))

    features = build_feature_vector(preds, game.home_team, game.away_team)

    model, explainer = _get_loaded(version_row.id, artifact_bytes)

    # Lazy numpy: only this request path needs it.
    import numpy as np

    row_matrix = np.asarray([features], dtype=float)
    shap_values = np.ravel(explainer.shap_values(row_matrix))
    base_value = float(np.ravel(explainer.expected_value)[0])
    prediction = float(np.ravel(model.predict(row_matrix))[0])

    contributions = {
        name: float(value) for name, value in zip(FEATURE_NAMES, shap_values)
    }
    contributions = dict(
        sorted(contributions.items(), key=lambda kv: (-abs(kv[1]), kv[0]))
    )

    tip = home_margin_to_tip(prediction, game.home_team, game.away_team)

    return {
        "game_id": game_id,
        "home_team": game.home_team,
        "away_team": game.away_team,
        "base_value": base_value,
        "prediction": prediction,
        "contributions": contributions,
        # "home"/"away" from the sign of the raw margin prediction.
        "winner": "home" if prediction >= 0 else "away",
        # Contextual tip (winner/margin/confidence) for the SHAP card.
        # Prediction is the sport-generic NamedTuple (pick, probability,
        # score_projection) whose positional order is the legacy
        # (winner, confidence, margin) — access by FIELD name here.
        "pick": {
            "winner": tip.pick,
            "margin": tip.score_projection,
            "confidence": tip.probability,
        },
    }
