"""CRUD operations for trained model versions and their coefficients.

Backs the new ``weighted_tip`` scikit-learn heuristic.  Each call to
:func:`create_model_version` inserts a :class:`ModelVersion` together
with its :class:`ModelCoefficient` rows in a single transaction, and
atomically promotes it to the active version when ``set_active=True``.

The runtime reads the currently-serving weights via
:func:`get_active_coefficients`; weekly retraining (Subtask 3) writes a
new version here and flips ``is_active`` so the changeover is
non-blocking.
"""

from typing import Optional

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
    metrics: Optional[dict],
    coefficients: dict[str, float],
    set_active: bool = True,
) -> ModelVersion:
    """Insert a model version and its coefficient rows in one transaction.

    Args:
        session: Async database session.
        model_name: Name of the model (e.g. ``"weighted_tip"``).
        version: Monotonically increasing version number for the model.
        intercept: The ``LinearRegression`` intercept term.
        training_rows: Number of rows the model was trained on.
        metrics: Optional quality metrics, e.g. ``{"r2": ..., "mae": ...}``.
        coefficients: Mapping of ``feature_name -> coefficient`` weight.
        set_active: When ``True`` (the default), deactivate every other
            version with the same ``model_name`` and mark this one active,
            so only one version per model is active at a time.

    Returns:
        The freshly inserted, refreshed :class:`ModelVersion`.
    """
    version_row = ModelVersion(
        model_name=model_name,
        version=version,
        intercept=intercept,
        training_rows=training_rows,
        metrics=metrics,
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


async def next_version_number(session: AsyncSession, model_name: str) -> int:
    """Return ``max(version) + 1`` for ``model_name`` (or ``1`` if none exist)."""
    result = await session.execute(
        select(func.max(ModelVersion.version)).where(
            ModelVersion.model_name == model_name
        )
    )
    max_version = result.scalar()
    return (max_version or 0) + 1
