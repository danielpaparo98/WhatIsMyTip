"""Unit tests for team-name canonicalisation at the tips write boundary.

``TipCRUD.create`` / ``upsert`` must store the compact canonical team
form regardless of the alias the caller passes, so the backtest
correctness join against ``games`` can never silently break on a name
mismatch (e.g. a stored ``selected_team`` of "Greater Western Sydney"
that never matches the canonical ``Giants``).

These tests mock the :class:`AsyncSession` and cache layer so they run
without a live database (``-m "not postgres"``).
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from packages.shared.crud.tips import TipCRUD


class TestTipCreateCanonicalisation:
    """``TipCRUD.create`` canonicalises ``selected_team`` before insert."""

    @pytest.mark.asyncio
    async def test_create_canonicalises_alias_selected_team(self):
        db = AsyncMock(spec=AsyncSession)
        added: list = []
        db.add = MagicMock(side_effect=lambda obj: added.append(obj))
        db.commit = AsyncMock()
        db.refresh = AsyncMock()

        with patch(
            "packages.shared.crud.tips.invalidate_cache_pattern", AsyncMock()
        ), patch(
            "packages.shared.cache.invalidate_cache_pattern", AsyncMock()
        ):
            tip = await TipCRUD.create(
                db,
                game_id=1,
                heuristic="best_bet",
                selected_team="Greater Western Sydney",
                margin=12,
                confidence=0.7,
                explanation="home ground",
            )

        # The row handed to the session must carry the canonical name.
        assert added[0].selected_team == "Giants"
        # And the returned object reflects it too.
        assert tip.selected_team == "Giants"

    @pytest.mark.asyncio
    async def test_create_is_idempotent_for_canonical_name(self):
        db = AsyncMock(spec=AsyncSession)
        added: list = []
        db.add = MagicMock(side_effect=lambda obj: added.append(obj))
        db.commit = AsyncMock()
        db.refresh = AsyncMock()

        with patch(
            "packages.shared.crud.tips.invalidate_cache_pattern", AsyncMock()
        ), patch(
            "packages.shared.cache.invalidate_cache_pattern", AsyncMock()
        ):
            tip = await TipCRUD.create(
                db,
                game_id=1,
                heuristic="best_bet",
                selected_team="Brisbane",
                margin=12,
                confidence=0.7,
                explanation="x",
            )

        assert added[0].selected_team == "Brisbane"
        assert tip.selected_team == "Brisbane"


class TestTipUpsertCanonicalisation:
    """``TipCRUD.upsert`` canonicalises ``selected_team`` in the
    generated ``INSERT ... ON CONFLICT`` statement.
    """

    @pytest.mark.asyncio
    async def test_upsert_canonicalises_alias_selected_team(self):
        db = AsyncMock(spec=AsyncSession)
        db.commit = AsyncMock()
        # ``execute`` is called twice: the upsert INSERT and the
        # re-fetch in ``get_by_game_and_heuristic``.  Returning a plain
        # MagicMock (with a non-async scalar result) keeps the mock
        # chain free of un-awaited coroutines.
        db.execute.return_value = MagicMock()

        with patch(
            "packages.shared.crud.tips.invalidate_cache_pattern", AsyncMock()
        ), patch(
            "packages.shared.cache.invalidate_cache_pattern", AsyncMock()
        ):
            await TipCRUD.upsert(
                db,
                game_id=1,
                heuristic="best_bet",
                selected_team="Western Bulldogs",
                margin=10,
                confidence=0.65,
                explanation="x",
            )

        # The first execute() is the INSERT ... ON CONFLICT upsert; the
        # second is the re-fetch in get_by_game_and_heuristic.
        stmt = db.execute.await_args_list[0].args[0]
        sql = str(stmt.compile(compile_kwargs={"literal_binds": True}))

        assert "Bulldogs" in sql
        assert "Western Bulldogs" not in sql
