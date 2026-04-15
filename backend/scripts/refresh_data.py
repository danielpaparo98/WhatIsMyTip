#!/usr/bin/env python3
"""
Manual data refresh script for WhatIsMyTip.

Provides granular control over each stage of the data pipeline,
from syncing raw games through to regenerating AI content.

Usage:
    # Full refresh - run everything
    uv run python scripts/refresh_data.py --all

    # Sync games from Squiggle API (current season)
    uv run python scripts/refresh_data.py --games

    # Sync games for a specific season
    uv run python scripts/refresh_data.py --games --season 2025

    # Sync games for a specific round
    uv run python scripts/refresh_data.py --games --season 2026 --round 5

    # Detect completed matches and update scores
    uv run python scripts/refresh_data.py --match-completion

    # Refresh Elo ratings cache
    uv run python scripts/refresh_data.py --elo-cache

    # Regenerate model predictions (ML model outputs per game)
    uv run python scripts/refresh_data.py --predictions --season 2026 --round 5

    # Regenerate tips (heuristic outputs)
    uv run python scripts/refresh_data.py --tips --season 2026 --round 5

    # Regenerate AI explanations for tips
    uv run python scripts/refresh_data.py --explanations --season 2026 --round 5

    # Regenerate AI match analysis talking points
    uv run python scripts/refresh_data.py --match-analysis --season 2026 --round 5

    # Clear in-memory cache
    uv run python scripts/refresh_data.py --clear-cache

    # Run the full generated pipeline (predictions -> tips -> explanations -> match-analysis)
    uv run python scripts/refresh_data.py --generated --season 2026 --round 5

    # Force regeneration (overwrite existing data)
    uv run python scripts/refresh_data.py --tips --regenerate --season 2026 --round 5

    # Historic refresh for multiple seasons
    uv run python/scripts/refresh_data.py --historic --seasons 2020-2025

    # Chain multiple steps
    uv run python scripts/refresh_data.py --games --elo-cache --tips --season 2026 --round 5
"""

import argparse
import asyncio
import logging
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

# Resolve project root (parent of scripts/) and ensure we operate from there
# so that relative SQLite paths like ./whatismytip.db resolve correctly.
PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Add project root to path for imports
sys.path.insert(0, str(PROJECT_ROOT))

from app.db import AsyncSessionLocal
from app.config import settings


# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("refresh_data")


# ---------------------------------------------------------------------------
# Database initialisation
# ---------------------------------------------------------------------------


def ensure_db_ready() -> None:
    """Change to the project root and run Alembic migrations.

    When this script is invoked from an arbitrary directory (e.g. ``~/scripts``
    on the production pod) the relative SQLite path ``./whatismytip.db`` would
    resolve against the *current* working directory instead of the project
    root.  We fix that by ``os.chdir``-ing into the project root **before**
    the engine is created (the module-level ``engine`` in ``app.db`` is lazily
    evaluated on first use, so changing dir here is sufficient).

    We also run ``alembic upgrade head`` so that the schema is guaranteed to
    exist even on a fresh / empty database.
    """
    os.chdir(PROJECT_ROOT)
    logger.info(f"Working directory set to {PROJECT_ROOT}")

    from alembic.config import Config as AlembicConfig
    from alembic import command as alembic_cmd

    alembic_cfg = AlembicConfig(str(PROJECT_ROOT / "alembic.ini"))
    # Ensure the (sync) URL matches settings – Alembic env.py also does this,
    # but setting it here keeps things consistent when run outside the normal
    # build pipeline.
    sync_url = settings.database_url.replace("+aiosqlite", "")
    alembic_cfg.set_main_option("sqlalchemy.url", sync_url)

    logger.info("Running Alembic migrations …")
    alembic_cmd.upgrade(alembic_cfg, "head")
    logger.info("Database schema is up to date")


# ---------------------------------------------------------------------------
# Step helpers
# ---------------------------------------------------------------------------


