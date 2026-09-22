"""Unit tests for sport-generic CRUD + ParticipantResolver (P1-5/P1-6).

The defining property of this layer: **scoping is mandatory**.  Every
multi-row lookup takes ``season_id`` (or a sport scope) as a REQUIRED
keyword argument — an unscoped query is a TypeError at the call site,
not silent cross-sport row mixing at runtime.
"""

from __future__ import annotations

import inspect
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from packages.shared.crud.multisport import EventCRUD, ParticipantResolver


class TestRequiredScoping:
    """P1-5: unscoped multi-row lookups cannot exist."""

    def test_get_by_season_requires_season_id(self):
        sig = inspect.signature(EventCRUD.get_by_season)
        assert sig.parameters["season_id"].kind is inspect.Parameter.KEYWORD_ONLY
        assert sig.parameters["season_id"].default is inspect.Parameter.empty

    def test_get_by_round_requires_season_id_and_round(self):
        sig = inspect.signature(EventCRUD.get_by_round)
        for name in ("season_id", "round_id"):
            assert sig.parameters[name].kind is inspect.Parameter.KEYWORD_ONLY, name
            assert sig.parameters[name].default is inspect.Parameter.empty, name

    def test_get_upcoming_requires_season_id(self):
        sig = inspect.signature(EventCRUD.get_upcoming)
        assert sig.parameters["season_id"].kind is inspect.Parameter.KEYWORD_ONLY
        assert sig.parameters["season_id"].default is inspect.Parameter.empty


def _result(scalars_return=None, scalar_return=None):
    r = MagicMock()
    r.scalars.return_value.all.return_value = scalars_return or []
    r.scalar_one_or_none.return_value = scalar_return
    r.scalar.return_value = scalar_return
    return r


class TestParticipantResolver:
    @pytest.mark.asyncio
    async def test_resolve_exact_name(self):
        participant = MagicMock(id=7, name="Adelaide")
        db = AsyncMock(spec=AsyncSession)
        db.execute = AsyncMock(return_value=_result(scalar_return=participant))

        resolver = ParticipantResolver(db, sport_id="afl")
        resolved = await resolver.resolve("Adelaide")

        assert resolved is participant
        # first query path: exact name lookup, no alias join needed

    @pytest.mark.asyncio
    async def test_resolve_via_alias(self):
        participant = MagicMock(id=9, name="West Coast")
        db = AsyncMock(spec=AsyncSession)
        db.execute = AsyncMock(
            side_effect=[
                _result(scalar_return=None),   # exact-name miss
                _result(scalar_return=participant),  # alias hit
            ]
        )

        resolver = ParticipantResolver(db, sport_id="afl")
        resolved = await resolver.resolve("Eagles")

        assert resolved is participant

    @pytest.mark.asyncio
    async def test_resolve_canonical_fallback(self):
        """Unknown alias ('Adelaide Crows') → canonical_team() → hit."""
        participant = MagicMock(id=7, name="Adelaide")
        db = AsyncMock(spec=AsyncSession)
        db.execute = AsyncMock(
            side_effect=[
                _result(scalar_return=None),  # exact miss ('Adelaide Crows')
                _result(scalar_return=None),  # alias miss
                _result(scalar_return=participant),  # canonical('Adelaide Crows') → 'Adelaide'
            ]
        )

        resolver = ParticipantResolver(db, sport_id="afl")
        resolved = await resolver.resolve("Adelaide Crows")

        assert resolved is participant

    @pytest.mark.asyncio
    async def test_resolve_unknown_returns_none(self):
        db = AsyncMock(spec=AsyncSession)
        db.execute = AsyncMock(
            side_effect=[
                _result(scalar_return=None),
                _result(scalar_return=None),
                _result(scalar_return=None),  # canonical name also unknown
            ]
        )

        resolver = ParticipantResolver(db, sport_id="afl")
        assert await resolver.resolve("Totally Made Up FC") is None

    @pytest.mark.asyncio
    async def test_ensure_team_creates_with_aliases(self):
        db = AsyncMock(spec=AsyncSession)
        db.execute = AsyncMock(
            side_effect=[
                _result(scalar_return=None),  # exact miss
                _result(scalar_return=None),  # alias miss
                _result(scalar_return=None),  # canonical miss
            ]
        )
        # Simulate the DB: flush assigns the id to the newly added row.
        added: list = []

        def _capture(obj):
            added.append(obj)

        def _assign_ids():
            for obj in added:
                if getattr(obj, "id", None) is None:
                    obj.id = 42

        db.add = MagicMock(side_effect=_capture)
        db.flush = AsyncMock(side_effect=_assign_ids)

        resolver = ParticipantResolver(db, sport_id="afl")
        created = await resolver.ensure_team("Adelaide", aliases=("Adelaide Crows", "Crowbies"))

        assert created.id == 42
        # participant + Team + 2 alias rows all registered
        kinds = [type(obj).__name__ for obj in added]
        assert kinds.count("Participant") == 1
        assert kinds.count("Team") == 1
        assert kinds.count("TeamAlias") == 2
