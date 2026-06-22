import asyncio
import time
from typing import Any, Dict, List, Tuple

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

logger = get_logger(__name__)

# How long the in-memory weighted-tip coefficient cache is considered
# fresh.  The orchestrator owns the db session (the heuristic's apply()
# does not), so it reloads the active model version's weights on this
# cadence and pushes them into the WeightedTipHeuristic.
WEIGHTED_TIP_COEFFICIENT_TTL_SECONDS = 3600


class ModelOrchestrator:
    """Orchestrates ML models and heuristic layers for predictions."""

    def __init__(self):
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

    async def predict(
        self, game: Game, heuristic: str = "best_bet", db: AsyncSession = None
    ) -> Tuple[str, float, int]:
        """Generate a prediction for a game using specified heuristic.

        Args:
            game: Game to predict
            heuristic: Heuristic to apply (best_bet, yolo, weighted_tip)
            db: Database session to use for queries

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

        # Get predictions from all models in parallel
        model_predictions: Dict[str, Tuple[str, float, int]] = {}
        model_start = time.time()

        async def predict_with_logging(model: BaseModel) -> Tuple[str, Tuple[str, float, int]]:
            """Predict with error handling and timing."""
            model_predict_start = time.time()
            try:
                result = await model.predict(game, db)
                model_predict_time = time.time() - model_predict_start
                logger.debug(
                    f"ModelOrchestrator.predict: {model.get_name()} "
                    f"model took {model_predict_time:.4f}s"
                )
                return model.get_name(), result
            except Exception as e:
                model_predict_time = time.time() - model_predict_start
                logger.error(
                    f"ModelOrchestrator.predict: {model.get_name()} "
                    f"model failed after {model_predict_time:.4f}s: {e}"
                )
                # Return a default prediction on error
                return model.get_name(), (str(game.home_team), 0.5, 0)

        # Run all models in parallel
        tasks = [predict_with_logging(model) for model in self.models]
        results = await asyncio.gather(*tasks)

        # Build predictions dictionary
        for model_name, prediction in results:
            model_predictions[model_name] = prediction

        model_total_time = time.time() - model_start
        logger.debug(f"ModelOrchestrator.predict: ALL MODELS took {model_total_time:.4f}s")

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
            db: Database session to use for queries

        Returns:
            Dict of heuristic -> {"model_predictions": dict, "tip": tuple}
        """
        # Load the active weighted-tip coefficients (cached) before
        # applying any heuristic.
        await self._ensure_weighted_tip_coefficients(db)

        start_time = time.time()
        logger.debug(f"ModelOrchestrator.predict_all: STARTING for game {game.id}")

        # Run all models once in parallel
        model_predictions: Dict[str, Tuple[str, float, int]] = {}
        model_start = time.time()

        async def predict_with_logging(model: BaseModel) -> Tuple[str, Tuple[str, float, int]]:
            """Predict with error handling and timing."""
            model_predict_start = time.time()
            try:
                result = await model.predict(game, db)
                model_predict_time = time.time() - model_predict_start
                logger.debug(
                    f"ModelOrchestrator.predict_all: {model.get_name()} "
                    f"model took {model_predict_time:.4f}s"
                )
                return model.get_name(), result
            except Exception as e:
                model_predict_time = time.time() - model_predict_start
                logger.error(
                    f"ModelOrchestrator.predict_all: {model.get_name()} "
                    f"model failed after {model_predict_time:.4f}s: {e}"
                )
                return model.get_name(), (str(game.home_team), 0.5, 0)

        tasks = [predict_with_logging(model) for model in self.models]
        results = await asyncio.gather(*tasks)

        for model_name, prediction in results:
            model_predictions[model_name] = prediction

        model_total_time = time.time() - model_start
        logger.debug(f"ModelOrchestrator.predict_all: ALL MODELS took {model_total_time:.4f}s")

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
            all_results[heuristic_name] = {"model_predictions": model_predictions, "tip": tip}

        total_time = time.time() - start_time
        logger.debug(f"ModelOrchestrator.predict_all: COMPLETED in {total_time:.4f}s")

        return all_results

    def get_available_heuristics(self) -> List[str]:
        """Get list of available heuristics."""
        return list(self.heuristics.keys())
