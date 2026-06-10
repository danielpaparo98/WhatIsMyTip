"""Unit tests for FootyWireClient (footywire.com scraper).

Tests mock HTTP responses and verify parsing of injury lists, team selections,
advanced player stats, caching, and error handling. No real HTTP requests are made.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from packages.shared.afl_data.footywire_client import FootyWireClient


# ---------------------------------------------------------------------------
# HTML fixtures
# ---------------------------------------------------------------------------

INJURY_LIST_HTML = """
<html><body>
<div class="injurylist">
<table class="injurylist">
<tr><th>Team</th><th>Player</th><th>Injury</th><th>Return</th></tr>
<tr><td class="team">Richmond</td><td class="player"><a href="/afl/footy/pp-dustin_martin">Dustin Martin</a></td><td>Calf</td><td>1-2 weeks</td></tr>
<tr><td class="team">Melbourne</td><td class="player"><a href="/afl/footy/pp-christian_petracca">Christian Petracca</a></td><td>Knee</td><td>Test</td></tr>
<tr><td class="team">Collingwood</td><td class="player"><a href="/afl/footy/pp-scott_pendlebury">Scott Pendlebury</a></td><td>Hamstring</td><td>3-4 weeks</td></tr>
</table>
</div>
</body></html>
"""

INJURY_LIST_WITH_EMPTY_ROWS_HTML = """
<html><body>
<table class="injurylist">
<tr><th>Team</th><th>Player</th><th>Injury</th><th>Return</th></tr>
<tr><td class="team">Richmond</td><td class="player"><a href="#">Dustin Martin</a></td><td>Calf</td><td>1-2 weeks</td></tr>
<tr><td class="team">Melbourne</td><td class="player"></td><td></td><td>TBC</td></tr>
<tr><td class="team">Carlton</td><td class="player"><a href="#">Patrick Cripps</a></td><td>Ankle</td><td>2 weeks</td></tr>
</table>
</body></html>
"""

TEAM_SELECTIONS_HTML = """
<html><body>
<div class="teamselections">
<table>
<tr><th>Team</th><th>In</th><th>Out</th></tr>
<tr><td>Richmond</td><td>Dustin Martin, Shai Bolton</td><td>Noah Cumberland</td></tr>
<tr><td>Melbourne</td><td>Christian Petracca</td><td>James Jordon</td></tr>
</table>
</div>
</body></html>
"""

PLAYER_ADVANCED_STATS_HTML = """
<html><body>
<div class="playerstats">
<table>
<tr><th>Round</th><th>Opp</th><th>TOG%</th><th>Disposals</th><th>Metres Gained</th><th>Score Involvements</th><th>Contested Possessions</th></tr>
<tr><td>R1</td><td>COL</td><td>82%</td><td>28</td><td>450</td><td>8</td><td>14</td></tr>
<tr><td>R2</td><td>MEL</td><td>78%</td><td>32</td><td>520</td><td>10</td><td>16</td></tr>
<tr><td>R3</td><td>SYD</td><td>85%</td><td>25</td><td>380</td><td>7</td><td>12</td></tr>
</table>
</div>
</body></html>
"""

EMPTY_INJURY_HTML = """
<html><body>
<p>No injury data available.</p>
</body></html>
"""


# ---------------------------------------------------------------------------
# TestGetInjuryList
# ---------------------------------------------------------------------------

class TestGetInjuryList:
    """Tests for fetching and parsing the current injury list."""

    @pytest.mark.asyncio
    async def test_success_parses_injuries(self):
        """Successful injury list page should parse team, player, injury, return."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()
        mock_response.text = INJURY_LIST_HTML

        mock_http_client = AsyncMock()
        mock_http_client.get = AsyncMock(return_value=mock_response)

        with patch(
            "packages.shared.afl_data.footywire_client.medium_cache",
            get=AsyncMock(return_value=None),
            set=AsyncMock(),
        ):
            client = FootyWireClient.__new__(FootyWireClient)
            client.client = mock_http_client

            result = await client.get_injury_list()

        assert isinstance(result, list)
        assert len(result) == 3

        # First injury
        assert result[0]["team"] == "Richmond"
        assert result[0]["player"] == "Dustin Martin"
        assert result[0]["injury"] == "Calf"
        assert result[0]["return_timeline"] == "1-2 weeks"

        # Second injury
        assert result[1]["team"] == "Melbourne"
        assert result[1]["player"] == "Christian Petracca"
        assert result[1]["injury"] == "Knee"
        assert result[1]["return_timeline"] == "Test"

        # Third injury
        assert result[2]["team"] == "Collingwood"
        assert result[2]["player"] == "Scott Pendlebury"
        assert result[2]["injury"] == "Hamstring"
        assert result[2]["return_timeline"] == "3-4 weeks"

    @pytest.mark.asyncio
    async def test_caches_result(self):
        """Result should be stored in cache with key footywire:injuries."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()
        mock_response.text = INJURY_LIST_HTML

        mock_http_client = AsyncMock()
        mock_http_client.get = AsyncMock(return_value=mock_response)

        mock_cache = MagicMock()
        mock_cache.get = AsyncMock(return_value=None)
        mock_cache.set = AsyncMock()

        with patch(
            "packages.shared.afl_data.footywire_client.medium_cache", mock_cache
        ):
            client = FootyWireClient.__new__(FootyWireClient)
            client.client = mock_http_client

            await client.get_injury_list()

        mock_cache.set.assert_called_once()
        call_args = mock_cache.set.call_args
        cache_key = call_args[0][0]
        assert cache_key == "footywire:injuries"

    @pytest.mark.asyncio
    async def test_returns_cached_data(self):
        """When cache has data, HTTP client should not be called."""
        cached_data = [
            {"team": "Richmond", "player": "Dustin Martin", "injury": "Calf", "return_timeline": "1-2 weeks"},
        ]

        mock_cache = MagicMock()
        mock_cache.get = AsyncMock(return_value=cached_data)
        mock_cache.set = AsyncMock()

        mock_http_client = AsyncMock()
        mock_http_client.get = AsyncMock()

        with patch(
            "packages.shared.afl_data.footywire_client.medium_cache", mock_cache
        ):
            client = FootyWireClient.__new__(FootyWireClient)
            client.client = mock_http_client

            result = await client.get_injury_list()

        assert result == cached_data
        mock_http_client.get.assert_not_called()

    @pytest.mark.asyncio
    async def test_api_error_handling(self):
        """HTTP error should be caught and return empty list."""
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock(
            side_effect=Exception("HTTP 500")
        )

        mock_http_client = AsyncMock()
        mock_http_client.get = AsyncMock(return_value=mock_response)

        with patch(
            "packages.shared.afl_data.footywire_client.medium_cache",
            get=AsyncMock(return_value=None),
            set=AsyncMock(),
        ):
            client = FootyWireClient.__new__(FootyWireClient)
            client.client = mock_http_client

            result = await client.get_injury_list()

        assert result == []


# ---------------------------------------------------------------------------
# TestGetTeamSelections
# ---------------------------------------------------------------------------

class TestGetTeamSelections:
    """Tests for fetching and parsing team selections."""

    @pytest.mark.asyncio
    async def test_success(self):
        """Successful team selections page should parse team in/out data."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()
        mock_response.text = TEAM_SELECTIONS_HTML

        mock_http_client = AsyncMock()
        mock_http_client.get = AsyncMock(return_value=mock_response)

        with patch(
            "packages.shared.afl_data.footywire_client.medium_cache",
            get=AsyncMock(return_value=None),
            set=AsyncMock(),
        ):
            client = FootyWireClient.__new__(FootyWireClient)
            client.client = mock_http_client

            result = await client.get_team_selections(round_num=5, year=2025)

        assert isinstance(result, list)
        assert len(result) == 2

        assert result[0]["team"] == "Richmond"
        assert result[0]["in"] == "Dustin Martin, Shai Bolton"
        assert result[0]["out"] == "Noah Cumberland"

        assert result[1]["team"] == "Melbourne"
        assert result[1]["in"] == "Christian Petracca"
        assert result[1]["out"] == "James Jordon"

    @pytest.mark.asyncio
    async def test_api_error_handling(self):
        """HTTP error should be caught and return empty list."""
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock(
            side_effect=Exception("HTTP 500")
        )

        mock_http_client = AsyncMock()
        mock_http_client.get = AsyncMock(return_value=mock_response)

        with patch(
            "packages.shared.afl_data.footywire_client.medium_cache",
            get=AsyncMock(return_value=None),
            set=AsyncMock(),
        ):
            client = FootyWireClient.__new__(FootyWireClient)
            client.client = mock_http_client

            result = await client.get_team_selections(round_num=5, year=2025)

        assert result == []


