#!/usr/bin/env python3
"""Scrape data from external APIs and write to CSV files.

No database interaction required — fetches from APIs and saves locally
for inspection before loading into the database.

Usage:
    # Scrape all data for current season
    uv run python scripts/scrape_to_csv.py

    # Scrape a specific season
    uv run python scripts/scrape_to_csv.py --season 2025

    # Scrape only injuries (fast, no DB needed)
    uv run python scripts/scrape_to_csv.py --table injuries

    # Scrape with limit for testing
    uv run python scripts/scrape_to_csv.py --table players --limit 5

    # Custom output directory
    uv run python scripts/scrape_to_csv.py --output-dir ./my_data
"""

import argparse
import asyncio
import csv
import os
import re
import sys
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set

# Ensure backend-faas is on sys.path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from bs4 import BeautifulSoup
import httpx

from packages.shared.afl_data import AFLTablesClient, FootyWireClient
from packages.shared.weather import WeatherClient

DEFAULT_OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "..", "data")

# ---------------------------------------------------------------------------
# CSV helpers
# ---------------------------------------------------------------------------


def write_csv(filepath: str, rows: List[dict], fieldnames: Optional[List[str]] = None) -> None:
    """Write a list of dicts to a CSV file."""
    if not rows:
        print(f"  -> {os.path.basename(filepath)}: 0 records (empty)")
        return

    if fieldnames is None:
        fieldnames = list(rows[0].keys())

    with open(filepath, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)

    print(f"  -> {os.path.basename(filepath)}: {len(rows)} records")


