from datetime import datetime
from typing import Dict, List, Optional

from sqlalchemy import and_, case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..crud import ModelPredictionCRUD
from ..crud.game_odds import DEFAULT_SOURCE
from ..logger import get_logger
from ..models import Game, GameOdds, ModelPrediction, Tip
from ..orchestrator import ModelOrchestrator
from ..schemas.backtest import (
    CurrentSeasonHeuristicPerformance,
    CurrentSeasonResponse,
)
from .settlement import (
    FALLBACK_DECIMAL_ODDS,
    settle_stake,
    tipped_side_price,
)
from .settlement import (
    # Explicit re-export (PEP 484): callers historically imported
    # STAKE_PER_GAME from this module.
    STAKE_PER_GAME as STAKE_PER_GAME,
)

logger = get_logger(__name__)

# Sensible fixture-size fallback when the current season has no games
# loaded yet (BT-ROUND: real seasons range 23–25 rounds; the projection
# denominator prefers the actual fixture count).
DEFAULT_TOTAL_ROUNDS = 24


def _odds_join_condition():
    """SQL join condition for the odds snapshot of a game.

    m-4 (code review): uniqueness on ``game_odds`` is ``(game_id,
    source)`` — the schema anticipates a second source.  Pinning the
    join to the canonical source keeps a future source from
    double-counting every tip (profit, accuracy, and coverage).
    """
    return and_(GameOdds.game_id == Game.id, GameOdds.source == DEFAULT_SOURCE)


def _sql_side_price(side_odds_column):
    """Usable decimal price for one side: the snapshot price when it is
    present and > 1.0, the representative fallback otherwise (m-1 — the
    SQL paths must enforce the same corrupt-price guard as the Python
    settlement kernel)."""
    return case(
        (side_odds_column > 1.0, side_odds_column),
        else_=FALLBACK_DECIMAL_ODDS,
    )


def _tipped_price_expression(selected_expr, home_expr, away_expr):
    """SQL decimal price for the tipped side, mirroring the Python
    :func:`tipped_side_price` fallback behaviour."""
    return case(
        (selected_expr == home_expr, _sql_side_price(GameOdds.home_odds)),
        (selected_expr == away_expr, _sql_side_price(GameOdds.away_odds)),
        else_=FALLBACK_DECIMAL_ODDS,
    )


def _tipped_coverage_expression(selected_expr, home_expr, away_expr):
    """SQL ``1/0`` flag for "settled at a usable real price".

    m-2 (code review): counts the tip as covered only when the *tipped
    side* has a usable price (> 1.0) — the same semantics as the Python
    paths, not merely "a snapshot row exists".
    """
    return case(
        (
            selected_expr == home_expr,
            case(
                (and_(GameOdds.home_odds.isnot(None), GameOdds.home_odds > 1.0), 1),
                else_=0,
            ),
        ),
        (
            selected_expr == away_expr,
            case(
                (and_(GameOdds.away_odds.isnot(None), GameOdds.away_odds > 1.0), 1),
                else_=0,
            ),
        ),
        else_=0,
    )


def actual_winner_name(game) -> Optional[str]:
    """Name of the side that won a completed game, or ``None`` on a draw.

    A drawn game has no winner, so a tip on either side is incorrect.
    (The previous ``home > away else away`` form credited the AWAY team
    with drawn games — P0-5.)
    """
    if game.home_score is not None and game.away_score is not None:
        if game.home_score > game.away_score:
            return game.home_team
        if game.away_score > game.home_score:
            return game.away_team
    return None


def actual_winner_case() -> "case":
    """SQL ``case`` resolving a game's actual winner name — ``NULL`` on draws.

    Because ``name = NULL`` is never true, tips on drawn games fall
    through to the incorrect/profit-losing branch of every scoring
    ``case`` that uses this helper.
    """
    return case(
        (Game.home_score > Game.away_score, Game.home_team),
        (Game.away_score > Game.home_score, Game.away_team),
    )


