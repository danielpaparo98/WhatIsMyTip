"""Check for games with None team names."""

import asyncio
from sqlalchemy import select, text
from app.db import get_db
from app.models import Game


async def check_none_teams():
    """Check for games with None team names."""
    print("Checking for games with None team names...")
    
    async for db in get_db():
        # Check for None home_team
        result = await db.execute(
            select(Game).where(Game.home_team == None)
        )
        none_home_games = result.scalars().all()
        print(f"\nGames with None home_team: {len(none_home_games)}")
        for game in none_home_games[:5]:  # Show first 5
            print(f"  - ID: {game.id}, Squiggle ID: {game.squiggle_id}, Date: {game.date}")
        
        # Check for None away_team
        result = await db.execute(
            select(Game).where(Game.away_team == None)
        )
        none_away_games = result.scalars().all()
        print(f"\nGames with None away_team: {len(none_away_games)}")
        for game in none_away_games[:5]:  # Show first 5
            print(f"  - ID: {game.id}, Squiggle ID: {game.squiggle_id}, Date: {game.date}")
        
        # Check distinct team names
        result = await db.execute(
            select(Game.home_team).distinct()
        )
        home_teams = [r[0] for r in result.all()]
        print(f"\nDistinct home teams: {len(home_teams)}")
        print(f"  None in home_teams: {None in home_teams}")
        
        result = await db.execute(
            select(Game.away_team).distinct()
        )
        away_teams = [r[0] for r in result.all()]
        print(f"\nDistinct away teams: {len(away_teams)}")
        print(f"  None in away_teams: {None in away_teams}")


if __name__ == "__main__":
    asyncio.run(check_none_teams())
