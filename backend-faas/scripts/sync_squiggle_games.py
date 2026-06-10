#!/usr/bin/env python3
"""Sync games from Squiggle API into the database for one or more seasons.

This must be run BEFORE load_csv_to_db.py so that DB games exist for
the AFL Tables CSV data to be matched against.

Usage:
    # Sync current season
    uv run python scripts/sync_squiggle_games.py

    # Sync specific seasons
    uv run python scripts/sync_squiggle_games.py --season 2024 2025 2026

    # Sync a range of seasons
    uv run python scripts/sync_squiggle_games.py --season-range 2012:2026

    # Reset afltables_match_id for a season before re-matching
    uv run python scripts/sync_squiggle_games.py --season 2026 --reset-matches
"""

import argparse
import asyncio
import os
import sys
from typing import List, Optional

# Ensure backend-faas is on sys.path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from packages.shared.db import get_engine
from packages.shared.models import Game
from packages.shared.squiggle import SquiggleClient
from packages.shared.crud.games import GameCRUD


async def reset_match_ids(session: AsyncSession, seasons: List[int]) -> int:
    """Reset afltables_match_id to NULL for given seasons.

    Returns number of games reset.
    """
    result = await session.execute(
        update(Game)
        .where(Game.season.in_(seasons))
        .where(Game.afltables_match_id.isnot(None))
        .values(afltables_match_id=None)
        .returning(Game.id)
    )
    rows = result.fetchall()
    return len(rows)


async def sync_season(
    session: AsyncSession,
    client: SquiggleClient,
    season: int,
    verbose: bool = False,
) -> int:
    """Sync games from Squiggle API for a single season.

    Returns number of games synced (created or updated).
    """
    games_data = await client.get_games(year=season)
    if not games_data:
        if verbose:
            print(f"  No games returned for season {season}")
        return 0

    synced = 0
    for game_data in games_data:
        game = await GameCRUD.create_or_update(session, game_data)
        if game is not None:
            synced += 1

    await session.commit()
    return synced


async def sync_squiggle_games(
    seasons: List[int],
    reset_matches: bool = False,
    verbose: bool = False,
) -> None:
    """Sync Squiggle games for all requested seasons."""
    engine = get_engine()
    session_factory = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )

    # Reset match IDs if requested
    if reset_matches:
        async with session_factory() as session:
            count = await reset_match_ids(session, seasons)
            await session.commit()
            if verbose:
                print(f"Reset afltables_match_id for {count} games across {len(seasons)} season(s)")

    # Sync each season
    async with SquiggleClient() as client:
        for season in seasons:
            if verbose:
                print(f"\nSyncing season {season} from Squiggle API...")

            async with session_factory() as session:
                try:
                    count = await sync_season(session, client, season, verbose=verbose)
                    if verbose:
                        print(f"  Season {season}: {count} games synced")
                except Exception as e:
                    await session.rollback()
                    print(f"  ERROR syncing season {season}: {e}")
                    raise

    await engine.dispose()

    if verbose:
        print(f"\nSync complete for {len(seasons)} season(s)")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Sync games from Squiggle API into the database"
    )
    parser.add_argument(
        "--season",
        type=int,
        nargs="*",
        default=None,
        help="Season(s) to sync (default: current year)",
    )
    parser.add_argument(
        "--season-range",
        type=str,
        default=None,
        help="Season range inclusive, e.g. '2012:2026'",
    )
    parser.add_argument(
        "--reset-matches",
        action="store_true",
        help="Reset afltables_match_id before syncing",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Print progress to stdout",
    )
    args = parser.parse_args()

    if args.season_range:
        parts = args.season_range.split(":")
        seasons = list(range(int(parts[0]), int(parts[1]) + 1))
    elif args.season:
        seasons = args.season
    else:
        from datetime import datetime
        seasons = [datetime.now().year]

    asyncio.run(
        sync_squiggle_games(
            seasons=seasons,
            reset_matches=args.reset_matches,
            verbose=args.verbose,
        )
    )


if __name__ == "__main__":
    main()
