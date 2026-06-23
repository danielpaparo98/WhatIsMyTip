"""Walk-forward backfill of historical predictions + tips.

The repo has all completed/scored games for 2010-2024 but never generated
``model_predictions`` / ``tips`` for those seasons. This script fills them in
chronologically so the point-in-time machinery (each model already filters on
``Game.date < game.date``) produces honest, non-leaking history.

Design goals
------------
* **Chronological** — games are processed ``ORDER BY date ASC`` so the Elo
  recompute and the per-game ``date < game.date`` filters stay coherent and the
  run resumes cleanly.
* **One model pass per game** — ``ModelOrchestrator.predict_all`` runs the 8
  models exactly once and returns both the raw ``model_predictions`` dict *and*
  the 3 heuristic tips, so we store 8 predictions + 3 tips per game with a
  single model pass (no redundant re-runs).
* **No NLP / match-analysis** — tip ``explanation`` is left empty and we never
  touch ``ExplanationService`` / ``MatchAnalysisService`` so the run is fast and
  free.
* **Idempotent / resumable** — uses the atomic CRUD upserts
  (``ModelPredictionCRUD.create_or_update`` keyed on ``uq_game_model`` and
  ``TipCRUD.upsert`` keyed on ``uq_game_heuristic``) and skips any game that
  already has all 8 predictions + 3 tips.
* **Throttled** — sleeps between games to be gentle on the live DB and reuses a
  single orchestrator instance + the small default pool.

Usage::

    uv run python scripts/run_walkforward_backfill.py --start-season 2024 --end-season 2024 --sleep 0.1
    uv run python scripts/run_walkforward_backfill.py --dry-run
"""

import argparse
import asyncio
import os
import sys
import time

# Setup path for imports — scripts/ is inside backend/
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from packages.shared.crud.model_predictions import ModelPredictionCRUD
from packages.shared.crud.tips import TipCRUD
from packages.shared.db import _get_session_factory, dispose_engine
from packages.shared.logger import get_logger
from packages.shared.models import Game, ModelPrediction, Tip
from packages.shared.orchestrator import ModelOrchestrator

logger = get_logger(__name__)

# One full orchestrator pass yields this many predictions / tips per game.
MODELS_COUNT = 8
HEURISTICS_COUNT = 3

# Tip explanations are generated on-demand by the live API; the backfill never
# pays for NLP / match analysis (keeps it fast and free).
_EMPTY_EXPLANATION = ""


# ---------------------------------------------------------------------------
# Selection helpers
# ---------------------------------------------------------------------------

async def fetch_game_ids(
    db: AsyncSession,
    start_season: int,
    end_season: int,
    limit: int | None = None,
) -> list[int]:
    """Return completed, scored game IDs for the season range, oldest first.

    Chronological order is what keeps the walk-forward point-in-time logic
    coherent (each model filters on ``Game.date < game.date``) and lets the run
    resume cleanly.
    """
    stmt = (
        select(Game.id)
        .where(
            Game.season.between(start_season, end_season),
            Game.completed.is_(True),
            Game.home_score.is_not(None),
        )
        .order_by(Game.date.asc())
    )
    if limit:
        stmt = stmt.limit(limit)

    result = await db.execute(stmt)
    return [row[0] for row in result.all()]


async def existing_counts(db: AsyncSession, game_id: int) -> tuple[int, int]:
    """Return ``(prediction_count, tip_count)`` already stored for a game."""
    pred_result = await db.execute(
        select(func.count(ModelPrediction.id)).where(
            ModelPrediction.game_id == game_id
        )
    )
    tip_result = await db.execute(
        select(func.count(Tip.id)).where(Tip.game_id == game_id)
    )
    return int(pred_result.scalar() or 0), int(tip_result.scalar() or 0)


def is_complete(pred_count: int, tip_count: int) -> bool:
    """True when a game already has every prediction + tip (resumability skip)."""
    return pred_count >= MODELS_COUNT and tip_count >= HEURISTICS_COUNT


# ---------------------------------------------------------------------------
# Core single-game work
# ---------------------------------------------------------------------------

