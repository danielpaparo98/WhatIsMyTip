from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, and_
from typing import List, Optional, Tuple, Dict, Any
from datetime import datetime
from app.models import Game, Tip
from app.squiggle import SquiggleClient
from app.cache import cached, short_cache, medium_cache


class GameCRUD:
    """CRUD operations for games."""
    
    @staticmethod
    @cached(cache=short_cache, key_prefix="game_by_id:")
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
    @cached(cache=short_cache, key_prefix="games_by_round:")
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
    @cached(cache=short_cache, key_prefix="upcoming_games:")
    async def get_upcoming(db: AsyncSession) -> List[Game]:
        """Get all upcoming (not completed) games."""
        result = await db.execute(
            select(Game)
            .where(Game.completed == False)
            .order_by(Game.date)
        )
        return list(result.scalars().all())
    
    @staticmethod
    @cached(cache=medium_cache, key_prefix="games_by_season:")
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
        from app.cache import invalidate_cache_pattern
        
        # Check if game exists by squiggle_id
        game = await GameCRUD.get_by_squiggle_id(db, game_data["id"])
        
        # Parse complete status (Squiggle returns 100 for complete, not True/False)
        complete_value = game_data.get("complete", False)
        if isinstance(complete_value, int):
            is_complete = complete_value == 100
        else:
            is_complete = bool(complete_value)
        
        # Get values with defaults for None
        home_score_val = game_data.get("hscore")
        away_score_val = game_data.get("ascore")
        
        if game:
            # Update existing game
            if game_data.get("hteam") is not None:
                setattr(game, 'home_team', game_data["hteam"])
            if game_data.get("ateam") is not None:
                setattr(game, 'away_team', game_data["ateam"])
            if home_score_val is not None:
                setattr(game, 'home_score', home_score_val)
            if away_score_val is not None:
                setattr(game, 'away_score', away_score_val)
            if game_data.get("venue") is not None:
                setattr(game, 'venue', game_data["venue"])
            if game_data.get("date") is not None:
                setattr(game, 'date', datetime.fromisoformat(game_data["date"].replace("Z", "+00:00")))
            setattr(game, 'completed', is_complete)
        else:
            # Create new game
            game = Game(
                squiggle_id=game_data["id"],
                round_id=game_data.get("round", 0),
                season=game_data.get("year", 0),
                home_team=game_data.get("hteam", ""),
                away_team=game_data.get("ateam", ""),
                home_score=home_score_val,
                away_score=away_score_val,
                venue=game_data.get("venue", ""),
                date=datetime.fromisoformat(game_data["date"].replace("Z", "+00:00")),
                completed=is_complete,
            )
            db.add(game)
        
        await db.commit()
        await db.refresh(game)
        
        # Invalidate cache for game-related queries
        invalidate_cache_pattern(short_cache, "game_by_id:")
        invalidate_cache_pattern(short_cache, "games_by_round:")
        invalidate_cache_pattern(short_cache, "upcoming_games:")
        invalidate_cache_pattern(medium_cache, "games_by_season:")
        
        return game
    
    @staticmethod
    async def create_or_update_with_tracking(
        db: AsyncSession, game_data: dict
    ) -> Dict[str, Any]:
        """Create or update a game from Squiggle data with sync tracking.
        
        This method tracks sync metadata including last_synced_at and sync_version.
        
        Args:
            db: Database session
            game_data: Game data from Squiggle API
            
        Returns:
            Dictionary with sync tracking info:
            - action: "created", "updated", or "skipped"
            - game: The Game object
            - squiggle_id: The Squiggle game ID
        """
        from app.cache import invalidate_cache_pattern
        
        # Check if game exists by squiggle_id
        game = await GameCRUD.get_by_squiggle_id(db, game_data["id"])
        
        # Parse complete status (Squiggle returns 100 for complete, not True/False)
        complete_value = game_data.get("complete", False)
        if isinstance(complete_value, int):
            is_complete = complete_value == 100
        else:
            is_complete = bool(complete_value)
        
        # Get values with defaults for None
        home_score_val = game_data.get("hscore")
        away_score_val = game_data.get("ascore")
        
        action = "skipped"
        now = datetime.utcnow()
        
        if game:
            # Check if any data actually changed
            changed = False
            if game_data.get("hteam") is not None and game.home_team != game_data["hteam"]:
                changed = True
                game.home_team = game_data["hteam"]
            if game_data.get("ateam") is not None and game.away_team != game_data["ateam"]:
                changed = True
                game.away_team = game_data["ateam"]
            if home_score_val is not None and game.home_score != home_score_val:
                changed = True
                game.home_score = home_score_val
            if away_score_val is not None and game.away_score != away_score_val:
                changed = True
                game.away_score = away_score_val
            if game_data.get("venue") is not None and game.venue != game_data["venue"]:
                changed = True
                game.venue = game_data["venue"]
            if game_data.get("date") is not None:
                new_date = datetime.fromisoformat(game_data["date"].replace("Z", "+00:00"))
                if game.date != new_date:
                    changed = True
                    game.date = new_date
            if game.completed != is_complete:
                changed = True
                game.completed = is_complete
            
            if changed:
                action = "updated"
                game.last_synced_at = now
                game.sync_version = (game.sync_version or 0) + 1
            else:
                # Still update last_synced_at even if no data changed
                game.last_synced_at = now
        else:
            # Create new game
            action = "created"
            game = Game(
                squiggle_id=game_data["id"],
                round_id=game_data.get("round", 0),
                season=game_data.get("year", 0),
                home_team=game_data.get("hteam", ""),
                away_team=game_data.get("ateam", ""),
                home_score=home_score_val,
                away_score=away_score_val,
                venue=game_data.get("venue", ""),
                date=datetime.fromisoformat(game_data["date"].replace("Z", "+00:00")),
                completed=is_complete,
                last_synced_at=now,
                sync_version=1,
            )
            db.add(game)
        
        await db.commit()
        await db.refresh(game)
        
        # Invalidate cache for game-related queries
        invalidate_cache_pattern(short_cache, "game_by_id:")
        invalidate_cache_pattern(short_cache, "games_by_round:")
        invalidate_cache_pattern(short_cache, "upcoming_games:")
        invalidate_cache_pattern(medium_cache, "games_by_season:")
        
        return {
            "action": action,
            "game": game,
            "squiggle_id": game_data["id"]
        }
    
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
    
    @staticmethod
    async def get_next_upcoming_round(db: AsyncSession) -> Optional[Tuple[int, int]]:
        """Get the next upcoming round (season, round_id) that needs tips.
        
        Finds the earliest round where completed=0 and no tips exist yet.
        
        Args:
            db: Database session
            
        Returns:
            Tuple of (season, round_id) or None if no upcoming rounds
        """
        # Get all upcoming games
        upcoming_games = await GameCRUD.get_upcoming(db)
        
        if not upcoming_games:
            return None
        
        # Group games by season and round
        rounds = {}
        for game in upcoming_games:
            key = (game.season, game.round_id)
            if key not in rounds:
                rounds[key] = []
            rounds[key].append(game)
        
        # Check each round for existing tips
        from app.crud import TipCRUD
        
        for (season, round_id), games in sorted(rounds.items()):
            game_ids = [g.id for g in games]
            
            # Check if tips exist for this round
            result = await db.execute(
                select(Tip).where(Tip.game_id.in_(game_ids))
            )
            existing_tips = list(result.scalars().all())
            
            # If no tips exist, this is the next round to generate
            if not existing_tips:
                return (season, round_id)
        
        # All rounds have tips, return the earliest upcoming round
        if rounds:
            return sorted(rounds.keys())[0]
        
        return None
    
    @staticmethod
    async def get_rounds_for_season(db: AsyncSession, season: int) -> List[int]:
        """Get all round numbers for a season.
        
        Args:
            db: Database session
            season: Season year
            
        Returns:
            List of round numbers sorted ascending
        """
        result = await db.execute(
            select(Game.round_id)
            .where(Game.season == season)
            .distinct()
            .order_by(Game.round_id)
        )
        return [row[0] for row in result.all()]
    
    @staticmethod
    async def get_latest_completed_round(db: AsyncSession) -> Optional[Tuple[int, int]]:
        """Get the most recent completed round (season, round_id).
        
        Args:
            db: Database session
            
        Returns:
            Tuple of (season, round_id) or None if no completed rounds
        """
        # Get the latest completed game
        result = await db.execute(
            select(Game)
            .where(Game.completed == True)
            .order_by(Game.date.desc())
            .limit(1)
        )
        game = result.scalar_one_or_none()
        
        if game:
            return (game.season, game.round_id)
        
        return None
    
    @staticmethod
    async def are_current_tips_stale(db: AsyncSession) -> bool:
        """Check if current tips are stale and need regeneration.
        
        Tips are stale if the latest completed round's game dates have passed
        and tips don't exist for the next round.
        
        Args:
            db: Database session
            
        Returns:
            True if tips are stale, False otherwise
        """
        latest_round = await GameCRUD.get_latest_completed_round(db)
        
        if not latest_round:
            # No completed rounds yet, tips are not stale
            return False
        
        season, round_id = latest_round
        
        # Get games from the latest completed round
        games = await GameCRUD.get_by_round(db, season, round_id)
        
        if not games:
            return False
        
        # Check if any game date has passed
        now = datetime.now()
        for game in games:
            if game.date and game.date < now:
                # Games have passed, check if next round has tips
                next_round = await GameCRUD.get_next_upcoming_round(db)
                if next_round:
                    next_season, next_round_id = next_round
                    # Check if tips exist for next round
                    next_games = await GameCRUD.get_by_round(db, next_season, next_round_id)
                    if next_games:
                        game_ids = [g.id for g in next_games]
                        result = await db.execute(
                            select(Tip).where(Tip.game_id.in_(game_ids))
                        )
                        existing_tips = list(result.scalars().all())
                        if not existing_tips:
                            return True
                break
        
        return False
    
    @staticmethod
    async def get_recently_finished_games(
        db: AsyncSession,
        buffer_minutes: int = 60
    ) -> List[Game]:
        """Find games that have recently finished but are not marked complete.
        
        Finds games where:
        - completed = False
        - Game date/time has passed
        - At least buffer_minutes have passed since scheduled time
        
        Args:
            db: Database session
            buffer_minutes: Buffer time after scheduled game time (default: 60)
            
        Returns:
            List of games that may be completed
        """
        from datetime import datetime, timedelta
        
        # Calculate cutoff time: now minus buffer
        cutoff_time = datetime.utcnow() - timedelta(minutes=buffer_minutes)
        
        # Find games where:
        # - completed = False
        # - date is not null
        # - date < cutoff_time (scheduled time has passed plus buffer)
        result = await db.execute(
            select(Game)
            .where(
                and_(
                    Game.completed == False,
                    Game.date.isnot(None),
                    Game.date < cutoff_time
                )
            )
            .order_by(Game.date.asc())
        )
        
        return list(result.scalars().all())
    
    @staticmethod
    async def update_game_completion(
        db: AsyncSession,
        game_id: int,
        squiggle_data: dict
    ) -> Optional[Game]:
        """Update game with final scores from Squiggle data.
        
        Updates game with:
        - Final scores from Squiggle
        - Sets completed = True
        - Updates last_synced_at timestamp
        - Increments sync_version
        
        Args:
            db: Database session
            game_id: Database ID of the game
            squiggle_data: Game data from Squiggle API
            
        Returns:
            Updated Game object or None if game not found
        """
        from app.cache import invalidate_cache_pattern
        from datetime import datetime
        
        # Get the game
        result = await db.execute(
            select(Game).where(Game.id == game_id)
        )
        game = result.scalar_one_or_none()
        
        if not game:
            return None
        
        # Check if already completed
        if game.completed:
            return game
        
        # Parse complete status
        complete_value = squiggle_data.get("complete", False)
        if isinstance(complete_value, int):
            is_complete = complete_value == 100
        else:
            is_complete = bool(complete_value)
        
        # Only update if Squiggle says it's complete
        if not is_complete:
            return None
        
        # Update scores
        home_score_val = squiggle_data.get("hscore")
        away_score_val = squiggle_data.get("ascore")
        
        if home_score_val is not None:
            game.home_score = home_score_val
        if away_score_val is not None:
            game.away_score = away_score_val
        
        # Mark as completed
        game.completed = True
        game.last_synced_at = datetime.utcnow()
        game.sync_version = (game.sync_version or 0) + 1
        
        # Commit changes
        await db.commit()
        await db.refresh(game)
        
        # Invalidate cache
        invalidate_cache_pattern(short_cache, "game_by_id:")
        invalidate_cache_pattern(short_cache, "games_by_round:")
        invalidate_cache_pattern(short_cache, "upcoming_games:")
        invalidate_cache_pattern(medium_cache, "games_by_season:")
        
        return game
