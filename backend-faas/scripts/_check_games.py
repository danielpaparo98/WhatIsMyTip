"""Quick check of games in DB — shows team names and dates for matching."""
import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from sqlalchemy import select, func, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from packages.shared.db import get_engine
from packages.shared.models import Game


async def check():
    engine = get_engine()
    sf = async_sessionmaker(engine, class_=AsyncSession)

    async with sf() as session:
        # Distinct team names in DB
        r = await session.execute(
            select(Game.home_team).where(Game.season == 2026).distinct()
        )
        print("Distinct home_team names in DB:")
        for row in r:
            print(f"  '{row[0]}'")

        # Sample 2026 games: Round 10+ to see if fixtures align later
        r = await session.execute(
            select(Game.home_team, Game.away_team, Game.date, Game.venue, Game.round_id)
            .where(Game.season == 2026, Game.round_id >= 10)
            .order_by(Game.date)
            .limit(20)
        )
        print("\nRound 10+ games (2026):")
        for row in r:
            print(f"  R{row[4]}: {row[0]} vs {row[1]} | {row[2]} | {row[3]}")

        # Count matched games
        r = await session.execute(
            select(func.count()).select_from(Game)
            .where(Game.afltables_match_id.isnot(None), Game.season == 2026)
        )
        print(f"\nMatched games (2026): {r.scalar()}")

    await engine.dispose()


asyncio.run(check())
