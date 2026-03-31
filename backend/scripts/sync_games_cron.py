#!/usr/bin/env python3
"""
Cron job script to automatically sync games from Squiggle API for the current season.

This script is designed to run daily to keep the game database up-to-date with
the latest fixtures and results from Squiggle API.

Usage:
    uv run python scripts/sync_games_cron.py
"""

import asyncio
import sys
import logging
from datetime import datetime
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.db import get_db
from app.crud.games import GameCRUD
from app.squiggle.client import SquiggleClient


# Configure logging (UTF-8 encoding for Windows compatibility)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler('sync_games_cron.log', encoding='utf-8')
    ]
)
logger = logging.getLogger(__name__)


async def main():
    """Main function to sync games from Squiggle API for the current season."""
    logger.info("=" * 60)
    logger.info("Starting games sync cron job")
    logger.info(f"Run time: {datetime.now().isoformat()}")
    logger.info("=" * 60)
    
    squiggle_client = None
    try:
        # Initialize Squiggle client
        squiggle_client = SquiggleClient()
        logger.info("Squiggle client initialized")
        
        # Get database session
        async for db in get_db():
            try:
                # Sync games from Squiggle API for current season
                logger.info("Syncing games from Squiggle API...")
                synced_games = await GameCRUD.sync_from_squiggle(db, squiggle_client)
                logger.info(f"[OK] Synced {len(synced_games)} games")
                
                # Update Elo cache after sync to keep ratings fresh
                logger.info("Updating Elo ratings cache...")
                from app.models_ml.elo import EloModel
                await EloModel.update_cache(db)
                logger.info("[OK] Updated Elo ratings cache")
                
                logger.info("=" * 60)
                logger.info("Games sync cron job completed successfully")
                logger.info("=" * 60)
                
            except Exception as e:
                logger.error(f"Error during games sync: {e}", exc_info=True)
                raise
            finally:
                await db.close()
    
    except Exception as e:
        logger.error(f"Fatal error in cron job: {e}", exc_info=True)
        sys.exit(1)
    finally:
        # Close Squiggle client if initialized
        if squiggle_client:
            await squiggle_client.close()


if __name__ == "__main__":
    asyncio.run(main())
