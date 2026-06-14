"""Reset afltables_match_id for a season."""
import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from sqlalchemy import text
from packages.shared.db import get_engine


async def reset(season: int = 2026):
    engine = get_engine()
    async with engine.begin() as conn:
        result = await conn.execute(
            text("UPDATE games SET afltables_match_id=NULL WHERE season=:s"),
            {"s": season}
        )
        print(f"Reset afltables_match_id for {result.rowcount} games (season {season})")
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(reset())
