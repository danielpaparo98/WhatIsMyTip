#!/usr/bin/env python3
"""
Live data seeding script for player stats, weather, and injuries.

Fetches data from AFL Tables, FootyWire, and Open-Meteo, then persists
to the database. Designed for idempotent operation (safe to re-run).

Usage:
    # Seed all data for current season
    uv run python scripts/seed_player_data.py

    # Seed a specific season
    uv run python scripts/seed_player_data.py --season 2025

    # Seed only specific tables
    uv run python scripts/seed_player_data.py --table players --table injuries

    # Clear new tables before seeding
    uv run python scripts/seed_player_data.py --clear

    # Verbose output
    uv run python scripts/seed_player_data.py --verbose
"""

import argparse
import asyncio
import os
import sys
from datetime import datetime, timezone
from typing import Dict, List, Optional, Set

# Ensure the backend directory is on sys.path so that
# `packages.shared` is importable when running from repo root.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from packages.shared.afl_data import AFLTablesClient, FootyWireClient
from packages.shared.weather import WeatherClient
from packages.shared.db import get_engine
from packages.shared.logger import get_logger
from packages.shared.models import (
    Injury,
    MatchWeather,
    Player,
    PlayerAdvancedStats,
    PlayerMatchStats,
)
from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

logger = get_logger(__name__)

# Tables to clear in FK-safe order
_CLEAR_TABLES: List[str] = [
    "injuries",
    "player_advanced_stats",
    "player_match_stats",
    "match_weather",
    "players",
]


# ---------------------------------------------------------------------------
# Player seeding
# ---------------------------------------------------------------------------


async def seed_players(
    session: AsyncSession,
    seasons: List[int],
    verbose: bool = False,
) -> int:
    """Seed the players table from AFL Tables match data.

    For each season, fetches game listings and player stats, then creates
    player entries for any new names encountered.

    Returns:
        Number of new players created.
    """
    created = 0

    async with AFLTablesClient() as client:
        for season in seasons:
            if verbose:
                print(f"  Fetching season {season} game listings...")

            games = await client.get_season_games(season)

            for game_info in games:
                match_id = game_info.get("game_id", "")
                if not match_id:
                    continue

                if verbose:
                    print(f"    Processing match {match_id}...")

                result = await client.get_player_stats_for_match(match_id)
                if not result:
                    continue

                for team_key in ("home_players", "away_players"):
                    for player_data in result.get(team_key, []):
                        name = player_data.get("name", "").strip()
                        if not name:
                            continue

                        # Upsert player by name
                        stmt = pg_insert(Player).values(
                            name=name,
                        )
                        stmt = stmt.on_conflict_do_nothing(
                            constraint="uq_players_name"
                        )
                        result_proxy = await session.execute(stmt)

                        if result_proxy.rowcount > 0:
                            created += 1

                # Flush periodically to avoid memory buildup
                if created > 0 and created % 100 == 0:
                    await session.flush()

    await session.flush()
    return created


# ---------------------------------------------------------------------------
# Match weather seeding
# ---------------------------------------------------------------------------


async def seed_match_weather(
    session: AsyncSession,
    seasons: List[int],
    verbose: bool = False,
) -> int:
    """Seed weather data for games.

    Queries games that don't have weather data yet, then fetches from
    Open-Meteo for each.

    Returns:
        Number of weather records created.
    """
    created = 0

    # Find games without weather data
    result = await session.execute(
        text(
            """
            SELECT g.id, g.venue, g.date, g.completed
            FROM games g
            LEFT JOIN match_weather mw ON g.id = mw.game_id
            WHERE mw.id IS NULL
              AND g.venue IS NOT NULL
              AND g.date IS NOT NULL
              AND g.season = ANY(:seasons)
            ORDER BY g.date
            """
        ),
        {"seasons": seasons},
    )
    games = result.fetchall()

    if verbose:
        print(f"  Found {len(games)} games needing weather data")

    async with WeatherClient() as client:
        for row in games:
            game_id, venue, game_date, completed = row

            if not venue or not game_date:
                continue

            data_type = "historical" if completed else "forecast"

            try:
                weather = await client.get_match_day_weather(
                    venue=venue,
                    match_date=game_date.date(),
                )
            except Exception as e:
                logger.warning(f"Weather fetch failed for game {game_id}: {e}")
                continue

            if not weather or "hourly" not in weather:
                continue

            hourly = weather.get("hourly", {})

            # Extract match-hour values (use the middle of the window)
            times = hourly.get("time", [])
            mid_idx = len(times) // 2 if times else 0

            def _get_val(key: str, idx: int = mid_idx, default=None):
                values = hourly.get(key, [])
                if idx < len(values):
                    return values[idx]
                return default

            weather_record = MatchWeather(
                game_id=game_id,
                venue=venue,
                match_date=game_date.date(),
                temperature=_get_val("temperature_2m"),
                precipitation=_get_val("precipitation"),
                wind_speed=_get_val("windspeed_10m"),
                wind_direction=_get_val("winddirection_10m"),
                wind_gusts=_get_val("windgusts_10m"),
                humidity=_get_val("relative_humidity_2m"),
                weather_code=_get_val("weathercode"),
                data_type=data_type,
                raw_hourly=hourly,
            )

            session.add(weather_record)
            created += 1

            # Flush periodically
            if created % 50 == 0:
                await session.flush()

    await session.flush()
    return created