async def step_sync_games(
    season: Optional[int] = None,
    round_id: Optional[int] = None,
) -> dict:
    """Sync games from Squiggle API."""
    from app.squiggle import SquiggleClient
    from app.services.game_sync import GameSyncService

    season = season or datetime.now().year
    squiggle = SquiggleClient()
    try:
        async with AsyncSessionLocal() as db:
            svc = GameSyncService(squiggle_client=squiggle, db_session=db, season=season)
            result = await svc.sync_games()
            logger.info(
                f"  Games sync complete: {result['total_games']} total, "
                f"{result['games_created']} created, {result['games_updated']} updated, "
                f"{result['games_skipped']} skipped"
            )
            if result.get("errors"):
                for err in result["errors"]:
                    logger.warning(f"  Error: {err}")
            return result
    finally:
        await squiggle.close()


async def step_match_completion() -> dict:
    """Detect and process completed matches."""
    from app.squiggle import SquiggleClient
    from app.services.match_completion import MatchCompletionDetectorService

    squiggle = SquiggleClient()
    try:
        async with AsyncSessionLocal() as db:
            svc = MatchCompletionDetectorService(
                squiggle_client=squiggle, db_session=db, buffer_minutes=60
            )
            result = await svc.detect_and_process_completed_matches()
            logger.info(
                f"  Match completion: {result['games_checked']} checked, "
                f"{result['games_completed']} completed, "
                f"{result['games_already_completed']} already done"
            )
            return result
    finally:
        await squiggle.close()


async def step_elo_cache() -> dict:
    """Refresh Elo ratings cache from completed games."""
    from app.models_ml.elo import EloModel

    async with AsyncSessionLocal() as db:
        await EloModel.update_cache(db)
        # Display current ratings from class-level cache
        ratings = EloModel.get_cached_ratings()
        logger.info(f"  Elo cache refreshed: {len(ratings)} teams")
        for team, rating in sorted(ratings.items(), key=lambda x: -x[1]):
            logger.info(f"    {team}: {rating:.0f}")
        return {"teams": len(ratings)}


async def step_predictions(
    season: int,
    round_id: Optional[int] = None,
    regenerate: bool = False,
) -> dict:
    """Regenerate ML model predictions for games."""
    from app.orchestrator import ModelOrchestrator
    from app.crud.games import GameCRUD
    from app.crud.model_predictions import ModelPredictionCRUD

    async with AsyncSessionLocal() as db:
        orchestrator = ModelOrchestrator()

        if round_id:
            games = await GameCRUD.get_by_round(db, season, round_id)
        else:
            games = await GameCRUD.get_by_season(db, season)

        if not games:
            logger.warning(f"  No games found for season {season}" + (f", round {round_id}" if round_id else ""))
            return {"games_processed": 0}

        # Clear cache so we get fresh data
        from app.cache import short_cache, medium_cache
        short_cache.clear()
        medium_cache.clear()

        created = 0
        updated = 0
        errors = []

        for game in games:
            try:
                for model in orchestrator.models:
                    winner, confidence, margin = await model.predict(game, db)
                    if regenerate:
                        await ModelPredictionCRUD.create_or_update(
                            db=db,
                            game_id=game.id,
                            model_name=model.get_name(),
                            winner=winner,
                            confidence=confidence,
                            margin=margin,
                        )
                        updated += 1
                    else:
                        existing = await ModelPredictionCRUD.get_by_game(db, game.id)
                        existing_names = {p.model_name for p in existing}
                        if model.get_name() in existing_names:
                            continue
                        await ModelPredictionCRUD.create(
                            db=db,
                            game_id=game.id,
                            model_name=model.get_name(),
                            winner=winner,
                            confidence=confidence,
                            margin=margin,
                        )
                        created += 1
            except Exception as e:
                errors.append(f"Game {game.id} ({game.home_team} vs {game.away_team}): {e}")
                logger.error(f"  Error: {e}")

        logger.info(
            f"  Predictions: {len(games)} games, {created} created, {updated} updated, "
            f"{len(errors)} errors"
        )
        return {"games_processed": len(games), "created": created, "updated": updated, "errors": errors}