async def backfill_game(
    orchestrator: ModelOrchestrator, db: AsyncSession, game: Game
) -> dict:
    """Run models once, upsert all 8 predictions + 3 tips for ``game``.

    ``predict_all`` runs every model exactly once and returns, for each
    heuristic, both the shared ``model_predictions`` dict and the heuristic's
    tip — so we obtain the raw predictions from that same dict instead of
    re-running any model.
    """
    all_results = await orchestrator.predict_all(game, db)

    # ``model_predictions`` is the same dict reference under every heuristic
    # entry (see ModelOrchestrator.predict_all); take it from the first one.
    first_payload = next(iter(all_results.values()))
    model_predictions = first_payload["model_predictions"]

    predictions_written = 0
    for model_name, prediction in model_predictions.items():
        winner, confidence, margin = prediction
        await ModelPredictionCRUD.create_or_update(
            db,
            game_id=game.id,
            model_name=model_name,
            winner=winner,
            confidence=confidence,
            margin=margin,
        )
        predictions_written += 1

    tips_written = 0
    for heuristic, payload in all_results.items():
        winner, confidence, margin = payload["tip"]
        await TipCRUD.upsert(
            db,
            game_id=game.id,
            heuristic=heuristic,
            selected_team=winner,
            margin=margin,
            confidence=confidence,
            explanation=_EMPTY_EXPLANATION,
        )
        tips_written += 1

    return {"predictions": predictions_written, "tips": tips_written, "game_id": game.id}


# ---------------------------------------------------------------------------
# Chronological processing loop
# ---------------------------------------------------------------------------

async def process_one(
    orchestrator: ModelOrchestrator,
    db: AsyncSession,
    game: Game,
    *,
    dry_run: bool = False,
) -> dict:
    """Process a single game with ``db``: skip if complete, else backfill.

    Returns ``{"action": "skipped" | "processed", "predictions": n, "tips": n}``.
    For ``dry_run`` the action is ``"processed"`` (would-process) with no writes.
    """
    pred_count, tip_count = await existing_counts(db, game.id)

    if is_complete(pred_count, tip_count):
        return {"action": "skipped", "predictions": 0, "tips": 0}

    if dry_run:
        logger.info(
            "DRY-RUN would backfill game %s: %s vs %s (%s)",
            game.id,
            game.home_team,
            game.away_team,
            getattr(game, "date", None),
        )
        return {"action": "processed", "predictions": 0, "tips": 0}

    result = await backfill_game(orchestrator, db, game)
    return {
        "action": "processed",
        "predictions": result["predictions"],
        "tips": result["tips"],
    }


async def process_games(
    orchestrator: ModelOrchestrator,
    session_factory,
    games: list[Game],
    *,
    sleep: float = 0.1,
    dry_run: bool = False,
    log_every: int = 25,
) -> dict:
    """Process ``games`` (already date-ordered): skip complete ones, backfill the rest.

    Opens a **fresh session per game** (via ``session_factory``) so a transient
    DB connection drop only costs one retry — the shared pool (with
    ``pool_pre_ping``) hands the next game a healthy connection. This is what
    keeps a multi-thousand-game backfill resilient against remote-DB blips.

    Resumable: a game that already has all 8 predictions + 3 tips is skipped.
    Idempotent: re-running over a fully-populated set writes nothing new.
    Throttled: ``sleep`` seconds are awaited after each game.
    """
    stats = {
        "processed": 0,
        "skipped": 0,
        "failed": 0,
        "predictions": 0,
        "tips": 0,
    }
    total = len(games)
    start = time.time()

    for idx, game in enumerate(games, start=1):
        # Per-game session: a dead connection can't poison the whole run.
        try:
            async with session_factory() as db:
                result = await process_one(
                    orchestrator, db, game, dry_run=dry_run
                )
        except Exception:
            stats["failed"] += 1
            logger.exception("backfill failed for game %s", game.id)
        else:
            stats[result["action"]] += 1
            stats["predictions"] += result["predictions"]
            stats["tips"] += result["tips"]

        if sleep:
            await asyncio.sleep(sleep)

        if log_every and (idx % log_every == 0 or idx == total):
            _log_progress(idx, total, game, start)

    stats["elapsed_seconds"] = time.time() - start
    return stats