# ---------------------------------------------------------------------------
# Player match stats seeding
# ---------------------------------------------------------------------------


async def seed_player_match_stats(
    session: AsyncSession,
    seasons: List[int],
    verbose: bool = False,
) -> int:
    """Seed player match statistics from AFL Tables.

    For each completed game with an afltables_match_id, fetches player
    stats and creates PlayerMatchStats records.

    Returns:
        Number of stat records created.
    """
    created = 0

    # Build a name → player_id lookup
    result = await session.execute(text("SELECT id, name FROM players"))
    player_map: Dict[str, int] = {
        row[1].strip(): row[0] for row in result.fetchall()
    }

    # Find completed games with AFL Tables match ID but no player stats
    result = await session.execute(
        text(
            """
            SELECT g.id, g.afltables_match_id, g.home_team, g.away_team
            FROM games g
            WHERE g.completed = true
              AND g.afltables_match_id IS NOT NULL
              AND g.season = ANY(:seasons)
              AND NOT EXISTS (
                  SELECT 1 FROM player_match_stats pms
                  WHERE pms.game_id = g.id
              )
            ORDER BY g.date
            """
        ),
        {"seasons": seasons},
    )
    games = result.fetchall()

    if verbose:
        print(f"  Found {len(games)} games needing player stats")

    async with AFLTablesClient() as client:
        for game_row in games:
            game_id, afltables_match_id, home_team, away_team = game_row

            if not afltables_match_id:
                continue

            try:
                match_data = await client.get_player_stats_for_match(
                    afltables_match_id
                )
            except Exception as e:
                logger.warning(
                    f"Player stats fetch failed for match {afltables_match_id}: {e}"
                )
                continue

            if not match_data:
                continue

            team_map = {
                "home_players": home_team,
                "away_players": away_team,
            }

            for team_key, team_name in team_map.items():
                for player_data in match_data.get(team_key, []):
                    name = player_data.get("name", "").strip()
                    player_id = player_map.get(name)

                    if not player_id:
                        logger.warning(
                            f"Player '{name}' not found in players table, skipping"
                        )
                        continue

                    stat_record = PlayerMatchStats(
                        game_id=game_id,
                        player_id=player_id,
                        team=team_name,
                        kicks=player_data.get("kicks", 0),
                        handballs=player_data.get("handballs", 0),
                        disposals=player_data.get("disposals", 0),
                        marks=player_data.get("marks", 0),
                        goals=player_data.get("goals", 0),
                        behinds=player_data.get("behinds", 0),
                        tackles=player_data.get("tackles", 0),
                        hitouts=player_data.get("hitouts", 0),
                        frees_for=player_data.get("frees_for", 0),
                        frees_against=player_data.get("frees_against", 0),
                    )

                    session.add(stat_record)
                    created += 1

            # Flush after each game
            await session.flush()

    await session.flush()
    return created


# ---------------------------------------------------------------------------
# Player advanced stats seeding
# ---------------------------------------------------------------------------


