"""Unit tests for team-name canonicalisation at the model-prediction
write boundary.

``ModelPredictionCRUD.create`` / ``create_or_update`` must store the
compact canonical team form regardless of the alias the model layer
passes, so the backtest correctness join against ``games`` can never
silently break (the original production bug: stored ``winner`` aliases
like "Greater Western Sydney" that never matched the canonical
``Giants``).

These tests mock the :class:`AsyncSession` and cache layer so they run
without a live database (``-m "not postgres"``).
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from packages.shared.crud.model_predictions import ModelPredictionCRUD


class TestModelPredictionCreateCanonicalisation:
    """``ModelPredictionCRUD.create`` canonicalises ``winner`` before insert."""

    @pytest.mark.asyncio
    async def test_create_canonicalises_alias_winner(self):
        db = AsyncMock(spec=AsyncSession)
        added: list = []
        db.add = MagicMock(side_effect=lambda obj: added.append(obj))
        db.commit = AsyncMock()
        db.refresh = AsyncMock()

        with patch(
            "packages.shared.cache.invalidate_cache_pattern", AsyncMock()
        ):
            pred = await ModelPredictionCRUD.create(
                db,
                game_id=1,
                model_name="elo",
                winner="Greater Western Sydney",
                confidence=0.6,
                margin=12,
            )

        assert added[0].winner == "Giants"
        assert pred.winner == "Giants"

    @pytest.mark.asyncio
    async def test_create_is_idempotent_for_canonical_name(self):
        db = AsyncMock(spec=AsyncSession)
        added: list = []
        db.add = MagicMock(side_effect=lambda obj: added.append(obj))
        db.commit = AsyncMock()
        db.refresh = AsyncMock()

        with patch(
            "packages.shared.cache.invalidate_cache_pattern", AsyncMock()
        ):
            pred = await ModelPredictionCRUD.create(
                db,
                game_id=1,
                model_name="elo",
                winner="Brisbane",
                confidence=0.6,
                margin=12,
            )

        assert added[0].winner == "Brisbane"
        assert pred.winner == "Brisbane"


class TestModelPredictionCreateOrUpdateCanonicalisation:
    """``create_or_update`` (the path ``regenerate_tips_for_round`` uses)
    canonicalises ``winner`` on both the update and the create branch.
    """

    @pytest.mark.asyncio
    async def test_update_branch_canonicalises_alias_winner(self):
        db = AsyncMock(spec=AsyncSession)
        # An existing row the caller would otherwise overwrite with the
        # raw alias.
        existing = SimpleNamespace(winner="Giants", confidence=0.5, margin=5)
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = existing
        db.execute.return_value = result_mock
        db.commit = AsyncMock()
        db.refresh = AsyncMock()

        with patch(
            "packages.shared.cache.invalidate_cache_pattern", AsyncMock()
        ):
            updated = await ModelPredictionCRUD.create_or_update(
                db,
                game_id=1,
                model_name="elo",
                winner="Greater Western Sydney",
                confidence=0.6,
                margin=12,
            )

        assert existing.winner == "Giants"
        assert updated is existing
