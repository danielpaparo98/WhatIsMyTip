#!/usr/bin/env python3
"""
Cron job script to automatically generate tips for the next upcoming round.

This script is designed to run weekly (e.g., every Monday) to assess and generate
tips for the next AFL round.

Usage:
    uv run python scripts/generate_tips_cron.py
"""

import asyncio
import sys
import logging
from datetime import datetime
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.db import get_db
from app.crud import GameCRUD, TipCRUD


# Configure logging (UTF-8 encoding for Windows compatibility)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler('tips_cron.log', encoding='utf-8')
    ]
)
logger = logging.getLogger(__name__)


async def main():
    """Main function to generate tips for the next upcoming round."""
    logger.info("=" * 60)
    logger.info("Starting tips generation cron job")
    logger.info(f"Run time: {datetime.now().isoformat()}")
    logger.info("=" * 60)
    
    try:
        # Get database session
        async for db in get_db():
            try:
                # Find the next upcoming round
                logger.info("Finding next upcoming round...")
                next_round = await GameCRUD.get_next_upcoming_round(db)
                
                if not next_round:
                    logger.warning("No upcoming rounds found. Nothing to do.")
                    return
                
                season, round_id = next_round
                logger.info(f"Next upcoming round: Season {season}, Round {round_id}")
                
                # Check if tips already exist for this round
                games = await GameCRUD.get_by_round(db, season, round_id)
                game_ids = [g.id for g in games]
                
                from app.models import Tip
                from sqlalchemy import select
                
                result = await db.execute(
                    select(Tip).where(Tip.game_id.in_(game_ids))
                )
                existing_tips = list(result.scalars().all())
                
                if existing_tips:
                    logger.info(
                        f"Tips already exist for round {round_id}, season {season} "
                        f"({len(existing_tips)} tips). Skipping generation."
                    )
                    return
                
                # Generate tips for the round
                logger.info(f"Generating tips for round {round_id}, season {season}...")
                result = await TipCRUD.regenerate_tips_for_round(db, season, round_id)
                
                if result["success"]:
                    logger.info(f"[OK] {result['message']}")
                    logger.info(f"  Heuristics used: {', '.join(result['heuristics_used'])}")
                    logger.info(f"  Tips created: {result['tips_count']}")
                    
                    # Update Elo cache after tip generation to keep cache fresh
                    logger.info("Updating Elo ratings cache...")
                    from app.models_ml.elo import EloModel
                    await EloModel.update_cache(db)
                    logger.info("[OK] Updated Elo ratings cache")
                else:
                    logger.error(f"[FAIL] {result['message']}")
                
                logger.info("=" * 60)
                logger.info("Tips generation cron job completed successfully")
                logger.info("=" * 60)
                
            except Exception as e:
                logger.error(f"Error during tips generation: {e}", exc_info=True)
                raise
            finally:
                await db.close()
    
    except Exception as e:
        logger.error(f"Fatal error in cron job: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
