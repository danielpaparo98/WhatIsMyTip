"""EventCRUD.upsert_fixture — the first WRITE path onto the new
events/event_participants/source-refs tables (Phase 5 WAFL pilot).

Resolution order: source ref → natural key → create.  Idempotent:
re-syncing the same feed updates in place, never duplicates.
"""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from packages.shared.crud.multisport import EventCRUD
from packages.shared.ingestion import FixtureDTO


def _fixture(**overrides):
    defaults = dict(
        source="sportix-wafl",
        external_id=4488,
        season=2026,
        round_id=1,
        home_participant="Peel Thunder",
        away_participant="East Fremantle",
        home_score=91,
        away_score=78,
        venue="Lane Group Stadium",
        starts_at=datetime(2026, 4, 3, 13, 10),  # naive Perth-local
        completed=True,
    )
    defaults.update(overrides)
    return FixtureDTO(**defaults)


def _result(scalar_return=None):
    r = MagicMock()
    r.scalar_one_or_none.return_value = scalar_return
    return r


def _make_db(side_effects: list):
    db = AsyncMock(spec=AsyncSession)
    db.execute = AsyncMock(side_effect=side_effects)
    added: list = []
    db.add = MagicMock(side_effect=lambda obj: added.append(obj))
    db.added = added
    return db


def _event_row(event_id=501):
    return SimpleNamespace(
        id=event_id,
        season_id=9,
        event_type="match",
        round_id=1,
        venue="Lane Group Stadium",
        starts_at=datetime(2026, 4, 3, 13, 10),
        status="completed",
        completed=True,
        slug="wafl-abc12345",
        sync_version=1,
        last_synced_at=None,
    )


class TestUpsertFixtureCreate:
    @pytest.mark.asyncio
    async def test_creates_event_sides_and_source_ref(self):
        sides_result = MagicMock()
        sides_result.scalars.return_value.all.return_value = []
        # Lookups all miss: source ref, then natural key; then sides (none).
        db = _make_db([_result(None), _result(None), sides_result])
        # flush assigns ids to added ORM objects in order.
        counter = {"n": 600}

        def _assign():
            for obj in db.added:
                if getattr(obj, "id", None) is None:
                    counter["n"] += 1
                    obj.id = counter["n"]

        db.flush = AsyncMock(side_effect=_assign)

        event = await EventCRUD.upsert_fixture(
            db,
            season_id=9,
            fixture=_fixture(),
            home_participant_id=71,
            away_participant_id=72,
        )

        assert event is not None
        assert event.season_id == 9
        added_types = [type(o).__name__ for o in db.added]
        assert added_types.count("Event") == 1
        assert added_types.count("EventParticipant") == 2
        assert added_types.count("EventSourceRef") == 1
        # sides carry scores + winner flag
        sides = [o for o in db.added if type(o).__name__ == "EventParticipant"]
        by_side = {o.side: o for o in sides}
        assert by_side["home"].score == 91
        assert by_side["home"].is_winner is True
        assert by_side["away"].score == 78
        assert by_side["away"].is_winner is False


class TestUpsertFixtureUpdate:
    @pytest.mark.asyncio
    async def test_source_ref_hit_updates_in_place(self):
        ref_row = SimpleNamespace(event_id=501)
        existing = _event_row()
        existing.completed = False
        existing.status = "scheduled"
        existing.home_score = None

        # 1: source ref hit; 2: event by id; 3: existing sides query
        sides_result = MagicMock()
        sides_result.scalars.return_value.all.return_value = [
            SimpleNamespace(id=900, event_id=501, participant_id=71, side="home",
                            score=None, is_winner=None),
            SimpleNamespace(id=901, event_id=501, participant_id=72, side="away",
                            score=None, is_winner=None),
        ]
        db = _make_db(
            [
                _result(ref_row),
                _result(existing),
                sides_result,
            ]
        )
        db.flush = AsyncMock()

        event = await EventCRUD.upsert_fixture(
            db,
            season_id=9,
            fixture=_fixture(),
            home_participant_id=71,
            away_participant_id=72,
        )

        assert event is existing
        # updated in place — nothing new added
        assert all(type(o).__name__ != "Event" for o in db.added)
        assert existing.completed is True
        assert existing.status == "completed"
        assert existing.sync_version == 2  # bumped

    @pytest.mark.asyncio
    async def test_draw_sets_no_winner(self):
        sides_result = MagicMock()
        sides_result.scalars.return_value.all.return_value = []
        db = _make_db(
            [
                _result(SimpleNamespace(event_id=501)),  # source ref hit
                _result(_event_row()),                   # event exists
                sides_result,                            # sides (none yet)
            ]
        )
        db.flush = AsyncMock()

        await EventCRUD.upsert_fixture(
            db,
            season_id=9,
            fixture=_fixture(home_score=60, away_score=60),
            home_participant_id=71,
            away_participant_id=72,
        )

        added_sides = [o for o in db.added if type(o).__name__ == "EventParticipant"]
        assert len(added_sides) == 2
        assert all(o.is_winner is None for o in added_sides)
