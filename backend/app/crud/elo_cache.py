"""CRUD operations for Elo ratings cache."""

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_
from typing import Dict, Optional
from datetime import datetime, timezone

from app.models import EloCache
from app.logger import get_logger


logger = get_logger(__name__)


class EloCacheCRUD:
    """CRUD operations for Elo ratings cache."""
    
    @staticmethod
    async def save_ratings(
        db: AsyncSession,
        ratings: Dict[str, float],
        season: int,
        games_played: Optional[Dict[str, int]] = None
    ) -> int:
        """Save Elo ratings to the cache.
        
        Args:
            db: Database session
            ratings: Dictionary mapping team names to Elo ratings
            season: Season for these ratings
            games_played: Optional dictionary mapping team names to games played
            
        Returns:
            Number of ratings saved
        """
        now = datetime.now(timezone.utc)
        saved_count = 0
        
        for team_name, rating in ratings.items():
            # Check if entry exists
            result = await db.execute(
                select(EloCache).where(EloCache.team_name == team_name)
            )
            cache_entry = result.scalar_one_or_none()
            
            if cache_entry:
                # Update existing entry
                cache_entry.rating = rating
                cache_entry.games_played = games_played.get(team_name, 0) if games_played else cache_entry.games_played
                cache_entry.last_updated = now
                cache_entry.season = season
            else:
                # Create new entry
                cache_entry = EloCache(
                    team_name=team_name,
                    rating=rating,
                    games_played=games_played.get(team_name, 0) if games_played else 0,
                    last_updated=now,
                    season=season
                )
                db.add(cache_entry)
            
            saved_count += 1
        
        await db.commit()
        logger.info(f"Saved {saved_count} Elo ratings for season {season}")
        return saved_count
    
    @staticmethod
    async def load_ratings(db: AsyncSession, season: Optional[int] = None) -> Dict[str, float]:
        """Load Elo ratings from the cache.
        
        Args:
            db: Database session
            season: Optional season filter. If None, loads latest ratings for all teams.
            
        Returns:
            Dictionary mapping team names to Elo ratings
        """
        if season is not None:
            # Load ratings for specific season
            result = await db.execute(
                select(EloCache).where(EloCache.season == season)
            )
        else:
            # Load latest ratings for all teams
            result = await db.execute(select(EloCache))
        
        cache_entries = result.scalars().all()
        
        ratings = {}
        for entry in cache_entries:
            ratings[entry.team_name] = entry.rating
        
        logger.info(f"Loaded {len(ratings)} Elo ratings from cache (season: {season or 'all'})")
        return ratings
    
    @staticmethod
    async def load_ratings_with_games_played(
        db: AsyncSession,
        season: Optional[int] = None
    ) -> tuple[Dict[str, float], Dict[str, int]]:
        """Load Elo ratings and games played from the cache.
        
        Args:
            db: Database session
            season: Optional season filter. If None, loads latest ratings for all teams.
            
        Returns:
            Tuple of (ratings dict, games_played dict)
        """
        if season is not None:
            # Load ratings for specific season
            result = await db.execute(
                select(EloCache).where(EloCache.season == season)
            )
        else:
            # Load latest ratings for all teams
            result = await db.execute(select(EloCache))
        
        cache_entries = result.scalars().all()
        
        ratings = {}
        games_played = {}
        for entry in cache_entries:
            ratings[entry.team_name] = entry.rating
            games_played[entry.team_name] = entry.games_played
        
        logger.info(f"Loaded {len(ratings)} Elo ratings with games played from cache (season: {season or 'all'})")
        return ratings, games_played
    
    @staticmethod
    async def clear_cache(db: AsyncSession, season: Optional[int] = None) -> int:
        """Clear cached Elo ratings.
        
        Args:
            db: Database session
            season: Optional season filter. If None, clears all cached ratings.
            
        Returns:
            Number of entries deleted
        """
        if season is not None:
            # Delete ratings for specific season
            result = await db.execute(
                select(EloCache).where(EloCache.season == season)
            )
        else:
            # Delete all ratings
            result = await db.execute(select(EloCache))
        
        entries = result.scalars().all()
        count = len(entries)
        
        for entry in entries:
            await db.delete(entry)
        
        await db.commit()
        logger.info(f"Cleared {count} Elo ratings from cache (season: {season or 'all'})")
        return count
    
    @staticmethod
    async def get_last_updated(db: AsyncSession) -> Optional[datetime]:
        """Get the last update timestamp for the cache.
        
        Args:
            db: Database session
            
        Returns:
            Last update timestamp or None if cache is empty
        """
        result = await db.execute(
            select(EloCache.last_updated).order_by(EloCache.last_updated.desc()).limit(1)
        )
        last_updated = result.scalar_one_or_none()
        return last_updated
    
    @staticmethod
    async def get_team_rating(
        db: AsyncSession,
        team_name: str,
        season: Optional[int] = None
    ) -> Optional[float]:
        """Get a specific team's Elo rating.
        
        Args:
            db: Database session
            team_name: Name of the team
            season: Optional season filter
            
        Returns:
            Team's Elo rating or None if not found
        """
        query = select(EloCache.rating).where(EloCache.team_name == team_name)
        if season is not None:
            query = query.where(EloCache.season == season)
        
        result = await db.execute(query)
        rating = result.scalar_one_or_none()
        return rating