def _log_progress(done: int, total: int, game: Game, start: float) -> None:
    """Emit a throttled progress line with rate and ETA."""
    elapsed = max(time.time() - start, 1e-6)
    rate_per_min = (done / elapsed) * 60.0
    remaining = max(total - done, 0)
    eta_seconds = remaining / max(done / elapsed, 1e-6) if done else 0.0
    logger.info(
        "progress %d/%d games | season=%s round=%s | %s vs %s | "
        "%.1f games/min | ETA %.0fs",
        done,
        total,
        getattr(game, "season", "?"),
        getattr(game, "round_id", "?"),
        game.home_team,
        game.away_team,
        rate_per_min,
        eta_seconds,
    )


# ---------------------------------------------------------------------------
# Top-level runner
# ---------------------------------------------------------------------------

async def run_backfill(
    start_season: int = 2010,
    end_season: int = 2024,
    limit: int | None = None,
    sleep: float = 0.1,
    dry_run: bool = False,
    log_every: int = 25,
) -> dict:
    """Fetch games chronologically, process them, and report a summary.

    Builds the async session factory the same way the sibling scripts do and
    reuses a single ``ModelOrchestrator`` instance for the whole run.
    """
    SessionLocal = _get_session_factory()
    orchestrator = ModelOrchestrator()
    had_error = False

    try:
        # Discover + load the games to process in a short-lived session, then
        # detach them so they can be processed across fresh per-game sessions.
        async with SessionLocal() as load_db:
            game_ids = await fetch_game_ids(
                load_db, start_season, end_season, limit
            )
            logger.info(
                "%s %d games for seasons %d-%d",
                "DRY-RUN selected" if dry_run else "Selected",
                len(game_ids),
                start_season,
                end_season,
            )

            if not game_ids:
                logger.info("No games to process; exiting.")
                return {"games_total": 0, "processed": 0, "skipped": 0}

            # Bulk-load full Game objects, preserving chronological order, then
            # expunge so the detached rows survive the session close (only
            # loaded columns are read during backfill — no lazy loads).
            result = await load_db.execute(
                select(Game)
                .where(Game.id.in_(game_ids))
                .order_by(Game.date.asc())
            )
            games = list(result.scalars().all())
            load_db.expunge_all()

        # process_games opens its own fresh session per game.
        stats = await process_games(
            orchestrator,
            SessionLocal,
            games,
            sleep=sleep,
            dry_run=dry_run,
            log_every=log_every,
        )
        stats["games_total"] = len(game_ids)
        return stats
    except Exception:
        had_error = True
        logger.exception("walk-forward backfill failed")
        raise
    finally:
        await dispose_engine(force=had_error)


async def main() -> None:
    parser = argparse.ArgumentParser(
        description="Walk-forward backfill of historical predictions + tips."
    )
    parser.add_argument(
        "--start-season",
        type=int,
        default=2010,
        help="First season to backfill (default: 2010).",
    )
    parser.add_argument(
        "--end-season",
        type=int,
        default=2024,
        help="Last season to backfill (default: 2024).",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Cap the number of games processed (useful for timing tests).",
    )
    parser.add_argument(
        "--sleep",
        type=float,
        default=0.1,
        help="Seconds to sleep between games (default: 0.1).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="List the games that would be processed without writing.",
    )
    parser.add_argument(
        "--log-every",
        type=int,
        default=25,
        help="Emit a progress line every N games (default: 25).",
    )
    args = parser.parse_args()

    stats = await run_backfill(
        start_season=args.start_season,
        end_season=args.end_season,
        limit=args.limit,
        sleep=args.sleep,
        dry_run=args.dry_run,
        log_every=args.log_every,
    )

    print("\n" + "=" * 70)
    print("Walk-forward backfill summary")
    print("=" * 70)
    print(f"  games selected : {stats.get('games_total', 0)}")
    print(f"  processed      : {stats.get('processed', 0)}")
    print(f"  skipped (done) : {stats.get('skipped', 0)}")
    print(f"  failed         : {stats.get('failed', 0)}")
    print(f"  predictions    : {stats.get('predictions', 0)}")
    print(f"  tips           : {stats.get('tips', 0)}")
    print(f"  dry-run        : {args.dry_run}")
    if stats.get("elapsed_seconds") is not None:
        elapsed = stats["elapsed_seconds"]
        rate = (stats.get("processed", 0) / elapsed * 60.0) if elapsed else 0.0
        print(f"  elapsed        : {elapsed:.1f}s ({rate:.1f} games/min)")
    print("=" * 70 + "\n")


if __name__ == "__main__":
    asyncio.run(main())
