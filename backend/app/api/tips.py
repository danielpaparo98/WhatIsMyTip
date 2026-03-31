from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, true
from slowapi import Limiter
from slowapi.util import get_remote_address
from typing import Optional

from app.db import get_db
from app.crud import TipCRUD, GameCRUD, ModelPredictionCRUD
from app.schemas import TipResponse, TipListResponse, ModelPrediction as ModelPredictionSchema
from app.models import Game, Tip
from app.logger import get_logger

router = APIRouter()
limiter = Limiter(key_func=get_remote_address)
logger = get_logger(__name__)

# Valid heuristics for input validation
VALID_HEURISTICS = ["best_bet", "high_risk_high_reward", "yolo"]


@router.get("/games-with-tips")
@limiter.limit("30/minute")
async def get_games_with_tips(
    request: Request,
    season: int = Query(..., description="Season year"),
    round_id: int = Query(..., alias="round", description="Round number"),
    heuristic: Optional[str] = Query("best_bet", description="Heuristic to use (default: best_bet)"),
    db: AsyncSession = Depends(get_db),
):
    """Get games with tips for a round.
    
    Automatically generates tips if they don't exist for the requested round.
    """
    # Validate heuristic parameter
    if heuristic and heuristic not in VALID_HEURISTICS:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid heuristic. Must be one of: {', '.join(VALID_HEURISTICS)}"
        )
    
    try:
        # Lock games for this round to prevent concurrent tip generation
        async with db.begin():
            stmt = select(Game).where(
                Game.season == season,
                Game.round_id == round_id
            ).with_for_update()
            
            games_result = await db.execute(stmt)
            games = list(games_result.scalars().all())
            
            if not games:
                return {"games": [], "count": 0}
            
            # Check if tips exist for these games
            game_ids = [g.id for g in games]
            if game_ids:
                if heuristic:
                    result = await db.execute(
                        select(Tip)
                        .where(
                            Tip.game_id.in_(game_ids),
                            Tip.heuristic == heuristic
                        )
                    )
                else:
                    result = await db.execute(
                        select(Tip)
                        .where(Tip.game_id.in_(game_ids))
                    )
                tips = list(result.scalars().all())
            else:
                tips = []
            
            # If no tips exist for this round, generate them synchronously
            # This is now safe due to the lock
            if not tips:
                logger.info(f"No tips found for round {round_id}, season {season}. Generating now...")
                generation_result = await TipCRUD.regenerate_tips_for_round(db, season, round_id)
            
                if generation_result["success"]:
                    logger.info(f"{generation_result['message']}")
                    
                    # Fetch the newly generated tips
                    if heuristic:
                        result = await db.execute(
                            select(Tip)
                            .where(
                                Tip.game_id.in_(game_ids),
                                Tip.heuristic == heuristic
                            )
                        )
                    else:
                        result = await db.execute(
                            select(Tip)
                            .where(Tip.game_id.in_(game_ids))
                        )
                    tips = list(result.scalars().all())
                else:
                    logger.warning(f"Failed to generate tips: {generation_result['message']}")
        
        # Create a dict of game_id -> tip
        tips_by_game = {tip.game_id: tip for tip in tips}
        
        # Fetch model predictions for all games in this round
        game_ids = [g.id for g in games]
        predictions_by_game = await ModelPredictionCRUD.get_by_games(db, game_ids)

        # Convert to schema format
        model_predictions_by_game = {}
        for game_id, predictions_db in predictions_by_game.items():
            model_predictions_by_game[game_id] = [
                ModelPredictionSchema(
                    model_name=p.model_name,
                    winner=p.winner,
                    confidence=p.confidence,
                    margin=p.margin
                )
                for p in predictions_db
            ]
        
        # Combine games with their tips and model predictions
        games_with_tips = []
        for game in games:
            game_dict = {
                "id": game.id,
                "squiggle_id": game.squiggle_id,
                "round_id": game.round_id,
                "season": game.season,
                "home_team": game.home_team,
                "away_team": game.away_team,
                "home_score": game.home_score,
                "away_score": game.away_score,
                "venue": game.venue,
                "date": game.date.isoformat() if game.date is not None else None,
                "completed": game.completed,
                "tip": None,
                "model_predictions": model_predictions_by_game.get(game.id, [])
            }
            
            # Add tip if available
            if game.id in tips_by_game:
                tip = tips_by_game[game.id]
                game_dict["tip"] = {
                    "id": tip.id,
                    "heuristic": tip.heuristic,
                    "selected_team": tip.selected_team,
                    "margin": tip.margin,
                    "confidence": tip.confidence,
                    "explanation": tip.explanation,
                    "created_at": tip.created_at.isoformat() if tip.created_at is not None else None
                }
            
            games_with_tips.append(game_dict)
        
        return {"games": games_with_tips, "count": len(games_with_tips)}
    except Exception as e:
        logger.error(f"Error in get_games_with_tips: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="An error occurred while fetching tips")


@router.get("", response_model=TipListResponse)
@limiter.limit("30/minute")
async def get_tips(
    request: Request,
    season: Optional[int] = Query(None, description="Filter by season year"),
    round_id: Optional[int] = Query(None, alias="round", description="Filter by round number"),
    heuristic: Optional[str] = Query(None, description="Filter by heuristic type"),
    limit: int = Query(100, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
):
    """Get tips with optional filtering."""
    try:
        if season and round_id:
            tips = await TipCRUD.get_by_round(db, season, round_id)
        elif heuristic:
            tips = await TipCRUD.get_by_heuristic(db, heuristic)
        else:
            # Return recent tips
            tips = await TipCRUD.get_by_heuristic(db, "best_bet", limit=50)
        
        return TipListResponse(
            tips=[TipResponse.model_validate(t) for t in tips],
            count=len(tips),
        )
    except Exception as e:
        logger.error(f"Error in get_tips: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="An error occurred while fetching tips")


@router.get("/{heuristic}", response_model=TipListResponse)
@limiter.limit("30/minute")
async def get_tips_by_heuristic(
    request: Request,
    heuristic: str,
    limit: int = Query(100, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
):
    """Get tips by heuristic type."""
    tips = await TipCRUD.get_by_heuristic(db, heuristic, limit=limit)
    return TipListResponse(
        tips=[TipResponse.model_validate(t) for t in tips],
        count=len(tips),
    )


