from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from typing import List, Optional
from datetime import datetime
from app.models import Game
from app.squiggle import SquiggleClient


class GameCRUD:
    """CRUD operations for games."""
    
    @staticmethod
    async def get_by_id(db: AsyncSession, game_id: int) -> Optional[Game]:
        """Get a game by database ID."""
        result = await db.execute(select(Game).where(Game.id == game_id))
        return result.scalar_one_or_none()
    
    @staticmethod
    async def get_by_squiggle_id(db: AsyncSession, squiggle_id: int) -> Optional[Game]:
        """Get a game by Squiggle ID."""
        result = await db.execute(
            select(Game).where(Game.squiggle_id == squiggle_id)
        )
        return result.scalar_one_or_none()
    
    @staticmethod
    async def get_by_round(
        db: AsyncSession, season: int, round_id: int
    ) -> List[Game]:
        """Get all games for a specific round and season."""
        result = await db.execute(
            select(Game)
            .where(Game.season == season, Game.round_id == round_id)
            .order_by(Game.date)
        )
        return list(result.scalars().all())
    
    @staticmethod
    async def get_upcoming(db: AsyncSession) -> List[Game]:
        """Get all upcoming (not completed) games."""
        result = await db.execute(
            select(Game)
            .where(Game.completed == False)
            .order_by(Game.date)
        )
        return list(result.scalars().all())
    
    @staticmethod
    async def get_by_season(db: AsyncSession, season: int) -> List[Game]:
        """Get all games for a season."""
        result = await db.execute(
            select(Game)
            .where(Game.season == season)
            .order_by(Game.date)
        )
        return list(result.scalars().all())
    
    @staticmethod
    async def create_or_update(
        db: AsyncSession, game_data: dict
    ) -> Game:
        """Create or update a game from Squiggle data."""
        # Check if game exists by squiggle_id
        game = await GameCRUD.get_by_squiggle_id(db, game_data["id"])
        
        if game:
            # Update existing game
            game.home_team = game_data.get("hometeam", game.home_team)
            game.away_team = game_data.get("awayteam", game.away_team)
            game.home_score = game_data.get("homescore")
            game.away_score = game_data.get("awayscore")
            game.venue = game_data.get("venue", game.venue)
            game.date = datetime.fromisoformat(game_data["date"].replace("Z", "+00:00"))
            game.completed = game_data.get("complete", False)
            game.updated_at = datetime.utcnow()
        else:
            # Create new game
            game = Game(
                squiggle_id=game_data["id"],
                round_id=game_data.get("round", 0),
                season=game_data.get("year", 0),
                home_team=game_data.get("hometeam", ""),
                away_team=game_data.get("awayteam", ""),
                home_score=game_data.get("homescore"),
                away_score=game_data.get("awayscore"),
                venue=game_data.get("venue", ""),
                date=datetime.fromisoformat(game_data["date"].replace("Z", "+00:00")),
                completed=game_data.get("complete", False),
            )
            db.add(game)
        
        await db.commit()
        await db.refresh(game)
        return game
    
    @staticmethod
    async def sync_from_squiggle(
        db: AsyncSession, client: SquiggleClient, year: Optional[int] = None
    ) -> List[Game]:
        """Sync games from Squiggle API to database.
        
        Args:
            db: Database session
            client: Squiggle API client
            year: Optional year to sync (defaults to current year)
            
        Returns:
            List of synced games
        """
        games_data = await client.get_games(year=year)
        synced_games = []
        
        for game_data in games_data:
            game = await GameCRUD.create_or_update(db, game_data)
            synced_games.append(game)
        
        return synced_games
