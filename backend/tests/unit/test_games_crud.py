"""Unit tests for GameCRUD.create_or_update cache invalidation (ME-002).

The ``create_or_update`` method used to call
``invalidate_cache_pattern`` with broad wildcard patterns (e.g.
``"game_by_id:"``) that triggered a full Redis SCAN and removed
*every* cached game lookup.  The fix replaces those pattern calls
with targeted ``cache.delete(...)`` calls that only touch the keys
this specific game contributes to.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from packages.shared.cache import (
    _make_cache_key,
    medium_cache,
    short_cache,
)
from packages.shared.crud.games import GameCRUD


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_game(*, game_id: int = 42, slug: str = "abc-12345", season: int = 2025, round_id: int = 1):
    """Build a stand-in Game ORM object with the fields the cache
    invalidation code reads.  Avoids the SQLAlchemy declarative
    machinery entirely.
    """
    return SimpleNamespace(
        id=game_id,
        slug=slug,
        season=season,
        round_id=round_id,
        # The "update" branch in create_or_update_with_tracking reads
        # these fields when comparing to the incoming ``game_data``.
        # Use values that match the default test payload below so the
        # function falls through to the cache invalidation block
        # without raising AttributeError.
        home_team="Home",
        away_team="Away",
        home_score=50,
        away_score=40,
        venue="MCG",
        date=__import__("datetime").datetime.fromisoformat(
            "2025-03-15T10:00:00+00:00"
        ),
        completed=True,
        last_synced_at=None,
        sync_version=1,
    )


# ---------------------------------------------------------------------------
# Targeted cache invalidation (ME-002)
# ---------------------------------------------------------------------------


class TestTargetedCacheInvalidation:
    @pytest.mark.asyncio
    async def test_create_or_update_uses_targeted_deletes(self):
        """``create_or_update`` must call ``cache.delete`` with the
        hashed keys for this game and must NOT call
        ``invalidate_cache_pattern`` (which performs a SCAN sweep)."""
        db = AsyncMock(spec=AsyncSession)

        # Build a game and a game_data payload matching it.
        game = _make_game(game_id=42, slug="abc-12345", season=2025, round_id=1)
        game_data = {
            "id": 9001,  # squiggle_id — different from game.id
            "year": 2025,
            "round": 1,
            "hteam": "Home",
            "ateam": "Away",
            "hscore": 50,
            "ascore": 40,
            "venue": "MCG",
            "date": "2025-03-15T10:00:00Z",
            "complete": 100,  # Squiggle sentinel for "final"
        }

        # Track the delete calls.
        short_delete_mock = AsyncMock(return_value=True)
        medium_delete_mock = AsyncMock(return_value=True)
        pattern_invalidate_mock = AsyncMock(return_value=0)

        # Pre-compute the expected hashed keys (the same way the
        # @cached decorator will compute them).
        expected_id_key = _make_cache_key(
            "game_by_id:get_by_id", (game.id,), {},
        )
        expected_slug_key = _make_cache_key(
            "game_by_slug:get_by_slug", (game.slug,), {},
        )
        expected_round_key = _make_cache_key(
            "games_by_round:get_by_round",
            (game.season, game.round_id, None),
            {},
        )
        expected_season_key = _make_cache_key(
            "games_by_season:get_by_season",
            (game.season, None),
            {},
        )

        with patch.object(GameCRUD, "get_by_squiggle_id", AsyncMock(return_value=game)), \
             patch("packages.shared.cache.invalidate_cache_pattern", pattern_invalidate_mock), \
             patch.object(short_cache, "delete", short_delete_mock), \
             patch.object(medium_cache, "delete", medium_delete_mock):
            await GameCRUD.create_or_update_with_tracking(db, game_data)

        # The pattern-invalidator must NOT have been called — it would
        # SCAN the whole cache.
        pattern_invalidate_mock.assert_not_called()

        # Targeted deletes happened for the affected keys.
        short_delete_calls = {c.args[0] for c in short_delete_mock.await_args_list}
        medium_delete_calls = {c.args[0] for c in medium_delete_mock.await_args_list}

        assert expected_id_key in short_delete_calls
        assert expected_slug_key in short_delete_calls
        assert expected_round_key in short_delete_calls

        # games_by_season lives in the medium cache.
        assert expected_season_key in medium_delete_calls

    @pytest.mark.asyncio
    async def test_create_or_update_does_not_use_blanket_pattern(self):
        """Regression guard: ``invalidate_cache_pattern`` is never
        invoked for any of the game query patterns any more."""
        db = AsyncMock(spec=AsyncSession)
        game = _make_game()
        game_data = {
            "id": 1,
            "year": 2025,
            "round": 1,
            "hteam": "H",
            "ateam": "A",
            "hscore": 1,
            "ascore": 0,
            "venue": "V",
            "date": "2025-03-15T10:00:00Z",
            "complete": 100,
        }

        pattern_invalidate_mock = AsyncMock(return_value=0)

        with patch.object(GameCRUD, "get_by_squiggle_id", AsyncMock(return_value=game)), \
             patch("packages.shared.cache.invalidate_cache_pattern", pattern_invalidate_mock), \
             patch.object(short_cache, "delete", AsyncMock(return_value=True)), \
             patch.object(medium_cache, "delete", AsyncMock(return_value=True)):
            await GameCRUD.create_or_update_with_tracking(db, game_data)

        # Critical assertion: no blanket pattern invalidation.
        assert pattern_invalidate_mock.await_count == 0
