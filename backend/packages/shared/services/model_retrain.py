"""Weekly retrain service for the ``weighted_tip`` scikit-learn model.

This is the reusable core invoked by the in-process cron job
:class:`app.cron.model_retrain.ModelRetrainJob` (and by the ad-hoc ops
script ``backend/scripts/run_model_retrain.py``).  It:

1. Gathers historical training rows by joining completed games (with final
   scores) to their ``model_predictions`` within a rolling
   :data:`TRAINING_LOOKBACK_SEASONS` window.
2. Builds the canonical 16-feature vector via
   :func:`packages.shared.heuristics.weighted_tip.build_feature_vector` — the
   exact same contract the runtime heuristic uses for prediction.
3. Fits a :class:`sklearn.linear_model.LinearRegression` to predict the
   home-team-signed margin.
4. Persists the intercept + coefficients as a new, *active*
   :class:`~packages.shared.models.ModelVersion` (deactivating priors
   atomically) via :mod:`packages.shared.crud.model_versions`.

When fewer than :data:`MIN_TRAINING_ROWS` usable rows are available the fit is
**skipped** and the currently-active model is left untouched — we never
overwrite a good model with a too-small training set.
"""

from __future__ import annotations

from typing import Dict, List, Tuple

import numpy as np
from sklearn.linear_model import LinearRegression
from sklearn.metrics import mean_absolute_error, r2_score
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..crud.model_versions import create_model_version, next_version_number
from ..heuristics.weighted_tip import FEATURE_NAMES, build_feature_vector
from ..logger import get_logger
from ..models import Game, ModelPrediction

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Module constants
# ---------------------------------------------------------------------------

#: Name under which ``weighted_tip`` versions are stored in ``model_versions``.
WEIGHTED_TIP_MODEL_NAME: str = "weighted_tip"

#: Only use games from the latest N seasons present in the DB.
TRAINING_LOOKBACK_SEASONS: int = 3

#: A game needs at least this many ``model_predictions`` rows to be a usable
#: training example (otherwise the feature vector is too sparse).
MIN_MODELS_PER_GAME: int = 4

#: Don't overwrite the active model with fewer rows than this.
MIN_TRAINING_ROWS: int = 20

#: A training row is ``(feature_vector(16), target_signed_home_margin)``.
TrainingRow = Tuple[List[float], float]


# ---------------------------------------------------------------------------
# Data gathering
# ---------------------------------------------------------------------------


async def _gather_training_rows(session: AsyncSession) -> List[TrainingRow]:
    """Collect ``(features, target)`` training rows from historical games.

    Queries completed games that have final scores, joined to their
    ``model_predictions``, restricted to the latest
    :data:`TRAINING_LOOKBACK_SEASONS` seasons present in the DB.  Each game
    whose prediction count meets :data:`MIN_MODELS_PER_GAME` contributes one
    row: the 16-length feature vector from :func:`build_feature_vector` and the
    signed home margin ``float(home_score - away_score)``.

    Returns an empty list when there are no games at all.  Pure data access —
    reused by tests and by :func:`run_model_retrain`.
    """
    latest_season = (
        await session.execute(select(func.max(Game.season)))
    ).scalar()
    if latest_season is None:
        # No games in the DB at all.
        return []

    min_season = latest_season - (TRAINING_LOOKBACK_SEASONS - 1)

    result = await session.execute(
        select(Game, ModelPrediction)
        .join(ModelPrediction, ModelPrediction.game_id == Game.id)
        .where(Game.completed.is_(True))
        .where(Game.home_score.is_not(None))
        .where(Game.away_score.is_not(None))
        .where(Game.season >= min_season)
        .order_by(Game.id)
    )

    # Group predictions by game, preserving game.id ordering.
    grouped: Dict[int, Tuple[Game, Dict[str, Tuple[str, float, int]]]] = {}
    for game, pred in result.all():
        game_id = game.id
        if game_id not in grouped:
            grouped[game_id] = (game, {})
        grouped[game_id][1][pred.model_name] = (
            pred.winner,
            float(pred.confidence),
            int(pred.margin),
        )

    rows: List[TrainingRow] = []
    for game, preds in grouped.values():
        if len(preds) < MIN_MODELS_PER_GAME:
            continue
        features = build_feature_vector(preds, game.home_team, game.away_team)
        target = float(game.home_score - game.away_score)  # signed home margin
        rows.append((features, target))
    return rows


# ---------------------------------------------------------------------------
# Fit + persist
# ---------------------------------------------------------------------------


async def run_model_retrain(session: AsyncSession) -> Dict[str, object]:
    """Gather rows, fit ``LinearRegression``, persist an active version.

    Args:
        session: Active :class:`AsyncSession`.  Persistence is a single
            transaction handled by :func:`create_model_version`.

    Returns:
        A summary dict.  ``status`` is ``"trained"`` on success (with
        ``model_name``, ``version``, ``model_version_id``, ``training_rows``,
        ``intercept``, ``metrics`` and ``coefficients``) or ``"skipped"`` with
        ``reason == "insufficient_training_rows"`` when too few usable rows
        exist — in which case the currently-active model is left untouched.
    """
    rows = await _gather_training_rows(session)
    logger.info(
        "model-retrain gathered %d usable training rows (min_required=%d)",
        len(rows),
        MIN_TRAINING_ROWS,
    )

    if len(rows) < MIN_TRAINING_ROWS:
        logger.warning(
            "model-retrain skipped: only %d rows (< %d); "
            "keeping currently-active %s model",
            len(rows),
            MIN_TRAINING_ROWS,
            WEIGHTED_TIP_MODEL_NAME,
        )
        return {
            "status": "skipped",
            "reason": "insufficient_training_rows",
            "rows": len(rows),
            "min_required": MIN_TRAINING_ROWS,
        }

    # Build the design matrix from the gathered rows.
    X = np.array([features for features, _ in rows], dtype=float)
    y = np.array([target for _, target in rows], dtype=float)

    # Fit + score.  LinearRegression is deterministic, so two fits on identical
    # data yield identical intercept/coefficients.
    model = LinearRegression()
    model.fit(X, y)
    preds = model.predict(X)
    metrics: Dict[str, float] = {
        "r2": float(r2_score(y, preds)),
        "mae": float(mean_absolute_error(y, preds)),
    }

    intercept = float(model.intercept_)
    coefficients: Dict[str, float] = {
        name: float(coef) for name, coef in zip(FEATURE_NAMES, model.coef_)
    }

    version = await next_version_number(session, WEIGHTED_TIP_MODEL_NAME)
    mv = await create_model_version(
        session,
        model_name=WEIGHTED_TIP_MODEL_NAME,
        version=version,
        intercept=intercept,
        training_rows=len(rows),
        metrics=metrics,
        coefficients=coefficients,
        set_active=True,
    )

    logger.info(
        "model-retrain trained %s version=%d training_rows=%d "
        "r2=%.4f mae=%.4f active=True",
        WEIGHTED_TIP_MODEL_NAME,
        version,
        len(rows),
        metrics["r2"],
        metrics["mae"],
    )

    return {
        "status": "trained",
        "model_name": WEIGHTED_TIP_MODEL_NAME,
        "version": version,
        "model_version_id": mv.id,
        "training_rows": len(rows),
        "intercept": intercept,
        "metrics": metrics,
        "coefficients": coefficients,
    }
