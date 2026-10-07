"""SHAP explanation services for the ``boosted_tip`` XGBoost model (BT-1).

One read path backs the backtest page's "Active Boosted Model" section:
:func:`get_active_boosted_model` — metadata for the currently-active
``boosted_tip`` :class:`~packages.shared.models.ModelVersion` plus its
*global* SHAP feature importances.  BT-1 intentionally overloads
``model_coefficients`` to carry ``mean |SHAP value|`` instead of linear
weights (see the CRUD docstrings), so this function reads the *same*
rows the ``weighted_tip`` coefficient chart reads and enriches them with
``model``/``type`` derived from the feature-name convention — mirroring
:meth:`BacktestService.get_active_weighted_model` (``services/backtest.py``)
so the frontend can render both charts with one shape.

Design rationale:

* **Global, not local.**  The page explains the model through its global
  mean |SHAP| importances only; the per-game (local) SHAP endpoint and
  its in-process explainer cache were removed by design decision
  (superseded — recoverable from git history if ever revived).  That
  also means this module never deserializes the artifact at request
  time: xgboost/shap do not load for these reads at all.
* **Lazy heavy imports (S3).**  Nothing here imports ML libraries at
  module level; the module is imported eagerly by ``app.api.backtest``
  at worker start and must stay memory-cheap.  Pinned by
  ``test_importing_boosted_explanations_module_does_not_load_heavy_libs``
  (and the repo-wide ``tests/unit/test_lazy_sklearn.py`` guard).
* **None, never raise, for "no explanation available".**  No active
  version returns ``None`` — the API layer maps that to 404.  A missing
  explanation must never crash a backtest page render.
"""

from __future__ import annotations

from typing import Any, Dict

from sqlalchemy.ext.asyncio import AsyncSession

from ..logger import get_logger

logger = get_logger(__name__)

#: Name under which boosted-tip versions are stored in ``model_versions``.
BOOSTED_TIP_MODEL_NAME: str = "boosted_tip"


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
