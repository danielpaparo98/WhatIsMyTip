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

FCL-4 (first-class league heuristics): every version-lookup / promotion
entry point now takes an optional ``competition_id`` scope, following ADR
0001 D1 ("shared tables + scope columns").  ``NULL`` scope = legacy global
model; a non-NULL scope = a version belonging to exactly one competition.
The NULL scope is its OWN scope — SQL ``competition_id IS NULL`` matching,
never ``== None``, because ``x = NULL`` is never true in SQL.  A promotion
in one scope can therefore never deactivate another scope's active row, so
the weekly global AFL retrain and a league's retrain coexist on the shared
table (pinned by the partial unique indexes of migration 0015).  Omitting
``competition_id`` (the default everywhere) keeps the legacy global-path
statement shape: on any pre-FCL database every row is NULL-scope, so the
appended ``IS NULL`` predicate selects exactly the rows the pre-FCL
statements selected — byte-identical observable behaviour, which is why
``services.model_retrain`` / ``services.boosted_retrain`` needed no
changes.

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
    competition_id: Optional[int] = None,
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
            version with the same ``model_name`` in the SAME scope and
            mark this one active, so only one version per
            ``(model_name, competition_id)`` is active at a time (FCL-4).
        artifact: Optional serialized model blob (BT-1).  For
            ``boosted_tip`` this is ``get_booster().save_raw(raw_format=
            "json")`` bytes; ``weighted_tip`` callers omit it.
        artifact_format: Serialization tag for ``artifact`` (e.g.
            ``"json"``).  Should accompany every artifact so readers
            never guess the format.
        shap_base_value: Optional TreeExplainer expected value — the
            SHAP base that per-feature contributions sum against
            (BT-1, ``boosted_tip`` only).
        competition_id: Optional scope (FCL-4).  ``None`` (the default)
            stores a legacy global version and promotes within the NULL
            scope; a competition id stores a version belonging to exactly
            that competition and promotes only within it.  Promotions
            deactivate ONLY versions in the same ``(model_name, scope)`` —
            the NULL scope is its own scope (``competition_id IS NULL``) —
            so a league's active model and the global active model are
            independent.

    Returns:
        The freshly inserted, refreshed :class:`ModelVersion`.
    """
    if set_active:
        # Deactivate all other active versions of the same model IN THE
        # SAME SCOPE first (FCL-4).  This MUST happen before the new row's
        # INSERT below: migration 0015's partial unique indexes allow only
        # one ACTIVE row per (model_name, scope), so an insert-then-
        # deactivate sequence would fail the index at flush time with two
        # active rows in flight.  Deactivating first needs no ``id != self``
        # predicate — the new row does not exist in the database yet — and
        # the observable end state is identical to the pre-FCL sequence.
        #
        # The scope predicate must handle NULL explicitly:
        # ``competition_id == None`` never matches in SQL, so the NULL
        # (legacy global) scope gets an ``IS NULL`` predicate and a scoped
        # promotion gets an equality predicate — a promotion can therefore
        # never touch another scope's active row.
        deactivate = update(ModelVersion).where(ModelVersion.model_name == model_name)
        if competition_id is None:
            deactivate = deactivate.where(ModelVersion.competition_id.is_(None))
        else:
            deactivate = deactivate.where(ModelVersion.competition_id == competition_id)
        await session.execute(deactivate.values(is_active=False))

    version_row = ModelVersion(
        model_name=model_name,
        version=version,
        competition_id=competition_id,
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

    await session.commit()
    await session.refresh(version_row)

    logger.info(
        "created model_version model_name=%s version=%s scope=%s "
        "coefficients=%d active=%s",
        model_name,
        version,
        "global" if competition_id is None else f"competition:{competition_id}",
        len(coefficients),
        set_active,
    )
    return version_row


async def get_active_model_version(
    session: AsyncSession,
    model_name: str,
    competition_id: Optional[int] = None,
) -> ModelVersion | None:
    """Return the currently-active version for ``model_name``, or ``None``.

    FCL-4: ``competition_id=None`` (the default) reads the legacy global
    (NULL-scope) active version only; a competition id reads that
    competition's active version only.  The NULL scope is its own scope —
    the filter is ``competition_id IS NULL``, never ``== None`` — so a
    scoped read returns ``None`` when only a different scope has an active
    version (no cross-scope leakage), and the legacy AFL serving path is
    untouched by league models.
    """
    active_filter = select(ModelVersion).where(
        ModelVersion.model_name == model_name,
        ModelVersion.is_active.is_(True),
    )
    if competition_id is None:
        active_filter = active_filter.where(ModelVersion.competition_id.is_(None))
    else:
        active_filter = active_filter.where(ModelVersion.competition_id == competition_id)
    result = await session.execute(
        active_filter.order_by(ModelVersion.version.desc()).limit(1)
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
    session: AsyncSession,
    model_name: str,
    competition_id: Optional[int] = None,
) -> tuple[float, dict[str, float]] | None:
    """Return ``(intercept, {feature_name: coefficient})`` for the active version.

    Convenience wrapper that reads the active version and its weights in
    one go.  Returns ``None`` when no version is active for ``model_name``
    in the requested scope (FCL-4: ``competition_id=None`` = legacy global
    NULL scope; a scoped read never leaks another scope's weights).
    """
    active = await get_active_model_version(
        session, model_name, competition_id=competition_id
    )
    if active is None:
        return None
    coefficients = await get_model_coefficients(session, active.id)
    return active.intercept, {c.feature_name: c.coefficient for c in coefficients}


async def get_active_model_artifact(
    session: AsyncSession,
    model_name: str,
    competition_id: Optional[int] = None,
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

    FCL-4: the lookup honours ``competition_id`` exactly like
    :func:`get_active_model_version` — ``None`` = legacy global artifact
    only; a scoped lookup never returns another league's artifact.
    """
    active = await get_active_model_version(
        session, model_name, competition_id=competition_id
    )
    if active is None or active.artifact is None:
        return None
    return active.artifact, active.artifact_format or "json", active


async def next_version_number(session: AsyncSession, model_name: str) -> int:
    """Return ``max(version) + 1`` for ``model_name`` (or ``1`` if none exist).

    FCL-4 revision (post-review C-1): the max is taken ACROSS ALL SCOPEs —
    every row with this ``model_name``, global (NULL ``competition_id``)
    and every league alike.  The pre-existing
    ``uq_model_versions_name_version`` constraint keeps
    ``(model_name, version)`` unique across the WHOLE table, so any
    per-scope counter hands out duplicates the moment a second scope
    allocates: the weekly global AFL retrain (NULL-scoped counter) would
    re-emit a version number a league row already owns and crash on
    INSERT — one week after the first league trains, deterministically.
    Cross-scope ``max`` is the only collision-free allocation.

    Active-version promotion is still scoped (see
    :func:`create_model_version`); only the number-plate is shared.
    """
    result = await session.execute(
        select(func.max(ModelVersion.version)).where(
            ModelVersion.model_name == model_name
        )
    )
    max_version = result.scalar()
    return (max_version or 0) + 1
