#!/usr/bin/env python3
"""Generate historical tips and model predictions for seasons 2010-2025."""
import asyncio
import sys
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Tuple

sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy import select, and_, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.crud.games import GameCRUD
from app.crud.tips import TipCRUD
from app.crud.model_predictions import ModelPredictionCRUD
from app.crud.generation_progress import GenerationProgressCRUD
from app.models import Game, Tip, ModelPrediction
from app.orchestrator import ModelOrchestrator
from app.models_ml import BaseModel, EloModel, FormModel, HomeAdvantageModel, ValueModel
from app.squiggle import SquiggleClient
from app.db import get_db
from app.logger import get_logger

logger = get_logger(__name__)

# Constants
BATCH_SIZE = 100
MODELS = ["elo", "form", "home_advantage", "value"]
HEURISTICS = ["best_bet", "yolo", "high_risk_high_reward"]


async def main():
    """Generate historical data for all seasons 2010-2025."""
    logger.info("============================================================")
    logger.info("Starting historical data generation")
    logger.info("Seasons: 2010-2025")
    logger.info("============================================================")
    
    current_year = datetime.utcnow().year
    seasons = list(range(2010, current_year + 1))
    total_seasons = len(seasons)
    
    try:
        async for db in get_db():
            # Initialize Elo cache
            logger.info("Initializing Elo ratings cache...")
            await EloModel.update_cache(db)
            logger.info("[OK] Elo cache initialized")
            
            # Create progress record
            progress = await GenerationProgressCRUD.create(
                db,
                operation_type="historical_generation",
                total_items=total_seasons
            )
            # Store progress ID as int for later use
            progress_id = progress.id  # type: ignore
            logger.info(f"[OK] Progress tracking initialized (ID: {progress_id})")
            
            # Initialize orchestrator
            orchestrator = ModelOrchestrator()
            
            # Initialize Squiggle client
            squiggle_client = SquiggleClient()
            
            # Process each season
            for i, season in enumerate(seasons, 1):
                logger.info(f"Processing season {season} ({i}/{total_seasons})...")
                
                try:
                    # Sync games for this season
                    games = await sync_season_games(db, season, squiggle_client)
                    logger.info(f"[OK] Synced {len(games)} games for season {season}")
                    
                    if not games:
                        logger.warning(f"[SKIP] No games found for season {season}")
                        await GenerationProgressCRUD.update_progress(
                            db, progress_id, completed_items=i
                        )
                        continue
                    
                    # Generate predictions and tips
                    await generate_for_season(db, season, games, orchestrator)
                    
                    # Update Elo cache after each season
                    await EloModel.update_cache(db)
                    logger.info(f"[OK] Elo cache updated for season {season}")
                    
                    # Update progress
                    await GenerationProgressCRUD.update_progress(
                        db, progress_id, completed_items=i
                    )
                    
                except Exception as e:
                    logger.error(f"[ERROR] Failed to process season {season}: {str(e)}")
                    import traceback
                    logger.error(traceback.format_exc())
                    # Continue with next season
                    await GenerationProgressCRUD.update_progress(
                        db, progress_id, completed_items=i
                    )
                    continue
            
            # Mark as completed
            await GenerationProgressCRUD.update_progress(
                db, progress_id, status="completed", completed_items=total_seasons
            )
            
            break  # Only use first db session
            
    except Exception as e:
        logger.error(f"[FATAL] Historical generation failed: {str(e)}")
        import traceback
        logger.error(traceback.format_exc())
        sys.exit(1)
    
    logger.info("============================================================")
    logger.info("Historical data generation completed successfully")
    logger.info("============================================================")


async def sync_season_games(
    db: AsyncSession,
    season: int,
    squiggle_client: SquiggleClient
) -> List[Game]:
    """Sync games for a season from Squiggle API.
    
    Args:
        db: Database session
        season: Season year to sync
        squiggle_client: Squiggle API client
        
    Returns:
        List of Game objects
    """
    # Check if games already exist for this season
    result = await db.execute(
        select(func.count(Game.id)).where(Game.season == season)
    )
    game_count = result.scalar() or 0
    
    if game_count > 0:
        # Games already exist, fetch them
        result = await db.execute(
            select(Game).where(Game.season == season).order_by(Game.date)
        )
        return list(result.scalars().all())
    
    # No games exist, fetch from Squiggle API
    try:
        logger.info(f"Fetching games from Squiggle API for season {season}...")
        # Don't use complete=True for historical seasons - it filters out games
        games_data = await squiggle_client.get_games(year=season)
        
        games = []
        for game_data in games_data:
            game = await GameCRUD.create_or_update(db, game_data)
            games.append(game)
        
        await db.commit()
        return games
        
    except Exception as e:
        logger.error(f"Failed to fetch games for season {season}: {str(e)}")
        raise


