from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, func, case
from typing import Dict, List, Tuple, Optional
from datetime import datetime
from app.models import Game, Tip, BacktestResult
from app.orchestrator import ModelOrchestrator
from app.crud import BacktestCRUD, GameCRUD, TipCRUD, GenerationProgressCRUD
from app.squiggle import SquiggleClient
from app.schemas.backtest import HistoricalSyncResponse, CurrentSeasonResponse, CurrentSeasonHeuristicPerformance, PreGenerateResponse


# Stake amount per game for profit calculation
STAKE_PER_GAME = 10.0


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
            .where(Game.completed == True)
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
        # Get tips for this heuristic in this season
        result = await db.execute(
            select(Tip, Game)
            .join(Game, Tip.game_id == Game.id)
            .where(
                and_(
                    Game.season == season,
                    Tip.heuristic == heuristic,
                    Game.completed == True,
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
            }
        
        tips_made = 0
        tips_correct = 0
        profit = 0.0
        
        for tip, game in tip_rows:
            tips_made += 1
            
            # Determine actual winner from the game object
            actual_winner_name = (
                game.home_team if game.home_score > game.away_score else game.away_team
            )
            
            # Check if prediction was correct
            if tip.selected_team == actual_winner_name:
                tips_correct += 1
                profit += STAKE_PER_GAME
            else:
                profit -= STAKE_PER_GAME
        
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
                func.count(Tip.id).label('total_tips'),
                func.sum(
                    case(
                        (Tip.selected_team == 
                         case(
                             (Game.home_score > Game.away_score, Game.home_team),
                             else_=Game.away_team
                         ), 1),
                        else_=0
                    )
                ).label('correct_tips')
            )
            .join(Tip, Tip.game_id == Game.id)
            .where(
                and_(
                    Game.season == season,
                    Tip.heuristic == heuristic,
                    Game.completed == True,
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
        
        Args:
            db: Database session
            season: Season year
            heuristic: Heuristic name
            
        Returns:
            List of round data with tips_made, tips_correct, accuracy, profit
        """
        result = await db.execute(
            select(
                Game.round_id,
                func.count(Tip.id).label('tips_made'),
                func.sum(
                    case(
                        (Tip.selected_team == 
                         case(
                             (Game.home_score > Game.away_score, Game.home_team),
                             else_=Game.away_team
                         ), 1),
                        else_=0
                    )
                ).label('tips_correct'),
                func.sum(
                    case(
                        (Tip.selected_team == 
                         case(
                             (Game.home_score > Game.away_score, Game.home_team),
                             else_=Game.away_team
                         ), STAKE_PER_GAME),
                        else_=-STAKE_PER_GAME
                    )
                ).label('profit')
            )
            .join(Tip, Tip.game_id == Game.id)
            .where(
                and_(
                    Game.season == season,
                    Tip.heuristic == heuristic,
                    Game.completed == True,
                    Game.home_score.isnot(None),
                    Game.away_score.isnot(None),
                )
            )
            .group_by(Game.round_id)
            .order_by(Game.round_id)
        )
        
        round_data = []
        for round_id, tips_made, tips_correct, profit in result.all():
            accuracy = tips_correct / tips_made if tips_made > 0 else 0.0
            round_data.append({
                "round_id": round_id,
                "tips_made": tips_made,
                "tips_correct": tips_correct,
                "accuracy": accuracy,
                "profit": profit,
            })
        
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
        
        Args:
            db: Database session
            
        Returns:
            CurrentSeasonResponse with YTD performance and projections
        """
        # Get current year
        current_year = datetime.now().year
        
        # Get completed games for current season
        result = await db.execute(
            select(func.count(func.distinct(Game.round_id)))
            .where(
                and_(
                    Game.season == current_year,
                    Game.completed == True,
                )
            )
        )
        rounds_completed = result.scalar() or 0
        
        # AFL typically has 24 rounds per season
        total_rounds = 24
        
        # Calculate performance for each heuristic
        heuristic_performances = []
        for heuristic in self.orchestrator.get_available_heuristics():
            stats = await self.calculate_backtest_from_tips(db, current_year, heuristic)
            
            total_profit = stats["total_profit"]
            total_accuracy = stats["overall_accuracy"]
            rounds_played = int(stats["total_rounds"])
            
            avg_profit_per_round = total_profit / rounds_played if rounds_played > 0 else 0.0
            
            # Calculate projected annual profit
            projected_annual_profit = avg_profit_per_round * total_rounds
            
            heuristic_performances.append(
                CurrentSeasonHeuristicPerformance(
                    heuristic=heuristic,
                    total_profit=total_profit,
                    total_accuracy=total_accuracy,
                    rounds_played=rounds_played,
                    avg_profit_per_round=avg_profit_per_round,
                    projected_annual_profit=projected_annual_profit,
                )
            )
        
        return CurrentSeasonResponse(
            season=current_year,
            heuristics=heuristic_performances,
            rounds_completed=rounds_completed,
            total_rounds=total_rounds,
        )
