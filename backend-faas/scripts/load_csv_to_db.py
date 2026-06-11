#!/usr/bin/env python3
"""Load verified CSV data into the database.

Reads CSV files produced by scrape_to_csv.py and loads them into
the PostgreSQL database, handling the AFL Tables → Squiggle game ID
mapping via the games.afltables_match_id column.

Usage:
    # Load all CSV data (uses DATABASE_URL from env)
    uv run python scripts/load_csv_to_db.py

    # Load specific tables only
    uv run python scripts/load_csv_to_db.py --table players --table player_match_stats

    # Custom CSV directory
    uv run python scripts/load_csv_to_db.py --input-dir ./my_data

    # Clear existing data before loading
    uv run python scripts/load_csv_to_db.py --clear
"""

import argparse
import asyncio
import csv
import os
import sys
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Set

try:
    from zoneinfo import ZoneInfo
except ImportError:
    from backports.zoneinfo import ZoneInfo  # type: ignore[no-redef]

# Ensure backend-faas is on sys.path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from packages.shared.db import Base, get_engine
from packages.shared.models import (
    Game,
    Injury,
    MatchWeather,
    Player,
    PlayerMatchStats,
)

DEFAULT_INPUT_DIR = os.path.join(os.path.dirname(__file__), "..", "data")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def read_csv(filepath: str) -> List[dict]:
    """Read a CSV file and return list of dicts."""
    if not os.path.exists(filepath):
        return []
    with open(filepath, "r", newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        return list(reader)


# ---------------------------------------------------------------------------
# Seed functions
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Game ID matching
# ---------------------------------------------------------------------------

# Canonical team name → all known aliases (Squiggle uses some, AFL Tables others)
_TEAM_NAME_SETS: Dict[str, Set[str]] = {
    "Adelaide": {"Adelaide", "Adelaide Crows"},
    "Brisbane": {"Brisbane", "Brisbane Lions"},
    "Carlton": {"Carlton"},
    "Collingwood": {"Collingwood"},
    "Essendon": {"Essendon"},
    "Fremantle": {"Fremantle", "Fremantle Dockers"},
    "Geelong": {"Geelong"},
    "Giants": {"Giants", "GWS", "Greater Western Sydney", "GWS Giants"},
    "GoldCoast": {"GoldCoast", "Gold Coast", "Gold Coast Suns"},
    "Hawthorn": {"Hawthorn"},
    "Melbourne": {"Melbourne"},
    "NorthMelbourne": {"NorthMelbourne", "North Melbourne", "Kangaroos"},
    "PortAdelaide": {"PortAdelaide", "Port Adelaide", "Port Power"},
    "Richmond": {"Richmond"},
    "StKilda": {"StKilda", "St Kilda"},
    "Sydney": {"Sydney", "Sydney Swans"},
    "WestCoast": {"WestCoast", "West Coast", "West Coast Eagles"},
    "Bulldogs": {"Bulldogs", "Western Bulldogs", "Footscray"},
}

# Reverse map: any alias → canonical (Squiggle) name
_ALIAS_TO_CANONICAL: Dict[str, str] = {}
for canonical, aliases in _TEAM_NAME_SETS.items():
    for alias in aliases:
        _ALIAS_TO_CANONICAL[alias.lower()] = canonical


# Australian Eastern timezone for date conversion
_AUS_EASTERN = ZoneInfo("Australia/Sydney")


def _to_aus_date(dt: Any) -> Optional[Any]:
    """Convert a DB datetime (stored as UTC) to Australian local date.

    The Squiggle API returns Australian-local datetimes (e.g. ``2026-03-14T18:30:00+11:00``)
    which asyncpg converts to UTC before storing in a ``DateTime`` column (no tzinfo).
    To match against AFL Tables Australian local dates, we convert back.
    """
    if dt is None:
        return None
    if hasattr(dt, "date"):
        # Treat naive datetime as UTC, convert to AUS Eastern
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        aus_dt = dt.astimezone(_AUS_EASTERN)
        return aus_dt.date()
    return dt  # already a date


def _date_match(db_date: Any, match_date: Any, tolerance_days: int = 1) -> bool:
    """Check if two dates match within a tolerance.

    The DB stores game times in UTC; AFL Tables uses Australian local dates.
    A game at e.g. 20:30 AEDT on Mar 15 is stored as 09:30 UTC on Mar 15 — same date.
    However, edge cases (late-night games, DST transitions) can shift the date by ±1 day.
    """
    if db_date is None or match_date is None:
        return False
    delta = abs((db_date - match_date).days)
    return delta <= tolerance_days


def _canonical_team(name: str) -> str:
    """Map any team name to its canonical (Squiggle) form."""
    return _ALIAS_TO_CANONICAL.get(name.strip().lower(), name.strip())


async def match_games(session: AsyncSession, input_dir: str, verbose: bool = False) -> int:
    """Match AFL Tables game IDs to DB games by team-pair + chronological order.

    The Squiggle API and AFL Tables may have different round structures and dates
    for the same matchups.  This function matches games by:

    1. **Team pair**: canonical team names (handles aliases and home/away swaps).
    2. **Chronological order**: both AFL Tables and Squiggle games are sorted by
       date; the *i*-th AFL Tables game for a team pair maps to the *i*-th
       Squiggle game for the same pair.  In a 23-round season each pair meets
       exactly twice, so this produces a reliable 1:1 mapping.
    3. **Date validation**: matched pairs are checked for implausible date gaps
       (> 30 days) and logged as warnings.

    Updates ``games.afltables_match_id`` for matched games.

    Returns number of games matched.
    """
    filepath = os.path.join(input_dir, "match_details.csv")
    rows = read_csv(filepath)
    if not rows:
        if verbose:
            print("  No match_details.csv found.")
        return 0

    if verbose:
        print(f"  Matching {len(rows)} AFL Tables games to DB games...")

    # Pre-load all season games into memory
    season = int(rows[0].get("season", "2026")) if rows else 2026
    result = await session.execute(
        select(Game).where(Game.season == season)
    )
    db_games = result.scalars().all()

    if verbose:
        print(f"  Found {len(db_games)} DB games for season {season}")

    # ------------------------------------------------------------------
    # Step 1: Parse CSV rows → (date, team_key, afl_id) and sort by date
    # ------------------------------------------------------------------
    csv_entries: List[tuple] = []  # (date, team_key, afl_id, home_raw, away_raw)
    for row in rows:
        afl_id = row.get("game_id", "").strip()
        home_raw = row.get("home_team", "").strip()
        away_raw = row.get("away_team", "").strip()
        date_str = row.get("match_date", "").strip()
        if not all([afl_id, home_raw, away_raw, date_str]):
            continue
        try:
            match_date = datetime.strptime(date_str, "%Y-%m-%d").date()
        except ValueError:
            continue
        team_key = frozenset({_canonical_team(home_raw), _canonical_team(away_raw)})
        csv_entries.append((match_date, team_key, afl_id, home_raw, away_raw))

    csv_entries.sort(key=lambda e: e[0])

    # ------------------------------------------------------------------
    # Step 2: Group DB games by team-pair, sorted by date
    # ------------------------------------------------------------------
    db_by_pair: Dict[frozenset, List[Any]] = {}
    for game in db_games:
        key = frozenset({
            _canonical_team(game.home_team or ""),
            _canonical_team(game.away_team or ""),
        })
        db_by_pair.setdefault(key, []).append(game)

    # Sort each group chronologically
    for key in db_by_pair:
        db_by_pair[key].sort(key=lambda g: g.date if g.date else datetime.min)

    # ------------------------------------------------------------------
    # Step 3: Group AFL Tables entries by team-pair, sorted by date
    # ------------------------------------------------------------------
    csv_by_pair: Dict[frozenset, List[tuple]] = {}
    for entry in csv_entries:
        csv_by_pair.setdefault(entry[1], []).append(entry)

    # ------------------------------------------------------------------
    # Step 4: Match i-th CSV → i-th DB for each team-pair
    # ------------------------------------------------------------------
    count = 0
    unmatched_examples: List[str] = []
    warnings: List[str] = []

    for team_key, csv_group in csv_by_pair.items():
        db_group = db_by_pair.get(team_key, [])
        if not db_group:
            for entry in csv_group[:3]:
                unmatched_examples.append(
                    f"  {entry[3]} vs {entry[4]} on {entry[0]} (no DB games for this team pair)"
                )
            continue

        for idx, entry in enumerate(csv_group):
            match_date, _, afl_id, home_raw, away_raw = entry

            if idx >= len(db_group):
                unmatched_examples.append(
                    f"  {home_raw} vs {away_raw} on {match_date} "
                    f"(more AFL games than DB games for this pair)"
                )
                continue

            db_game = db_group[idx]

            if db_game.afltables_match_id:
                # Already consumed by a prior CSV entry (shouldn't happen with
                # the index-based approach, but guard anyway)
                unmatched_examples.append(
                    f"  {home_raw} vs {away_raw} on {match_date} (DB slot already used)"
                )
                continue

            # Validate date gap
            db_date = _to_aus_date(db_game.date)
            if db_date is not None:
                delta = abs((db_date - match_date).days)
                if delta > 30:
                    warnings.append(
                        f"  {home_raw} vs {away_raw}: AFL {match_date}, "
                        f"DB {db_date} (delta={delta}d) — fixtures may not align"
                    )

            db_game.afltables_match_id = afl_id
            count += 1

    await session.flush()

    if verbose:
        print(f"  -> Matched {count} games (unmatched: {len(csv_entries) - count})")
        if warnings:
            print(f"  Date-gap warnings ({len(warnings)}):")
            for w in warnings[:10]:
                print(w)
        if unmatched_examples:
            print("  Sample unmatched:")
            for ex in unmatched_examples[:10]:
                print(f"    {ex}")

    return count


# ---------------------------------------------------------------------------
# Seed functions
# ---------------------------------------------------------------------------


async def load_players(session: AsyncSession, input_dir: str, verbose: bool = False) -> int:
    """Load players from CSV into the database.

    Uses batch INSERT ... ON CONFLICT (name) DO NOTHING for speed.
    """
    filepath = os.path.join(input_dir, "players.csv")
    rows = read_csv(filepath)
    if not rows:
        if verbose:
            print("  No players.csv found or empty.")
        return 0

    if verbose:
        print(f"  Loading {len(rows)} players from CSV...")

    batch_size = 500
    total_inserted = 0
    batch: List[dict] = []

    for row in rows:
        name = row.get("name", "").strip()
        if not name:
            continue

        batch.append({"name": name})

        if len(batch) >= batch_size:
            result = await session.execute(
                pg_insert(Player).values(batch).on_conflict_do_nothing(
                    index_elements=["name"]
                )
            )
            total_inserted += result.rowcount
            batch = []

    # Flush remaining
    if batch:
        result = await session.execute(
            pg_insert(Player).values(batch).on_conflict_do_nothing(
                index_elements=["name"]
            )
        )
        total_inserted += result.rowcount

    if verbose:
        print(f"  -> Created {total_inserted} new player records (skipped {len(rows) - total_inserted} existing)")

    return total_inserted


async def load_player_match_stats(
    session: AsyncSession,
    input_dir: str,
    verbose: bool = False,
) -> int:
    """Load player match stats from CSV into the database.

    Uses batch INSERT ... ON CONFLICT DO NOTHING for speed.
    ~150K rows load in ~30s instead of ~2.5h with per-row SELECT+INSERT.

    Requires players and games to already exist in the DB.
    Maps CSV game_id (AFL Tables format) → games.id via afltables_match_id.
    Maps player_name → players.id.
    """
    filepath = os.path.join(input_dir, "player_match_stats.csv")
    rows = read_csv(filepath)
    if not rows:
        if verbose:
            print("  No player_match_stats.csv found or empty.")
        return 0

    # Build game ID map: afltables_match_id → games.id
    result = await session.execute(
        select(Game.id, Game.afltables_match_id).where(Game.afltables_match_id.isnot(None))
    )
    game_map: Dict[str, int] = {
        str(r.afltables_match_id): r.id for r in result
    }

    # Build player name map: name → players.id
    result = await session.execute(select(Player.id, Player.name))
    player_map: Dict[str, int] = {r.name: r.id for r in result}

    if verbose:
        print(f"  Game map: {len(game_map)} games, Player map: {len(player_map)} players")
        print(f"  Loading {len(rows)} player match stats from CSV...")

    # Build batch of dicts for bulk insert
    batch: List[dict] = []
    skipped_game = 0
    skipped_player = 0
    batch_size = 500
    total_inserted = 0

    for row in rows:
        afltables_id = row.get("game_id", "").strip()
        player_name = row.get("player_name", "").strip()

        db_game_id = game_map.get(afltables_id)
        if not db_game_id:
            skipped_game += 1
            continue

        db_player_id = player_map.get(player_name)
        if not db_player_id:
            skipped_player += 1
            continue

        batch.append({
            "game_id": db_game_id,
            "player_id": db_player_id,
            "team": row.get("team"),
            "kicks": int(row.get("kicks") or 0),
            "handballs": int(row.get("handballs") or 0),
            "disposals": int(row.get("disposals") or 0),
            "marks": int(row.get("marks") or 0),
            "goals": int(row.get("goals") or 0),
            "behinds": int(row.get("behinds") or 0),
            "tackles": int(row.get("tackles") or 0),
            "hitouts": int(row.get("hitouts") or 0),
            "frees_for": int(row.get("frees_for") or 0),
            "frees_against": int(row.get("frees_against") or 0),
        })

        if len(batch) >= batch_size:
            stmt = pg_insert(PlayerMatchStats).values(batch).on_conflict_do_nothing(
                index_elements=["game_id", "player_id"]
            )
            result = await session.execute(stmt)
            total_inserted += result.rowcount  # type: ignore[union-attr]
            batch = []

    # Flush remaining
    if batch:
        stmt = pg_insert(PlayerMatchStats).values(batch).on_conflict_do_nothing(
            index_elements=["game_id", "player_id"]
        )
        result = await session.execute(stmt)
        total_inserted += result.rowcount  # type: ignore[union-attr]

    if verbose:
        print(f"  -> Created {total_inserted} stat records")
        if skipped_game:
            print(f"     Skipped {skipped_game} (game not in DB)")
        if skipped_player:
            print(f"     Skipped {skipped_player} (player not in DB)")

    return total_inserted


async def load_match_weather(
    session: AsyncSession,
    input_dir: str,
    verbose: bool = False,
) -> int:
    """Load match weather from CSV into the database.

    Uses batch INSERT ... ON CONFLICT (game_id) DO NOTHING for speed.
    Maps CSV game_id (AFL Tables format) → games.id via afltables_match_id.
    """
    filepath = os.path.join(input_dir, "match_weather.csv")
    rows = read_csv(filepath)
    if not rows:
        if verbose:
            print("  No match_weather.csv found or empty.")
        return 0

    # Build game ID map
    result = await session.execute(
        select(Game.id, Game.afltables_match_id).where(Game.afltables_match_id.isnot(None))
    )
    game_map: Dict[str, int] = {
        str(r.afltables_match_id): r.id for r in result
    }

    if verbose:
        print(f"  Loading {len(rows)} weather records from CSV...")

    batch: List[dict] = []
    skipped_game = 0
    batch_size = 500
    total_inserted = 0

    for row in rows:
        afltables_id = row.get("game_id", "").strip()
        db_game_id = game_map.get(afltables_id)
        if not db_game_id:
            skipped_game += 1
            continue

        batch.append({
            "game_id": db_game_id,
            "venue": row.get("venue"),
            "data_type": row.get("data_type", "historical"),
            "temperature": float(row["temperature"]) if row.get("temperature") else None,
            "precipitation": float(row["precipitation"]) if row.get("precipitation") else None,
            "wind_speed": float(row["wind_speed"]) if row.get("wind_speed") else None,
            "wind_direction": int(row["wind_direction"]) if row.get("wind_direction") else None,
            "wind_gusts": float(row["wind_gusts"]) if row.get("wind_gusts") else None,
            "humidity": int(row["humidity"]) if row.get("humidity") else None,
            "weather_code": int(row["weather_code"]) if row.get("weather_code") else None,
        })

        if len(batch) >= batch_size:
            stmt = pg_insert(MatchWeather).values(batch).on_conflict_do_nothing(
                index_elements=["game_id"]
            )
            result = await session.execute(stmt)
            total_inserted += result.rowcount  # type: ignore[union-attr]
            batch = []

    # Flush remaining
    if batch:
        stmt = pg_insert(MatchWeather).values(batch).on_conflict_do_nothing(
            index_elements=["game_id"]
        )
        result = await session.execute(stmt)
        total_inserted += result.rowcount  # type: ignore[union-attr]

    if verbose:
        print(f"  -> Created {total_inserted} weather records")
        if skipped_game:
            print(f"     Skipped {skipped_game} (game not in DB)")

    return total_inserted


async def load_injuries(
    session: AsyncSession,
    input_dir: str,
    verbose: bool = False,
) -> int:
    """Load injuries from CSV into the database.

    Uses INSERT ... ON CONFLICT (player_name, injury_type) DO UPDATE.
    Maps player_name → players.id where possible.
    """
    filepath = os.path.join(input_dir, "injuries.csv")
    rows = read_csv(filepath)
    if not rows:
        if verbose:
            print("  No injuries.csv found or empty.")
        return 0

    # Build player name map
    result = await session.execute(select(Player.id, Player.name))
    player_map: Dict[str, int] = {row.name: row.id for row in result}

    if verbose:
        print(f"  Loading {len(rows)} injury records from CSV...")

    count = 0
    for row in rows:
        player_name = row.get("player_name", "").strip()
        injury_type = row.get("injury_type", "").strip()
        team = row.get("team", "").strip()

        if not player_name:
            continue

        db_player_id = player_map.get(player_name)

        # Check for existing injury record
        existing = await session.execute(
            select(Injury).where(
                Injury.player_name == player_name,
                Injury.injury_type == injury_type,
            )
        )
        existing_injury = existing.scalar_one_or_none()
        if existing_injury:
            # Update existing
            existing_injury.team = team
            existing_injury.player_id = db_player_id
            existing_injury.return_timeline = row.get("return_timeline")
            existing_injury.scraped_at = datetime.now(timezone.utc)
            existing_injury.updated_at = datetime.now(timezone.utc)
        else:
            injury = Injury(
                player_id=db_player_id,
                player_name=player_name,
                team=team,
                injury_type=injury_type,
                return_timeline=row.get("return_timeline"),
                source="footywire",
                scraped_at=datetime.now(timezone.utc),
            )
            session.add(injury)
            count += 1

    await session.flush()

    if verbose:
        print(f"  -> Created {count} new injury records")

    return count


# ---------------------------------------------------------------------------
# Clear tables
# ---------------------------------------------------------------------------


_CLEAR_TABLES: List[str] = [
    "player_advanced_stats",
    "player_match_stats",
    "match_weather",
    "injuries",
    "players",
]


async def clear_player_tables(session: AsyncSession) -> None:
    """Clear data from the new tables in FK-safe order."""
    for table in _CLEAR_TABLES:
        await session.execute(text(f"DELETE FROM {table}"))
    await session.commit()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


async def _load_season_data(
    session: AsyncSession,
    input_dir: str,
    tables: Set[str],
    verbose: bool = False,
) -> Dict[str, int]:
    """Load per-season CSV data (match, players, stats, weather) in a single session.

    Commits after each step so progress is preserved on partial failure.
    """
    counts: Dict[str, int] = {}

    # --- Match games first (sets afltables_match_id) ---
    if verbose:
        print("  Matching AFL Tables game IDs to DB games...")
    match_count = await match_games(session, input_dir, verbose=verbose)
    await session.commit()
    if verbose and match_count == 0:
        print("  WARNING: No games matched. Player stats and weather may not load.")
        print("  Ensure the games table has season games synced from Squiggle.")
    if verbose:
        print(f"  Matched {match_count} games")

    # --- Players (must be first for FK dependencies) ---
    if "players" in tables:
        if verbose:
            print("  Loading players...")
        count = await load_players(session, input_dir, verbose=verbose)
        counts["players"] = count
        await session.commit()

    # --- Player Match Stats ---
    if "player_match_stats" in tables:
        if verbose:
            print("  Loading player match stats...")
        count = await load_player_match_stats(session, input_dir, verbose=verbose)
        counts["player_match_stats"] = count
        await session.commit()

    # --- Match Weather ---
    if "match_weather" in tables:
        if verbose:
            print("  Loading match weather...")
        count = await load_match_weather(session, input_dir, verbose=verbose)
        counts["match_weather"] = count
        await session.commit()

    return counts


async def load_csv_data(
    input_dir: str,
    tables: Optional[Set[str]] = None,
    clear: bool = False,
    verbose: bool = False,
    seasons: Optional[List[int]] = None,
    use_subdirs: bool = False,
) -> Dict[str, int]:
    """Load CSV data into the database.

    When *seasons* is provided with more than one season, each season's CSV
    files are expected in ``{input_dir}/{season}/`` sub-directories (as
    produced by ``scrape_to_csv.py --season-range``).  Injuries are loaded
    once from the top-level *input_dir*.

    Args:
        input_dir: Directory containing CSV files (or parent of season dirs).
        tables: Set of table names to load. Defaults to all.
        clear: Whether to clear existing data first.
        verbose: Whether to print progress.
        seasons: Optional list of seasons for multi-season loading.
        use_subdirs: If True, always use ``{input_dir}/{season}/`` sub-dirs
            even for a single season.  Set when ``--season-range`` is used.

    Returns:
        Dict mapping table names to total number of records created.
    """
    if tables is None:
        tables = {"players", "player_match_stats", "match_weather", "injuries"}

    all_counts: Dict[str, int] = {}

    engine = get_engine()
    session_factory = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )

    # --- Clear tables once ---
    if clear:
        async with session_factory() as session:
            if verbose:
                print("Clearing player data tables...")
            await clear_player_tables(session)
            await session.commit()
            if verbose:
                print("Player data cleared.")

    # --- Injuries (global, not per-season) ---
    if "injuries" in tables:
        async with session_factory() as session:
            if verbose:
                print("Loading injuries...")
            count = await load_injuries(session, input_dir, verbose=verbose)
            await session.commit()
            all_counts["injuries"] = count

    # --- Determine season directories ---
    if seasons and (len(seasons) > 1 or use_subdirs):
        season_dirs = [(s, os.path.join(input_dir, str(s))) for s in seasons]
    elif seasons and len(seasons) == 1:
        season_dirs = [(seasons[0], input_dir)]
    else:
        season_dirs = [(None, input_dir)]

    # --- Load per-season data ---
    for season, season_dir in season_dirs:
        if not os.path.isdir(season_dir):
            if verbose:
                print(f"  Skipping {season or 'default'}: {season_dir} not found")
            continue

        if verbose and season:
            print(f"\n{'='*60}")
            print(f"Loading season {season} from {season_dir}")
            print(f"{'='*60}")

        async with session_factory() as session:
            try:
                counts = await _load_season_data(
                    session, season_dir, tables, verbose=verbose
                )
                for k, v in counts.items():
                    all_counts[k] = all_counts.get(k, 0) + v
            except Exception:
                await session.rollback()
                raise

    if verbose:
        print("\nCSV load complete! Summary:")
        for table, count in all_counts.items():
            print(f"   {table}: {count} records")

    await engine.dispose()
    return all_counts


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Load verified CSV data into the database"
    )
    parser.add_argument(
        "--input-dir",
        type=str,
        default=DEFAULT_INPUT_DIR,
        help="Directory containing CSV files (default: backend-faas/data)",
    )
    parser.add_argument(
        "--season",
        type=int,
        nargs="*",
        default=None,
        help="Season(s) to load from subdirectories (e.g. --season 2024 2025)",
    )
    parser.add_argument(
        "--season-range",
        type=str,
        default=None,
        help="Season range inclusive, e.g. '2012:2026'",
    )
    parser.add_argument(
        "--table",
        type=str,
        action="append",
        dest="tables",
        choices=["players", "player_match_stats", "match_weather", "injuries"],
        help="Specific table(s) to load (default: all)",
    )
    parser.add_argument(
        "--clear",
        action="store_true",
        help="Clear player data tables before loading",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Print progress to stdout",
    )
    args = parser.parse_args()

    use_subdirs = False
    if args.season_range:
        parts = args.season_range.split(":")
        seasons = list(range(int(parts[0]), int(parts[1]) + 1))
        use_subdirs = True  # --season-range always uses subdirectories
    elif args.season:
        seasons = args.season
    else:
        seasons = None

    tables = set(args.tables) if args.tables else None

    asyncio.run(
        load_csv_data(
            input_dir=os.path.abspath(args.input_dir),
            tables=tables,
            clear=args.clear,
            verbose=args.verbose,
            seasons=seasons,
            use_subdirs=use_subdirs,
        )
    )


if __name__ == "__main__":
    main()
