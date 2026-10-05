"""CRUD operations for trained model versions and their coefficients.

Backs the new ``weighted_tip`` scikit-learn heuristic.  Each call to
:func:`create_model_version` inserts a :class:`ModelVersion` together
with its :class:`ModelCoefficient` rows in a single transaction, and
atomically promotes it to the active version when ``set_active=True``.

The runtime reads the currently-serving weights via
:func:`get_active_coefficients`; weekly retraining writes a
new version here and flips ``is_active`` so the changeover is
non-blocking.

BT-1 (boosted_tip XGBoost heuristic): the same table now also stores
gradient-boosted models.  Decision recorded here on purpose — we
EXTENDED :func:`create_model_version` with optional ``artifact`` /
``artifact_format`` / ``shap_base_value`` keyword arguments instead of
adding a parallel ``create_boosted_model_version``, because both
heuristics share the identical insert/activate/deactivate transaction
and a second function would duplicate it.  Omitting the new arguments
(None defaults) reproduces the exact pre-BT-1 behaviour, so every
``weighted_tip`` caller is unchanged.  Two overloads to know about:

* ``artifact`` — the serialized XGBoost ensemble
  (``get_booster().save_raw(raw_format="json")`` bytes) stored as
  ``BYTEA``; read back via :func:`get_active_model_artifact`.  A tree
  ensemble has no per-feature weights, so the blob IS the model.
* ``coefficients`` — for ``boosted_tip`` versions this mapping carries
  the global SHAP feature importance (``feature_name -> mean |SHAP
  value|``) instead of linear weights.  Same rows, same unique
  constraint, different semantics — documented on the
  :class:`~packages.shared.models.ModelCoefficient` model too — so the
  existing coefficient-chart pipeline works unchanged.

This module deliberately imports NO heavy ML library (no xgboost, shap
or sklearn at module level): artifacts are opaque bytes here and
serialisation stays in the (lazy) retrain/heuristic code paths — pinned
by ``tests/unit/test_lazy_sklearn.py``.
"""

from typing import Any, Optional

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..logger import get_logger
from ..models import ModelCoefficient, ModelVersion

logger = get_logger(__name__)


async def create_model_version(
    session: AsyncSession,
    *,
    model_name: str,
    version: int,
    intercept: float,
    training_rows: int,
    metrics: Optional[dict[str, Any]],
    coefficients: dict[str, float],
    set_active: bool = True,
    artifact: Optional[bytes] = None,
    artifact_format: Optional[str] = None,
    shap_base_value: Optional[float] = None,
) -> ModelVersion:
    """Insert a model version and its coefficient rows in one transaction.

    Args:
        session: Async database session.
        model_name: Name of the model (e.g. ``"weighted_tip"`` or the
            BT-1 ``"boosted_tip"``).
        version: Monotonically increasing version number for the model.
        intercept: The ``LinearRegression`` intercept term (``0.0`` for
            boosted_tip versions — the ensemble has no intercept, the
            SHAP base value plays that role, see ``shap_base_value``).
        training_rows: Number of rows the model was trained on.
        metrics: Optional quality metrics, e.g. ``{"r2": ..., "mae": ...}``
            (boosted_tip adds ``"shap_base_value"``).
        coefficients: Mapping of ``feature_name -> coefficient`` weight.
            BT-1 overload: for ``boosted_tip`` versions this carries the
            global SHAP feature importance — ``feature_name -> mean
            |SHAP value|`` — stored in the same ``model_coefficients``
            rows so the existing coefficient-chart pipeline works
            unchanged.
        set_active: When ``True`` (the default), deactivate every other
            version with the same ``model_name`` and mark this one active,
            so only one version per model is active at a time.
        artifact: Optional serialized model blob (BT-1).  For
            ``boosted_tip`` this is ``get_booster().save_raw(raw_format=
            "json")`` bytes; ``weighted_tip`` callers omit it.
        artifact_format: Serialization tag for ``artifact`` (e.g.
            ``"json"``).  Should accompany every artifact so readers
            never guess the format.
        shap_base_value: Optional TreeExplainer expected value — the
            SHAP base that per-feature contributions sum against
            (BT-1, ``boosted_tip`` only).

    Returns:
        The freshly inserted, refreshed :class:`ModelVersion`.
    """
    version_row = ModelVersion(
        model_name=model_name,
        version=version,
        intercept=intercept,
        training_rows=training_rows,
        metrics=metrics,
        artifact=artifact,
        artifact_format=artifact_format,
        shap_base_value=shap_base_value,
        is_active=bool(set_active),
    )
    session.add(version_row)
    await session.flush()  # populate version_row.id before adding coefficients

    for feature_name, coefficient in coefficients.items():
        session.add(
            ModelCoefficient(
                model_version_id=version_row.id,
                feature_name=feature_name,
                coefficient=coefficient,
            )
        )

    if set_active:
        # Deactivate all other versions of the same model so at most one
        # version per model_name is active at a time.
        await session.execute(
            update(ModelVersion)
            .where(ModelVersion.model_name == model_name)
            .where(ModelVersion.id != version_row.id)
            .values(is_active=False)
        )
        version_row.is_active = True

    await session.commit()
    await session.refresh(version_row)

    logger.info(
        "created model_version model_name=%s version=%s coefficients=%d active=%s",
        model_name,
        version,
        len(coefficients),
        set_active,
    )
    return version_row


