import asyncio
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

from sqlalchemy.ext.asyncio import AsyncSession

from .crud.model_versions import get_active_coefficients
from .heuristics import BaseHeuristic, BestBetHeuristic, WeightedTipHeuristic, YOLOHeuristic
from .logger import get_logger
from .models import Game
from .models_ml import (
    BaseModel,
    EloModel,
    FormModel,
    HomeAdvantageModel,
    InjuryImpactModel,
    MatchupModel,
    PlayerFormModel,
    ValueModel,
    WeatherImpactModel,
)
from .models_ml.prediction import Abstained, is_abstained

logger = get_logger(__name__)

# How long the in-memory weighted-tip coefficient cache is considered
# fresh.  The orchestrator owns the db session (the heuristic's apply()
# does not), so it reloads the active model version's weights on this
# cadence and pushes them into the WeightedTipHeuristic.
WEIGHTED_TIP_COEFFICIENT_TTL_SECONDS = 3600

# A session factory is any callable returning an async context manager
# that yields an AsyncSession (e.g. ``async_sessionmaker``).  Each model
# task gets its own session so the parallel ``asyncio.gather`` never
# shares one AsyncSession (SQLAlchemy forbids concurrent use of a single
# session — see the ORCH-H1 review finding).
SessionFactory = Callable[[], Any]


def _default_session_factory() -> Any:
    """Return an async context manager yielding a fresh ``AsyncSession``.

    B1 regression fix (2026-09 pre-deploy review): this must return the
    *session* (``AsyncSession`` IS an async context manager), not the
    ``async_sessionmaker`` itself — the maker has no
    ``__aenter__``/``__aexit__``, so the original implementation made
    every model raise ``TypeError`` on the default production path,
    silently abstaining 100% of the time.
    """
    from .db import _get_session_factory

    return _get_session_factory()()


