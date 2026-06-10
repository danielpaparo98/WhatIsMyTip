"""Client for afltables.com — historical match and player statistics.

Scrapes player stats, season game listings, and player profiles.
Follows the same patterns as WeatherClient (httpx.AsyncClient + Redis cache).
"""

import httpx
from typing import Any, Dict, List
from bs4 import BeautifulSoup

from ..cache import medium_cache
from ..logger import get_logger

logger = get_logger(__name__)


class AFLTablesClient:
    """Scraper for afltables.com — historical match and player statistics."""

    BASE_URL = "https://afltables.com/afl"

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

    async def __aenter__(self) -> "AFLTablesClient":
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        await self.close()

    async def get_player_stats_for_match(
        self, afltables_match_id: str
    ) -> Dict[str, Any]:
        """Scrape player statistics for a specific match.

        Args:
            afltables_match_id: AFLTables match identifier (e.g. "2025060101")

        Returns:
            Dict with home_players and away_players lists, or empty dict on error.
        """
        cache_key = f"afltables:match:{afltables_match_id}"

        # Check cache first
        cached = await medium_cache.get(cache_key)
        if cached is not None:
            logger.debug(f"Cache hit for match: {cache_key}")
            return cached

        url = f"{self.BASE_URL}/stats/games/{afltables_match_id}.html"

        try:
            response = await self.client.get(url)
            response.raise_for_status()
        except Exception as e:
            logger.error(f"AFL Tables match page error: {e}")
            return {}

        soup = BeautifulSoup(response.text, "lxml")
        result = self._parse_match_page(soup)

        await medium_cache.set(cache_key, result)
        logger.debug(f"Cache set for match: {cache_key}")

        return result

    async def get_season_games(self, year: int) -> List[Dict[str, str]]:
        """Get list of all game IDs for a season.

        Args:
            year: Season year (e.g. 2025)

        Returns:
            List of dicts with game_id, round, and url keys.
        """
        url = f"{self.BASE_URL}/seas/{year}.html"

        try:
            response = await self.client.get(url)
            response.raise_for_status()
        except Exception as e:
            logger.error(f"AFL Tables season page error: {e}")
            return []

        soup = BeautifulSoup(response.text, "lxml")
        games: List[Dict[str, str]] = []

        for link in soup.find_all("a", href=True):
            href = str(link["href"])
            if "/stats/games/" in href and href.endswith(".html"):
                # Extract game_id from URL like /afl/stats/games/20250101.html
                game_id = href.split("/games/")[-1].replace(".html", "")
                round_text = link.get_text(strip=True)
                games.append(
                    {"game_id": game_id, "round": round_text, "url": href}
                )

        return games

    async def get_player_profile(self, player_id: str) -> Dict[str, Any]:
        """Scrape player bio (born, height, weight, draft).

        Args:
            player_id: AFLTables player identifier (e.g. "01A/Player_One")

        Returns:
            Dict with bio fields, or empty dict on error.
        """
        cache_key = f"afltables:player:{player_id}"

        # Check cache first
        cached = await medium_cache.get(cache_key)
        if cached is not None:
            logger.debug(f"Cache hit for player: {cache_key}")
            return cached

        url = f"{self.BASE_URL}/stats/players/{player_id}.html"

        try:
            response = await self.client.get(url)
            response.raise_for_status()
        except Exception as e:
            logger.error(f"AFL Tables player profile error: {e}")
            return {}

        soup = BeautifulSoup(response.text, "lxml")
        result = self._parse_player_profile(soup)

        await medium_cache.set(cache_key, result)
        logger.debug(f"Cache set for player: {cache_key}")

        return result

    def _parse_match_page(self, soup: BeautifulSoup) -> Dict[str, Any]:
        """Parse player stats tables from match HTML.

        AFLTables match pages have two sortable tables: home team and away team.
        Each row contains: Player, K, H, D, M, G, B, T, HO, FF, FA.
        Totals rows are excluded.

        Args:
            soup: Parsed HTML document

        Returns:
            Dict with home_players and away_players lists.
        """
        tables = soup.find_all("table", class_="sortable")

        result: Dict[str, Any] = {
            "home_players": [],
            "away_players": [],
        }

        for idx, table in enumerate(tables):
            team_key = "home_players" if idx == 0 else "away_players"
            tbody = table.find("tbody")
            if not tbody:
                continue

            for row in tbody.find_all("tr"):
                cells = row.find_all("td")
                if len(cells) < 11:
                    continue

                # Get player name from anchor or cell text
                name_cell = cells[0]
                anchor = name_cell.find("a")
                name = anchor.get_text(strip=True) if anchor else name_cell.get_text(strip=True)

                # Skip totals rows
                if name.lower() == "totals":
                    continue

                try:
                    player = {
                        "name": name,
                        "kicks": int(cells[1].get_text(strip=True)),
                        "handballs": int(cells[2].get_text(strip=True)),
                        "disposals": int(cells[3].get_text(strip=True)),
                        "marks": int(cells[4].get_text(strip=True)),
                        "goals": int(cells[5].get_text(strip=True)),
                        "behinds": int(cells[6].get_text(strip=True)),
                        "tackles": int(cells[7].get_text(strip=True)),
                        "hitouts": int(cells[8].get_text(strip=True)),
                        "frees_for": int(cells[9].get_text(strip=True)),
                        "frees_against": int(cells[10].get_text(strip=True)),
                    }
                    result[team_key].append(player)
                except (ValueError, IndexError):
                    continue

        return result

    def _parse_player_profile(self, soup: BeautifulSoup) -> Dict[str, Any]:
        """Parse player bio fields from profile HTML.

        Extracts born, height, weight, and draft info from profile page tables.

        Args:
            soup: Parsed HTML document

        Returns:
            Dict with bio fields.
        """
        result: Dict[str, Any] = {}

        for row in soup.find_all("tr"):
            cells = row.find_all("td")
            if len(cells) != 2:
                continue

            label = cells[0].get_text(strip=True).rstrip(":")
            value = cells[1].get_text(strip=True)

            if label == "Born":
                result["born"] = value
            elif label == "Height":
                result["height"] = value
            elif label == "Weight":
                result["weight"] = value
            elif label == "Draft":
                result["draft"] = value

        return result
