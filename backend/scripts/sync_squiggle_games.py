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

    # Replace ALL seeded data with real Squiggle games (destructive!)
    uv run python scripts/sync_squiggle_games.py --season-range 2010:2026 --replace -v
"""

import argparse
import asyncio
import os
import sys
from typing import List, Optional

# Ensure backend is on sys.path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import string
import random

from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from packages.shared.db import get_engine
from packages.shared.models import Game
from packages.shared.squiggle import SquiggleClient
from packages.shared.crud.games import GameCRUD
from packages.shared.squiggle.utils import parse_squiggle_complete


# Tables to clear in FK-safe order when --replace is used
_REPLACE_CLEAR_TABLES: List[str] = [
    # Tables with FK to games (must be cleared first)
    "player_advanced_stats",
    "player_match_stats",
    "match_weather",
    "match_analyses",
    # Tables with FK to players
    "injuries",
    "players",
    # Tables referencing game_id without FK constraint
    "tips",
    "model_predictions",
    # Finally, games themselves
    "games",
]


async def clear_all_for_replace(session: AsyncSession, verbose: bool = False) -> None:
    """Clear all game-related data in FK-safe order for --replace mode.

    This truncates all tables that reference games, then the games table itself.
    Uses TRUNCATE CASCADE for efficiency and to handle any missed FK constraints.
    """
    if verbose:
        print("  Clearing all existing data (--replace mode)...")

    # Use TRUNCATE ... CASCADE to handle all FK dependencies in one shot
    # We need to do games last since other tables depend on it
    tables_without_games = [t for t in _REPLACE_CLEAR_TABLES if t != "games"]
    tables_str = ", ".join(tables_without_games)
    await session.execute(text(f"TRUNCATE TABLE {tables_str} CASCADE"))
    await session.execute(text("TRUNCATE TABLE games CASCADE"))

    if verbose:
        print("  All tables cleared.")


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


def _generate_slug(length: int = 11) -> str:
    """Generate a random alphanumeric slug."""
    chars = string.ascii_lowercase + string.digits
    return "".join(random.choices(chars, k=length))


async def _generate_unique_slug_bulk(session: AsyncSession) -> str:
    """Generate a slug that doesn't exist in the DB."""
    for _ in range(10):
        slug = _generate_slug()
        result = await session.execute(
            select(Game.id).where(Game.slug == slug).limit(1)
        )
        if result.scalar_one_or_none() is None:
            return slug
    raise RuntimeError("Failed to generate unique slug after 10 attempts")


async def sync_season_bulk(
    session: AsyncSession,
    client: SquiggleClient,
    season: int,
    verbose: bool = False,
) -> int:
    """Bulk-insert games for a season (fast, no per-game commit).

    Only safe when we know all games are new (e.g., after --replace).
    Uses batch insert with a single commit per season.
    """
    from datetime import datetime, timezone

    games_data = await client.get_games(year=season)
    if not games_data:
        if verbose:
            print(f"  No games returned for season {season}")
        return 0

    now = datetime.now(timezone.utc)
    game_objects: List[Game] = []

    for game_data in games_data:
        is_complete = parse_squiggle_complete(game_data.get("complete", False))
        home_score_val = game_data.get("hscore")
        away_score_val = game_data.get("ascore")

        game_date = None
        if game_data.get("date"):
            game_date = datetime.fromisoformat(
                game_data["date"].replace("Z", "+00:00")
            )

        game = Game(
            slug=await _generate_unique_slug_bulk(session),
            squiggle_id=game_data["id"],
            round_id=game_data.get("round", 0),
            season=game_data.get("year", 0),
            home_team=game_data.get("hteam", ""),
            away_team=game_data.get("ateam", ""),
            home_score=home_score_val,
            away_score=away_score_val,
            venue=game_data.get("venue", ""),
            date=game_date,
            completed=is_complete,
            last_synced_at=now,
            sync_version=1,
        )
        game_objects.append(game)

    session.add_all(game_objects)
    await session.commit()
    return len(game_objects)


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
    replace: bool = False,
    verbose: bool = False,
) -> None:
    """Sync Squiggle games for all requested seasons."""
    engine = get_engine()
    session_factory = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )

    # Replace mode: clear all existing data first
    if replace:
        async with session_factory() as session:
            await clear_all_for_replace(session, verbose=verbose)
            await session.commit()

    # Reset match IDs if requested
    if reset_matches:
        async with session_factory() as session:
            count = await reset_match_ids(session, seasons)
            await session.commit()
            if verbose:
                print(f"Reset afltables_match_id for {count} games across {len(seasons)} season(s)")

    # Sync each season — use bulk mode when replace=True (all games are new)
    sync_fn = sync_season_bulk if replace else sync_season
    mode_label = "bulk" if replace else "upsert"

    async with SquiggleClient() as client:
        for season in seasons:
            if verbose:
                print(f"\nSyncing season {season} ({mode_label})...")

            async with session_factory() as session:
                try:
                    count = await sync_fn(session, client, season, verbose=verbose)
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
        "--replace",
        action="store_true",
        help="DESTRUCTIVE: Clear ALL game-related data before syncing "
             "(replaces seeded/fake data with real Squiggle games)",
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
            replace=args.replace,
            verbose=args.verbose,
        )
    )


if __name__ == "__main__":
    main()
