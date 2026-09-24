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