async def generate_for_season(
    db: AsyncSession,
    season: int,
    games: List[Game],
    orchestrator: ModelOrchestrator
):
    """Generate predictions and tips for all games in a season.
    
    Args:
        db: Database session
        season: Season year
        games: List of Game objects
        orchestrator: ModelOrchestrator instance
    """
    logger.info(f"Generating predictions and tips for {len(games)} games...")
    
    # Generate model predictions
    logger.info("Generating model predictions...")
    predictions_generated = await generate_model_predictions(
        db, games, orchestrator
    )
    logger.info(f"[OK] Generated {predictions_generated} model predictions")
    
    # Generate tips for all heuristics
    logger.info("Generating tips...")
    tips_generated = await generate_tips(db, games, orchestrator)
    logger.info(f"[OK] Generated {tips_generated} tips")
    
    # Update game status flags
    logger.info("Updating game status flags...")
    await update_game_status_flags(db, games)
    logger.info(f"[OK] Updated status flags for {len(games)} games")


async def generate_model_predictions(
    db: AsyncSession,
    games: List[Game],
    orchestrator: ModelOrchestrator
) -> int:
    """Generate model predictions for all games.
    
    Args:
        db: Database session
        games: List of Game objects
        orchestrator: ModelOrchestrator instance
        
    Returns:
        Number of predictions generated
    """
    predictions_data = []
    total_predictions = 0
    
    # Process games in batches
    for i in range(0, len(games), BATCH_SIZE):
        batch = games[i:i + BATCH_SIZE]
        logger.debug(f"Processing batch {i // BATCH_SIZE + 1} ({len(batch)} games)...")
        
        for game in batch:
            # Generate predictions for each model
            for model_name in MODELS:
                try:
                    # Get model instance
                    model = get_model_instance(model_name)
                    
                    # Get prediction
                    winner, confidence, margin = await model.predict(game, db)
                    
                    predictions_data.append({
                        "game_id": game.id,
                        "model_name": model_name,
                        "winner": winner,
                        "confidence": confidence,
                        "margin": margin,
                    })
                    total_predictions += 1
                    
                except Exception as e:
                    logger.warning(
                        f"Failed to generate {model_name} prediction for game {game.id}: {str(e)}"
                    )
                    continue
    
    # Batch insert predictions
    if predictions_data:
        await ModelPredictionCRUD.create_batch(db, predictions_data)
    
    return total_predictions


async def generate_tips(
    db: AsyncSession,
    games: List[Game],
    orchestrator: ModelOrchestrator
) -> int:
    """Generate tips for all games using all heuristics.
    
    Args:
        db: Database session
        games: List of Game objects
        orchestrator: ModelOrchestrator instance
        
    Returns:
        Number of tips generated
    """
    tips_data = []
    total_tips = 0
    
    # Process games in batches
    for i in range(0, len(games), BATCH_SIZE):
        batch = games[i:i + BATCH_SIZE]
        logger.debug(f"Processing tips batch {i // BATCH_SIZE + 1} ({len(batch)} games)...")
        
        for game in batch:
            # Generate tips for each heuristic
            for heuristic in HEURISTICS:
                try:
                    # Check if tip already exists
                    result = await db.execute(
                        select(Tip).where(
                            and_(
                                Tip.game_id == game.id,
                                Tip.heuristic == heuristic,
                            )
                        )
                    )
                    existing_tip = result.scalars().first()
                    
                    if existing_tip:
                        continue
                    
                    # Get prediction from orchestrator
                    winner, confidence, margin = await orchestrator.predict(
                        game, heuristic, db
                    )
                    
                    tips_data.append({
                        "game_id": game.id,
                        "heuristic": heuristic,
                        "selected_team": winner,
                        "margin": margin,
                        "confidence": confidence,
                        "explanation": f"Generated by {heuristic} heuristic",
                    })
                    total_tips += 1
                    
                except Exception as e:
                    logger.warning(
                        f"Failed to generate {heuristic} tip for game {game.id}: {str(e)}"
                    )
                    continue
    
    # Batch insert tips
    if tips_data:
        await TipCRUD.create_batch(db, tips_data)
    
    return total_tips


async def update_game_status_flags(db: AsyncSession, games: List[Game]):
    """Update status flags for games.
    
    Args:
        db: Database session
        games: List of Game objects
    """
    for game in games:
        game.predictions_generated = True  # type: ignore
        game.tips_generated = True  # type: ignore
    
    await db.commit()


def get_model_instance(model_name: str) -> BaseModel:
    """Get model instance by name.
    
    Args:
        model_name: Name of the model (elo, form, home_advantage, value)
        
    Returns:
        BaseModel instance
    """
    models = {
        "elo": EloModel(),
        "form": FormModel(),
        "home_advantage": HomeAdvantageModel(),
        "value": ValueModel(),
    }
    
    if model_name not in models:
        raise ValueError(f"Unknown model: {model_name}")
    
    return models[model_name]


if __name__ == "__main__":
    asyncio.run(main())