class BacktestService:
    """Service for backtesting heuristic performance."""

    def __init__(self):
        self.orchestrator = ModelOrchestrator()

    async def get_available_seasons(self, db: AsyncSession) -> List[int]:
        """Get list of seasons that have tips for completed games.

        Args:
            db: Database session

        Returns:
            List of season years (descending order)
        """
        # Get distinct seasons from games that have tips and are completed
        result = await db.execute(
            select(Game.season)
            .join(Tip, Game.id == Tip.game_id)
            .where(Game.completed)
            .distinct()
            .order_by(Game.season.desc())
        )
        return [row[0] for row in result.all()]

    async def calculate_backtest_from_tips(
        self,
        db: AsyncSession,
        season: int,
        heuristic: str,
    ) -> Dict[str, float]:
        """Calculate backtest metrics for a season/heuristic from tips.

        Args:
            db: Database session
            season: Season year
            heuristic: Heuristic name

        Returns:
            Dict with backtest metrics
        """
        # Get tips for this heuristic in this season, with any odds
        # snapshot for the game (BT-ODDS: settle at real prices where
        # available, representative fallback otherwise).
        result = await db.execute(
            select(Tip, Game, GameOdds)
            .join(Game, Tip.game_id == Game.id)
            .outerjoin(GameOdds, _odds_join_condition())
            .where(
                and_(
                    Game.season == season,
                    Tip.heuristic == heuristic,
                    Game.completed,
                    Game.home_score.isnot(None),
                    Game.away_score.isnot(None),
                )
            )
        )
        tip_rows = result.all()

        if not tip_rows:
            return {
                "total_rounds": 0,
                "total_tips": 0,
                "total_correct": 0,
                "overall_accuracy": 0.0,
                "total_profit": 0.0,
                "avg_profit_per_round": 0.0,
                "best_round_accuracy": 0.0,
                "worst_round_accuracy": 0.0,
                "odds_coverage": 0.0,
            }

        tips_made = 0
        tips_correct = 0
        profit = 0.0
        real_odds_tips = 0

        for tip, game, odds in tip_rows:
            tips_made += 1

            # Determine actual winner from the game object (None on a draw)
            winner_name = actual_winner_name(game)
            is_draw = winner_name is None
            is_correct = tip.selected_team == winner_name
            if is_correct:
                tips_correct += 1

            # Decimal price for the tipped side from the snapshot, if any
            price = tipped_side_price(
                selected_team=tip.selected_team,
                home_team=game.home_team,
                away_team=game.away_team,
                home_odds=odds.home_odds if odds is not None else None,
                away_odds=odds.away_odds if odds is not None else None,
            )
            if price is not None:
                real_odds_tips += 1

            # BT-ODDS: real price where available, representative fallback
            # otherwise; a drawn game pushes (stake refunded).
            profit += settle_stake(is_correct=is_correct, is_draw=is_draw, decimal_odds=price)

        # Calculate metrics
        accuracy = tips_correct / tips_made if tips_made > 0 else 0.0

        # Get round-level accuracy for best/worst rounds
        round_accuracies = await self._get_round_accuracies(db, season, heuristic)

        return {
            "total_rounds": len(round_accuracies),
            "total_tips": tips_made,
            "total_correct": tips_correct,
            "overall_accuracy": accuracy,
            "total_profit": profit,
            "avg_profit_per_round": profit / len(round_accuracies) if round_accuracies else 0.0,
            "best_round_accuracy": max(round_accuracies) if round_accuracies else 0.0,
            "worst_round_accuracy": min(round_accuracies) if round_accuracies else 0.0,
            "odds_coverage": real_odds_tips / tips_made if tips_made > 0 else 0.0,
        }

    async def _get_round_accuracies(
        self,
        db: AsyncSession,
        season: int,
        heuristic: str,
    ) -> List[float]:
        """Get accuracy for each round in a season.

        Args:
            db: Database session
            season: Season year
            heuristic: Heuristic name

        Returns:
            List of accuracy values per round
        """
        # Calculate accuracy per round using case statement
        result = await db.execute(
            select(
                Game.round_id,
                func.count(Tip.id).label("total_tips"),
                func.sum(case((Tip.selected_team == actual_winner_case(), 1), else_=0)).label(
                    "correct_tips"
                ),
            )
            .join(Tip, Tip.game_id == Game.id)
            .where(
                and_(
                    Game.season == season,
                    Tip.heuristic == heuristic,
                    Game.completed,
                    Game.home_score.isnot(None),
                    Game.away_score.isnot(None),
                )
            )
            .group_by(Game.round_id)
            .order_by(Game.round_id)
        )

        accuracies = []
        for round_id, total_tips, correct_tips in result.all():
            if total_tips > 0:
                accuracies.append(correct_tips / total_tips)

        return accuracies

    async def get_round_by_round_data(
        self,
        db: AsyncSession,
        season: int,
        heuristic: str,
    ) -> List[Dict]:
        """Get round-by-round backtest data for a season/heuristic.

        BT-ODDS: profit settles at real decimal odds where a
        ``game_odds`` snapshot exists (representative fallback price
        otherwise) and drawn games push ($0).  Each round also reports
        ``odds_coverage`` — the share of tips settled at real prices.
        """
        draw_expr = Game.home_score == Game.away_score
        correct_expr = Tip.selected_team == actual_winner_case()
        # m-1/m-2 (code review): the SQL paths share the same >1.0 price
        # guard and tipped-side coverage semantics as the Python kernel.
        tipped_price = _tipped_price_expression(Tip.selected_team, Game.home_team, Game.away_team)
        covered = _tipped_coverage_expression(Tip.selected_team, Game.home_team, Game.away_team)

        result = await db.execute(
            select(
                Game.round_id,
                func.count(Tip.id).label("tips_made"),
                func.sum(case((correct_expr, 1), else_=0)).label("tips_correct"),
                func.sum(
                    case(
                        (draw_expr, 0.0),
                        (correct_expr, STAKE_PER_GAME * (tipped_price - 1.0)),
                        else_=-STAKE_PER_GAME,
                    )
                ).label("profit"),
                func.sum(covered).label("real_odds_tips"),
            )
            .join(Tip, Tip.game_id == Game.id)
            .outerjoin(GameOdds, _odds_join_condition())
            .where(
                and_(
                    Game.season == season,
                    Tip.heuristic == heuristic,
                    Game.completed,
                    Game.home_score.isnot(None),
                    Game.away_score.isnot(None),
                )
            )
            .group_by(Game.round_id)
            .order_by(Game.round_id)
        )

        round_data = []
        for round_id, tips_made, tips_correct, profit, real_odds_tips in result.all():
            accuracy = tips_correct / tips_made if tips_made > 0 else 0.0
            round_data.append(
                {
                    "round_id": round_id,
                    "tips_made": tips_made,
                    "tips_correct": tips_correct,
                    "accuracy": accuracy,
                    "profit": profit,
                    "odds_coverage": (real_odds_tips / tips_made) if tips_made > 0 else 0.0,
                }
            )

        return round_data

    async def compare_heuristics(
        self,
        db: AsyncSession,
        season: int,
    ) -> Dict[str, Dict[str, float]]:
        """Compare all heuristics for a season by calculating from tips.

        Args:
            db: Database session
            season: Season year

        Returns:
            Dict of heuristic -> summary statistics
        """
        comparison = {}

        for heuristic in self.orchestrator.get_available_heuristics():
            comparison[heuristic] = await self.calculate_backtest_from_tips(db, season, heuristic)

        return comparison

    async def get_current_season_performance(
        self,
        db: AsyncSession,
    ) -> CurrentSeasonResponse:
        """Get year-to-date performance for the current season with projections.

        BT-ROUND fixes (2026-10 review):

        * ``total_rounds`` derives from the season's fixture (distinct
          ``round_id`` values) instead of a hard-coded 24.
        * ``rounds_completed`` counts rounds where **every** game is
          completed — previously any round with a game whose kick-off had
          passed counted as "completed", mid-round (the old kick-off
          comparison also mixed server-local time with the UTC feed
          dates).

        Args:
            db: Database session

        Returns:
            CurrentSeasonResponse with YTD performance and projections
        """
        current_year = datetime.now().year

        # BT-ROUND: total rounds in this season's fixture.
        fixture_rounds_result = await db.execute(
            select(func.count(func.distinct(Game.round_id))).where(
                and_(Game.season == current_year, Game.round_id.isnot(None))
            )
        )
        total_rounds = fixture_rounds_result.scalar() or 0
        if total_rounds == 0:
            total_rounds = DEFAULT_TOTAL_ROUNDS

        # BT-ROUND: rounds where EVERY game is completed.
        rounds_completed_result = await db.execute(
            select(func.count()).select_from(
                select(Game.round_id)
                .where(and_(Game.season == current_year, Game.round_id.isnot(None)))
                .group_by(Game.round_id)
                .having(func.bool_and(Game.completed).is_(True))
                .subquery()
            )
        )
        rounds_completed = rounds_completed_result.scalar() or 0

        # Calculate performance for each heuristic
        heuristic_performances = []
        for heuristic in self.orchestrator.get_available_heuristics():
            stats = await self.calculate_backtest_from_tips(db, current_year, heuristic)

            total_profit = stats["total_profit"]
            total_accuracy = stats["overall_accuracy"]
            rounds_played = int(stats["total_rounds"])

            avg_profit_per_round = total_profit / rounds_played if rounds_played > 0 else 0.0

            # Calculate projected annual profit (linear pace — the card's
            # disclaimer covers the early-season volatility caveat).
            projected_annual_profit = avg_profit_per_round * total_rounds

            heuristic_performances.append(
                CurrentSeasonHeuristicPerformance(
                    heuristic=heuristic,
                    total_profit=total_profit,
                    total_accuracy=total_accuracy,
                    rounds_played=rounds_played,
                    avg_profit_per_round=avg_profit_per_round,
                    projected_annual_profit=projected_annual_profit,
                    odds_coverage=stats["odds_coverage"],
                )
            )

        return CurrentSeasonResponse(
            season=current_year,
            heuristics=heuristic_performances,
            rounds_completed=rounds_completed,
            total_rounds=total_rounds,
        )

    # -----------------------------------------------------------------------
    # Model-level backtest methods
    # -----------------------------------------------------------------------

    async def calculate_backtest_from_model_predictions(
        self,
        db: AsyncSession,
        season: int,
        model_name: str,
    ) -> Dict[str, float]:
        """Calculate backtest metrics for a season/model from model predictions.

        Args:
            db: Database session
            season: Season year
            model_name: Model name (e.g. "elo", "form")

        Returns:
            Dict with backtest metrics
        """
        # Get predictions for this model in this season, with any odds
        # snapshot for the game (BT-ODDS).
        result = await db.execute(
            select(ModelPrediction, Game, GameOdds)
            .join(Game, ModelPrediction.game_id == Game.id)
            .outerjoin(GameOdds, _odds_join_condition())
            .where(
                and_(
                    Game.season == season,
                    ModelPrediction.model_name == model_name,
                    Game.completed,
                    Game.home_score.isnot(None),
                    Game.away_score.isnot(None),
                )
            )
        )
        prediction_rows = result.all()

        if not prediction_rows:
            return {
                "model_name": model_name,
                "season": season,
                "total_tips": 0,
                "total_correct": 0,
                "overall_accuracy": 0.0,
                "total_profit": 0.0,
                "avg_margin": 0.0,
                "odds_coverage": 0.0,
            }

        tips_made = 0
        tips_correct = 0
        profit = 0.0
        total_margin = 0
        real_odds_tips = 0

        for prediction, game, odds in prediction_rows:
            tips_made += 1
            total_margin += abs(prediction.margin or 0)

            # Determine actual winner from the game object (None on a draw)
            winner_name = actual_winner_name(game)
            is_draw = winner_name is None
            is_correct = prediction.winner == winner_name
            if is_correct:
                tips_correct += 1

            price = tipped_side_price(
                selected_team=prediction.winner,
                home_team=game.home_team,
                away_team=game.away_team,
                home_odds=odds.home_odds if odds is not None else None,
                away_odds=odds.away_odds if odds is not None else None,
            )
            if price is not None:
                real_odds_tips += 1

            # BT-ODDS: real price where available, representative fallback
            # otherwise; a drawn game pushes (stake refunded).
            profit += settle_stake(is_correct=is_correct, is_draw=is_draw, decimal_odds=price)

        # Calculate metrics
        accuracy = tips_correct / tips_made if tips_made > 0 else 0.0
        avg_margin = total_margin / tips_made if tips_made > 0 else 0.0

        return {
            "model_name": model_name,
            "season": season,
            "total_tips": tips_made,
            "total_correct": tips_correct,
            "overall_accuracy": accuracy,
            "total_profit": profit,
            "avg_margin": avg_margin,
            "odds_coverage": real_odds_tips / tips_made if tips_made > 0 else 0.0,
        }

    async def compare_models(
        self,
        db: AsyncSession,
        season: int,
    ) -> List[Dict]:
        """Compare all models for a season by calculating from model predictions.

        Args:
            db: Database session
            season: Season year

        Returns:
            List of result dicts sorted by accuracy descending
        """
        # Get all distinct model names
        result = await db.execute(select(ModelPrediction.model_name).distinct())
        model_names = [row[0] for row in result.all()]

        comparison = []
        for model_name in model_names:
            metrics = await self.calculate_backtest_from_model_predictions(db, season, model_name)
            comparison.append(metrics)

        # Sort by accuracy descending
        comparison.sort(key=lambda x: x["overall_accuracy"], reverse=True)

        return comparison

    async def get_model_round_by_round(
        self,
        db: AsyncSession,
        season: int,
        model_name: str,
    ) -> List[Dict]:
        """Get round-by-round backtest data for a season/model.

        BT-ODDS: profit settles at real decimal odds where a
        ``game_odds`` snapshot exists (representative fallback price
        otherwise) and drawn games push ($0).
        """
        draw_expr = Game.home_score == Game.away_score
        correct_expr = ModelPrediction.winner == actual_winner_case()
        # m-1/m-2 (code review): same shared guard/coverage helpers as
        # the heuristic SQL path — no drift between the two endpoints.
        tipped_price = _tipped_price_expression(
            ModelPrediction.winner, Game.home_team, Game.away_team
        )
        covered = _tipped_coverage_expression(
            ModelPrediction.winner, Game.home_team, Game.away_team
        )

        result = await db.execute(
            select(
                Game.round_id,
                func.count(ModelPrediction.id).label("tips_made"),
                func.sum(case((correct_expr, 1), else_=0)).label("tips_correct"),
                func.sum(
                    case(
                        (draw_expr, 0.0),
                        (correct_expr, STAKE_PER_GAME * (tipped_price - 1.0)),
                        else_=-STAKE_PER_GAME,
                    )
                ).label("profit"),
                func.sum(covered).label("real_odds_tips"),
            )
            .join(ModelPrediction, ModelPrediction.game_id == Game.id)
            .outerjoin(GameOdds, _odds_join_condition())
            .where(
                and_(
                    Game.season == season,
                    ModelPrediction.model_name == model_name,
                    Game.completed,
                    Game.home_score.isnot(None),
                    Game.away_score.isnot(None),
                )
            )
            .group_by(Game.round_id)
            .order_by(Game.round_id)
        )

        round_data = []
        for round_id, tips_made, tips_correct, profit, real_odds_tips in result.all():
            accuracy = tips_correct / tips_made if tips_made > 0 else 0.0
            round_data.append(
                {
                    "round_id": round_id,
                    "tips_made": tips_made,
                    "tips_correct": tips_correct,
                    "accuracy": accuracy,
                    "profit": profit,
                    "odds_coverage": (real_odds_tips / tips_made) if tips_made > 0 else 0.0,
                }
            )

        return round_data

    async def get_active_weighted_model(
        self,
        db: AsyncSession,
    ) -> dict | None:
        """Return the currently-active ``weighted_tip`` model version + coefficients.

        Enriches each coefficient with a ``model`` (the base model name) and
        ``type`` (``"margin"`` or ``"confidence"``) derived from the feature
        name convention established in :mod:`heuristics.weighted_tip`.

        Returns ``None`` when no active version exists (e.g. before the first
        weekly retrain has run).
        """
        from ..crud.model_versions import (
            get_active_model_version,
            get_model_coefficients,
        )

        model_version = await get_active_model_version(db, "weighted_tip")
        if model_version is None:
            return None

        coefficient_rows = await get_model_coefficients(db, model_version.id)

        coefficients = []
        for row in coefficient_rows:
            # Derive model + type from the feature name.
            # Convention: "{model_name}_margin_home" or "{model_name}_conf"
            fname = row.feature_name
            if fname.endswith("_margin_home"):
                model_name = fname[: -len("_margin_home")]
                ctype = "margin"
            elif fname.endswith("_conf"):
                model_name = fname[: -len("_conf")]
                ctype = "confidence"
            else:
                model_name = fname
                ctype = "other"

            coefficients.append(
                {
                    "feature_name": fname,
                    "coefficient": row.coefficient,
                    "model": model_name,
                    "type": ctype,
                }
            )

        return {
            "model_name": "weighted_tip",
            "version": model_version.version,
            "trained_at": model_version.trained_at.isoformat()
            if model_version.trained_at
            else None,
            "training_rows": model_version.training_rows,
            "intercept": model_version.intercept,
            "metrics": model_version.metrics or {},
            "coefficients": coefficients,
        }

    async def run_model_backtest(
        self,
        db: AsyncSession,
        season: int,
    ) -> List[Dict]:
        """Run full model backtest: generate missing predictions, then compare.

        For each completed game in the season, checks which models don't have
        predictions yet and generates only those missing predictions.
        Then returns comparison data.

        Args:
            db: Database session
            season: Season year

        Returns:
            List of model comparison dicts sorted by accuracy descending
        """
        # Get all completed games for this season with scores
        games_result = await db.execute(
            select(Game).where(
                and_(
                    Game.season == season,
                    Game.completed,
                    Game.home_score.isnot(None),
                    Game.away_score.isnot(None),
                )
            )
        )
        games = list(games_result.scalars().all())

        if games:
            game_ids = [g.id for g in games]

            # Get existing (game_id, model_name) pairs so we know exactly
            # which models already have predictions for each game
            existing_result = await db.execute(
                select(ModelPrediction.game_id, ModelPrediction.model_name)
                .where(ModelPrediction.game_id.in_(game_ids))
                .distinct()
            )
            existing_predictions = {(row[0], row[1]) for row in existing_result.all()}

            # Get all models from orchestrator
            models = self.orchestrator.models

            for game in games:
                for model in models:
                    # Skip if this specific game+model prediction already exists
                    if (game.id, model.get_name()) in existing_predictions:
                        continue
                    try:
                        winner, confidence, margin = await model.predict(game, db)
                        await ModelPredictionCRUD.create(
                            db=db,
                            game_id=game.id,
                            model_name=model.get_name(),
                            winner=winner,
                            confidence=confidence,
                            margin=margin,
                        )
                    except Exception as exc:  # noqa: BLE001 - best-effort per game
                        # Skip failed predictions rather than aborting
                        # the whole backtest, but log the failure (ME-006)
                        # so silent regressions are no longer possible.
                        logger.exception(
                            "Model %s failed for game %s: %s",
                            model.get_name(),
                            getattr(game, "id", "<unknown>"),
                            exc,
                        )

        # Return comparison results
        return await self.compare_models(db, season)
