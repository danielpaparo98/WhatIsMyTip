"""Weekly retrain service for the ``boosted_tip`` XGBoost model (BT-1).

This is the boosted-tip sibling of
:mod:`packages.shared.services.model_retrain`, invoked by the same in-process
cron job (BT-1 decision 4: ONE weekly job trains BOTH models) and by the
ad-hoc ops script ``backend/scripts/run_boosted_retrain.py``.  It:

1. Gathers historical training rows by reusing
   :func:`packages.shared.services.model_retrain._gather_training_rows` —
   the weighted-tip data access is pure and yields the exact same
   ``(16-feature vector, signed home margin)`` rows, so importing it (never
   copying) keeps both models trained on an identical dataset.
2. Fits an :class:`xgboost.XGBRegressor` gradient-boosted regression on the
   full training set with the deterministic baseline parameters in
   :data:`BOOSTED_PARAMS` (the notebook is the tuning surface; the ``params``
   argument of :func:`run_boosted_retrain` is how tuned parameters reach
   production).
3. Explains the fit with SHAP: ``shap.TreeExplainer`` over the training
   matrix yields per-feature contributions whose global mean |SHAP| becomes
   the persisted "coefficients" (BT-1 overload documented in
   :mod:`packages.shared.crud.model_versions`), plus the base value stored as
   ``shap_base_value``.  Additivity (``base + sum(shap) == prediction``) is
   asserted as a fit sanity check — Tree SHAP is exact for gbtree models.
4. Persists the serialized ensemble (``get_booster().save_raw(raw_format=
   "json")`` bytes — a tree ensemble has no per-feature weights, so the
   blob IS the model) as a new, *active*
   :class:`~packages.shared.models.ModelVersion` via
   :func:`packages.shared.crud.model_versions.create_model_version`.

Heavy ML dependencies (numpy, xgboost, shap, sklearn.metrics) are imported
LAZILY inside :func:`run_boosted_retrain`, strictly past the
:data:`MIN_TRAINING_ROWS` skip gate (S3) — a skipped retrain never loads
them, and the always-on API workers never pay their resident-memory cost.
Pinned by ``tests/unit/test_boosted_retrain_service.py`` and
``tests/unit/test_lazy_sklearn.py``.

When fewer than :data:`MIN_TRAINING_ROWS` usable rows are available the fit
is **skipped** and the currently-active model is left untouched — identical
to the weighted-tip skip contract: we never overwrite a good model with a
too-small training set.
"""

from __future__ import annotations

from typing import Any, Dict

from sqlalchemy.ext.asyncio import AsyncSession

from ..crud.model_versions import create_model_version, next_version_number
from ..heuristics.weighted_tip import FEATURE_NAMES
from ..logger import get_logger
from .model_retrain import MIN_TRAINING_ROWS, _gather_training_rows

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Module constants
# ---------------------------------------------------------------------------

#: Name under which ``boosted_tip`` versions are stored in ``model_versions``
#: (parallel to ``weighted_tip`` — BT-1 decision 1).
BOOSTED_TIP_MODEL_NAME: str = "boosted_tip"

#: Deterministic baseline XGBRegressor parameters (BT-1).  A module-level
#: dict so tests and the notebook can reference/compare it; ``n_jobs=4`` and
#: ``tree_method="hist"`` keep the weekly fit fast, ``random_state=42``
#: pins subsample/colsample draws so identical data reproduces the model
#: byte-for-byte.
BOOSTED_PARAMS: Dict[str, Any] = {
    "n_estimators": 400,
    "max_depth": 3,
    "learning_rate": 0.08,
    "subsample": 0.8,
    "colsample_bytree": 0.8,
    "tree_method": "hist",
    "eval_metric": "rmse",
    "random_state": 42,
    "n_jobs": 4,
}


# ---------------------------------------------------------------------------
# Fit + persist
# ---------------------------------------------------------------------------


