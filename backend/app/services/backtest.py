from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_
from typing import Dict, List, Tuple, Optional
from datetime import datetime
from app.models import Game, Tip, BacktestResult
from app.orchestrator import ModelOrchestrator
from app.crud import BacktestCRUD


class BacktestService:
    """Service for backtesting heuristic performance."""
    
    def __init__(self):
        self.orchestrator = ModelOrchestrator()
    
    async def backtest_round(
        self,
        db: AsyncSession,
        season: int,
        round_id: int,
        heuristic: str,
    ) -> BacktestResult:
        """Backtest a single round for a heuristic.
        
        Args:
            db: Database session
            season: Season year
            round_id: Round number
            heuristic: Heuristic to backtest
            
        Returns:
            BacktestResult with performance metrics
        """
        # Get completed games for the round
        result = await db.execute(
            select(Game)
            .where(
                and_(
                    Game.season == season,
                    Game.round_id == round_id,
                    Game.completed == True,
                    Game.home_score.isnot(None),
                    Game.away_score.isnot(None),
                )
            )
            .order_by(Game.date)
        )
        games = result.scalars().all()
        
        if not games:
            raise ValueError(f"No completed games found for round {round_id}, {season}")
        
        tips_made = 0
        tips_correct = 0
        profit = 0.0
        
        for game in games:
            # Generate prediction using the heuristic
            try:
                winner, confidence, margin = await self.orchestrator.predict(game, heuristic)
            except Exception:
                # Skip if prediction fails
                continue
            
            tips_made += 1
            
            # Determine actual winner
            if game.home_score is None or game.away_score is None:
                continue
            
            actual_winner = (
                game.home_team if game.home_score > game.away_score else game.away_team
            )
            
            # Check if prediction was correct
            if winner == actual_winner:
                tips_correct += 1
                # Simple profit calculation: $1 profit per correct tip
                profit += 1.0
            else:
                # Loss of $1 per incorrect tip
                profit -= 1.0
        
        # Calculate accuracy
        accuracy = tips_correct / tips_made if tips_made > 0 else 0.0
        
        # Create backtest result
        result = await BacktestCRUD.create(
            db=db,
            heuristic=heuristic,
            season=season,
            round_id=round_id,
            tips_made=tips_made,
            tips_correct=tips_correct,
            accuracy=accuracy,
            profit=profit,
        )
        
        return result
    
    async def backtest_season(
        self,
        db: AsyncSession,
        season: int,
        heuristic: str,
    ) -> List[BacktestResult]:
        """Backtest an entire season for a heuristic.
        
        Args:
            db: Database session
            season: Season year
            heuristic: Heuristic to backtest
            
        Returns:
            List of BacktestResult for each round
        """
        # Get all unique rounds for the season
        result = await db.execute(
            select(Game.round_id)
            .where(
                and_(
                    Game.season == season,
                    Game.completed == True,
                )
            )
            .distinct()
            .order_by(Game.round_id)
        )
        rounds = [r[0] for r in result.all()]
        
        results = []
        for round_id in rounds:
            try:
                result = await self.backtest_round(db, season, round_id, heuristic)
                results.append(result)
            except ValueError:
                # Skip rounds with no completed games
                continue
        
        return results
    
    async def backtest_all_heuristics(
        self,
        db: AsyncSession,
        season: int,
        round_id: Optional[int] = None,
    ) -> Dict[str, List[BacktestResult]]:
        """Backtest all heuristics for a season or round.
        
        Args:
            db: Database session
            season: Season year
            round_id: Optional round number (if None, backtest entire season)
            
        Returns:
            Dict of heuristic -> List of BacktestResult
        """
        results = {}
        
        for heuristic in self.orchestrator.get_available_heuristics():
            if round_id:
                # Backtest single round
                try:
                    result = await self.backtest_round(db, season, round_id, heuristic)
                    results[heuristic] = [result]
                except ValueError:
                    results[heuristic] = []
            else:
                # Backtest entire season
                results[heuristic] = await self.backtest_season(db, season, heuristic)
        
        return results
    
    def calculate_summary_stats(
        self, results: List[BacktestResult]
    ) -> Dict[str, float]:
        """Calculate summary statistics from backtest results.
        
        Args:
            results: List of BacktestResult
            
        Returns:
            Dict with summary statistics
        """
        if not results:
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
        
        total_tips = sum(r.tips_made for r in results)
        total_correct = sum(r.tips_correct for r in results)
        total_profit = sum(r.profit for r in results)
        accuracies = [r.accuracy for r in results]
        
        return {
            "total_rounds": len(results),
            "total_tips": total_tips,
            "total_correct": total_correct,
            "overall_accuracy": total_correct / total_tips if total_tips > 0 else 0.0,
            "total_profit": total_profit,
            "avg_profit_per_round": total_profit / len(results),
            "best_round_accuracy": max(accuracies),
            "worst_round_accuracy": min(accuracies),
        }
    
    async def compare_heuristics(
        self,
        db: AsyncSession,
        season: int,
    ) -> Dict[str, Dict[str, float]]:
        """Compare all heuristics for a season.
        
        Args:
            db: Database session
            season: Season year
            
        Returns:
            Dict of heuristic -> summary statistics
        """
        comparison = {}
        
        for heuristic in self.orchestrator.get_available_heuristics():
            results = await self.backtest_season(db, season, heuristic)
            comparison[heuristic] = self.calculate_summary_stats(results)
        
        return comparison