async def step_tips(
    season: int,
    round_id: Optional[int] = None,
    regenerate: bool = False,
) -> dict:
    """Regenerate tips using heuristics."""
    from app.services.tip_generation import TipGenerationService

    async with AsyncSessionLocal() as db:
        svc = TipGenerationService(db_session=db, season=season, round_id=round_id)

        if round_id:
            result = await svc.generate_for_round(season, round_id, regenerate=regenerate)
        else:
            # Generate for next upcoming round
            result = await svc.generate_for_next_upcoming_round(regenerate=regenerate)

        logger.info(
            f"  Tips: {result['games_processed']} games, "
            f"{result['tips_created']} created, {result['tips_updated']} updated, "
            f"{result['tips_skipped']} skipped"
        )
        if result.get("errors"):
            for err in result["errors"]:
                logger.warning(f"  Error: {err}")
        return result


async def step_explanations(
    season: int,
    round_id: Optional[int] = None,
    regenerate: bool = False,
) -> dict:
    """Regenerate AI explanations for tips."""
    from app.services.explanation import ExplanationService
    from app.crud.games import GameCRUD
    from app.crud.tips import TipCRUD
    from app.models import Tip
    from sqlalchemy import select

    svc = ExplanationService()
    try:
        async with AsyncSessionLocal() as db:
            total = 0

            if round_id:
                games = await GameCRUD.get_by_round(db, season, round_id)
            else:
                games = await GameCRUD.get_by_season(db, season)

            for game in games:
                tips = await TipCRUD.get_by_game(db, game.id)
                for tip in tips:
                    if regenerate or not tip.explanation:
                        try:
                            await svc.generate_and_store_explanation(db, tip, game)
                            total += 1
                        except Exception as e:
                            logger.warning(f"  Failed explanation for tip {tip.id}: {e}")

            logger.info(f"  Explanations: {total} generated")
            return {"explanations_generated": total}
    finally:
        await svc.close()


async def step_match_analysis(
    season: int,
    round_id: Optional[int] = None,
    regenerate: bool = False,
) -> dict:
    """Regenerate AI match analysis talking points."""
    from app.services.match_analysis import MatchAnalysisService
    from app.crud.games import GameCRUD
    from app.crud.match_analysis import MatchAnalysisCRUD

    svc = MatchAnalysisService()
    try:
        async with AsyncSessionLocal() as db:
            total = 0

            if round_id:
                games = await GameCRUD.get_by_round(db, season, round_id)
            else:
                games = await GameCRUD.get_by_season(db, season)

            for game in games:
                try:
                    if not regenerate:
                        existing = await MatchAnalysisCRUD.get_by_game_id(db, game.id)
                        if existing:
                            continue

                    # If regenerating, delete existing first
                    if regenerate:
                        existing = await MatchAnalysisCRUD.get_by_game_id(db, game.id)
                        if existing:
                            await db.delete(existing)
                            await db.commit()

                    analysis = await svc.generate_and_store_analysis(db, game)
                    if analysis:
                        total += 1
                except Exception as e:
                    logger.warning(f"  Failed match analysis for game {game.id}: {e}")

            logger.info(f"  Match analysis: {total} generated")
            return {"analyses_generated": total}
    finally:
        await svc.close()


async def step_clear_cache() -> dict:
    """Clear all in-memory caches."""
    from app.cache import short_cache, medium_cache

    short_cache.clear()
    medium_cache.clear()
    logger.info("  In-memory cache cleared")
    return {"cleared": True}