async def run_boosted_retrain(
    session: AsyncSession, params: Dict[str, Any] | None = None
) -> Dict[str, object]:
    """Gather rows, fit ``XGBRegressor``, SHAP-explain, persist an active version.

    Args:
        session: Active :class:`AsyncSession`.  Persistence is a single
            transaction handled by :func:`create_model_version`.
        params: Optional full replacement for :data:`BOOSTED_PARAMS` (the
            notebook/tuning hook — no merging is performed).  ``None`` uses
            the deterministic baseline.

    Returns:
        A summary dict.  ``status`` is ``"trained"`` on success (with
        ``model_name``, ``version``, ``model_version_id``, ``training_rows``,
        ``metrics``, ``shap_importance`` and ``params_used``) or ``"skipped"``
        with ``reason == "insufficient_training_rows"`` when too few usable
        rows exist — in which case the currently-active model is left
        untouched.
    """
    # Copy so callers mutating the returned ``params_used`` can never
    # corrupt the module-level baseline constant.
    used_params: Dict[str, Any] = dict(BOOSTED_PARAMS if params is None else params)

    rows = await _gather_training_rows(session)
    logger.info(
        "boosted-retrain gathered %d usable training rows (min_required=%d)",
        len(rows),
        MIN_TRAINING_ROWS,
    )

    if len(rows) < MIN_TRAINING_ROWS:
        logger.warning(
            "boosted-retrain skipped: only %d rows (< %d); "
            "keeping currently-active %s model",
            len(rows),
            MIN_TRAINING_ROWS,
            BOOSTED_TIP_MODEL_NAME,
        )
        return {
            "status": "skipped",
            "reason": "insufficient_training_rows",
            "rows": len(rows),
            "min_required": MIN_TRAINING_ROWS,
        }

    # Build the design matrix from the gathered rows.
    #
    # Heavy ML deps are imported lazily (S3) so the always-on API workers
    # never pay the numpy + xgboost + shap + sklearn resident-memory cost.
    # Only the weekly retrain actually fits, and only past this skip gate —
    # a skipped retrain (too few rows) never loads them at all.
    import numpy as np
    import shap
    import xgboost as xgb
    from sklearn.metrics import mean_absolute_error, r2_score

    X = np.array([features for features, _ in rows], dtype=float)  # noqa: N806 — ML design-matrix convention (mirrors model_retrain.py)
    y = np.array([target for _, target in rows], dtype=float)

    # Fit + score on the full training set (parity with the linear retrain's
    # train-set metrics).  XGBoost with a fixed random_state is deterministic,
    # so two fits on identical data yield byte-identical models.
    model = xgb.XGBRegressor(**used_params)
    model.fit(X, y)
    preds = model.predict(X)
    metrics: Dict[str, float] = {
        "r2": float(r2_score(y, preds)),
        "mae": float(mean_absolute_error(y, preds)),
    }

    # Global SHAP explanation: mean |SHAP| per feature is persisted in the
    # coefficient rows (BT-1 overload); expected_value is the base every
    # per-game contribution sums against.
    explainer = shap.TreeExplainer(model)
    sv = explainer.shap_values(X)  # (n_rows, n_features)
    importance = np.abs(sv).mean(axis=0)
    base = float(np.asarray(explainer.expected_value).ravel()[0])

    shap_importance: Dict[str, float] = {
        name: float(value) for name, value in zip(FEATURE_NAMES, importance)
    }
    metrics["shap_base_value"] = base

    # Additivity sanity (Tree SHAP is exact for gbtree):
    # prediction_i == base + sum_j shap_values[i, j].  Tolerance is 1e-4,
    # not machine epsilon: shap_values are float32 (accumulated over 16
    # features), so real-data deviations of ~1e-5 are expected.
    max_deviation = float(np.abs(base + sv.sum(axis=1) - preds).max())
    assert max_deviation <= 1e-4, (
        f"SHAP additivity violated for {BOOSTED_TIP_MODEL_NAME}: "
        f"max |base + sum(shap) - prediction| = {max_deviation:.3e}"
    )

    model_bytes: bytes = model.get_booster().save_raw(raw_format="json")

    version = await next_version_number(session, BOOSTED_TIP_MODEL_NAME)
    mv = await create_model_version(
        session,
        model_name=BOOSTED_TIP_MODEL_NAME,
        version=version,
        intercept=0.0,  # the ensemble has no intercept; base value plays that role
        training_rows=len(rows),
        metrics=metrics,
        coefficients=shap_importance,
        artifact=model_bytes,
        artifact_format="json",
        shap_base_value=base,
        set_active=True,
    )

    logger.info(
        "boosted-retrain trained %s version=%d training_rows=%d "
        "r2=%.4f mae=%.4f shap_base=%.4f active=True",
        BOOSTED_TIP_MODEL_NAME,
        version,
        len(rows),
        metrics["r2"],
        metrics["mae"],
        base,
    )

    return {
        "status": "trained",
        "model_name": BOOSTED_TIP_MODEL_NAME,
        "version": version,
        "model_version_id": mv.id,
        "training_rows": len(rows),
        "metrics": metrics,
        "shap_importance": shap_importance,
        "params_used": used_params,
    }