async def seed_player_advanced_stats(
    session: AsyncSession,
    seasons: List[int],
    verbose: bool = False,
) -> int:
    """Seed advanced player statistics from FootyWire.

    For each player with a footywire_id, fetches per-round advanced
    stats and links them to games.

    Returns:
        Number of advanced stat records created.
    """
    created = 0

    # Find players with FootyWire IDs
    result = await session.execute(
        text("SELECT id, footywire_id, name FROM players WHERE footywire_id IS NOT NULL")
    )
    players = result.fetchall()

    if verbose:
        print(f"  Found {len(players)} players with FootyWire IDs")

    async with FootyWireClient() as client:
        for player_id, footywire_id, player_name in players:
            for season in seasons:
                try:
                    stats = await client.get_player_advanced_stats(
                        footywire_id, season
                    )
                except Exception as e:
                    logger.warning(
                        f"Advanced stats fetch failed for player {player_name}: {e}"
                    )
                    continue

                if not stats:
                    continue

                for round_stats in stats:
                    round_label = round_stats.get("round", "")
                    opponent = round_stats.get("opponent", "")

                    # Try to match to a game by round + opponent + season
                    # This is a best-effort match
                    game_result = await session.execute(
                        text(
                            """
                            SELECT id FROM games
                            WHERE season = :season
                              AND (home_team ILIKE :opp OR away_team ILIKE :opp)
                              AND round_id = :round_num
                            LIMIT 1
                            """
                        ),
                        {
                            "season": season,
                            "opp": f"%{opponent}%",
                            "round_num": _extract_round_number(round_label),
                        },
                    )
                    game_row = game_result.fetchone()

                    if not game_row:
                        continue

                    game_id = game_row[0]

                    # Upsert advanced stats
                    stmt = pg_insert(PlayerAdvancedStats).values(
                        game_id=game_id,
                        player_id=player_id,
                        round_label=round_label,
                        opponent=opponent,
                        tog_pct=_safe_float(round_stats.get("tog_pct")),
                        metres_gained=_safe_int(round_stats.get("metres_gained")),
                        score_involvements=_safe_int(
                            round_stats.get("score_involvements")
                        ),
                        contested_possessions=_safe_int(
                            round_stats.get("contested_possessions")
                        ),
                    )
                    stmt = stmt.on_conflict_do_nothing(
                        constraint="uq_pas_game_player"
                    )
                    result_proxy = await session.execute(stmt)
                    created += result_proxy.rowcount

                await session.flush()

    await session.flush()
    return created


# ---------------------------------------------------------------------------
# Injury seeding
# ---------------------------------------------------------------------------


async def seed_injuries(
    session: AsyncSession,
    verbose: bool = False,
) -> int:
    """Seed current injury data from FootyWire.

    Uses upsert to update existing injuries and add new ones.

    Returns:
        Number of injury records created or updated.
    """
    upserted = 0
    now = datetime.now(timezone.utc)

    async with FootyWireClient() as client:
        injuries = await client.get_injury_list()

    if not injuries:
        if verbose:
            print("  No injury data returned from FootyWire")
        return 0

    if verbose:
        print(f"  Processing {len(injuries)} injury records")

    # Build player name → id lookup
    result = await session.execute(text("SELECT id, name FROM players"))
    player_map: Dict[str, int] = {
        row[1].strip().lower(): row[0] for row in result.fetchall()
    }

    for injury in injuries:
        player_name = injury.get("player", "").strip()
        team = injury.get("team", "").strip()
        injury_type = injury.get("injury", "").strip()
        return_timeline = injury.get("return_timeline", "").strip()

        if not player_name or not injury_type:
            continue

        # Try to match player
        player_id = player_map.get(player_name.lower())

        stmt = pg_insert(Injury).values(
            player_id=player_id,
            player_name=player_name,
            team=team,
            injury_type=injury_type,
            return_timeline=return_timeline,
            source="footywire",
            scraped_at=now,
        )
        stmt = stmt.on_conflict_do_update(
            constraint="uq_injuries_player_injury",
            set_={
                "return_timeline": stmt.excluded.return_timeline,
                "team": stmt.excluded.team,
                "player_id": stmt.excluded.player_id,
                "scraped_at": stmt.excluded.scraped_at,
                "updated_at": now,
            },
        )
        await session.execute(stmt)
        upserted += 1

    # Remove stale injuries (not in latest scrape)
    await session.execute(
        text("DELETE FROM injuries WHERE scraped_at < :now"),
        {"now": now},
    )

    await session.flush()
    return upserted


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _extract_round_number(round_label: str) -> int:
    """Extract round number from a label like 'R1', 'Round 5', etc."""
    import re

    match = re.search(r"\d+", round_label)
    return int(match.group()) if match else 0


