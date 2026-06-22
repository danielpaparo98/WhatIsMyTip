"""Regression net for team-name canonicalisation through the game-sync
service boundary.

``GameSyncService.sync_games`` is the daily-sync write path: it pulls
games from Squiggle (which sends aliases like "Western Bulldogs" /
"Greater Western Sydney") and persists them via
``GameCRUD.create_or_update_with_tracking``.  That CRUD layer already
canonicalises ``home_team`` / ``away_team``; this test guards the full
service → CRUD path so a future refactor can never bypass the
canonicalisation and silently break the backtest correctness join
against ``games``.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from packages.shared.cache import medium_cache, short_cache
from packages.shared.crud.games import GameCRUD
from packages.shared.services.game_sync import GameSyncService


class TestGameSyncCanonicalisation:
    @pytest.mark.asyncio
    async def test_sync_canonicalises_alias_team_names(self):
        db = AsyncMock(spec=AsyncSession)
        # Create path: no existing game for this squiggle_id.
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = None
        db.execute.return_value = result_mock

        added: list = []
        db.add = MagicMock(side_effect=lambda obj: added.append(obj))
        db.commit = AsyncMock()
        db.refresh = AsyncMock()

        upstream = AsyncMock()
        upstream.get_games = AsyncMock(
            return_value=[
                {
                    "id": 9001,
                    "year": 2025,
                    "round": 1,
                    "hteam": "Western Bulldogs",
                    "ateam": "Greater Western Sydney",
                    "hscore": 100,
                    "ascore": 90,
                    "venue": "MCG",
                    "date": "2025-03-15T10:00:00Z",
                    "complete": 100,
                }
            ]
        )

        with patch.object(
            GameCRUD, "_generate_unique_slug", AsyncMock(return_value="abc-12345")
        ), patch.object(
            short_cache, "delete", AsyncMock(return_value=True)
        ), patch.object(
            medium_cache, "delete", AsyncMock(return_value=True)
        ):
            service = GameSyncService(upstream, db, season=2025)
            stats = await service.sync_games()

        assert len(added) == 1
        game = added[0]
        assert game.home_team == "Bulldogs"
        assert game.away_team == "Giants"
        assert stats["games_created"] == 1
        assert stats["total_games"] == 1
        assert stats["errors"] == []

    @pytest.mark.asyncio
    async def test_sync_is_idempotent_for_canonical_names(self):
        db = AsyncMock(spec=AsyncSession)
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = None
        db.execute.return_value = result_mock

        added: list = []
        db.add = MagicMock(side_effect=lambda obj: added.append(obj))
        db.commit = AsyncMock()
        db.refresh = AsyncMock()

        upstream = AsyncMock()
        upstream.get_games = AsyncMock(
            return_value=[
                {
                    "id": 9002,
                    "year": 2025,
                    "round": 1,
                    "hteam": "Brisbane",
                    "ateam": "Collingwood",
                    "hscore": 80,
                    "ascore": 70,
                    "venue": "Gabba",
                    "date": "2025-04-01T10:00:00Z",
                    "complete": 100,
                }
            ]
        )

        with patch.object(
            GameCRUD, "_generate_unique_slug", AsyncMock(return_value="def-67890")
        ), patch.object(
            short_cache, "delete", AsyncMock(return_value=True)
        ), patch.object(
            medium_cache, "delete", AsyncMock(return_value=True)
        ):
            service = GameSyncService(upstream, db, season=2025)
            await service.sync_games()

        assert added[0].home_team == "Brisbane"
        assert added[0].away_team == "Collingwood"
