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
from packages.shared.ingestion.state_leagues import LeagueConfig
from packages.shared.services.local_competition_sync import (
    LocalCompetitionSyncService,
    run_all_leagues_sync,
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

    @pytest.mark.asyncio
    async def test_team_metadata_is_threaded_into_ensure_team(self):
        """Migration 0011: an optional per-name metadata callback feeds
        logo/colours into the resolver for that side; names the provider
        has no metadata for sync unchanged (identity stays NULL)."""
        db = AsyncMock()
        db.flush = AsyncMock()
        db.commit = AsyncMock()

        competition = SimpleNamespace(id=3, timezone="Australia/Perth")
        season = SimpleNamespace(id=11, label="2026")

        metadata_by_name = {
            "Peel Thunder": {
                "logo_url": "https://cdn.example/peel.png",
                "primary_color": "#002B5C",
                "secondary_color": "#E31937",
            },
        }

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
            provider.get_fixtures = AsyncMock(return_value=[_fixture()])

            service = LocalCompetitionSyncService(
                db,
                provider=provider,
                competition_name="West Australian Football League",
                season=2026,
                team_metadata=metadata_by_name.get,
            )
            await service.sync()

        calls = {
            call.args[0]: call.kwargs
            for call in resolver_cls.return_value.ensure_team.await_args_list
        }
        home = calls["Peel Thunder"]
        assert home["logo_url"] == "https://cdn.example/peel.png"
        assert home["primary_color"] == "#002B5C"
        assert home["secondary_color"] == "#E31937"

        # No metadata for the away side → all identity kwargs None.
        away = calls["East Fremantle"]
        assert away["logo_url"] is None
        assert away["primary_color"] is None
        assert away["secondary_color"] is None

    @pytest.mark.asyncio
    async def test_team_metadata_is_optional(self):
        """Default construction (WAFL bootstrap) passes no identity."""
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
                return_value=SimpleNamespace(id=71)
            )
            event_crud.upsert_fixture = AsyncMock(
                return_value=SimpleNamespace(id=501)
            )
            provider = MagicMock()
            provider.sport_id = "afl"
            provider.get_fixtures = AsyncMock(return_value=[_fixture()])

            service = LocalCompetitionSyncService(
                db, provider=provider, competition_name="X", season=2026
            )
            assert service.team_metadata is None
            await service.sync()

        for call in resolver_cls.return_value.ensure_team.await_args_list:
            assert call.kwargs["logo_url"] is None


def _league_config(
    status: str = "live",
    has_provider: bool = True,
    name: str = "X",
) -> LeagueConfig:
    """A STATE_LEAGUES registry entry for iteration tests."""
    return LeagueConfig(
        name=name,
        timezone="Australia/Perth",
        provider_factory=(lambda: MagicMock()) if has_provider else None,
        status=status,
        source_note="test",
    )


def _league_stats(key: str, fixtures: int = 0) -> dict:
    return {
        "league": key,
        "competition": key.upper(),
        "fixtures_synced": fixtures,
        "errors": [],
        "status": "success",
    }


