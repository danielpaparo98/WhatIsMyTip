"""Tests for the seed_player_data script."""

import sys
import os
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Ensure backend-faas is on sys.path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from scripts.seed_player_data import (
    _extract_round_number,
    _safe_float,
    _safe_int,
    seed_injuries,
    seed_match_weather,
    seed_player_advanced_stats,
    seed_player_match_stats,
    seed_players,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class TestExtractRoundNumber:
    """Verify round number extraction from labels."""

    def test_r_prefix(self):
        assert _extract_round_number("R1") == 1
        assert _extract_round_number("R12") == 12
        assert _extract_round_number("R24") == 24

    def test_round_prefix(self):
        assert _extract_round_number("Round 5") == 5
        assert _extract_round_number("Round 23") == 23

    def test_plain_number(self):
        assert _extract_round_number("1") == 1

    def test_empty_string(self):
        assert _extract_round_number("") == 0

    def test_no_number(self):
        assert _extract_round_number("Finals") == 0

    def test_qualifying_final(self):
        assert _extract_round_number("QF1") == 1


class TestSafeInt:
    """Verify safe int conversion."""

    def test_normal_int(self):
        assert _safe_int(42) == 42
        assert _safe_int("42") == 42

    def test_none(self):
        assert _safe_int(None) is None

    def test_invalid_string(self):
        assert _safe_int("abc") is None

    def test_float_string(self):
        assert _safe_int("12.5") is None

    def test_negative(self):
        assert _safe_int(-5) == -5


class TestSafeFloat:
    """Verify safe float conversion."""

    def test_normal_float(self):
        assert _safe_float(85.5) == 85.5
        assert _safe_float("85.5") == 85.5

    def test_none(self):
        assert _safe_float(None) is None

    def test_invalid_string(self):
        assert _safe_float("abc") is None

    def test_percentage_string(self):
        assert _safe_float("85%") == 85.0

    def test_integer(self):
        assert _safe_float(100) == 100.0


# ---------------------------------------------------------------------------
# Seed players tests
# ---------------------------------------------------------------------------


class TestSeedPlayers:
    """Verify player seeding logic."""

    @pytest.mark.asyncio
    async def test_creates_new_players(self):
        """Should create player records for new names."""
        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.rowcount = 1
        mock_session.execute = AsyncMock(return_value=mock_result)
        mock_session.flush = AsyncMock()

        mock_client = AsyncMock()
        mock_client.get_season_games = AsyncMock(return_value=[
            {"game_id": "2025010101", "round": "R1", "url": "/afl/stats/games/2025010101.html"},
        ])
        mock_client.get_player_stats_for_match = AsyncMock(return_value={
            "home_players": [
                {"name": "Player One", "kicks": 10, "handballs": 5, "disposals": 15, "marks": 3, "goals": 1, "behinds": 0, "tackles": 4, "hitouts": 0, "frees_for": 1, "frees_against": 0},
            ],
            "away_players": [
                {"name": "Player Two", "kicks": 8, "handballs": 7, "disposals": 15, "marks": 2, "goals": 0, "behinds": 1, "tackles": 6, "hitouts": 0, "frees_for": 0, "frees_against": 2},
            ],
        })
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        with patch("scripts.seed_player_data.AFLTablesClient", return_value=mock_client):
            count = await seed_players(mock_session, [2025])

        assert count == 2  # Two new players created
        assert mock_session.flush.called

    @pytest.mark.asyncio
    async def test_handles_empty_match_data(self):
        """Should handle empty match data gracefully."""
        mock_session = AsyncMock()
        mock_session.flush = AsyncMock()

        mock_client = AsyncMock()
        mock_client.get_season_games = AsyncMock(return_value=[
            {"game_id": "2025010101", "round": "R1", "url": "/afl/stats/games/2025010101.html"},
        ])
        mock_client.get_player_stats_for_match = AsyncMock(return_value={})
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        with patch("scripts.seed_player_data.AFLTablesClient", return_value=mock_client):
            count = await seed_players(mock_session, [2025])

        assert count == 0


# ---------------------------------------------------------------------------
# Seed injuries tests
# ---------------------------------------------------------------------------


class TestSeedInjuries:
    """Verify injury seeding logic."""

    @pytest.mark.asyncio
    async def test_upserts_injuries(self):
        """Should create injury records from FootyWire data."""
        mock_session = AsyncMock()
        mock_player_result = MagicMock()
        mock_player_result.fetchall = MagicMock(return_value=[])
        mock_session.execute = AsyncMock(return_value=mock_player_result)
        mock_session.flush = AsyncMock()

        mock_client = AsyncMock()
        mock_client.get_injury_list = AsyncMock(return_value=[
            {"team": "Brisbane", "player": "Lachie Neale", "injury": "Hamstring", "return_timeline": "2-3 weeks"},
            {"team": "Carlton", "player": "Patrick Cripps", "injury": "Ankle", "return_timeline": "TBC"},
        ])
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        with patch("scripts.seed_player_data.FootyWireClient", return_value=mock_client):
            count = await seed_injuries(mock_session)

        assert count == 2

    @pytest.mark.asyncio
    async def test_handles_empty_injury_list(self):
        """Should handle empty injury list gracefully."""
        mock_session = AsyncMock()

        mock_client = AsyncMock()
        mock_client.get_injury_list = AsyncMock(return_value=[])
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        with patch("scripts.seed_player_data.FootyWireClient", return_value=mock_client):
            count = await seed_injuries(mock_session)

        assert count == 0

    @pytest.mark.asyncio
    async def test_skips_injuries_without_player_or_injury(self):
        """Should skip entries with empty player name or injury type."""
        mock_session = AsyncMock()
        mock_player_result = MagicMock()
        mock_player_result.fetchall = MagicMock(return_value=[])
        mock_session.execute = AsyncMock(return_value=mock_player_result)
        mock_session.flush = AsyncMock()

        mock_client = AsyncMock()
        mock_client.get_injury_list = AsyncMock(return_value=[
            {"team": "Brisbane", "player": "", "injury": "Hamstring", "return_timeline": "1 week"},
            {"team": "Carlton", "player": "Patrick Cripps", "injury": "", "return_timeline": "TBC"},
            {"team": "Geelong", "player": "  ", "injury": "Knee", "return_timeline": "4-6 weeks"},
        ])
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        with patch("scripts.seed_player_data.FootyWireClient", return_value=mock_client):
            count = await seed_injuries(mock_session)

        assert count == 0  # All entries skipped due to empty fields