async def step_historic(
    seasons: list[int],
    round_id: Optional[int] = None,
    regenerate_tips: bool = False,
) -> dict:
    """Run historic data refresh for specified seasons."""
    from app.services.historic_data_refresh import HistoricDataRefreshService

    async with AsyncSessionLocal() as db:
        svc = HistoricDataRefreshService(
            db_session=db,
            seasons=seasons,
            round_id=round_id,
            regenerate_tips=regenerate_tips,
        )
        result = await svc.refresh()
        logger.info(
            f"  Historic refresh: {result['seasons_processed']} seasons, "
            f"{result['games_synced']} games synced, "
            f"{result['tips_generated']} tips generated"
        )
        if result.get("errors"):
            for err in result["errors"]:
                logger.warning(f"  Error: {err}")
        return result


# ---------------------------------------------------------------------------
# Pipeline runner
# ---------------------------------------------------------------------------


async def run_pipeline(args: argparse.Namespace) -> None:
    """Execute the requested refresh steps in order."""
    start = time.time()
    season = args.season or datetime.now().year
    round_id = args.round
    regenerate = args.regenerate

    steps = []

    # Determine which steps to run
    if args.all:
        steps = [
            "games", "match_completion", "elo_cache", "predictions",
            "tips", "explanations", "match_analysis", "clear_cache",
        ]
    elif args.generated:
        steps = ["predictions", "tips", "explanations", "match_analysis"]
    elif args.historic:
        steps = ["historic"]
    else:
        if args.games:
            steps.append("games")
        if args.match_completion:
            steps.append("match_completion")
        if args.elo_cache:
            steps.append("elo_cache")
        if args.predictions:
            steps.append("predictions")
        if args.tips:
            steps.append("tips")
        if args.explanations:
            steps.append("explanations")
        if args.match_analysis:
            steps.append("match_analysis")
        if args.clear_cache:
            steps.append("clear_cache")

    if not steps:
        logger.error("No steps selected. Use --all, --generated, or individual flags like --games, --tips, etc.")
        sys.exit(1)

    logger.info("=" * 70)
    logger.info("WhatIsMyTip Data Refresh")
    logger.info(f"Time: {datetime.now().isoformat()}")
    logger.info(f"Season: {season}, Round: {round_id or 'all'}")
    logger.info(f"Regenerate: {regenerate}")
    logger.info(f"Steps: {', '.join(steps)}")
    logger.info("=" * 70)

    for i, step in enumerate(steps, 1):
        logger.info("-" * 50)
        logger.info(f"[{i}/{len(steps)}] {step}")
        logger.info("-" * 50)
        step_start = time.time()

        try:
            if step == "games":
                await step_sync_games(season=season, round_id=round_id)
            elif step == "match_completion":
                await step_match_completion()
            elif step == "elo_cache":
                await step_elo_cache()
            elif step == "predictions":
                await step_predictions(season=season, round_id=round_id, regenerate=regenerate)
            elif step == "tips":
                await step_tips(season=season, round_id=round_id, regenerate=regenerate)
            elif step == "explanations":
                await step_explanations(season=season, round_id=round_id, regenerate=regenerate)
            elif step == "match_analysis":
                await step_match_analysis(season=season, round_id=round_id, regenerate=regenerate)
            elif step == "clear_cache":
                await step_clear_cache()
            elif step == "historic":
                seasons_list = parse_seasons(args.seasons) if args.seasons else list(range(2010, datetime.now().year + 1))
                await step_historic(
                    seasons=seasons_list,
                    round_id=round_id,
                    regenerate_tips=regenerate,
                )
        except Exception as e:
            logger.error(f"  Step '{step}' FAILED: {e}", exc_info=True)
            if args.fail_fast:
                logger.error("Aborting due to --fail-fast")
                sys.exit(1)

        elapsed = time.time() - step_start
        logger.info(f"  Completed in {elapsed:.2f}s")

    total_elapsed = time.time() - start
    logger.info("=" * 70)
    logger.info(f"All steps completed in {total_elapsed:.2f}s")
    logger.info("=" * 70)


