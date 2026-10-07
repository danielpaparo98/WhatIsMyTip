"""Walk-forward backfill of ``boosted_tip`` tips (BT-1).

The boosted model was trained after the 2026 season completed, so its
Current-Season card reads zeros: no completed game has a ``boosted_tip``
tip.  This service backfills those tips HONESTLY, mirroring production's
weekly-cutoff semantics: for each completed round, train the boosted
model ONLY on completed games strictly before that round (within the
same 3-season lookback window the weekly cron uses), tip the round from
the stored 8-model ``model_predictions``, then add the round's games to
the training pool and move on.

* Below ``MIN_TRAINING_ROWS`` a round falls back to the shared majority
  vote (:func:`heuristics.weighted_tip.weighted_tip_fallback`) — exactly
  the runtime heuristic's pre-first-retrain cold-start behaviour, so the
  backfilled history shows the model's genuine early-season honesty.
* Games with no stored ``model_predictions`` are skipped (never tipped,
  never trained on) — fabricating a feature vector from nothing would
  misrepresent what the heuristic could have known.
* Structure follows the repo convention: a PURE compute core
  (:func:`compute_walkforward_plans`, fully unit-testable without a
  database) plus a thin async I/O wrapper
  (:func:`run_boosted_walkforward_backfill`) that fetches, persists and
  summarises.  ``dry_run=True`` computes the full plan and returns the
  per-round preview without writing a single row.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Sequence, Tuple

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..heuristics.weighted_tip import (
    build_feature_vector,
    home_margin_to_tip,
    weighted_tip_fallback,
)
from ..logger import get_logger
from ..models import Game, ModelPrediction, Tip
from ..models_ml.prediction import Prediction

logger = get_logger(__name__)

#: Name under which boosted-tip tips/versions are stored (BT-1).
BOOSTED_TIP_MODEL_NAME: str = "boosted_tip"

#: Only completed games with at least this many stored model predictions
#: may train or be tipped (mirrors ``services/model_retrain.py``).
MIN_MODELS_PER_GAME: int = 4

#: Minimum training rows before a round is tipped by a real fit (below
#: it the round falls back to majority vote — cold-start honesty).
MIN_TRAINING_ROWS: int = 100

#: Same rolling window the weekly cron trains on.
TRAINING_LOOKBACK_SEASONS: int = 3

#: Baseline boosted params — mirrors ``services/boosted_retrain.py``.
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
# Pure compute core
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class GameRecord:
    """One completed game with its stored 8-model predictions."""

    game_id: int
    season: int
    round_id: int
    home_team: str
    away_team: str
    home_score: int
    away_score: int
    preds: Dict[str, Prediction]


@dataclass(frozen=True)
class GameTip:
    """The boosted tip for one game (mapped through the shared contract)."""

    game_id: int
    prediction: Prediction


@dataclass(frozen=True)
class RoundPlan:
    """The walk-forward plan for one round: mode, pool size, and tips."""

    season: int
    round_id: int
    mode: str  # "trained" | "fallback"
    training_rows: int
    tips: List[GameTip] = field(default_factory=list)
    n_skipped_no_preds: int = 0
    n_correct: int = 0

    @property
    def n_tipped(self) -> int:
        return len(self.tips)


def _actual_winner(record: GameRecord) -> str | None:
    """Home/away/None(draw) from final scores."""
    if record.home_score > record.away_score:
        return record.home_team
    if record.away_score > record.home_score:
        return record.away_team
    return None


def compute_walkforward_plans(
    games: Sequence[GameRecord],
    *,
    min_training_rows: int = MIN_TRAINING_ROWS,
    lookback_seasons: int = TRAINING_LOOKBACK_SEASONS,
    params: Dict[str, Any] | None = None,
) -> List[RoundPlan]:
    """Compute per-round boosted tips with strictly-prior training pools.

    Pure and side-effect-free.  Games are processed in ascending
    ``(season, round_id, game_id)``; each round's training pool is every
    prior game with ``>= MIN_MODELS_PER_GAME`` predictions inside the
    ``lookback_seasons`` window.  Deterministic: fixed seed + fixed
    params (see ``BOOSTED_PARAMS``).
    """
    used_params = dict(params) if params is not None else dict(BOOSTED_PARAMS)
    ordered = sorted(games, key=lambda g: (g.season, g.round_id, g.game_id))

    # Training pool: (season, features, target) — grows as rounds complete.
    pool: List[Tuple[int, List[float], float]] = []

    plans: List[RoundPlan] = []
    for round_key in dict.fromkeys((g.season, g.round_id) for g in ordered):
        season, round_id = round_key
        round_games = [g for g in ordered if (g.season, g.round_id) == round_key]
        window_min_season = season - (lookback_seasons - 1)
        usable = [(f, t) for (s, f, t) in pool if s >= window_min_season]

        tippable = [g for g in round_games if len(g.preds) > 0]
        skipped_no_preds = len(round_games) - len(tippable)

        if len(usable) >= min_training_rows:
            # S3: heavy imports only past the fallback decision point.
            import numpy as np
            import xgboost as xgb

            model = xgb.XGBRegressor(**used_params)
            model.fit(
                np.array([f for f, _ in usable], dtype=float),
                np.array([t for _, t in usable], dtype=float),
            )
            tips: List[GameTip] = []
            for g in tippable:
                features = build_feature_vector(g.preds, g.home_team, g.away_team)
                y_pred = float(model.predict(np.array([features], dtype=float))[0])
                tips.append(GameTip(game_id=g.game_id, prediction=home_margin_to_tip(
                    y_pred, g.home_team, g.away_team
                )))
            mode = "trained"
            training_rows = len(usable)
        else:
            tips = [
                GameTip(
                    game_id=g.game_id,
                    prediction=weighted_tip_fallback(g.preds, g.home_team, g.away_team),
                )
                for g in tippable
            ]
            mode = "fallback"
            training_rows = len(usable)

        records_by_id = {g.game_id: g for g in round_games}
        n_correct = sum(
            1
            for tip in tips
            if tip.prediction.pick == _actual_winner(records_by_id[tip.game_id])
        )

        plans.append(
            RoundPlan(
                season=season,
                round_id=round_id,
                mode=mode,
                training_rows=training_rows,
                tips=tips,
                n_skipped_no_preds=skipped_no_preds,
                n_correct=n_correct,
            )
        )

        # Round complete: its games (with enough stored predictions) join
        # the pool for every later round.
        for g in round_games:
            if len(g.preds) >= MIN_MODELS_PER_GAME:
                features = build_feature_vector(g.preds, g.home_team, g.away_team)
                pool.append((g.season, features, float(g.home_score - g.away_score)))

    return plans


# ---------------------------------------------------------------------------
# I/O wrapper (fetch -> compute -> persist -> summarise)
# ---------------------------------------------------------------------------


async def run_boosted_walkforward_backfill(
    session: AsyncSession,
    seasons: Sequence[int],
    *,
    dry_run: bool = False,
    min_training_rows: int = MIN_TRAINING_ROWS,
) -> Dict[str, Any]:
    """Backfill ``boosted_tip`` tips for ``seasons`` and return a summary.

    Fetches every completed, scored game (and its stored model
    predictions) across the full lookback window covering ``seasons``,
    computes the walk-forward plan, and inserts a ``Tip`` row per
    planned tip that does not already exist (idempotent — safe to
    re-run).  ``dry_run=True`` persists nothing.

    The summary nests one entry per processed round
    (``season/round/mode/training_rows/tipped/correct/skipped``) plus
    ``tips_created``/``tips_skipped_existing`` totals.
    """
    if not seasons:
        raise ValueError("seasons must be a non-empty sequence of years")

    window_min = min(seasons) - (TRAINING_LOOKBACK_SEASONS - 1)
    window_max = max(seasons)

    result = await session.execute(
        select(Game, ModelPrediction)
        .join(ModelPrediction, ModelPrediction.game_id == Game.id)
        .where(
            Game.completed.is_(True),
            Game.home_score.is_not(None),
            Game.away_score.is_not(None),
            Game.season.between(window_min, window_max),
        )
        .order_by(Game.season, Game.round_id, Game.id)
    )
    grouped: Dict[int, Tuple[Game, Dict[str, Prediction]]] = {}
    for game, pred in result.all():
        entry = grouped.setdefault(game.id, (game, {}))
        entry[1][pred.model_name] = Prediction(
            pred.winner, float(pred.confidence), int(pred.margin)
        )

    records = [
        GameRecord(
            game_id=game.id,
            season=game.season,
            round_id=game.round_id,
            home_team=game.home_team,
            away_team=game.away_team,
            home_score=int(game.home_score),
            away_score=int(game.away_score),
            preds=preds,
        )
        for game, preds in grouped.values()
    ]

    plans = compute_walkforward_plans(
        records, min_training_rows=min_training_rows
    )

    existing_result = await session.execute(
        select(Tip.game_id).where(Tip.heuristic == BOOSTED_TIP_MODEL_NAME)
    )
    existing = set(existing_result.scalars().all())

    tips_created = 0
    tips_skipped_existing = 0
    if not dry_run:
        for plan in plans:
            if plan.season not in seasons:
                continue  # window seasons train the pool but are not tipped
            for tip in plan.tips:
                if tip.game_id in existing:
                    tips_skipped_existing += 1
                    continue
                session.add(
                    Tip(
                        game_id=tip.game_id,
                        heuristic=BOOSTED_TIP_MODEL_NAME,
                        selected_team=tip.prediction.pick,
                        margin=tip.prediction.score_projection,
                        confidence=tip.prediction.probability,
                        explanation=(
                            f"BT-1 walk-forward backfill ({plan.mode}, "
                            f"trained on {plan.training_rows} prior games)"
                        ),
                    )
                )
                tips_created += 1
        await session.commit()

    per_round = [
        {
            "season": p.season,
            "round_id": p.round_id,
            "mode": p.mode,
            "training_rows": p.training_rows,
            "tipped": p.n_tipped,
            "correct": p.n_correct,
            "skipped_no_preds": p.n_skipped_no_preds,
            "accuracy": round(p.n_correct / p.n_tipped, 4) if p.n_tipped else 0.0,
        }
        for p in plans
        if p.season in seasons
    ]

    logger.info(
        "boosted walk-forward backfill: seasons=%s dry_run=%s rounds=%d "
        "created=%d skipped_existing=%d",
        list(seasons),
        dry_run,
        len(per_round),
        tips_created,
        tips_skipped_existing,
    )

    return {
        "status": "dry_run" if dry_run else "completed",
        "seasons": list(seasons),
        "rounds_processed": len(per_round),
        "tips_created": tips_created,
        "tips_skipped_existing": tips_skipped_existing,
        "per_round": per_round,
    }
