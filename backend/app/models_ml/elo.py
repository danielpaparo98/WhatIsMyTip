import asyncio
import numpy as np
import time
import logging
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from typing import Dict, Tuple, Optional
from app.models_ml.base import BaseModel
from app.models import Game

logger = logging.getLogger(__name__)


class EloModel(BaseModel):
    """Elo rating model for predicting game outcomes.
    
    Uses a simplified Elo rating system adapted for AFL.
    """
    
    # Class-level cache for Elo ratings
    _ratings_cache: Dict[str, float] = {}
    _cache_initialized = False
    _cache_lock = asyncio.Lock()
    
    def __init__(self, k_factor: float = 32.0, home_advantage: float = 50.0):
        self.k_factor = k_factor
        self.home_advantage = home_advantage
        # Instance-level ratings for backward compatibility
        self.ratings: Dict[str, float] = {}
        self._lock = asyncio.Lock()
    
    def get_name(self) -> str:
        return "Elo"
    
    @classmethod
    async def _initialize_cache(cls, db: AsyncSession):
        """Initialize the class-level ratings cache from database."""
        async with cls._cache_lock:
            if cls._cache_initialized:
                logger.info("EloModel._initialize_cache: Cache already initialized, skipping")
                return
            
            start_time = time.time()
            logger.info("EloModel._initialize_cache: Initializing Elo ratings cache")
            
            # Get all teams (excluding None values)
            result = await db.execute(
                select(Game.home_team).distinct().where(Game.home_team != None)
            )
            home_teams = set(r[0] for r in result.all())
            result = await db.execute(
                select(Game.away_team).distinct().where(Game.away_team != None)
            )
            away_teams = set(r[0] for r in result.all())
            all_teams = home_teams.union(away_teams)
            
            # Initialize all teams with 1500 rating
            cls._ratings_cache = {team: 1500.0 for team in all_teams}
            
            # Load all completed games and update ratings
            query_start = time.time()
            result = await db.execute(
                select(Game)
                .where(Game.completed == True)
                .order_by(Game.date)
            )
            games = result.scalars().all()
            query_time = time.time() - query_start
            
            logger.info(f"EloModel._initialize_cache: Loaded {len(games)} completed games from database (query took {query_time:.4f}s)")
            
            # Process games in chronological order
            update_start = time.time()
            for game in games:
                home_rating = cls._ratings_cache.get(game.home_team, 1500.0)
                away_rating = cls._ratings_cache.get(game.away_team, 1500.0)
                
                # Expected scores
                expected_home = 1.0 / (1.0 + 10.0 ** ((away_rating - home_rating - 50.0) / 400.0))
                expected_away = 1.0 - expected_home
                
                # Actual scores
                if game.home_score is not None and game.away_score is not None:
                    actual_home = 1.0 if game.home_score > game.away_score else 0.0
                    actual_away = 1.0 - actual_home
                    
                    # Update ratings
                    cls._ratings_cache[game.home_team] = home_rating + 32.0 * (actual_home - expected_home)
                    cls._ratings_cache[game.away_team] = away_rating + 32.0 * (actual_away - expected_away)
            
            update_time = time.time() - update_start
            total_time = time.time() - start_time
            cls._cache_initialized = True
            logger.info(f"EloModel._initialize_cache: Cache initialized with {len(cls._ratings_cache)} teams in {total_time:.4f}s (update took {update_time:.4f}s)")
    
    @classmethod
    async def update_cache(cls, db: AsyncSession):
        """Update the class-level ratings cache from database.
        
        This should be called after new games are completed or synced.
        It reloads all completed games and recomputes ratings.
        """
        async with cls._cache_lock:
            start_time = time.time()
            logger.info("EloModel.update_cache: Updating Elo ratings cache")
            
            # Get all teams (excluding None values)
            result = await db.execute(
                select(Game.home_team).distinct().where(Game.home_team != None)
            )
            home_teams = set(r[0] for r in result.all())
            result = await db.execute(
                select(Game.away_team).distinct().where(Game.away_team != None)
            )
            away_teams = set(r[0] for r in result.all())
            all_teams = home_teams.union(away_teams)
            
            # Initialize all teams with 1500 rating
            cls._ratings_cache = {team: 1500.0 for team in all_teams}
            
            # Load all completed games and update ratings
            query_start = time.time()
            result = await db.execute(
                select(Game)
                .where(Game.completed == True)
                .order_by(Game.date)
            )
            games = result.scalars().all()
            query_time = time.time() - query_start
            
            logger.info(f"EloModel.update_cache: Loaded {len(games)} completed games from database (query took {query_time:.4f}s)")
            
            # Process games in chronological order
            update_start = time.time()
            for game in games:
                home_rating = cls._ratings_cache.get(game.home_team, 1500.0)
                away_rating = cls._ratings_cache.get(game.away_team, 1500.0)
                
                # Expected scores
                expected_home = 1.0 / (1.0 + 10.0 ** ((away_rating - home_rating - 50.0) / 400.0))
                expected_away = 1.0 - expected_home
                
                # Actual scores
                if game.home_score is not None and game.away_score is not None:
                    actual_home = 1.0 if game.home_score > game.away_score else 0.0
                    actual_away = 1.0 - actual_home
                    
                    # Update ratings
                    cls._ratings_cache[game.home_team] = home_rating + 32.0 * (actual_home - expected_home)
                    cls._ratings_cache[game.away_team] = away_rating + 32.0 * (actual_away - expected_away)
            
            update_time = time.time() - update_start
            total_time = time.time() - start_time
            cls._cache_initialized = True
            logger.info(f"EloModel.update_cache: Cache updated with {len(cls._ratings_cache)} teams in {total_time:.4f}s (update took {update_time:.4f}s)")
            
            # Save to database for persistence
            await cls.save_to_cache(db, cls._ratings_cache)
    
    @classmethod
    async def save_to_cache(cls, db: AsyncSession, ratings: Dict[str, float], season: Optional[int] = None):
        """Save Elo ratings to database cache.
        
        Args:
            db: Database session
            ratings: Dictionary of team ratings to save
            season: Optional season (defaults to current year)
        """
        from app.crud.elo_cache import EloCacheCRUD
        from datetime import datetime
        
        if season is None:
            season = datetime.now().year
        
        try:
            await EloCacheCRUD.save_ratings(db, ratings, season)
            logger.info(f"EloModel.save_to_cache: Saved {len(ratings)} ratings for season {season}")
        except Exception as e:
            logger.error(f"EloModel.save_to_cache: Failed to save ratings: {e}", exc_info=True)
    
    @classmethod
    async def load_from_cache(cls, db: AsyncSession, season: Optional[int] = None) -> bool:
        """Load Elo ratings from database cache.
        
        Args:
            db: Database session
            season: Optional season to load (defaults to current year)
            
        Returns:
            True if ratings were loaded successfully, False otherwise
        """
        from app.crud.elo_cache import EloCacheCRUD
        from datetime import datetime
        
        if season is None:
            season = datetime.now().year
        
        try:
            ratings = await EloCacheCRUD.load_ratings(db, season)
            
            if not ratings:
                logger.info(f"EloModel.load_from_cache: No cached ratings found for season {season}")
                return False
            
            async with cls._cache_lock:
                cls._ratings_cache = ratings
                cls._cache_initialized = True
            
            logger.info(f"EloModel.load_from_cache: Loaded {len(ratings)} ratings for season {season}")
            return True
        except Exception as e:
            logger.error(f"EloModel.load_from_cache: Failed to load ratings: {e}", exc_info=True)
            return False
    
    @classmethod
    def get_cached_ratings(cls) -> Dict[str, float]:
        """Get a copy of the cached ratings.
        
        Returns:
            Dict[str, float]: Copy of the cached ratings dictionary
        """
        return cls._ratings_cache.copy()
    
    async def _get_team_ratings(self, db: AsyncSession) -> Dict[str, float]:
        """Get or initialize team Elo ratings."""
        async with self._lock:
            if not self.ratings:
                # Initialize all teams with 1500 rating
                result = await db.execute(
                    select(Game.home_team).distinct()
                )
                home_teams = set(r[0] for r in result.all())
                result = await db.execute(
                    select(Game.away_team).distinct()
                )
                away_teams = set(r[0] for r in result.all())
                all_teams = home_teams.union(away_teams)
                
                for team in all_teams:
                    self.ratings[team] = 1500.0
            
            return self.ratings.copy()
    
    async def _update_ratings(self, db: AsyncSession):
        """Update Elo ratings based on historical games."""
        from sqlalchemy import and_
        
        start_time = time.time()
        
        async with self._lock:
            query_start = time.time()
            result = await db.execute(
                select(Game)
                .where(Game.completed == True)
                .order_by(Game.date)
            )
            games = result.scalars().all()
            query_time = time.time() - query_start
            
            logger.warning(f"EloModel._update_ratings: LOADED {len(games)} COMPLETED GAMES FROM DATABASE (query took {query_time:.4f}s)")
            
            update_start = time.time()
            for game in games:
                home_rating = self.ratings.get(game.home_team, 1500.0)
                away_rating = self.ratings.get(game.away_team, 1500.0)
                
                # Expected scores
                expected_home = 1.0 / (1.0 + 10.0 ** ((away_rating - home_rating - self.home_advantage) / 400.0))
                expected_away = 1.0 - expected_home
                
                # Actual scores
                if game.home_score is not None and game.away_score is not None:
                    actual_home = 1.0 if game.home_score > game.away_score else 0.0
                    actual_away = 1.0 - actual_home
                    
                    # Update ratings
                    self.ratings[game.home_team] = home_rating + self.k_factor * (actual_home - expected_home)
                    self.ratings[game.away_team] = away_rating + self.k_factor * (actual_away - expected_away)
            
            update_time = time.time() - update_start
            total_time = time.time() - start_time
            logger.warning(f"EloModel._update_ratings: UPDATED RATINGS FOR {len(games)} GAMES (update took {update_time:.4f}s, total {total_time:.4f}s)")
    
    async def predict(self, game: Game, db: AsyncSession) -> Tuple[str, float, int]:
        """Predict winner using Elo ratings with point-in-time data.
        
        Only uses games that occurred BEFORE the prediction game's date
        to ensure no data leakage in backtesting.
        """
        start_time = time.time()
        logger.info(f"EloModel.predict: STARTING PREDICTION for game {game.id} ({game.home_team} vs {game.away_team}) on {game.date}")
        
        # Get all teams
        result = await db.execute(
            select(Game.home_team).distinct()
        )
        home_teams = set(r[0] for r in result.all())
        result = await db.execute(
            select(Game.away_team).distinct()
        )
        away_teams = set(r[0] for r in result.all())
        all_teams = home_teams.union(away_teams)
        
        # Initialize all teams with 1500 rating
        ratings = {team: 1500.0 for team in all_teams}
        
        # Load only games that occurred BEFORE the prediction game's date
        # This ensures point-in-time accuracy for backtesting
        query_start = time.time()
        result = await db.execute(
            select(Game)
            .where(
                Game.completed == True,
                Game.date < game.date
            )
            .order_by(Game.date)
        )
        games = result.scalars().all()
        query_time = time.time() - query_start
        
        logger.info(f"EloModel.predict: Loaded {len(games)} historical games before {game.date} (query took {query_time:.4f}s)")
        
        # Process games in chronological order to calculate ratings
        update_start = time.time()
        for historical_game in games:
            home_rating = ratings.get(historical_game.home_team, 1500.0)
            away_rating = ratings.get(historical_game.away_team, 1500.0)
            
            # Expected scores
            expected_home = 1.0 / (1.0 + 10.0 ** ((away_rating - home_rating - 50.0) / 400.0))
            expected_away = 1.0 - expected_home
            
            # Actual scores
            if historical_game.home_score is not None and historical_game.away_score is not None:
                actual_home = 1.0 if historical_game.home_score > historical_game.away_score else 0.0
                actual_away = 1.0 - actual_home
                
                # Update ratings
                ratings[historical_game.home_team] = home_rating + 32.0 * (actual_home - expected_home)
                ratings[historical_game.away_team] = away_rating + 32.0 * (actual_away - expected_away)
        
        update_time = time.time() - update_start
        
        # Get ratings for the prediction game
        home_rating = ratings.get(game.home_team, 1500.0)
        away_rating = ratings.get(game.away_team, 1500.0)
        
        # Apply home advantage
        effective_home = home_rating + self.home_advantage
        
        # Calculate expected probability
        expected_home = 1.0 / (1.0 + 10.0 ** ((away_rating - effective_home) / 400.0))
        expected_away = 1.0 - expected_home
        
        # Predict winner and margin
        if expected_home > expected_away:
            winner = game.home_team
            confidence = expected_home
            margin = int((effective_home - away_rating) / 10)
        else:
            winner = game.away_team
            confidence = expected_away
            margin = int((away_rating - effective_home) / 10)
        
        # Clamp margin to reasonable range
        margin = max(1, min(100, margin))
        
        total_time = time.time() - start_time
        logger.info(f"EloModel.predict: COMPLETED in {total_time:.4f}s | winner={winner}, confidence={confidence:.2f}, margin={margin} (ratings calc took {update_time:.4f}s)")
        
        return winner, confidence, margin
