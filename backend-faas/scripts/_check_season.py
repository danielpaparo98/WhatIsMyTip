#!/usr/bin/env python3
"""Check if DB games are real Squiggle data or seeded test data."""
import asyncio
import os
import sys

from sqlalchemy import text

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from packages.shared.db import _get_session_factory


async def check():
    sf = _get_session_factory()
    async with sf() as session:
        # Check a few games from 2025
        r = await session.execute(
            text(
                "SELECT id, season, round_id, home_team, away_team, venue, date, "
                "squiggle_id, completed FROM games WHERE season = 2025 ORDER BY date LIMIT 10"
            )
        )
        print("=== 2025 season (first 10 games) ===")
        for row in r.fetchall():
            print(f"  {row}")

        # Check how many have squiggle_id
        r = await session.execute(
            text(
                "SELECT season, COUNT(*) as total, "
                "COUNT(CASE WHEN completed THEN 1 END) as completed, "
                "MIN(date) as first_date, MAX(date) as last_date "
                "FROM games GROUP BY season ORDER BY season"
            )
        )
        print("\n=== Season summary ===")
        for row in r.fetchall():
            print(f"  {row}")


if __name__ == "__main__":
    asyncio.run(check())
