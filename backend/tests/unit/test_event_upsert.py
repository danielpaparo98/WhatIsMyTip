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


class TestUpsertFixtureIdentityRefresh:
    """AFLW-2026 REGRESSION (2026-11-12, prod): the AFL platform feed
    resolves finals brackets IN PLACE - the same external_id flips from
    a placeholder fixture ("1st" vs "4th", venue "To Be Confirmed",
    nominal time) to the real one. The old update path only refreshed
    completed/status, and the sides upsert (keyed by participant_id)
    then tried to INSERT second home/away rows -> unique(event_id, side)
    violation -> per-fixture savepoint rollback -> the real fixture
    stayed trapped in its placeholder identity forever (Carlton v
    Hawthorn hid inside "Loser of QF1 vs Winner of EF1" for a week)."""

    @staticmethod
    def _placeholder_event():
        event = _event_row(501)
        event.round_id = 14
        event.venue = "To Be Confirmed"
        event.starts_at = datetime(2026, 11, 9, 12, 0)
        event.status = "scheduled"
        event.completed = False
        return event

    @staticmethod
    def _placeholder_sides():
        home = MagicMock()
        home.side = "home"
        home.participant_id = 101
        home.score = None
        home.is_winner = None
        away = MagicMock()
        away.side = "away"
        away.participant_id = 102
        away.score = None
        away.is_winner = None
        return [home, away]

    @staticmethod
    def _resolved_fixture():
        return _fixture(
            source="aflapi",
            external_id=9076,
            round_id=14,
            home_participant="Carlton",
            away_participant="Hawthorn",
            home_score=None,
            away_score=None,
            venue="IKON Park",
            starts_at=datetime(2026, 11, 14, 19, 15),
            completed=False,
        )

    @pytest.mark.asyncio
    async def test_source_ref_resolution_repoints_sides_and_identity(self):
        event = self._placeholder_event()
        sides = self._placeholder_sides()
        db = _make_db(
            [
                _result(SimpleNamespace(event_id=501)),  # 1 source ref hit
                _result(event),                          # 2 event by id
                _result(None),                           # 3 natural-key clash check
                MagicMock(                               # 4 existing sides
                    scalars=MagicMock(return_value=MagicMock(all=lambda: sides))
                ),
                MagicMock(),                             # 5-8 stale derived deletes
                MagicMock(),
                MagicMock(),
                MagicMock(),
            ]
        )
        db.flush = AsyncMock()

        out = await EventCRUD.upsert_fixture(
            db,
            season_id=9,
            fixture=self._resolved_fixture(),
            home_participant_id=201,
            away_participant_id=202,
        )

        assert out is event
        # identity refreshed to the feed's truth
        assert event.venue == "IKON Park"
        assert event.starts_at == datetime(2026, 11, 14, 19, 15)
        assert event.round_id == 14
        assert event.sync_version == 2  # bumped on change
        # sides REPOINTED in place - never a second home/away insert
        assert not [
            o for o in db.added if type(o).__name__ == "EventParticipant"
        ]
        assert sides[0].participant_id == 201
        assert sides[1].participant_id == 202
        # stale derived rows (tips etc.) cleared for regeneration
        deletes = [
            str(c.args[0]).lower() for c in db.execute.call_args_list
            if "DELETE" in str(c.args[0])
        ]
        tables = " ".join(deletes)
        for expected in ("league_tips", "league_model_predictions",
                         "league_match_analys", "league_match_report"):
            assert expected in tables, f"missing delete for {expected}"

    @pytest.mark.asyncio
    async def test_identity_refresh_skipped_when_target_natural_key_taken(self):
        event = self._placeholder_event()
        sides = self._placeholder_sides()
        db = _make_db(
            [
                _result(SimpleNamespace(event_id=501)),  # source ref hit
                _result(event),                          # event by id
                _result(SimpleNamespace(id=999)),        # clash! key taken
                MagicMock(                               # existing sides
                    scalars=MagicMock(return_value=MagicMock(all=lambda: sides))
                ),
                MagicMock(),                             # stale derived deletes
                MagicMock(),
                MagicMock(),
                MagicMock(),
            ]
        )
        db.flush = AsyncMock()

        await EventCRUD.upsert_fixture(
            db,
            season_id=9,
            fixture=self._resolved_fixture(),
            home_participant_id=201,
            away_participant_id=202,
        )

        # venue/time must NOT change (constraint would reject it)
        assert event.venue == "To Be Confirmed"
        assert event.starts_at == datetime(2026, 11, 9, 12, 0)
        # but the sides still repoint - real teams render, no side dupes
        assert sides[0].participant_id == 201
        assert sides[1].participant_id == 202
        # and the stale matchup's derived rows are still cleared
        deletes = [
            str(c.args[0]).lower() for c in db.execute.call_args_list
            if "DELETE" in str(c.args[0])
        ]
        assert "league_tips" in " ".join(deletes)

    @pytest.mark.asyncio
    async def test_plain_re_sync_stays_untouched(self):
        """Same venue/time (no placeholder identity): zero extra queries,
        sides merged by participant_id exactly as before the fix."""
        event = _event_row(501)
        sides = [
            SimpleNamespace(id=900, event_id=501, participant_id=71, side="home",
                            score=None, is_winner=None),
            SimpleNamespace(id=901, event_id=501, participant_id=72, side="away",
                            score=None, is_winner=None),
        ]
        db = _make_db(
            [
                _result(SimpleNamespace(event_id=501)),
                _result(event),
                MagicMock(scalars=MagicMock(return_value=MagicMock(all=lambda: sides))),
            ]
        )
        db.flush = AsyncMock()

        await EventCRUD.upsert_fixture(
            db,
            season_id=9,
            fixture=_fixture(),  # same venue/time as _event_row
            home_participant_id=71,
            away_participant_id=72,
        )

        assert event.venue == "Lane Group Stadium"  # unchanged
        assert event.sync_version == 1  # completed/status unchanged too
        assert sides[0].participant_id == 71  # merged, not repointed
        assert not [
            o for o in db.added if type(o).__name__ == "EventParticipant"
        ]
