"""Unit tests for the local-competition sync (Phase 5 WAFL pilot).

The service orchestrates the tested pieces: competition/season
registration → provider fixtures → participant resolution → tz
conversion (UTC → competition venue-local naive) → event upsert.
Collaborators are patched; their internals are covered elsewhere.
"""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from zoneinfo import ZoneInfo

from packages.shared.ingestion import FixtureDTO
from packages.shared.services.local_competition_sync import (
    LocalCompetitionSyncService,
)


def _fixture(**overrides):
    defaults = dict(
        source="sportix-wafl",
        external_id="m-1",
        season=2026,
        round_id=1,
        home_participant="Peel Thunder",
        away_participant="East Fremantle",
        home_score=91,
        away_score=78,
        venue="Lane Group Stadium",
        # Provider emits UTC-aware datetimes…
        starts_at=datetime(2026, 4, 3, 5, 10, tzinfo=timezone.utc),
        completed=True,
    )
    defaults.update(overrides)
    return FixtureDTO(**defaults)


class TestLocalCompetitionSync:
    @pytest.mark.asyncio
    async def test_competition_timezone_is_configurable(self):
        """SANFL (Adelaide) / VFL (Melbourne) must register with their own
        timezone — not the WAFL bootstrap's Perth default."""
        db = AsyncMock()
        db.flush = AsyncMock()
        db.commit = AsyncMock()
        competition = SimpleNamespace(id=4, timezone="Australia/Adelaide")
        season = SimpleNamespace(id=12, label="2026")

        with patch(
            "packages.shared.services.local_competition_sync.CompetitionCRUD"
        ) as crud, patch(
            "packages.shared.services.local_competition_sync.ParticipantResolver"
        ) as resolver_cls, patch(
            "packages.shared.services.local_competition_sync.EventCRUD"
        ) as event_crud:
            crud.ensure_competition = AsyncMock(return_value=competition)
            crud.ensure_season = AsyncMock(return_value=season)
            resolver_cls.return_value.ensure_team = AsyncMock(
                return_value=SimpleNamespace(id=71)
            )
            event_crud.upsert_fixture = AsyncMock(
                return_value=SimpleNamespace(id=501)
            )
            provider = MagicMock()
            provider.sport_id = "afl"
            provider.get_fixtures = AsyncMock(return_value=[])

            service = LocalCompetitionSyncService(
                db,
                provider=provider,
                competition_name="South Australian National Football League",
                season=2026,
                competition_timezone="Australia/Adelaide",
            )
            await service.sync()

        kwargs = crud.ensure_competition.await_args.kwargs
        assert kwargs["timezone"] == "Australia/Adelaide"

    @pytest.mark.asyncio
    async def test_registers_competition_season_and_upserts(self):
        db = AsyncMock()
        db.flush = AsyncMock()
        db.commit = AsyncMock()

        competition = SimpleNamespace(id=3, timezone="Australia/Perth")
        season = SimpleNamespace(id=11, label="2026")

        with patch(
            "packages.shared.services.local_competition_sync.CompetitionCRUD"
        ) as crud, patch(
            "packages.shared.services.local_competition_sync.ParticipantResolver"
        ) as resolver_cls, patch(
            "packages.shared.services.local_competition_sync.EventCRUD"
        ) as event_crud:
            crud.ensure_competition = AsyncMock(return_value=competition)
            crud.ensure_season = AsyncMock(return_value=season)
            resolver_cls.return_value.ensure_team = AsyncMock(
                side_effect=lambda name, **k: SimpleNamespace(id=71 if name.startswith("Peel") else 72)
            )
            event_crud.upsert_fixture = AsyncMock(
                return_value=SimpleNamespace(id=501)
            )

            provider = MagicMock()
            provider.sport_id = "afl"
            provider.get_fixtures = AsyncMock(return_value=[_fixture()])

            service = LocalCompetitionSyncService(
                db,
                provider=provider,
                competition_name="West Australian Football League",
                season=2026,
            )
            stats = await service.sync()

        crud.ensure_competition.assert_awaited_once()
        crud.ensure_season.assert_awaited_once_with(
            db, competition_id=3, label="2026", is_current=True
        )
        # The participant resolver was engaged for both sides.
        assert resolver_cls.return_value.ensure_team.await_count == 2

        # The upsert happened ONCE, scoped to the season, with the
        # datetime converted from UTC to Perth-local naive.
        assert event_crud.upsert_fixture.await_count == 1
        kwargs = event_crud.upsert_fixture.await_args.kwargs
        assert kwargs["season_id"] == 11
        fixture_arg = kwargs["fixture"]
        assert fixture_arg.starts_at == datetime(2026, 4, 3, 13, 10)
        assert fixture_arg.starts_at.tzinfo is None
        assert kwargs["home_participant_id"] == 71
        assert kwargs["away_participant_id"] == 72

        assert stats["fixtures_synced"] == 1

    @pytest.mark.asyncio
    async def test_tz_conversion_uses_competition_timezone(self):
        """The configured competition timezone — not a hardcoded one —
        drives the naive conversion (a 2027 east-coast local league
        would use its own)."""
        db = AsyncMock()
        db.flush = AsyncMock()
        db.commit = AsyncMock()

        competition = SimpleNamespace(id=3, timezone="Australia/Sydney")
        season = SimpleNamespace(id=11, label="2026")

        captured: dict = {}

        async def _capture(db, **kwargs):
            captured.update(kwargs)
            return SimpleNamespace(id=502)

        with patch(
            "packages.shared.services.local_competition_sync.CompetitionCRUD"
        ) as crud, patch(
            "packages.shared.services.local_competition_sync.ParticipantResolver"
        ) as resolver_cls, patch(
            "packages.shared.services.local_competition_sync.EventCRUD"
        ) as event_crud:
            crud.ensure_competition = AsyncMock(return_value=competition)
            crud.ensure_season = AsyncMock(return_value=season)
            resolver_cls.return_value.ensure_team = AsyncMock(
                return_value=SimpleNamespace(id=71)
            )
            event_crud.upsert_fixture = AsyncMock(side_effect=_capture)

            provider = MagicMock()
            provider.sport_id = "afl"
            provider.get_fixtures = AsyncMock(
                return_value=[_fixture(starts_at=datetime(2026, 4, 3, 5, 10, tzinfo=timezone.utc))]
            )

            service = LocalCompetitionSyncService(
                db,
                provider=provider,
                competition_name="X",
                season=2026,
                competition_timezone="Australia/Sydney",
            )
            await service.sync()

        converted = captured["fixture"].starts_at
        expected = (
            datetime(2026, 4, 3, 5, 10, tzinfo=timezone.utc)
            .astimezone(ZoneInfo("Australia/Sydney"))
            .replace(tzinfo=None)
        )
        assert converted == expected

    @pytest.mark.asyncio
    async def test_fixture_errors_do_not_abort_the_pass(self):
        db = AsyncMock()
        db.flush = AsyncMock()
        db.commit = AsyncMock()

        competition = SimpleNamespace(id=3, timezone="Australia/Perth")
        season = SimpleNamespace(id=11, label="2026")

        with patch(
            "packages.shared.services.local_competition_sync.CompetitionCRUD"
        ) as crud, patch(
            "packages.shared.services.local_competition_sync.ParticipantResolver"
        ) as resolver_cls, patch(
            "packages.shared.services.local_competition_sync.EventCRUD"
        ) as event_crud:
            crud.ensure_competition = AsyncMock(return_value=competition)
            crud.ensure_season = AsyncMock(return_value=season)
            calls = {"n": 0}

            async def _ensure_team(name, **k):
                calls["n"] += 1
                if calls["n"] == 1:
                    raise RuntimeError("resolver boom")
                return SimpleNamespace(id=72)

            resolver_cls.return_value.ensure_team = AsyncMock(side_effect=_ensure_team)
            event_crud.upsert_fixture = AsyncMock(return_value=SimpleNamespace(id=503))

            provider = MagicMock()
            provider.sport_id = "afl"
            provider.get_fixtures = AsyncMock(
                return_value=[
                    _fixture(external_id="m-1"),
                    _fixture(external_id="m-2"),
                ]
            )

            service = LocalCompetitionSyncService(
                db, provider=provider, competition_name="X", season=2026
            )
            stats = await service.sync()

        assert stats["errors"], "resolver failure must be recorded"
        # The pass continued: fixture 2 synced despite fixture 1 failing.
        assert stats["fixtures_synced"] == 1
