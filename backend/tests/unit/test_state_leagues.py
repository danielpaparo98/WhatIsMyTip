"""State-league registry tests (Phase 5 rollout)."""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch


from packages.shared.ingestion.state_leagues import (
    STATE_LEAGUES,
    get_league,
    run_league_sync,
)


class TestRegistry:
    def test_all_state_leagues_registered(self):
        assert {"wafl", "waflw", "sanfl", "vfl", "vflw", "aflw", "qafl", "tsl"} <= set(STATE_LEAGUES)

    def test_timezones_differ_by_state(self):
        assert STATE_LEAGUES["wafl"].timezone == "Australia/Perth"
        assert STATE_LEAGUES["sanfl"].timezone == "Australia/Adelaide"
        assert STATE_LEAGUES["vfl"].timezone == "Australia/Melbourne"

    def test_remaining_unknown_source_declares_no_provider(self):
        config = STATE_LEAGUES["tsl"]
        assert config.provider_factory is None
        assert config.status == "source-unknown"
        # Every pending league documents WHERE its data lives.
        assert config.source_note

    def test_unknown_league_rejected(self):
        with pytest.raises(ValueError, match="available"):
            get_league("nrl")

    @pytest.mark.asyncio
    async def test_unknown_source_league_sync_raises_with_guidance(self):
        session = AsyncMock()
        with pytest.raises(NotImplementedError, match="no provider yet"):
            await run_league_sync(session, "tsl", 2026)

    @pytest.mark.asyncio
    async def test_live_league_sync_uses_registered_timezones(self):
        from types import SimpleNamespace
        from unittest.mock import MagicMock, patch

        session = AsyncMock()
        session.commit = AsyncMock()
        competition = SimpleNamespace(id=9, timezone="Australia/Perth")
        season = SimpleNamespace(id=21, label="2026")

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
                return_value=SimpleNamespace(id=1)
            )
            event_crud.upsert_fixture = AsyncMock(
                return_value=SimpleNamespace(id=1)
            )
            # Patch the registry's provider FACTORY (not the provider
            # class) — this test covers orchestration; provider
            # internals hit no network here.
            with patch(
                "packages.shared.ingestion.state_leagues._sportix"
            ) as sportix_factory:
                provider = MagicMock()
                provider.sport_id = "afl"
                provider.get_fixtures = AsyncMock(return_value=[])
                sportix_factory.return_value = provider

                stats = await run_league_sync(session, "wafl", 2026)

        assert stats["league"] == "wafl"
        assert stats["fixtures_synced"] == 0
        assert stats["status"] == "success"
        assert (
            crud.ensure_competition.await_args.kwargs["timezone"]
            == "Australia/Perth"
        )


class TestLiveWiring:
    """The four AFL-platform leagues and two iSports leagues wired live."""

    def test_afl_platform_leagues_are_live(self):
        from packages.shared.ingestion.afl_platform_provider import (
            AflPlatformProvider,
        )

        # Verified against the live platform (agent recon 2026-09-24):
        # 8 was a 2020 stub and 4 was "NAB League Boys".
        for key, competition_id in (
            ("vfl", 7),
            ("aflw", 3),
            ("vflw", 11),
            ("sanfl", 14),
        ):
            config = STATE_LEAGUES[key]
            assert config.status == "live", key
            assert config.provider_factory is not None, key
            provider = config.provider_factory()
            assert isinstance(provider, AflPlatformProvider), key
            assert provider.competition_id == competition_id, key
            assert provider.source == "aflapi"
            assert "aflapi.afl.com.au/afl/v2" in config.source_note

    def test_isports_leagues_are_live(self):
        from packages.shared.ingestion.isports_provider import ISportsProvider

        for key, league_id in (("qafl", 1), ("qaflw", 4)):
            config = STATE_LEAGUES[key]
            assert config.status == "live", key
            assert config.provider_factory is not None, key
            provider = config.provider_factory()
            assert isinstance(provider, ISportsProvider), key
            assert provider.league_id == league_id, key
            assert provider.source == "isports"
            assert "stats.isports.net.au/api" in config.source_note

    def test_timezones_kept_per_state(self):
        assert STATE_LEAGUES["vfl"].timezone == "Australia/Melbourne"
        assert STATE_LEAGUES["aflw"].timezone == "Australia/Melbourne"
        assert STATE_LEAGUES["vflw"].timezone == "Australia/Melbourne"
        assert STATE_LEAGUES["sanfl"].timezone == "Australia/Adelaide"
        assert STATE_LEAGUES["qafl"].timezone == "Australia/Brisbane"
        assert STATE_LEAGUES["qaflw"].timezone == "Australia/Brisbane"


