"""Client for footywire.com — advanced stats, injuries, team selections.

Scrapes injury lists, team selections, and advanced player statistics.
Follows the same patterns as AFLTablesClient (httpx.AsyncClient + Redis cache).
"""

import httpx
from typing import Any, Dict, List
from bs4 import BeautifulSoup

from ..cache import medium_cache
from ..logger import get_logger

logger = get_logger(__name__)


class FootyWireClient:
    """Scraper for footywire.com — advanced stats, injuries, team selections."""

    BASE_URL = "https://www.footywire.com/afl/footy"

    def __init__(self) -> None:
        self.client = httpx.AsyncClient(
            timeout=30.0,
            headers={
                "User-Agent": (
                    "WhatIsMyTip/1.0 (contact@whatismytip.com) "
                    "- Non-commercial research"
                ),
            },
        )

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        await self.client.aclose()

    async def __aenter__(self) -> "FootyWireClient":
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        await self.close()

    async def get_injury_list(self) -> List[Dict[str, Any]]:
        """Scrape current injury list from FootyWire.

        Returns:
            List of dicts with team, player, injury, return_timeline keys.
            Empty list on error.
        """
        cache_key = "footywire:injuries"

        # Check cache first
        cached = await medium_cache.get(cache_key)
        if cached is not None:
            logger.debug(f"Cache hit for injuries: {cache_key}")
            return cached

        url = f"{self.BASE_URL}/injury_list"

        try:
            response = await self.client.get(url)
            response.raise_for_status()
        except Exception as e:
            logger.error(f"FootyWire injury list error: {e}")
            return []

        soup = BeautifulSoup(response.text, "lxml")
        result = self._parse_injury_table(soup)

        await medium_cache.set(cache_key, result)
        logger.debug(f"Cache set for injuries: {cache_key}")

        return result

    async def get_team_selections(
        self, round_num: int, year: int
    ) -> List[Dict[str, Any]]:
        """Scrape team selections for a specific round.

        Args:
            round_num: Round number (e.g. 5)
            year: Season year (e.g. 2025)

        Returns:
            List of dicts with team, in, out keys. Empty list on error.
        """
        cache_key = f"footywire:team_selections:{year}:{round_num}"

        # Check cache first
        cached = await medium_cache.get(cache_key)
        if cached is not None:
            logger.debug(f"Cache hit for team selections: {cache_key}")
            return cached

        url = f"{self.BASE_URL}/afl_team_selections"
        params = {"year": year, "round": round_num}

        try:
            response = await self.client.get(url, params=params)
            response.raise_for_status()
        except Exception as e:
            logger.error(f"FootyWire team selections error: {e}")
            return []

        soup = BeautifulSoup(response.text, "lxml")
        result = self._parse_team_selections(soup)

        await medium_cache.set(cache_key, result)
        logger.debug(f"Cache set for team selections: {cache_key}")

        return result

    async def get_player_advanced_stats(
        self, player_id: int, year: int
    ) -> List[Dict[str, Any]]:
        """Fetch advanced stats for a specific player.

        Args:
            player_id: FootyWire player identifier (e.g. 1234)
            year: Season year to filter stats (e.g. 2025)

        Returns:
            List of dicts with per-round advanced stats. Empty list on error.
        """
        cache_key = f"footywire:player_stats:{player_id}:{year}"

        # Check cache first
        cached = await medium_cache.get(cache_key)
        if cached is not None:
            logger.debug(f"Cache hit for player stats: {cache_key}")
            return cached

        url = f"{self.BASE_URL}/pp-{player_id}"

        try:
            response = await self.client.get(url)
            response.raise_for_status()
        except Exception as e:
            logger.error(f"FootyWire player stats error: {e}")
            return []

        soup = BeautifulSoup(response.text, "lxml")
        result = self._parse_player_stats(soup, year)

        await medium_cache.set(cache_key, result)
        logger.debug(f"Cache set for player stats: {cache_key}")

        return result

    def _parse_injury_table(self, soup: BeautifulSoup) -> List[Dict[str, Any]]:
        """Parse injury HTML tables from FootyWire.

        FootyWire structures the injury list as one <table> per team:
          - Row 0: Team header, e.g. "Brisbane Lions (14 Players)"
          - Row 1: Mobile/hidden row (skip — contains concatenated data)
          - Row 2: Column headers: Player | Injury | Returning
          - Row 3+: Individual injury rows with 3 cells each

        Args:
            soup: Parsed HTML document

        Returns:
            List of injury dicts with team, player, injury, return_timeline keys.
        """
        injuries: List[Dict[str, Any]] = []
        import re

        tables = soup.find_all("table")

        for table in tables:
            rows = table.find_all("tr")

            # Skip tiny tables (not injury data) and huge container tables
            # Individual team tables have ~7-25 rows; container tables have 200+
            if len(rows) < 3 or len(rows) > 35:
                continue

            # Extract team name from the first row
            first_row_cells = rows[0].find_all("td")
            if not first_row_cells:
                continue

            team_header = first_row_cells[0].get_text(strip=True)
            # Only process tables where row 0 matches "Team Name (N Players)"
            if not re.match(r".+\(\d+\s+Players?\)", team_header):
                continue
            # Extract team name before the parenthetical player count
            # e.g. "Brisbane Lions (14 Players)" → "Brisbane Lions"
            team_name = re.sub(r"\s*\(\d+\s+Players?\)", "", team_header).strip()

            # Parse data rows (skip header rows)
            for row in rows[1:]:
                cells = row.find_all("td")

                # Need exactly 3 cells: Player, Injury, Returning
                if len(cells) != 3:
                    continue

                player_text = cells[0].get_text(strip=True)
                injury_text = cells[1].get_text(strip=True)
                return_text = cells[2].get_text(strip=True)

                # Skip header rows
                if player_text.lower() == "player":
                    continue

                # Skip rows with empty player or injury
                if not player_text or not injury_text:
                    continue

                injuries.append(
                    {
                        "team": team_name,
                        "player": player_text,
                        "injury": injury_text,
                        "return_timeline": return_text,
                    }
                )

        return injuries

    def _parse_team_selections(self, soup: BeautifulSoup) -> List[Dict[str, Any]]:
        """Parse team selection tables from FootyWire.

        Extracts team name, players in, and players out from the selection page.

        Args:
            soup: Parsed HTML document

        Returns:
            List of dicts with team, in, out keys.
        """
        selections: List[Dict[str, Any]] = []

        tables = soup.find_all("table")

        for table in tables:
            for row in table.find_all("tr"):
                cells = row.find_all("td")

                if len(cells) < 3:
                    continue

                # Skip header rows
                if row.find("th"):
                    continue

                team = cells[0].get_text(strip=True)
                players_in = cells[1].get_text(strip=True)
                players_out = cells[2].get_text(strip=True)

                # Skip rows that look like headers
                if team.lower() == "team":
                    continue

                selections.append(
                    {
                        "team": team,
                        "in": players_in,
                        "out": players_out,
                    }
                )

        return selections

    def _parse_player_stats(
        self, soup: BeautifulSoup, year: int
    ) -> List[Dict[str, Any]]:
        """Parse player advanced stats table from FootyWire.

        Extracts per-round advanced metrics like TOG%, disposals, metres gained,
        score involvements, and contested possessions.

        Args:
            soup: Parsed HTML document
            year: Season year to filter stats

        Returns:
            List of dicts with per-round advanced stats.
        """
        stats: List[Dict[str, Any]] = []

        tables = soup.find_all("table")

        for table in tables:
            # Find the header row to identify column positions
            header_row = table.find("tr")
            if not header_row:
                continue

            headers = [th.get_text(strip=True).lower() for th in header_row.find_all("th")]
            if not headers:
                continue

            # Build column index map
            col_map = {}
            for idx, h in enumerate(headers):
                if "round" in h:
                    col_map["round"] = idx
                elif "opp" in h:
                    col_map["opponent"] = idx
                elif "tog" in h:
                    col_map["tog_pct"] = idx
                elif "disposal" in h:
                    col_map["disposals"] = idx
                elif "metres" in h:
                    col_map["metres_gained"] = idx
                elif "score involvements" in h or "score_inv" in h:
                    col_map["score_involvements"] = idx
                elif "contested pos" in h or "contested_pos" in h:
                    col_map["contested_possessions"] = idx

            # Need at least round to process
            if "round" not in col_map:
                continue

            for row in table.find_all("tr"):
                cells = row.find_all("td")
                if not cells:
                    continue

                try:
                    entry: Dict[str, Any] = {}

                    if "round" in col_map and col_map["round"] < len(cells):
                        entry["round"] = cells[col_map["round"]].get_text(strip=True)
                    if "opponent" in col_map and col_map["opponent"] < len(cells):
                        entry["opponent"] = cells[col_map["opponent"]].get_text(strip=True)
                    if "tog_pct" in col_map and col_map["tog_pct"] < len(cells):
                        entry["tog_pct"] = cells[col_map["tog_pct"]].get_text(strip=True)
                    if "disposals" in col_map and col_map["disposals"] < len(cells):
                        entry["disposals"] = int(
                            cells[col_map["disposals"]].get_text(strip=True)
                        )
                    if "metres_gained" in col_map and col_map["metres_gained"] < len(cells):
                        entry["metres_gained"] = int(
                            cells[col_map["metres_gained"]].get_text(strip=True)
                        )
                    if "score_involvements" in col_map and col_map["score_involvements"] < len(cells):
                        entry["score_involvements"] = int(
                            cells[col_map["score_involvements"]].get_text(strip=True)
                        )
                    if "contested_possessions" in col_map and col_map["contested_possessions"] < len(cells):
                        entry["contested_possessions"] = int(
                            cells[col_map["contested_possessions"]].get_text(strip=True)
                        )

                    if entry:
                        stats.append(entry)

                except (ValueError, IndexError):
                    continue

        return stats
