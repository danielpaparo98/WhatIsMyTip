"""Competition/Season registration tests (Phase 5: WAFL expansion).

Registering a new competition must be IDEMPOTENT — re-running the
seeding must return the same rows, never duplicate.  WAFL is the
bootstrap local competition: sport 'afl', tier 'state', Perth timezone.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from packages.shared.crud.competitions import CompetitionCRUD


def _result(scalar_return=None, scalars_return=None):
    r = MagicMock()
    r.scalar_one_or_none.return_value = scalar_return
    r.scalar.return_value = scalar_return
    scalars = MagicMock()
    scalars.all.return_value = scalars_return or []
    r.scalars.return_value = scalars
    return r


def _competition_row(**overrides):
    defaults = dict(
        id=3,
        sport_id="afl",
        name="West Australian Football League",
        tier="state",
        format="rounds",
        timezone="Australia/Perth",
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


class TestEnsureCompetition:
    @pytest.mark.asyncio
    async def test_creates_when_missing(self):
        db = AsyncMock(spec=AsyncSession)
        db.execute = AsyncMock(
            side_effect=[
                _result(scalar_return=SimpleNamespace(id="afl")),  # sport exists
                _result(scalar_return=None),  # competition lookup (miss)
            ]
        )
        added: list = []
        db.add = MagicMock(side_effect=lambda obj: added.append(obj))

        def _assign_ids():
            for obj in added:
                if getattr(obj, "id", None) is None:
                    obj.id = 3

        db.flush = AsyncMock(side_effect=_assign_ids)

        comp = await CompetitionCRUD.ensure_competition(
            db,
            sport_id="afl",
            name="West Australian Football League",
            tier="state",
            timezone="Australia/Perth",
        )

        assert comp.id == 3
        assert comp.tier == "state"
        assert db.add.call_count == 1

    @pytest.mark.asyncio
    async def test_idempotent_when_existing(self):
        db = AsyncMock(spec=AsyncSession)
        existing = _competition_row()
        db.execute = AsyncMock(return_value=_result(scalar_return=existing))

        comp = await CompetitionCRUD.ensure_competition(
            db, sport_id="afl", name="West Australian Football League"
        )

        assert comp is existing
        db.add.assert_not_called()

    @pytest.mark.asyncio
    async def test_invalid_tier_rejected(self):
        db = AsyncMock(spec=AsyncSession)
        with pytest.raises(ValueError):
            await CompetitionCRUD.ensure_competition(
                db, sport_id="afl", name="X", tier="international"
            )

    @pytest.mark.asyncio
    async def test_unknown_sport_rejected(self):
        db = AsyncMock(spec=AsyncSession)
        # The sport lookup misses.
        db.execute = AsyncMock(return_value=_result(scalar_return=None))

        with pytest.raises(ValueError, match="sport"):
            await CompetitionCRUD.ensure_competition(
                db, sport_id="quidditch", name="X"
            )


class TestEnsureSeason:
    @pytest.mark.asyncio
    async def test_creates_when_missing(self):
        db = AsyncMock(spec=AsyncSession)
        db.execute = AsyncMock(
            side_effect=[
                _result(scalar_return=None),  # season lookup (miss)
            ]
        )
        added: list = []
        db.add = MagicMock(side_effect=lambda obj: added.append(obj))

        def _assign_ids():
            for obj in added:
                if getattr(obj, "id", None) is None:
                    obj.id = 11

        db.flush = AsyncMock(side_effect=_assign_ids)

        season = await CompetitionCRUD.ensure_season(
            db, competition_id=3, label="2026", is_current=True
        )

        assert season.id == 11
        assert season.is_current is True

    @pytest.mark.asyncio
    async def test_idempotent_when_existing(self):
        db = AsyncMock(spec=AsyncSession)
        existing = SimpleNamespace(
            id=11, competition_id=3, label="2026", is_current=True
        )
        db.execute = AsyncMock(return_value=_result(scalar_return=existing))

        season = await CompetitionCRUD.ensure_season(
            db, competition_id=3, label="2026"
        )

        assert season is existing
        db.add.assert_not_called()