def _safe_int(value) -> Optional[int]:
    """Safely convert a value to int, returning None on failure."""
    if value is None:
        return None
    try:
        return int(value)
    except (ValueError, TypeError):
        return None


def _safe_float(value) -> Optional[float]:
    """Safely convert a value to float, returning None on failure."""
    if value is None:
        return None
    try:
        # Handle percentage strings like "85%"
        if isinstance(value, str):
            value = value.replace("%", "").strip()
        return float(value)
    except (ValueError, TypeError):
        return None


# ---------------------------------------------------------------------------
# Main orchestration
# ---------------------------------------------------------------------------


async def clear_player_tables(session: AsyncSession) -> None:
    """Clear data from the new tables in FK-safe order."""
    for table in _CLEAR_TABLES:
        await session.execute(text(f"DELETE FROM {table}"))
    await session.commit()


async def seed_player_data(
    seasons: Optional[List[int]] = None,
    tables: Optional[Set[str]] = None,
    clear: bool = False,
    verbose: bool = False,
) -> Dict[str, int]:
    """Seed the database with live player, weather, and injury data.

    Args:
        seasons: List of seasons to seed. Defaults to current season.
        tables: Set of table names to seed. Defaults to all.
        clear: Whether to clear existing data first.
        verbose: Whether to print progress.

    Returns:
        Dict mapping table names to number of records created/updated.
    """
    if seasons is None:
        seasons = [datetime.now().year]

    if tables is None:
        tables = {"players", "match_weather", "player_match_stats", "player_advanced_stats", "injuries"}

    counts: Dict[str, int] = {}

    engine = get_engine()
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with session_factory() as session:
        try:
            if clear:
                if verbose:
                    print("🗑️  Clearing player data tables...")
                await clear_player_tables(session)
                if verbose:
                    print("✅ Player data cleared.")

            # --- Players ---
            if "players" in tables:
                if verbose:
                    print("👤 Seeding players from AFL Tables...")
                count = await seed_players(session, seasons, verbose=verbose)
                counts["players"] = count
                await session.commit()
                if verbose:
                    print(f"   Created {count} player records")

            # --- Match Weather ---
            if "match_weather" in tables:
                if verbose:
                    print("🌤️  Seeding match weather data...")
                count = await seed_match_weather(session, seasons, verbose=verbose)
                counts["match_weather"] = count
                await session.commit()
                if verbose:
                    print(f"   Created {count} weather records")

            # --- Player Match Stats ---
            if "player_match_stats" in tables:
                if verbose:
                    print("📊 Seeding player match stats...")
                count = await seed_player_match_stats(session, seasons, verbose=verbose)
                counts["player_match_stats"] = count
                await session.commit()
                if verbose:
                    print(f"   Created {count} stat records")

            # --- Player Advanced Stats ---
            if "player_advanced_stats" in tables:
                if verbose:
                    print("⚡ Seeding player advanced stats...")
                count = await seed_player_advanced_stats(session, seasons, verbose=verbose)
                counts["player_advanced_stats"] = count
                await session.commit()
                if verbose:
                    print(f"   Created {count} advanced stat records")

            # --- Injuries ---
            if "injuries" in tables:
                if verbose:
                    print("🏥 Seeding injury data...")
                count = await seed_injuries(session, verbose=verbose)
                counts["injuries"] = count
                await session.commit()
                if verbose:
                    print(f"   Upserted {count} injury records")

            if verbose:
                print("\n✅ Player data seed complete! Summary:")
                for table, count in counts.items():
                    print(f"   {table}: {count} records")

            return counts

        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()

    await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Seed player, weather, and injury data from live sources"
    )
    parser.add_argument(
        "--season",
        type=int,
        nargs="*",
        default=None,
        help="Season(s) to seed (default: current year)",
    )
    parser.add_argument(
        "--table",
        type=str,
        action="append",
        dest="tables",
        choices=["players", "match_weather", "player_match_stats", "player_advanced_stats", "injuries"],
        help="Specific table(s) to seed (default: all)",
    )
    parser.add_argument(
        "--clear",
        action="store_true",
        help="Clear player data tables before seeding",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Print progress to stdout",
    )
    args = parser.parse_args()

    seasons = args.season if args.season else None
    tables = set(args.tables) if args.tables else None

    asyncio.run(
        seed_player_data(
            seasons=seasons,
            tables=tables,
            clear=args.clear,
            verbose=args.verbose,
        )
    )


if __name__ == "__main__":
    main()