class TestTeamIdentityWiring:
    """Migration 0011: providers that expose ``get_team_metadata`` feed
    the sync service's per-name identity lookup — best-effort, never
    sync-breaking (Sportix/WAFL exposes none and stays on the frontend
    fallbacks)."""

    @pytest.mark.asyncio
    async def test_lookup_built_from_provider_metadata(self):
        from packages.shared.ingestion.state_leagues import (
            build_team_metadata_lookup,
        )

        provider = MagicMock()
        provider.source = "isports"
        provider.get_team_metadata = AsyncMock(
            return_value={
                "Mt Gravatt Vultures": {
                    "logo_url": "https://cdn.example/v.png"
                }
            }
        )

        lookup = await build_team_metadata_lookup(provider, 2026)

        assert lookup is not None
        provider.get_team_metadata.assert_awaited_once_with(2026)
        assert lookup("Mt Gravatt Vultures") == {
            "logo_url": "https://cdn.example/v.png"
        }
        assert lookup("Unknown FC") is None

    @pytest.mark.asyncio
    async def test_provider_without_metadata_yields_none(self):
        from packages.shared.ingestion.state_leagues import (
            build_team_metadata_lookup,
        )

        provider = MagicMock(spec=["sport_id", "source", "get_fixtures"])

        assert await build_team_metadata_lookup(provider, 2026) is None

    @pytest.mark.asyncio
    async def test_metadata_fetch_failure_yields_none(self):
        """A broken metadata endpoint must never break a sync pass."""
        from packages.shared.ingestion.state_leagues import (
            build_team_metadata_lookup,
        )

        provider = MagicMock()
        provider.source = "isports"
        provider.get_team_metadata = AsyncMock(
            side_effect=RuntimeError("feed down")
        )

        assert await build_team_metadata_lookup(provider, 2026) is None

    @pytest.mark.asyncio
    async def test_empty_metadata_yields_none(self):
        from packages.shared.ingestion.state_leagues import (
            build_team_metadata_lookup,
        )

        provider = MagicMock()
        provider.source = "isports"
        provider.get_team_metadata = AsyncMock(return_value={})

        assert await build_team_metadata_lookup(provider, 2026) is None

    @pytest.mark.asyncio
    async def test_run_league_sync_threads_metadata_into_service(self):
        from types import SimpleNamespace

        from packages.shared.ingestion import state_leagues
        from packages.shared.services.local_competition_sync import (
            LocalCompetitionSyncService,
        )

        session = AsyncMock()
        session.commit = AsyncMock()
        competition = SimpleNamespace(id=9, timezone="Australia/Brisbane")
        season = SimpleNamespace(id=21, label="2026")

        provider = MagicMock()
        provider.sport_id = "afl"
        provider.source = "isports"
        provider.get_fixtures = AsyncMock(return_value=[])
        provider.get_team_metadata = AsyncMock(
            return_value={"Some Team": {"logo_url": "x"}}
        )

        def _factory():
            return provider

        fake_config = SimpleNamespace(
            name="Queensland Australian Football League",
            timezone="Australia/Brisbane",
            provider_factory=_factory,
        )

        with patch(
            "packages.shared.services.local_competition_sync.CompetitionCRUD"
        ) as crud, patch(
            "packages.shared.services.local_competition_sync.ParticipantResolver"
        ) as resolver_cls, patch(
            "packages.shared.services.local_competition_sync.EventCRUD"
        ) as event_crud, patch.object(
            state_leagues, "LocalCompetitionSyncService",
            wraps=state_leagues.LocalCompetitionSyncService,
        ) as service_cls, patch.object(
            state_leagues, "get_league", return_value=fake_config
        ):
            crud.ensure_competition = AsyncMock(return_value=competition)
            crud.ensure_season = AsyncMock(return_value=season)
            resolver_cls.return_value.ensure_team = AsyncMock(
                return_value=SimpleNamespace(id=1)
            )
            event_crud.upsert_fixture = AsyncMock(
                return_value=SimpleNamespace(id=1)
            )
            service_cls.side_effect = (
                lambda *a, **kw: LocalCompetitionSyncService(*a, **kw)
            )

            stats = await run_league_sync(session, "qafl", 2026)

        assert stats["status"] == "success"
        # The REAL build_team_metadata_lookup ran against the provider's
        # metadata and its lookup reached the service intact.
        provider.get_team_metadata.assert_awaited_once_with(2026)
        lookup = service_cls.call_args.kwargs["team_metadata"]
        assert lookup("Some Team") == {"logo_url": "x"}
        assert lookup("Unknown") is None

    @pytest.mark.asyncio
    async def test_wafl_league_sync_stays_without_metadata(self):
        """A provider with NO ``get_team_metadata`` (or one that blows
        up mid-await) keeps team_metadata None — the non-breaking
        default.  (Sportix itself DOES expose crests now — see
        test_wafl_league_sync_threads_sportix_metadata, which uses the
        real provider.)"""
        from types import SimpleNamespace

        session = AsyncMock()
        session.commit = AsyncMock()
        competition = SimpleNamespace(id=9, timezone="Australia/Perth")
        season = SimpleNamespace(id=21, label="2026")

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
                return_value=SimpleNamespace(id=1)
            )
            event_crud.upsert_fixture = AsyncMock(
                return_value=SimpleNamespace(id=1)
            )
            with patch(
                "packages.shared.ingestion.state_leagues._sportix"
            ) as sportix_factory:
                provider = MagicMock()
                provider.sport_id = "afl"
                provider.get_fixtures = AsyncMock(return_value=[])
                sportix_factory.return_value = provider

                stats = await run_league_sync(session, "wafl", 2026)

        assert stats["status"] == "success"
        assert stats["fixtures_synced"] == 0

    @pytest.mark.asyncio
    async def test_wafl_league_sync_threads_sportix_metadata(self):
        """The REAL SportixProvider (mocked transport) — its
        get_team_metadata crests flow through build_team_metadata_lookup
        into the sync service's team_metadata untouched."""
        import json as _json
        import os
        from types import SimpleNamespace

        from packages.shared.ingestion import state_leagues
        from packages.shared.ingestion.sportix_provider import SportixProvider

        fixture = os.path.join(
            os.path.dirname(__file__), "..", "fixtures", "wafl",
            "sportix_matches_2026_trimmed.json",
        )
        club_logos = {
            "632ebb80-4bd7-11e9-9660-19fd5993277e": (
                "clubs/peel-thunder-145-LCdnaJ.png"
            ),
        }

        async def fake_fetch(path, params=None):
            if path.startswith("clubs/"):
                club_id = path.split("/", 1)[1]
                return {"id": club_id, "logo": club_logos.get(club_id)}
            if params and params.get("season_slug"):
                with open(fixture, encoding="utf-8") as f:
                    return _json.load(f)
            raise AssertionError(f"unexpected fetch: {path} {params}")

        provider = SportixProvider(
            source="sportix-wafl",
            competition_name="League",
            fetch_json=fake_fetch,
        )

        def _factory():
            return provider

        fake_config = SimpleNamespace(
            name="West Australian Football League",
            timezone="Australia/Perth",
            provider_factory=_factory,
        )

        session = AsyncMock()
        session.commit = AsyncMock()

        with patch(
            "packages.shared.services.local_competition_sync.CompetitionCRUD"
        ) as crud, patch(
            "packages.shared.services.local_competition_sync.ParticipantResolver"
        ) as resolver_cls, patch(
            "packages.shared.services.local_competition_sync.EventCRUD"
        ) as event_crud, patch.object(
            state_leagues, "LocalCompetitionSyncService",
            wraps=state_leagues.LocalCompetitionSyncService,
        ) as service_cls, patch.object(
            state_leagues, "get_league", return_value=fake_config
        ):
            competition = SimpleNamespace(id=9, timezone="Australia/Perth")
            season = SimpleNamespace(id=21, label="2026")
            crud.ensure_competition = AsyncMock(return_value=competition)
            crud.ensure_season = AsyncMock(return_value=season)
            resolver_cls.return_value.ensure_team = AsyncMock(
                return_value=SimpleNamespace(id=1)
            )
            event_crud.upsert_fixture = AsyncMock(
                return_value=SimpleNamespace(id=1)
            )
            provider.get_fixtures = AsyncMock(return_value=[])

            stats = await run_league_sync(session, "wafl", 2026)

        assert stats["status"] == "success"
        lookup = service_cls.call_args.kwargs["team_metadata"]
        assert lookup is not None
        assert lookup("Peel Thunder") == {
            "logo_url": (
                "https://storage-cdn.sportix.cloud"
                "/clubs/peel-thunder-145-LCdnaJ.png"
            )
        }
        assert lookup("East Fremantle") is None  # crest not on file here


