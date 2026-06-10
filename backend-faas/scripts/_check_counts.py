#!/usr/bin/env python3
"""Quick count check for all player data tables."""
import asyncio
import os
import sys

from sqlalchemy import text

# Ensure DATABASE_URL is set
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from packages.shared.db import _get_session_factory


async def check():
    sf = _get_session_factory()
    async with sf() as session:
        for table in ["players", "player_match_stats", "match_weather", "injuries", "games"]:
            r = await session.execute(text(f"SELECT COUNT(*) FROM {table}"))
            count = r.scalar()
            print(f"{table}: {count}")

        r = await session.execute(
            text("SELECT COUNT(*) FROM games WHERE afltables_match_id IS NOT NULL")
        )
        print(f"games with afltables_match_id: {r.scalar()}")

        # Season breakdown
        print("\n--- Games by season ---")
        r = await session.execute(
            text("SELECT season, COUNT(*) as cnt FROM games GROUP BY season ORDER BY season")
        )
        for row in r.fetchall():
            print(f"  {row[0]}: {row[1]} games")


if __name__ == "__main__":
    asyncio.run(check())
