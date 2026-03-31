from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, func
from typing import Dict, List, Tuple, Optional
from datetime import datetime
from app.models import Game, Tip, BacktestResult
from app.orchestrator import ModelOrchestrator
from app.crud import BacktestCRUD, GameCRUD, TipCRUD
from app.squiggle import SquiggleClient
from app.schemas.backtest import HistoricalSyncResponse, CurrentSeasonResponse, CurrentSeasonHeuristicPerformance, PreGenerateResponse


# Stake amount per game for profit calculation
STAKE_PER_GAME = 10.0


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
                # Profit calculation: $10 profit per correct tip
                profit += STAKE_PER_GAME
            else:
                # Loss of $10 per incorrect tip
                profit -= STAKE_PER_GAME
        
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
        """Compare all heuristics for a season by reading from database.
        
        Args:
            db: Database session
            season: Season year
            
        Returns:
            Dict of heuristic -> summary statistics
        """
        comparison = {}
        
        for heuristic in self.orchestrator.get_available_heuristics():
            # Read backtest results from database instead of running new backtests
            result = await db.execute(
                select(BacktestResult)
                .where(
                    BacktestResult.season == season,
                    BacktestResult.heuristic == heuristic
                )
                .order_by(BacktestResult.round_id)
            )
            results = list(result.scalars().all())
            comparison[heuristic] = self.calculate_summary_stats(results)
        
        return comparison
    
    async def sync_historical_season(
        self,
        db: AsyncSession,
        season: int,
        squiggle_client: SquiggleClient,
    ) -> HistoricalSyncResponse:
        """Sync historical game data and generate tips for a season.
        
        Args:
            db: Database session
            season: Season year to sync
            squiggle_client: Squiggle API client
            
        Returns:
            HistoricalSyncResponse with sync summary
        """
        # Check if games already exist for this season
        result = await db.execute(
            select(func.count(Game.id)).where(Game.season == season)
        )
        game_count = result.scalar()
        
        games_synced = 0
        tips_generated = 0
        
        if game_count == 0:
            # No games exist, fetch from Squiggle API
            try:
                games_data = await squiggle_client.get_games(year=season, complete=True)
                
                for game_data in games_data:
                    game = await GameCRUD.create_or_update(db, game_data)
                    games_synced += 1
                
                if games_synced == 0:
                    return HistoricalSyncResponse(
                        season=season,
                        games_synced=0,
                        tips_generated=0,
                        message=f"No historical data available for season {season}",
                    )
                
            except Exception as e:
                return HistoricalSyncResponse(
                    season=season,
                    games_synced=0,
                    tips_generated=0,
                    message=f"Failed to fetch historical data: {str(e)}",
                )
        else:
            # Games already exist
            games_synced = game_count
        
        # Get all completed games for the season
        result = await db.execute(
            select(Game)
            .where(
                and_(
                    Game.season == season,
                    Game.completed == True,
                )
            )
            .order_by(Game.date)
        )
        games = list(result.scalars().all())
        
        if not games:
            return HistoricalSyncResponse(
                season=season,
                games_synced=games_synced,
                tips_generated=0,
                message=f"No completed games found for season {season}",
            )
        
        # Generate tips for all games using all heuristics
        for game in games:
            for heuristic in self.orchestrator.get_available_heuristics():
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
                
                if not existing_tip:
                    try:
                        winner, confidence, margin = await self.orchestrator.predict(game, heuristic)
                        
                        tip = Tip(
                            game_id=game.id,
                            heuristic=heuristic,
                            selected_team=winner,
                            margin=margin,
                            confidence=confidence,
                            explanation=f"Generated by {heuristic} heuristic",
                        )
                        db.add(tip)
                        tips_generated += 1
                    except Exception:
                        # Skip if prediction fails
                        continue
        
        await db.commit()
        
        return HistoricalSyncResponse(
            season=season,
            games_synced=games_synced,
            tips_generated=tips_generated,
            message=f"Successfully synced {games_synced} games and generated {tips_generated} tips for season {season}",
        )
    
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
        
        # Check if backtest results exist for current season
        result = await db.execute(
            select(func.count(BacktestResult.id)).where(BacktestResult.season == current_year)
        )
        backtest_count = result.scalar()
        
        # If no backtest results exist and there are completed rounds, generate them
        if backtest_count == 0 and rounds_completed > 0:
            await self.backtest_all_heuristics(db, current_year)
        
        # Get backtest results for current season
        result = await db.execute(
            select(BacktestResult)
            .where(BacktestResult.season == current_year)
            .order_by(BacktestResult.heuristic, BacktestResult.round_id)
        )
        backtest_results = list(result.scalars().all())
        
        # Group results by heuristic
        heuristics_data: Dict[str, List[BacktestResult]] = {}
        for br in backtest_results:
            if br.heuristic not in heuristics_data:
                heuristics_data[br.heuristic] = []
            heuristics_data[br.heuristic].append(br)
        
        # Calculate performance for each heuristic
        heuristic_performances = []
        for heuristic in self.orchestrator.get_available_heuristics():
            results = heuristics_data.get(heuristic, [])
            
            if results:
                total_profit = sum(r.profit for r in results)
                total_tips = sum(r.tips_made for r in results)
                total_correct = sum(r.tips_correct for r in results)
                rounds_played = len(results)
                
                total_accuracy = total_correct / total_tips if total_tips > 0 else 0.0
                avg_profit_per_round = total_profit / rounds_played if rounds_played > 0 else 0.0
                
                # Calculate projected annual profit
                projected_annual_profit = avg_profit_per_round * total_rounds
            else:
                total_profit = 0.0
                total_accuracy = 0.0
                rounds_played = 0
                avg_profit_per_round = 0.0
                projected_annual_profit = 0.0
            
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
    
    async def pre_generate_all_seasons(
        self,
        db: AsyncSession,
        specific_season: Optional[int] = None,
    ) -> PreGenerateResponse:
        """Generate backtest results for all seasons from 2010 to current_year.
        
        Includes current year if it has completed games.
        
        Args:
            db: Database session
            specific_season: Optional specific season to pre-generate (if None, all seasons)
            
        Returns:
            PreGenerateResponse with summary of what was processed
        """
        # Get current year
        current_year = datetime.now().year
        
        # Generate list of seasons from 2010 to current_year
        if specific_season:
            seasons = [specific_season]
        else:
            seasons = list(range(2010, current_year + 1))
        
        seasons_processed = 0
        total_backtest_results = 0
        squiggle_client = SquiggleClient()
        
        try:
            for season in seasons:
                # Check if backtest results exist for this season
                result = await db.execute(
                    select(func.count(BacktestResult.id)).where(BacktestResult.season == season)
                )
                backtest_count = result.scalar() or 0
                
                # If no backtest results exist, generate them
                if backtest_count == 0:
                    try:
                        # Sync games for this season
                        sync_result = await self.sync_historical_season(db, season, squiggle_client)
                        
                        # Generate backtest results for all completed rounds
                        backtest_results = await self.backtest_all_heuristics(db, season)
                        
                        # Count total backtest results generated
                        results_count = sum(len(results) for results in backtest_results.values())
                        total_backtest_results += results_count
                        seasons_processed += 1
                        
                    except Exception as e:
                        # Skip seasons where sync or backtest fails
                        continue
                else:
                    # Backtest results already exist, count them
                    seasons_processed += 1
                    total_backtest_results += backtest_count
            
            return PreGenerateResponse(
                message=f"Processed {seasons_processed} season(s) with {total_backtest_results} total backtest results",
                seasons_processed=seasons_processed,
                total_backtest_results=total_backtest_results,
            )
        finally:
            await squiggle_client.close()