def parse_seasons(seasons_str: str) -> list[int]:
    """Parse a seasons string like '2020-2025' or '2020,2021,2022'."""
    seasons = []
    if "-" in seasons_str:
        parts = seasons_str.split("-")
        if len(parts) == 2:
            start, end = int(parts[0].strip()), int(parts[1].strip())
            return list(range(start, end + 1))
    for part in seasons_str.split(","):
        part = part.strip()
        if part:
            seasons.append(int(part))
    return seasons


def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser."""
    parser = argparse.ArgumentParser(
        description="WhatIsMyTip data refresh script",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Full refresh of everything
  uv run python scripts/refresh_data.py --all

  # Just sync games for current season
  uv run python scripts/refresh_data.py --games

  # Refresh all generated content for a specific round
  uv run python scripts/refresh_data.py --generated --season 2026 --round 5

  # Regenerate tips (overwrite existing) for a round
  uv run python scripts/refresh_data.py --tips --regenerate --season 2026 --round 5

  # Historic refresh for a range of seasons
  uv run python scripts/refresh_data.py --historic --seasons 2020-2025

  # Chain multiple steps
  uv run python scripts/refresh_data.py --games --elo-cache --tips --season 2026 --round 5
        """,
    )

    # Step selection - groups
    step_group = parser.add_argument_group("Step Selection (pick one or more)")
    step_group.add_argument(
        "--all", action="store_true",
        help="Run full pipeline: games -> match-completion -> elo-cache -> predictions -> tips -> explanations -> match-analysis -> clear-cache",
    )
    step_group.add_argument(
        "--generated", action="store_true",
        help="Run the generated-content pipeline: predictions -> tips -> explanations -> match-analysis",
    )
    step_group.add_argument(
        "--historic", action="store_true",
        help="Run historic data refresh (sync games + generate tips for past seasons)",
    )

    # Individual steps
    indiv_group = parser.add_argument_group("Individual Steps")
    indiv_group.add_argument("--games", action="store_true", help="Sync games from Squiggle API")
    indiv_group.add_argument("--match-completion", action="store_true", help="Detect and process completed matches")
    indiv_group.add_argument("--elo-cache", action="store_true", help="Refresh Elo ratings cache")
    indiv_group.add_argument("--predictions", action="store_true", help="Regenerate ML model predictions")
    indiv_group.add_argument("--tips", action="store_true", help="Regenerate tips using heuristics")
    indiv_group.add_argument("--explanations", action="store_true", help="Regenerate AI explanations for tips")
    indiv_group.add_argument("--match-analysis", action="store_true", help="Regenerate AI match analysis talking points")
    indiv_group.add_argument("--clear-cache", action="store_true", help="Clear in-memory cache")

    # Filters
    filter_group = parser.add_argument_group("Filters")
    filter_group.add_argument(
        "--season", type=int, default=None,
        help=f"Season year (default: current year {datetime.now().year})",
    )
    filter_group.add_argument(
        "--round", type=int, default=None,
        help="Round number (default: all rounds, or next upcoming for tips)",
    )
    filter_group.add_argument(
        "--seasons", type=str, default=None,
        help="Seasons for historic refresh (e.g., '2020-2025' or '2020,2021,2022')",
    )

    # Options
    opt_group = parser.add_argument_group("Options")
    opt_group.add_argument(
        "--regenerate", action="store_true",
        help="Overwrite existing data (tips, predictions, explanations, match analysis)",
    )
    opt_group.add_argument(
        "--fail-fast", action="store_true",
        help="Stop on first error instead of continuing",
    )
    opt_group.add_argument(
        "--verbose", "-v", action="store_true",
        help="Enable debug-level logging",
    )

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    # Ensure we're in the project root and the database schema exists before
    # doing anything.  This must happen *before* ``asyncio.run`` so that the
    # working directory is correct when the SQLAlchemy engine is created.
    ensure_db_ready()

    asyncio.run(run_pipeline(args))


if __name__ == "__main__":
    main()
