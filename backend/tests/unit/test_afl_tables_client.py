"""Unit tests for AFLTablesClient (afltables.com scraper).

Tests mock HTTP responses and verify parsing of match stats, season games,
player profiles, caching, and error handling. No real HTTP requests are made.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from packages.shared.afl_data.tables_client import AFLTablesClient

# ---------------------------------------------------------------------------
# HTML fixtures
# ---------------------------------------------------------------------------

MATCH_PAGE_HTML = """
<html><body>
<h2>Team A 12.10 (82) vs Team B 10.8 (68)</h2>
<table class="sortable">
<thead><tr><th>Player</th><th>K</th><th>H</th><th>D</th><th>M</th><th>G</th><th>B</th><th>T</th><th>HO</th><th>FF</th><th>FA</th></tr></thead>
<tbody>
<tr><td><a href="/afl/stats/players/01A/Player_One.html">Player One</a></td><td>15</td><td>10</td><td>25</td><td>5</td><td>2</td><td>1</td><td>4</td><td>0</td><td>2</td><td>1</td></tr>
<tr><td><a href="/afl/stats/players/02B/Player_Two.html">Player Two</a></td><td>12</td><td>8</td><td>20</td><td>3</td><td>1</td><td>0</td><td>6</td><td>22</td><td>1</td><td>0</td></tr>
<tr><td>Totals</td><td>27</td><td>18</td><td>45</td><td>8</td><td>3</td><td>1</td><td>10</td><td>22</td><td>3</td><td>1</td></tr>
</tbody>
</table>
<table class="sortable">
<thead><tr><th>Player</th><th>K</th><th>H</th><th>D</th><th>M</th><th>G</th><th>B</th><th>T</th><th>HO</th><th>FF</th><th>FA</th></tr></thead>
<tbody>
<tr><td><a href="/afl/stats/players/03C/Player_Three.html">Player Three</a></td><td>20</td><td>12</td><td>32</td><td>7</td><td>3</td><td>2</td><td>5</td><td>1</td><td>3</td><td>2</td></tr>
<tr><td><a href="/afl/stats/players/04D/Player_Four.html">Player Four</a></td><td>8</td><td>6</td><td>14</td><td>2</td><td>0</td><td>1</td><td>3</td><td>0</td><td>0</td><td>1</td></tr>
<tr><td>Totals</td><td>28</td><td>18</td><td>46</td><td>9</td><td>3</td><td>3</td><td>8</td><td>1</td><td>3</td><td>3</td></tr>
</tbody>
</table>
</body></html>
"""

SEASON_PAGE_HTML = """
<html><body>
<table>
<tr><td><a href="/afl/stats/games/20250101.html">Round 1</a></td></tr>
<tr><td><a href="/afl/stats/games/20250201.html">Round 2</a></td></tr>
<tr><td><a href="/afl/stats/games/20250301.html">Round 3</a></td></tr>
</table>
</body></html>
"""

PLAYER_PROFILE_HTML = """
<html><body>
<table>
<tr><td>Born:</td><td>15 Mar 1995</td></tr>
<tr><td>Height:</td><td>185 cm</td></tr>
<tr><td>Weight:</td><td>83 kg</td></tr>
<tr><td>Draft:</td><td>2013 National Draft - Pick 12</td></tr>
</table>
</body></html>
"""

EMPTY_SEASON_HTML = """
<html><body>
<p>No games found.</p>
</body></html>
"""


# ---------------------------------------------------------------------------
# TestGetPlayerStatsForMatch
# ---------------------------------------------------------------------------


class TestGetPlayerStatsForMatch:
    """Tests for fetching and parsing player stats for a specific match."""

    @pytest.mark.asyncio
    async def test_success_home_and_away(self):
        """Successful match page should parse home and away player stats."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()
        mock_response.text = MATCH_PAGE_HTML

        mock_http_client = AsyncMock()
        mock_http_client.get = AsyncMock(return_value=mock_response)

        with patch(
            "packages.shared.afl_data.tables_client.medium_cache",
            get=AsyncMock(return_value=None),
            set=AsyncMock(),
        ):
            client = AFLTablesClient.__new__(AFLTablesClient)
            client.client = mock_http_client

            result = await client.get_player_stats_for_match("2025060101")

        assert "home_players" in result
        assert "away_players" in result
        # Two home players (before Totals row)
        assert len(result["home_players"]) == 2
        assert len(result["away_players"]) == 2

        # Verify first home player stats
        p1 = result["home_players"][0]
        assert p1["name"] == "Player One"
        assert p1["kicks"] == 15
        assert p1["handballs"] == 10
        assert p1["disposals"] == 25
        assert p1["marks"] == 5
        assert p1["goals"] == 2
        assert p1["behinds"] == 1
        assert p1["tackles"] == 4
        assert p1["hitouts"] == 0
        assert p1["frees_for"] == 2
        assert p1["frees_against"] == 1

        # Verify away player
        p3 = result["away_players"][0]
        assert p3["name"] == "Player Three"
        assert p3["kicks"] == 20
        assert p3["goals"] == 3

    @pytest.mark.asyncio
    async def test_caches_result(self):
        """Result should be stored in cache."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()
        mock_response.text = MATCH_PAGE_HTML

        mock_http_client = AsyncMock()
        mock_http_client.get = AsyncMock(return_value=mock_response)

        mock_cache = MagicMock()
        mock_cache.get = AsyncMock(return_value=None)
        mock_cache.set = AsyncMock()

        with patch("packages.shared.afl_data.tables_client.medium_cache", mock_cache):
            client = AFLTablesClient.__new__(AFLTablesClient)
            client.client = mock_http_client

            await client.get_player_stats_for_match("2025060101")

        mock_cache.set.assert_called_once()
        call_args = mock_cache.set.call_args
        cache_key = call_args[0][0]
        assert "afltables:match:" in cache_key
        assert "2025060101" in cache_key

    @pytest.mark.asyncio
    async def test_api_error_handling(self):
        """HTTP error should be caught and return empty dict."""
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock(side_effect=Exception("HTTP 500"))

        mock_http_client = AsyncMock()
        mock_http_client.get = AsyncMock(return_value=mock_response)

        with patch(
            "packages.shared.afl_data.tables_client.medium_cache",
            get=AsyncMock(return_value=None),
            set=AsyncMock(),
        ):
            client = AFLTablesClient.__new__(AFLTablesClient)
            client.client = mock_http_client

            result = await client.get_player_stats_for_match("2025060101")

        assert result == {}


# ---------------------------------------------------------------------------
# TestGetSeasonGames
# ---------------------------------------------------------------------------


class TestGetSeasonGames:
    """Tests for fetching season game listings."""

    @pytest.mark.asyncio
    async def test_success(self):
        """Season page should parse game links."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()
        mock_response.text = SEASON_PAGE_HTML

        mock_http_client = AsyncMock()
        mock_http_client.get = AsyncMock(return_value=mock_response)

        with patch(
            "packages.shared.afl_data.tables_client.medium_cache",
            get=AsyncMock(return_value=None),
            set=AsyncMock(),
        ):
            client = AFLTablesClient.__new__(AFLTablesClient)
            client.client = mock_http_client

            result = await client.get_season_games(2025)

        assert isinstance(result, list)
        assert len(result) == 3
        assert result[0]["game_id"] == "20250101"
        assert result[0]["round"] == "Round 1"
        assert "/afl/stats/games/20250101.html" in result[0]["url"]

    @pytest.mark.asyncio
    async def test_empty_season(self):
        """Season with no game links returns empty list."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()
        mock_response.text = EMPTY_SEASON_HTML

        mock_http_client = AsyncMock()
        mock_http_client.get = AsyncMock(return_value=mock_response)

        with patch(
            "packages.shared.afl_data.tables_client.medium_cache",
            get=AsyncMock(return_value=None),
            set=AsyncMock(),
        ):
            client = AFLTablesClient.__new__(AFLTablesClient)
            client.client = mock_http_client

            result = await client.get_season_games(2025)

        assert result == []


# ---------------------------------------------------------------------------
# TestGetPlayerProfile
# ---------------------------------------------------------------------------


class TestGetPlayerProfile:
    """Tests for fetching and parsing player profiles."""

    @pytest.mark.asyncio
    async def test_success(self):
        """Player profile page should parse bio fields."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()
        mock_response.text = PLAYER_PROFILE_HTML

        mock_http_client = AsyncMock()
        mock_http_client.get = AsyncMock(return_value=mock_response)

        with patch(
            "packages.shared.afl_data.tables_client.medium_cache",
            get=AsyncMock(return_value=None),
            set=AsyncMock(),
        ):
            client = AFLTablesClient.__new__(AFLTablesClient)
            client.client = mock_http_client

            result = await client.get_player_profile("01A/Player_One")

        assert result["born"] == "15 Mar 1995"
        assert result["height"] == "185 cm"
        assert result["weight"] == "83 kg"
        assert result["draft"] == "2013 National Draft - Pick 12"

    @pytest.mark.asyncio
    async def test_caches_result(self):
        """Result should be stored in cache."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()
        mock_response.text = PLAYER_PROFILE_HTML

        mock_http_client = AsyncMock()
        mock_http_client.get = AsyncMock(return_value=mock_response)

        mock_cache = MagicMock()
        mock_cache.get = AsyncMock(return_value=None)
        mock_cache.set = AsyncMock()

        with patch("packages.shared.afl_data.tables_client.medium_cache", mock_cache):
            client = AFLTablesClient.__new__(AFLTablesClient)
            client.client = mock_http_client

            await client.get_player_profile("01A/Player_One")

        mock_cache.set.assert_called_once()
        call_args = mock_cache.set.call_args
        cache_key = call_args[0][0]
        assert "afltables:player:" in cache_key

    @pytest.mark.asyncio
    async def test_api_error_handling(self):
        """HTTP error should be caught and return empty dict."""
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock(side_effect=Exception("HTTP 500"))

        mock_http_client = AsyncMock()
        mock_http_client.get = AsyncMock(return_value=mock_response)

        with patch(
            "packages.shared.afl_data.tables_client.medium_cache",
            get=AsyncMock(return_value=None),
            set=AsyncMock(),
        ):
            client = AFLTablesClient.__new__(AFLTablesClient)
            client.client = mock_http_client

            result = await client.get_player_profile("01A/Player_One")

        assert result == {}


# ---------------------------------------------------------------------------
# TestContextManager
# ---------------------------------------------------------------------------


class TestContextManager:
    """Tests for async context manager protocol."""

    @pytest.mark.asyncio
    async def test_aenter_aexit(self):
        """__aenter__ should return self; __aexit__ should call close()."""
        client = AFLTablesClient()
        assert isinstance(client.client, type(client.client))  # httpx.AsyncClient

        entered = await client.__aenter__()
        assert entered is client

        # Mock aclose so we don't actually close a real client
        client.client.aclose = AsyncMock()
        await client.__aexit__(None, None, None)
        client.client.aclose.assert_called_once()


# ---------------------------------------------------------------------------
# TestParseMatchPage
# ---------------------------------------------------------------------------


class TestParseMatchPage:
    """Tests for internal _parse_match_page method."""

    def test_parse_player_stats_table(self):
        """Should parse player stats from HTML table rows."""
        from bs4 import BeautifulSoup

        client = AFLTablesClient.__new__(AFLTablesClient)
        soup = BeautifulSoup(MATCH_PAGE_HTML, "lxml")

        result = client._parse_match_page(soup)

        assert "home_players" in result
        assert "away_players" in result
        assert len(result["home_players"]) == 2
        assert len(result["away_players"]) == 2

        # Verify Totals rows are excluded
        for player in result["home_players"] + result["away_players"]:
            assert player["name"] != "Totals"

        # Verify stats parsing for second home player
        p2 = result["home_players"][1]
        assert p2["name"] == "Player Two"
        assert p2["kicks"] == 12
        assert p2["handballs"] == 8
        assert p2["disposals"] == 20
        assert p2["marks"] == 3
        assert p2["goals"] == 1
        assert p2["behinds"] == 0
        assert p2["tackles"] == 6
        assert p2["hitouts"] == 22
        assert p2["frees_for"] == 1
        assert p2["frees_against"] == 0


# ---------------------------------------------------------------------------
# TestParsePlayerProfile
# ---------------------------------------------------------------------------


class TestParsePlayerProfile:
    """Tests for internal _parse_player_profile method."""

    def test_parse_bio_fields(self):
        """Should extract born, height, weight, draft from HTML."""
        from bs4 import BeautifulSoup

        client = AFLTablesClient.__new__(AFLTablesClient)
        soup = BeautifulSoup(PLAYER_PROFILE_HTML, "lxml")

        result = client._parse_player_profile(soup)

        assert result["born"] == "15 Mar 1995"
        assert result["height"] == "185 cm"
        assert result["weight"] == "83 kg"
        assert result["draft"] == "2013 National Draft - Pick 12"