class TestRunAllLeaguesSync:
    """``run_all_leagues_sync`` — the multi-league cron/script core."""

    @pytest.mark.asyncio
    async def test_runs_every_live_league_for_current_season(self, monkeypatch):
        """Only registry entries whose status starts with 'live' run, on
        the current season when none is given.  Provider-less leagues
        (e.g. TSL, 'source-unknown') are never attempted."""
        from packages.shared.config import settings

        monkeypatch.setattr(settings, "current_season", 2027)
        registry = {
            "wafl": _league_config(name="WAFL"),
            "tsl": _league_config(
                status="source-unknown", has_provider=False, name="TSL"
            ),
            "nwfl": _league_config(name="NWFL"),
        }
        mock_sync = AsyncMock(
            side_effect=lambda session, key, season: _league_stats(key)
        )
        with patch(
            "packages.shared.ingestion.state_leagues.STATE_LEAGUES", registry
        ), patch(
            "packages.shared.ingestion.state_leagues.run_league_sync", mock_sync
        ):
            result = await run_all_leagues_sync(AsyncMock())

        assert mock_sync.await_count == 2
        awaited_keys = [call.args[1] for call in mock_sync.await_args_list]
        assert awaited_keys == ["wafl", "nwfl"]
        # Current season pulled from settings, mark_current defaults on.
        assert all(call.args[2] == 2027 for call in mock_sync.await_args_list)
        assert result["season"] == 2027
        assert result["leagues_synced"] == ["wafl", "nwfl"]
        assert result["leagues_failed"] == []
        assert result["status"] == "success"

    @pytest.mark.asyncio
    async def test_explicit_leagues_and_season_are_respected(self):
        """An explicit ``leagues`` subset + ``season`` overrides the
        registry derivation (backfills a single league-season)."""
        registry = {
            "wafl": _league_config(name="WAFL"),
            "nwfl": _league_config(name="NWFL"),
            "sfl": _league_config(name="SFL"),
        }
        mock_sync = AsyncMock(
            side_effect=lambda session, key, season: _league_stats(key)
        )
        with patch(
            "packages.shared.ingestion.state_leagues.STATE_LEAGUES", registry
        ), patch(
            "packages.shared.ingestion.state_leagues.run_league_sync", mock_sync
        ):
            result = await run_all_leagues_sync(
                AsyncMock(), season=2024, leagues=["nwfl", "sfl"]
            )

        awaited = [(c.args[1], c.args[2]) for c in mock_sync.await_args_list]
        assert awaited == [("nwfl", 2024), ("sfl", 2024)]
        assert result["leagues_synced"] == ["nwfl", "sfl"]

    @pytest.mark.asyncio
    async def test_league_error_does_not_abort_the_sweep(self):
        """A failing league (network, provider bug, …) is recorded and
        the remaining leagues still sync."""
        registry = {
            "wafl": _league_config(name="WAFL"),
            "nwfl": _league_config(name="NWFL"),
            "sfl": _league_config(name="SFL"),
        }

        async def _sync(session, key, season):
            if key == "nwfl":
                raise RuntimeError("playhq exploded")
            return _league_stats(key)

        mock_sync = AsyncMock(side_effect=_sync)
        with patch(
            "packages.shared.ingestion.state_leagues.STATE_LEAGUES", registry
        ), patch(
            "packages.shared.ingestion.state_leagues.run_league_sync", mock_sync
        ):
            result = await run_all_leagues_sync(AsyncMock())

        assert mock_sync.await_count == 3, "all leagues attempted"
        assert result["leagues_synced"] == ["wafl", "sfl"]
        assert result["leagues_failed"] == ["nwfl"]
        assert len(result["errors"]) == 1
        assert "nwfl" in result["errors"][0]
        assert result["status"] == "partial"

    @pytest.mark.asyncio
    async def test_aggregates_fixture_counts_and_per_league_stats(self):
        """Top-level fixtures_synced sums the per-league totals and the
        full per-league stats dicts are retained."""
        registry = {
            "wafl": _league_config(name="WAFL"),
            "nwfl": _league_config(name="NWFL"),
        }
        mock_sync = AsyncMock(
            side_effect=lambda session, key, season: _league_stats(
                key, fixtures=5 if key == "wafl" else 7
            )
        )
        with patch(
            "packages.shared.ingestion.state_leagues.STATE_LEAGUES", registry
        ), patch(
            "packages.shared.ingestion.state_leagues.run_league_sync", mock_sync
        ):
            result = await run_all_leagues_sync(AsyncMock())

        assert result["fixtures_synced"] == 12
        assert result["results"]["wafl"]["fixtures_synced"] == 5
        assert result["results"]["nwfl"]["fixtures_synced"] == 7

    @pytest.mark.asyncio
    async def test_unknown_and_providerless_league_keys_are_skipped(self):
        """Explicit keys that are not in the registry — or registered
        without a provider (the Tasmania case if the provider list
        changes) — are recorded as failures, never raise."""
        registry = {
            "wafl": _league_config(name="WAFL"),
            "tsl": _league_config(
                status="source-unknown", has_provider=False, name="TSL"
            ),
        }

        async def _sync(session, key, season):
            raise NotImplementedError(f"{key}: no provider yet")

        mock_sync = AsyncMock(side_effect=_sync)
        with patch(
            "packages.shared.ingestion.state_leagues.STATE_LEAGUES", registry
        ), patch(
            "packages.shared.ingestion.state_leagues.run_league_sync", mock_sync
        ):
            result = await run_all_leagues_sync(
                AsyncMock(), leagues=["nope", "tsl"]
            )

        # Only the registered key is attempted; the unknown key is
        # rejected before any provider lookup.
        assert mock_sync.await_count == 1
        assert mock_sync.await_args.args[1] == "tsl"
        assert result["leagues_synced"] == []
        assert result["leagues_failed"] == ["nope", "tsl"]
        assert result["status"] == "partial"