# ---------------------------------------------------------------------------
# TestGetPlayerAdvancedStats
# ---------------------------------------------------------------------------

class TestGetPlayerAdvancedStats:
    """Tests for fetching and parsing player advanced stats."""

    @pytest.mark.asyncio
    async def test_success(self):
        """Successful player page should parse advanced stats per round."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()
        mock_response.text = PLAYER_ADVANCED_STATS_HTML

        mock_http_client = AsyncMock()
        mock_http_client.get = AsyncMock(return_value=mock_response)

        with patch(
            "packages.shared.afl_data.footywire_client.medium_cache",
            get=AsyncMock(return_value=None),
            set=AsyncMock(),
        ):
            client = FootyWireClient.__new__(FootyWireClient)
            client.client = mock_http_client

            result = await client.get_player_advanced_stats(player_id=1234, year=2025)

        assert isinstance(result, list)
        assert len(result) == 3

        # Round 1 stats
        r1 = result[0]
        assert r1["round"] == "R1"
        assert r1["opponent"] == "COL"
        assert r1["tog_pct"] == "82%"
        assert r1["disposals"] == 28
        assert r1["metres_gained"] == 450
        assert r1["score_involvements"] == 8
        assert r1["contested_possessions"] == 14

        # Round 2 stats
        r2 = result[1]
        assert r2["round"] == "R2"
        assert r2["disposals"] == 32
        assert r2["metres_gained"] == 520

    @pytest.mark.asyncio
    async def test_api_error_handling(self):
        """HTTP error should be caught and return empty list."""
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock(
            side_effect=Exception("HTTP 500")
        )

        mock_http_client = AsyncMock()
        mock_http_client.get = AsyncMock(return_value=mock_response)

        with patch(
            "packages.shared.afl_data.footywire_client.medium_cache",
            get=AsyncMock(return_value=None),
            set=AsyncMock(),
        ):
            client = FootyWireClient.__new__(FootyWireClient)
            client.client = mock_http_client

            result = await client.get_player_advanced_stats(player_id=1234, year=2025)

        assert result == []


# ---------------------------------------------------------------------------
# TestParseInjuryTable
# ---------------------------------------------------------------------------

class TestParseInjuryTable:
    """Tests for internal _parse_injury_table method."""

    def test_parse_injury_rows(self):
        """Should extract team, player, injury, return_timeline from HTML table."""
        from bs4 import BeautifulSoup

        client = FootyWireClient.__new__(FootyWireClient)
        soup = BeautifulSoup(INJURY_LIST_HTML, "lxml")

        result = client._parse_injury_table(soup)

        assert len(result) == 3
        assert result[0]["team"] == "Richmond"
        assert result[0]["player"] == "Dustin Martin"
        assert result[0]["injury"] == "Calf"
        assert result[0]["return_timeline"] == "1-2 weeks"

    def test_skip_empty_rows(self):
        """Rows with empty player or injury should be skipped."""
        from bs4 import BeautifulSoup

        client = FootyWireClient.__new__(FootyWireClient)
        soup = BeautifulSoup(INJURY_LIST_WITH_EMPTY_ROWS_HTML, "lxml")

        result = client._parse_injury_table(soup)

        assert len(result) == 2
        # First and third rows have valid data; second is empty
        assert result[0]["player"] == "Dustin Martin"
        assert result[1]["player"] == "Patrick Cripps"

    def test_skip_header_rows(self):
        """Header rows (th cells) should be skipped."""
        from bs4 import BeautifulSoup

        client = FootyWireClient.__new__(FootyWireClient)
        soup = BeautifulSoup(INJURY_LIST_HTML, "lxml")

        result = client._parse_injury_table(soup)

        # None of the results should be header text
        for entry in result:
            assert entry["team"] not in ("Team", "")
            assert entry["player"] not in ("Player", "")

    def test_empty_page_returns_empty_list(self):
        """Page with no injury tables should return empty list."""
        from bs4 import BeautifulSoup

        client = FootyWireClient.__new__(FootyWireClient)
        soup = BeautifulSoup(EMPTY_INJURY_HTML, "lxml")

        result = client._parse_injury_table(soup)

        assert result == []


# ---------------------------------------------------------------------------
# TestContextManager
# ---------------------------------------------------------------------------

class TestContextManager:
    """Tests for async context manager protocol."""

    @pytest.mark.asyncio
    async def test_aenter_aexit(self):
        """__aenter__ should return self; __aexit__ should call close()."""
        client = FootyWireClient()
        assert isinstance(client.client, type(client.client))  # httpx.AsyncClient

        entered = await client.__aenter__()
        assert entered is client

        # Mock aclose so we don't actually close a real client
        client.client.aclose = AsyncMock()
        await client.__aexit__(None, None, None)
        client.client.aclose.assert_called_once()