def read_csv(filepath: str) -> List[dict]:
    """Read a CSV file and return list of dicts."""
    if not os.path.exists(filepath):
        return []

    with open(filepath, "r", newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        return list(reader)


# ---------------------------------------------------------------------------
# Match metadata extraction from real AFL Tables HTML
# ---------------------------------------------------------------------------


# Mapping of abbreviated/alternate venue names → canonical names (WeatherClient keys)
_VENUE_NORMALIZE_MAP: Dict[str, str] = {
    # Dotted abbreviations used by AFL Tables (dots stripped before lookup)
    "SCG": "SCG",
    "MCG": "MCG",
    # Canonical names (pass through)
    "Marvel Stadium": "Marvel Stadium",
    "Adelaide Oval": "Adelaide Oval",
    "Optus Stadium": "Optus Stadium",
    "Gabba": "Gabba",
    "GMHBA Stadium": "GMHBA Stadium",
    "People First Stadium": "People First Stadium",
    "UTAS Stadium": "UTAS Stadium",
    "Manuka Oval": "Manuka Oval",
    # Historical / alternate names
    "Docklands": "Marvel Stadium",
    "Docklands Stadium": "Marvel Stadium",
    "Etihad Stadium": "Marvel Stadium",
    "Perth Stadium": "Optus Stadium",
    "Metricon Stadium": "People First Stadium",
    "Carrara": "People First Stadium",
    "Kardinia Park": "GMHBA Stadium",
    "Skoda Stadium": "GMHBA Stadium",
    "York Park": "UTAS Stadium",
    "Aurora Stadium": "UTAS Stadium",
    "Sydney Showground": "People First Stadium",
    "Stadium Australia": "Accor Stadium",
}


def _normalize_venue(raw: str) -> str:
    """Normalize an abbreviated AFL Tables venue name to canonical form.

    AFL Tables uses dotted abbreviations like "S.C.G.", "M.C.G." and
    historical names like "Etihad Stadium".  This function strips dots,
    trims whitespace / arrow characters, and maps to the canonical names
    used by WeatherClient.VENUE_COORDS.

    Returns the original (stripped) string if no mapping is found.
    """
    # Strip trailing arrow character (→) used on AFL Tables pages
    cleaned = raw.replace(".", "").replace(",", "").rstrip("\u2192").strip()
    raw_stripped = raw.rstrip("\u2192").strip()
    # Try dot-stripped version first, then original
    return _VENUE_NORMALIZE_MAP.get(cleaned, _VENUE_NORMALIZE_MAP.get(raw_stripped, raw_stripped))


def _extract_match_metadata(soup: BeautifulSoup) -> Dict[str, Any]:
    """Extract teams, date, and venue from an AFL Tables match page.

    The page <title> contains: "AFL Tables - Sydney v Carlton - Thu, 5-Mar-2026 7:30 PM ..."

    Venue is extracted using three fallback methods:
      1. "Venue: XXX" pattern in page header text (most reliable)
      2. Venue link in HTML (e.g. <a href="../../venues/scg.html">S.C.G.</a>)
      3. Legacy "at VENUE" pattern in page text

    Returns:
        Dict with home_team, away_team, match_date, venue.
    """
    result: Dict[str, Any] = {
        "home_team": None,
        "away_team": None,
        "match_date": None,
        "venue": None,
    }

    # Parse title for teams and date
    title = soup.find("title")
    if title:
        title_text = title.get_text()
        # "AFL Tables - Sydney v Carlton - Thu, 5-Mar-2026 7:30 PM (6:30 PM) - Match Stats"
        title_match = re.match(
            r"AFL Tables - (.+?) v (.+?) - .+?,\s*(\d{1,2}-\w{3}-\d{4})",
            title_text,
        )
        if title_match:
            result["home_team"] = title_match.group(1).strip()
            result["away_team"] = title_match.group(2).strip()
            date_str = title_match.group(3)
            try:
                result["match_date"] = datetime.strptime(date_str, "%d-%b-%Y").strftime(
                    "%Y-%m-%d"
                )
            except ValueError:
                pass

    text = soup.get_text()

    # Method 1: "Venue: XXX" pattern in page header (most common on AFL Tables)
    # Real pages have: "Venue: S.C.G. Date: ..." (single space before Date:)
    venue_match = re.search(r"Venue:\s*(.+?)(?:\s+Date:|\s{2,}|\n)", text[:3000])
    if venue_match:
        raw_venue = venue_match.group(1).strip().rstrip("\u2192").strip()
        result["venue"] = _normalize_venue(raw_venue)

    # Method 2: Try venue link in HTML (e.g. <a href="../../venues/scg.html">S.C.G.</a>)
    if not result["venue"]:
        for a in soup.find_all("a", href=True):
            href = str(a["href"])
            if "venues/" in href:
                raw_venue = a.get_text(strip=True)
                result["venue"] = _normalize_venue(raw_venue)
                break

    # Method 3: Legacy "at VENUE" pattern in page text
    if not result["venue"]:
        venue_match = re.search(
            r"at\s+(MCG|Marvel Stadium|Adelaide Oval|Optus Stadium|Gabba|SCG|"
            r"GMHBA Stadium|People First Stadium|UTAS Stadium|Manuka Oval|"
            r"Docklands Stadium|Etihad Stadium|Perth Stadium|Metricon Stadium|"
            r"Kardinia Park|Carrara|York Park|Aurora Stadium|Skoda Stadium)",
            text[:5000],
        )
        if venue_match:
            result["venue"] = _normalize_venue(venue_match.group(1))

    return result


def _parse_player_stats(soup: BeautifulSoup) -> Dict[str, List[dict]]:
    """Parse player stats from AFL Tables sortable tables.

    Real AFL Tables pages have:
    - 4 sortable tables (2 teams x 2 tables: basic + advanced)
    - Table 0 = home team basic stats, Table 1 = away team basic stats
    - Tables 2-3 = advanced stats (TOG, metres gained, etc.)
    - Row 0 = header with team name (e.g., "Sydney Match Statistics")
    - Row 1 = column headers ("#", "Player", "KI", "MK", "HB", ...)
    - Rows 2+ = data rows with 25 columns

    Column mapping for basic stats (25 cols):
        0: #, 1: Player, 2: KI, 3: MK, 4: HB, 5: DI, 6: GL, 7: BH,
        8: TK, 9: HO, 10: FF, 11: FA, ...

    Returns:
        Dict with "home_players" and "away_players" lists of player stat dicts.
    """
    tables = soup.find_all("table", class_="sortable")

    result: Dict[str, List[dict]] = {
        "home_players": [],
        "away_players": [],
    }

    for idx, table in enumerate(tables[:2]):  # Only first 2 tables (basic stats)
        team_key = "home_players" if idx == 0 else "away_players"
        tbody = table.find("tbody")
        if not tbody:
            continue

        for row in tbody.find_all("tr"):
            cells = row.find_all("td")
            if len(cells) < 12:
                continue

            # Get player name from cells[1] (cells[0] is jumper number)
            name_cell = cells[1]
            anchor = name_cell.find("a")
            name = anchor.get_text(strip=True) if anchor else name_cell.get_text(strip=True)

            if not name or name.lower() == "totals":
                continue

            try:
                player = {
                    "name": name,
                    "kicks": int(cells[2].get_text(strip=True) or "0"),
                    "marks": int(cells[3].get_text(strip=True) or "0"),
                    "handballs": int(cells[4].get_text(strip=True) or "0"),
                    "disposals": int(cells[5].get_text(strip=True) or "0"),
                    "goals": int(cells[6].get_text(strip=True) or "0"),
                    "behinds": int(cells[7].get_text(strip=True) or "0"),
                    "tackles": int(cells[8].get_text(strip=True) or "0"),
                    "hitouts": int(cells[9].get_text(strip=True) or "0"),
                    "frees_for": int(cells[10].get_text(strip=True) or "0"),
                    "frees_against": int(cells[11].get_text(strip=True) or "0"),
                }
                result[team_key].append(player)
            except (ValueError, IndexError):
                continue

    return result


# ---------------------------------------------------------------------------
# Scrape injuries
# ---------------------------------------------------------------------------


async def scrape_injuries(output_dir: str, verbose: bool = False) -> int:
    """Scrape current injury list from FootyWire and write to CSV.

    Returns:
        Number of injury records.
    """
    if verbose:
        print("\n--- Scraping injuries from FootyWire ---")

    async with FootyWireClient() as client:
        injuries = await client.get_injury_list()

    if not injuries:
        if verbose:
            print("  No injury data returned")
        write_csv(os.path.join(output_dir, "injuries.csv"), [])
        return 0

    rows = []
    now = datetime.now(timezone.utc).isoformat()
    for inj in injuries:
        rows.append(
            {
                "player_name": inj.get("player", ""),
                "team": inj.get("team", ""),
                "injury": inj.get("injury", ""),
                "return_timeline": inj.get("return_timeline", ""),
                "source": "footywire",
                "scraped_at": now,
            }
        )

    write_csv(os.path.join(output_dir, "injuries.csv"), rows)
    return len(rows)


# ---------------------------------------------------------------------------
# Scrape season games
# ---------------------------------------------------------------------------


async def scrape_season_games(
    season: int,
    output_dir: str,
    verbose: bool = False,
) -> List[Dict[str, str]]:
    """Scrape season game listings from AFL Tables and write to CSV.

    Returns:
        List of game info dicts (game_id, round, url).
    """
    if verbose:
        print(f"\n--- Scraping season {season} game listings from AFL Tables ---")

    async with AFLTablesClient() as client:
        games = await client.get_season_games(season)

    if not games:
        if verbose:
            print(f"  No games found for season {season}")
        return []

    rows = [{"game_id": g["game_id"], "round": g["round"], "season": season} for g in games]
    write_csv(os.path.join(output_dir, "season_games.csv"), rows)

    if verbose:
        print(f"  Found {len(games)} games for season {season}")

    return games


# ---------------------------------------------------------------------------
# Scrape player data and match stats
# ---------------------------------------------------------------------------


async def scrape_players_and_stats(
    season: int,
    output_dir: str,
    limit: int = 0,
    verbose: bool = False,
) -> Dict[str, int]:
    """Scrape player stats from AFL Tables match pages and write to CSVs.

    For each game in the season, fetches the match page which contains:
    - Match metadata (teams, date, venue) from page <title>
    - Player stats from sortable tables

    Writes:
    - match_details.csv: Game-level info (game_id, venue, date, teams)
    - players.csv: Unique player names
    - player_match_stats.csv: Per-player per-game stats

    Args:
        season: Season year.
        output_dir: Directory for CSV output.
        limit: Max games to process (0 = all).
        verbose: Print progress.

    Returns:
        Dict with counts of players, stat records, and matches.
    """
    if verbose:
        print(f"\n--- Scraping player data for season {season} from AFL Tables ---")

    # Get season games listing
    async with AFLTablesClient() as client:
        games = await client.get_season_games(season)

    if not games:
        if verbose:
            print(f"  No games found for season {season}")
        return {"players": 0, "player_match_stats": 0, "match_details": 0}

    if limit > 0:
        games = games[:limit]
        if verbose:
            print(f"  Limited to first {limit} games")

    all_player_names: Set[str] = set()
    stat_rows: List[dict] = []
    match_details: List[dict] = []
    seen_players: Dict[str, int] = {}
    player_counter = 0

    async with httpx.AsyncClient(
        timeout=30.0,
        headers={
            "User-Agent": (
                "WhatIsMyTip/1.0 (contact@whatismytip.com) "
                "- Non-commercial research"
            ),
        },
    ) as http_client:
        for i, game in enumerate(games):
            game_id = game["game_id"]
            if verbose:
                print(f"  [{i + 1}/{len(games)}] Fetching match {game_id}...")

            try:
                url = f"{AFLTablesClient.BASE_URL}/stats/games/{game_id}.html"
                response = await http_client.get(url)
                response.raise_for_status()
                html = response.text
                soup = BeautifulSoup(html, "lxml")

                # Extract metadata from page
                metadata = _extract_match_metadata(soup)

                # Parse player stats with our real-HTML-aware parser
                match_data = _parse_player_stats(soup)
            except Exception as e:
                if verbose:
                    print(f"    WARNING: Failed to fetch match {game_id}: {e}")
                continue

            # Save match detail
            match_details.append(
                {
                    "game_id": game_id,
                    "home_team": metadata.get("home_team", ""),
                    "away_team": metadata.get("away_team", ""),
                    "venue": metadata.get("venue", ""),
                    "match_date": metadata.get("match_date", ""),
                    "round": game.get("round", ""),
                    "season": season,
                }
            )

            # Process player stats
            for team_key in ("home_players", "away_players"):
                team_name = metadata.get(
                    f"{'home' if team_key == 'home_players' else 'away'}_team", ""
                )
                for player_data in match_data.get(team_key, []):
                    name = player_data.get("name", "").strip()
                    if not name:
                        continue

                    if name not in seen_players:
                        player_counter += 1
                        seen_players[name] = player_counter
                        all_player_names.add(name)

                    stat_rows.append(
                        {
                            "game_id": game_id,
                            "player_name": name,
                            "player_id": seen_players[name],
                            "team": team_name,
                            "kicks": player_data.get("kicks", 0),
                            "handballs": player_data.get("handballs", 0),
                            "disposals": player_data.get("disposals", 0),
                            "marks": player_data.get("marks", 0),
                            "goals": player_data.get("goals", 0),
                            "behinds": player_data.get("behinds", 0),
                            "tackles": player_data.get("tackles", 0),
                            "hitouts": player_data.get("hitouts", 0),
                            "frees_for": player_data.get("frees_for", 0),
                            "frees_against": player_data.get("frees_against", 0),
                        }
                    )

    # Write CSVs
    player_rows = [
        {"id": seen_players[name], "name": name}
        for name in sorted(seen_players, key=lambda n: seen_players[n])
    ]

    write_csv(
        os.path.join(output_dir, "players.csv"),
        player_rows,
        fieldnames=["id", "name"],
    )
    write_csv(os.path.join(output_dir, "player_match_stats.csv"), stat_rows)
    write_csv(os.path.join(output_dir, "match_details.csv"), match_details)

    return {
        "players": len(all_player_names),
        "player_match_stats": len(stat_rows),
        "match_details": len(match_details),
    }


# ---------------------------------------------------------------------------
# Scrape weather
# ---------------------------------------------------------------------------


async def scrape_weather(
    season: int,
    output_dir: str,
    limit: int = 0,
    verbose: bool = False,
) -> int:
    """Scrape weather data for games and write to CSV.

    Reads match_details.csv (from a previous scrape_players_and_stats run)
    to get venue/date pairs, then fetches weather from Open-Meteo.

    Returns:
        Number of weather records.
    """
    details_path = os.path.join(output_dir, "match_details.csv")

    if not os.path.exists(details_path):
        if verbose:
            print(
                "\n  match_details.csv not found. Run --table players first to generate game data."
            )
        return 0

    match_details = read_csv(details_path)
    if not match_details:
        if verbose:
            print("\n  No match details found in CSV.")
        return 0

    if limit > 0:
        match_details = match_details[:limit]

    if verbose:
        print(f"\n--- Scraping weather data for {len(match_details)} games ---")

    weather_rows: List[dict] = []

    async with WeatherClient() as client:
        for i, game in enumerate(match_details):
            game_id = game.get("game_id", "")
            venue = game.get("venue", "")
            match_date_str = game.get("match_date", "")

            if not venue or not match_date_str:
                if verbose:
                    print(f"  [{i + 1}] Skipping game {game_id}: missing venue or date")
                continue

            if verbose:
                print(
                    f"  [{i + 1}/{len(match_details)}] Fetching weather for {venue} on {match_date_str}..."
                )

            try:
                match_date = datetime.strptime(match_date_str, "%Y-%m-%d").date()
            except ValueError:
                if verbose:
                    print(f"    Invalid date: {match_date_str}")
                continue

            try:
                weather = await client.get_match_day_weather(
                    venue=venue,
                    match_date=match_date,
                )
            except Exception as e:
                if verbose:
                    print(f"    WARNING: Weather fetch failed: {e}")
                continue

            if not weather or "hourly" not in weather:
                if verbose:
                    print("    No weather data returned")
                continue

            hourly = weather.get("hourly", {})
            times = hourly.get("time", [])
            mid_idx = len(times) // 2 if times else 0

            def _get_val(key: str, idx: int = mid_idx, default=None):
                values = hourly.get(key, [])
                if idx < len(values):
                    return values[idx]
                return default

            match_dt = datetime.strptime(match_date_str, "%Y-%m-%d")
            data_type = "historical" if match_dt < datetime.now() else "forecast"

            weather_rows.append(
                {
                    "game_id": game_id,
                    "venue": venue,
                    "match_date": match_date_str,
                    "temperature": _get_val("temperature_2m"),
                    "precipitation": _get_val("precipitation"),
                    "wind_speed": _get_val("windspeed_10m"),
                    "wind_direction": _get_val("winddirection_10m"),
                    "wind_gusts": _get_val("windgusts_10m"),
                    "humidity": _get_val("relative_humidity_2m"),
                    "weather_code": _get_val("weathercode"),
                    "data_type": data_type,
                }
            )

    write_csv(os.path.join(output_dir, "match_weather.csv"), weather_rows)
    return len(weather_rows)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


async def async_main(
    seasons: List[int],
    tables: Set[str],
    limit: int,
    output_dir: str,
    verbose: bool,
) -> None:
    """Run all requested scrape operations."""
    os.makedirs(output_dir, exist_ok=True)

    if verbose:
        print(f"Output directory: {os.path.abspath(output_dir)}")
        print(f"Seasons: {seasons}")
        print(f"Tables: {tables}")

    counts: Dict[str, int] = {}

    for season in seasons:
        # Injuries (no season dependency, same for all)
        if "injuries" in tables:
            counts["injuries"] = await scrape_injuries(output_dir, verbose=verbose)

        # Season games listing
        if "season_games" in tables:
            await scrape_season_games(season, output_dir, verbose=verbose)

        # Players + match stats
        if "players" in tables or "player_match_stats" in tables:
            result = await scrape_players_and_stats(
                season, output_dir, limit=limit, verbose=verbose
            )
            counts["players"] = result.get("players", 0)
            counts["player_match_stats"] = result.get("player_match_stats", 0)
            counts["match_details"] = result.get("match_details", 0)

        # Weather (depends on match_details.csv from players step)
        if "match_weather" in tables:
            weather_count = await scrape_weather(
                season, output_dir, limit=limit, verbose=verbose
            )
            counts["match_weather"] = weather_count

    if verbose:
        print("\n--- Summary ---")
        for table, count in counts.items():
            print(f"   {table}: {count} records")
        print(f"\nCSV files written to: {os.path.abspath(output_dir)}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Scrape data from external APIs and write to CSV files (no DB required)"
    )
    parser.add_argument(
        "--season",
        type=int,
        nargs="*",
        default=None,
        help="Season(s) to scrape (default: current year)",
    )
    parser.add_argument(
        "--table",
        type=str,
        action="append",
        dest="tables",
        choices=[
            "injuries",
            "season_games",
            "players",
            "player_match_stats",
            "match_weather",
        ],
        help="Specific table(s) to scrape (default: all)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Limit number of games to process (for testing, 0 = all)",
    )
    parser.add_argument(
        "--output-dir",
        default=None,
        help=f"Output directory (default: {DEFAULT_OUTPUT_DIR})",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Print progress to stdout",
    )
    args = parser.parse_args()

    seasons = args.season if args.season else [datetime.now().year]
    tables = set(args.tables) if args.tables else {
        "injuries", "season_games", "players", "player_match_stats", "match_weather"
    }
    output_dir = args.output_dir or DEFAULT_OUTPUT_DIR

    # If player_match_stats requested, also include players (dependency)
    if "player_match_stats" in tables:
        tables.add("players")

    # If match_weather requested, also include players (generates match_details.csv)
    if "match_weather" in tables:
        tables.add("players")

    asyncio.run(
        async_main(
            seasons=seasons,
            tables=tables,
            limit=args.limit,
            output_dir=output_dir,
            verbose=args.verbose,
        )
    )


if __name__ == "__main__":
    main()
