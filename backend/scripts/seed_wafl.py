"""Seed/sync the WAFL competition onto the events tables (Phase 5).

Usage:
    uv run python scripts/seed_wafl.py [--season 2026]

Registers the West Australian Football League (sport 'afl', tier
'state', Perth timezone — idempotent), then pulls the season's
fixtures from the official WAFL/Sportix feed and upserts them into
``events`` / ``event_participants`` / ``event_source_refs`` via the
canonical ingestion pipeline (FixtureDTO → ParticipantResolver →
EventCRUD.upsert_fixture).  Safe to re-run: existing fixtures update
in place.
"""

from __future__ import annotations

import argparse
import asyncio
from typing import Optional

from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncSession

from packages.shared.db import get_engine
from packages.shared.services.local_competition_sync import run_wafl_sync


async def main(season: Optional[int]) -> None:
    engine = get_engine()
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:  # type: AsyncSession
        stats = await run_wafl_sync(session, season=season)
    await engine.dispose()

    print(
        f"WAFL {stats['season']} sync: "
        f"{stats['fixtures_synced']} fixtures, "
        f"{len(stats['errors'])} errors"
    )
    for error in stats["errors"][:10]:
        print(f"  ERROR: {error}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--season",
        type=int,
        default=None,
        help="Season year (defaults to the current year)",
    )
    args = parser.parse_args()
    asyncio.run(main(args.season))