class ModelOrchestrator:
    """Orchestrates ML models and heuristic layers for predictions.

    Args:
        session_factory: Optional callable returning an async context
            manager that yields an ``AsyncSession`` (defaults to the
            shared application factory from :mod:`packages.shared.db`).
            Each model task opens its own session — a single
            ``AsyncSession`` must never be shared across concurrent
            tasks.  Tests inject a fake factory here.
    """

    def __init__(self, session_factory: Optional[SessionFactory] = None):
        self._session_factory = session_factory or _default_session_factory

        # Initialize ML models
        self.models: List[BaseModel] = [
            EloModel(),
            FormModel(),
            HomeAdvantageModel(),
            ValueModel(),
            WeatherImpactModel(),
            InjuryImpactModel(),
            MatchupModel(),
            PlayerFormModel(),
        ]

        # Initialize heuristics
        self.heuristics: Dict[str, BaseHeuristic] = {
            "best_bet": BestBetHeuristic(self.models),
            "yolo": YOLOHeuristic(self.models),
            "weighted_tip": WeightedTipHeuristic(self.models),
        }

        # In-memory cache of the active weighted-tip coefficients so we
        # don't hit the DB on every tip.  ``_wt_coeffs`` is the
        # ``(intercept, {feature: coef})`` tuple, or ``None`` when no
        # version is active / not yet loaded.
        self._wt_coeffs: Tuple[float, Dict[str, float]] | None = None
        self._wt_coeffs_loaded_at: float = 0.0

    async def _ensure_weighted_tip_coefficients(self, db) -> None:
        """Refresh the weighted-tip coefficient cache and push it into the heuristic.

        Called at the start of :meth:`predict` / :meth:`predict_all`
        (the orchestrator owns the db session; the heuristic's ``apply``
        does not).  Uses a TTL cache so repeated tip generation within
        ``WEIGHTED_TIP_COEFFICIENT_TTL_SECONDS`` does not re-read the DB.
        When no active version exists the heuristic is switched back to
        its majority-vote fallback.  Any error is logged and swallowed
        so tip generation never crashes because of a model-load failure.
        """
        now = time.monotonic()
        if (
            self._wt_coeffs is not None
            and (now - self._wt_coeffs_loaded_at) < WEIGHTED_TIP_COEFFICIENT_TTL_SECONDS
        ):
            return  # cache still fresh

        try:
            result = await get_active_coefficients(db, "weighted_tip")
        except Exception as e:  # noqa: BLE001 — never crash tip generation
            logger.error(
                "weighted_tip coefficient load failed; staying on fallback: %s",
                e,
                exc_info=True,
            )
            return

        heuristic = self.heuristics["weighted_tip"]
        if result is None:
            heuristic.clear_coefficients()
            self._wt_coeffs = None
        else:
            intercept, coefficients = result
            heuristic.set_coefficients(intercept, coefficients)
            self._wt_coeffs = result
        self._wt_coeffs_loaded_at = now

    async def _predict_one(
        self, model: BaseModel, game: Game, ctx: str
    ) -> Tuple[str, Optional[object]]:
        """Run ONE model in its OWN session.

        Opens a fresh session from the session factory (never shares the
        caller's session across the concurrent ``gather``), times the
        call, and — on failure — logs the error and returns ``None`` for
        the prediction so the model **abstains** rather than voting a
        home-team default (ORCH-M7).

        Returns:
            ``(model_name, result)`` where ``result`` is a
            :class:`Prediction`, the ``ABSTAINED`` singleton (explicit
            "no opinion" — P2-1), or ``None`` (internal failure).
        """
        model_predict_start = time.time()
        try:
            async with self._session_factory() as session:
                result = await model.predict(game, session)
            model_predict_time = time.time() - model_predict_start
            if isinstance(result, Abstained):
                # Explicit abstention (P2-1): the model has no usable
                # opinion — excluded from consensus, not a failure.
                logger.info(
                    f"ModelOrchestrator.{ctx}: {model.get_name()} "
                    f"abstained (no usable opinion)"
                )
            else:
                logger.debug(
                    f"ModelOrchestrator.{ctx}: {model.get_name()} "
                    f"model took {model_predict_time:.4f}s"
                )
            return model.get_name(), result
        except Exception as e:
            model_predict_time = time.time() - model_predict_start
            logger.error(
                f"ModelOrchestrator.{ctx}: {model.get_name()} "
                f"model failed after {model_predict_time:.4f}s: {e}"
            )
            # Abstain: exclude the model from consensus instead of
            # substituting a home-team default.
            return model.get_name(), None

    async def _gather_model_predictions(
        self, game: Game, ctx: str
    ) -> Tuple[Dict[str, object], List[str]]:
        """Run all models concurrently, each in its own session.

        Returns:
            ``(model_predictions, failed_models)`` — the predictions dict
            contains only models that produced a :class:`Prediction`;
            explicit ``ABSTAINED`` results are excluded but NOT failures;
            ``failed_models`` lists the names of models that raised.
        """
        model_start = time.time()

        tasks = [self._predict_one(model, game, ctx) for model in self.models]
        results = await asyncio.gather(*tasks)

        model_predictions: Dict[str, object] = {}
        failed_models: List[str] = []
        abstained_models: List[str] = []
        for model_name, prediction in results:
            if isinstance(prediction, Abstained):
                abstained_models.append(model_name)
            elif prediction is None:
                failed_models.append(model_name)
            else:
                model_predictions[model_name] = prediction

        model_total_time = time.time() - model_start
        logger.debug(f"ModelOrchestrator.{ctx}: ALL MODELS took {model_total_time:.4f}s")

        if failed_models:
            logger.warning(
                f"ModelOrchestrator.{ctx}: {len(failed_models)}/{len(self.models)} "
                f"models failed and abstained: {sorted(failed_models)}"
            )
        if abstained_models:
            logger.info(
                f"ModelOrchestrator.{ctx}: {len(abstained_models)}/{len(self.models)} "
                f"models abstained (no usable opinion): {sorted(abstained_models)}"
            )

        return model_predictions, failed_models

    async def predict(
        self, game: Game, heuristic: str = "best_bet", db: AsyncSession = None
    ) -> Tuple[str, float, int]:
        """Generate a prediction for a game using specified heuristic.

        Args:
            game: Game to predict
            heuristic: Heuristic to apply (best_bet, yolo, weighted_tip)
            db: Database session used for heuristic-support queries
                (e.g. weighted-tip coefficient loading).  Model tasks
                open their own sessions.

        Returns:
            Tuple of (winner, confidence, margin)
        """
        # Load the active weighted-tip coefficients (cached) before
        # applying any heuristic.  Harmless for non-weighted_tip heuristics.
        await self._ensure_weighted_tip_coefficients(db)

        start_time = time.time()
        logger.debug(
            f"ModelOrchestrator.predict: STARTING for game {game.id} with heuristic '{heuristic}'"
        )

        if heuristic not in self.heuristics:
            raise ValueError(f"Unknown heuristic: {heuristic}")

        # Get predictions from all models in parallel (session per task,
        # failed models abstain).
        model_predictions, failed_models = await self._gather_model_predictions(
            game, ctx="predict"
        )

        # Apply heuristic
        heuristic_obj = self.heuristics[heuristic]
        heuristic_start = time.time()
        result = await heuristic_obj.apply(game, model_predictions)
        heuristic_time = time.time() - heuristic_start

        total_time = time.time() - start_time
        logger.debug(
            f"ModelOrchestrator.predict: COMPLETED in "
            f"{total_time:.4f}s (heuristic: {heuristic_time:.4f}s)"
        )

        return result

    async def predict_all(self, game: Game, db: AsyncSession = None) -> Dict[str, Dict[str, Any]]:
        """Generate predictions for all heuristics.

        Runs all models ONCE, then applies all heuristics to the same
        model predictions, avoiding redundant model computation.

        Args:
            game: Game to predict
            db: Database session used for heuristic-support queries
                (e.g. weighted-tip coefficient loading).  Model tasks
                open their own sessions.

        Returns:
            Dict of heuristic -> {"model_predictions": dict, "tip": tuple,
            "failed_models": list[str]}
        """
        # Load the active weighted-tip coefficients (cached) before
        # applying any heuristic.
        await self._ensure_weighted_tip_coefficients(db)

        start_time = time.time()
        logger.debug(f"ModelOrchestrator.predict_all: STARTING for game {game.id}")

        # Run all models once in parallel (session per task, abstain on failure).
        model_predictions, failed_models = await self._gather_model_predictions(
            game, ctx="predict_all"
        )

        # Apply all heuristics to the same model predictions
        all_results = {}
        for heuristic_name, heuristic_obj in self.heuristics.items():
            heuristic_start = time.time()
            tip = await heuristic_obj.apply(game, model_predictions)
            heuristic_time = time.time() - heuristic_start
            logger.debug(
                f"ModelOrchestrator.predict_all: heuristic "
                f"'{heuristic_name}' took {heuristic_time:.4f}s"
            )
            all_results[heuristic_name] = {
                "model_predictions": model_predictions,
                "tip": tip,
                "failed_models": failed_models,
            }

        total_time = time.time() - start_time
        logger.debug(f"ModelOrchestrator.predict_all: COMPLETED in {total_time:.4f}s")

        return all_results

    def get_available_heuristics(self) -> List[str]:
        """Get list of available heuristics."""
        return list(self.heuristics.keys())