async def get_active_model_version(
    session: AsyncSession, model_name: str
) -> ModelVersion | None:
    """Return the currently-active version for ``model_name``, or ``None``."""
    result = await session.execute(
        select(ModelVersion)
        .where(ModelVersion.model_name == model_name)
        .where(ModelVersion.is_active.is_(True))
        .order_by(ModelVersion.version.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def get_model_coefficients(
    session: AsyncSession, model_version_id: int
) -> list[ModelCoefficient]:
    """Return all coefficient rows for a version, ordered by feature name."""
    result = await session.execute(
        select(ModelCoefficient)
        .where(ModelCoefficient.model_version_id == model_version_id)
        .order_by(ModelCoefficient.feature_name)
    )
    return list(result.scalars().all())


async def get_active_coefficients(
    session: AsyncSession, model_name: str
) -> tuple[float, dict[str, float]] | None:
    """Return ``(intercept, {feature_name: coefficient})`` for the active version.

    Convenience wrapper that reads the active version and its weights in
    one go.  Returns ``None`` when no version is active for ``model_name``.
    """
    active = await get_active_model_version(session, model_name)
    if active is None:
        return None
    coefficients = await get_model_coefficients(session, active.id)
    return active.intercept, {c.feature_name: c.coefficient for c in coefficients}


async def get_active_model_artifact(
    session: AsyncSession, model_name: str
) -> tuple[bytes, str, ModelVersion] | None:
    """Return ``(artifact_bytes, artifact_format, version_row)`` for the active version.

    BT-1 read path for the ``boosted_tip`` XGBoost heuristic: the caller
    lazily deserializes ``artifact_bytes`` (``xgboost`` import stays
    inside the caller) and predicts against it.

    Returns ``None`` when either:

    * no version of ``model_name`` is active (e.g. before the first
      weekly retrain), or
    * the active version stores no artifact (every ``weighted_tip``
      version keeps its weights in coefficient rows instead).

    Callers must treat ``None`` as "no learned model available" and fall
    back to the deterministic heuristic — a ``None`` here must never
    crash tip generation.

    A legacy row that stored bytes without an ``artifact_format`` tag
    reads back as ``"json"`` — the canonical format this CRUD writes
    (``save_raw(raw_format="json")``), so the default is always correct
    for artifacts created by this codebase.
    """
    active = await get_active_model_version(session, model_name)
    if active is None or active.artifact is None:
        return None
    return active.artifact, active.artifact_format or "json", active


async def next_version_number(session: AsyncSession, model_name: str) -> int:
    """Return ``max(version) + 1`` for ``model_name`` (or ``1`` if none exist)."""
    result = await session.execute(
        select(func.max(ModelVersion.version)).where(
            ModelVersion.model_name == model_name
        )
    )
    max_version = result.scalar()
    return (max_version or 0) + 1
