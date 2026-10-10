"""National-league registry tests (Phase 5.2 — rugby-league expansion).

The national registry mirrors the state-league pattern (frozen
``LeagueConfig`` entries + ``get_league``/``run_league_sync``) for the
three owner-approved rugby-league competitions (2026-10-09): ``nrl``,
``nrlw`` and ``origin``.  The facade ``get_league`` resolves across
BOTH registries; ``run_league_sync`` drives
``LocalCompetitionSyncService`` with the config's tier/format (the
shared config defaults to the state values, so state keys behave
identically through either runner).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.exceptions import BackendServiceError
from packages.shared.ingestion.league_config import LeagueConfig
from packages.shared.ingestion.league_seeding import (
    venue_timezone as league_venue_timezone,
)
from packages.shared.ingestion.national_leagues import (
    NATIONAL_LEAGUES,
    get_league,
    run_league_sync,
)
from packages.shared.ingestion.state_leagues import STATE_LEAGUES

_IDENTITY_PATCH = (
    "packages.shared.ingestion.national_leagues."
    "_ensure_rugby_league_identity"
)


def _mock_provider() -> MagicMock:
    provider = MagicMock()
    provider.sport_id = "rugby-league"
    provider.get_fixtures = AsyncMock(return_value=[])
    return provider


class TestNationalRegistry:
    """The three rugby-league competitions with their owner-approved
    identity (name / tier / format / timezone)."""

    def test_exactly_the_three_approved_competitions(self):
        assert set(NATIONAL_LEAGUES) == {"nrl", "nrlw", "origin"}

    def test_nrl_config(self):
        config = NATIONAL_LEAGUES["nrl"]
        assert config.name == "National Rugby League"
        assert config.tier == "national"
        assert config.format == "rounds"
        assert config.timezone == "Australia/Brisbane"
        assert config.status == "live"
        assert config.provider_factory is not None

    def test_nrlw_config(self):
        config = NATIONAL_LEAGUES["nrlw"]
        assert config.name == "NRL Women's Premiership"
        assert config.tier == "national"
        assert config.format == "rounds"
        assert config.timezone == "Australia/Brisbane"
        assert config.status == "live"
        assert config.provider_factory is not None

    def test_origin_config(self):
        config = NATIONAL_LEAGUES["origin"]
        assert config.name == "State of Origin"
        assert config.tier == "national"
        assert config.format == "tournament"
        assert config.timezone == "Australia/Brisbane"
        assert config.status == "live"
        assert config.provider_factory is not None

    def test_provider_factories_yield_nrl_providers(self):
        from packages.shared.ingestion.nrl_provider import NrlProvider

        for key in ("nrl", "nrlw", "origin"):
            provider = NATIONAL_LEAGUES[key].provider_factory()
            assert isinstance(provider, NrlProvider), key
            assert provider.competition == key, key
            assert provider.sport_id == "rugby-league", key
            assert provider.source == f"fixturedownload-{key}", key

    def test_source_notes_name_the_feed(self):
        for key, config in NATIONAL_LEAGUES.items():
            assert "fixturedownload.com" in config.source_note, key

    def test_config_shape_is_shared_with_state_leagues(self):
        """ONE frozen LeagueConfig class for both registries — the state
        module re-exports the shared one, no duplicate definition."""
        from packages.shared.ingestion import state_leagues

        assert state_leagues.LeagueConfig is LeagueConfig
        assert isinstance(NATIONAL_LEAGUES["nrl"], LeagueConfig)
        assert isinstance(STATE_LEAGUES["wafl"], LeagueConfig)

    def test_state_registry_unchanged_by_the_expansion(self):
        assert {
            "wafl",
            "waflw",
            "sanfl",
            "vfl",
            "vflw",
            "aflw",
            "qafl",
            "tsl",
            "nwfl",
            "sfl",
        } <= set(STATE_LEAGUES)


class TestGetLeagueFacade:
    """``get_league`` resolves across BOTH registries."""

    def test_resolves_state_league_to_the_same_config(self):
        assert get_league("wafl") is STATE_LEAGUES["wafl"]
        assert get_league("tsl") is STATE_LEAGUES["tsl"]

    def test_resolves_national_leagues(self):
        assert get_league("nrl") is NATIONAL_LEAGUES["nrl"]
        assert get_league("nrlw") is NATIONAL_LEAGUES["nrlw"]
        assert get_league("origin") is NATIONAL_LEAGUES["origin"]

    def test_unknown_league_raises_repo_standard_error(self):
        with pytest.raises(ValueError, match="available"):
            get_league("not-a-league")

    def test_error_message_lists_both_registries(self):
        with pytest.raises(ValueError) as excinfo:
            get_league("not-a-league")
        message = str(excinfo.value)
        assert "wafl" in message  # a state key
        assert "nrl" in message  # a national key


class TestRunLeagueSync:
    """The facade runner drives ``LocalCompetitionSyncService`` with the
    registry config's identity (name / tz / tier / format)."""

    @pytest.mark.asyncio
    async def test_nrl_sync_passes_national_identity_to_the_service(self):
        session = AsyncMock()

        with patch(
            "packages.shared.ingestion.national_leagues."
            "LocalCompetitionSyncService"
        ) as service_cls, patch(
            "packages.shared.ingestion.national_leagues."
            "build_team_metadata_lookup",
            new=AsyncMock(return_value=None),
        ), patch(
            "packages.shared.ingestion.national_leagues._nrl"
        ) as nrl_factory, patch(
            _IDENTITY_PATCH, new=AsyncMock()
        ) as ensure_identity:
            nrl_factory.return_value = _mock_provider()
            service_cls.return_value.sync = AsyncMock(
                return_value={"competition_id": 77, "fixtures_synced": 0}
            )

            stats = await run_league_sync(session, "nrl", 2026)

        assert stats["league"] == "nrl"
        assert stats["status"] == "success"
        ensure_identity.assert_awaited_once_with(session)
        kwargs = service_cls.call_args.kwargs
        assert kwargs["provider"] is nrl_factory.return_value
        assert kwargs["competition_name"] == "National Rugby League"
        assert kwargs["season"] == 2026
        assert kwargs["competition_tier"] == "national"
        assert kwargs["competition_format"] == "rounds"
        assert kwargs["competition_timezone"] == "Australia/Brisbane"
        # Venue-level tz truth rides along for rugby-league.
        assert kwargs["venue_timezone"] is league_venue_timezone
        assert kwargs["team_metadata"] is None
        service_cls.return_value.sync.assert_awaited_once_with()

    @pytest.mark.asyncio
    async def test_origin_sync_passes_tournament_format(self):
        session = AsyncMock()

        with patch(
            "packages.shared.ingestion.national_leagues."
            "LocalCompetitionSyncService"
        ) as service_cls, patch(
            "packages.shared.ingestion.national_leagues."
            "build_team_metadata_lookup",
            new=AsyncMock(return_value=None),
        ), patch(
            "packages.shared.ingestion.national_leagues._nrl"
        ) as nrl_factory, patch(
            _IDENTITY_PATCH, new=AsyncMock()
        ):
            nrl_factory.return_value = _mock_provider()
            service_cls.return_value.sync = AsyncMock(
                return_value={"competition_id": 78, "fixtures_synced": 0}
            )

            stats = await run_league_sync(session, "origin", 2026)

        assert stats["league"] == "origin"
        kwargs = service_cls.call_args.kwargs
        assert kwargs["competition_name"] == "State of Origin"
        assert kwargs["competition_tier"] == "national"
        assert kwargs["competition_format"] == "tournament"
        assert kwargs["venue_timezone"] is league_venue_timezone

    @pytest.mark.asyncio
    async def test_real_nrl_provider_keeps_team_metadata_none(self):
        """The REAL NrlProvider (no network: the service is mocked
        wholesale) exposes no ``get_team_metadata`` — the real
        best-effort lookup yields None and the service keeps its
        no-identity default."""
        with patch(
            "packages.shared.ingestion.national_leagues."
            "LocalCompetitionSyncService"
        ) as service_cls, patch(
            "packages.shared.ingestion.national_leagues."
            "build_team_metadata_lookup",
            new=AsyncMock(return_value=None),
        ), patch(
            _IDENTITY_PATCH, new=AsyncMock()
        ):
            service_cls.return_value.sync = AsyncMock(
                return_value={"competition_id": 77, "fixtures_synced": 0}
            )

            stats = await run_league_sync(AsyncMock(), "nrl", 2026)

        assert stats["status"] == "success"
        kwargs = service_cls.call_args.kwargs
        assert kwargs["team_metadata"] is None
        assert kwargs["provider"].sport_id == "rugby-league"

    @pytest.mark.asyncio
    async def test_state_league_via_facade_matches_state_defaults(self):
        """A state key resolved through the facade passes tier/format
        identical to the state registry's own runner (the shared
        config's 'state'/'rounds' defaults) — and never touches the
        rugby-league identity set or its venue tz table."""
        session = AsyncMock()

        with patch(
            "packages.shared.ingestion.national_leagues."
            "LocalCompetitionSyncService"
        ) as service_cls, patch(
            "packages.shared.ingestion.national_leagues."
            "build_team_metadata_lookup",
            new=AsyncMock(return_value=None),
        ), patch(
            # The wafl provider factory resolves _sportix through the
            # state_leagues module globals — patch it there.
            "packages.shared.ingestion.state_leagues._sportix"
        ) as sportix_factory, patch(
            _IDENTITY_PATCH, new=AsyncMock()
        ) as ensure_identity:
            sportix_factory.return_value = _mock_provider()
            service_cls.return_value.sync = AsyncMock(
                return_value={"competition_id": 9, "fixtures_synced": 0}
            )

            stats = await run_league_sync(session, "wafl", 2026)

        assert stats["league"] == "wafl"
        assert stats["status"] == "success"
        ensure_identity.assert_not_awaited()
        kwargs = service_cls.call_args.kwargs
        assert kwargs["competition_tier"] == "state"
        assert kwargs["competition_format"] == "rounds"
        assert kwargs["competition_timezone"] == "Australia/Perth"
        assert kwargs["venue_timezone"] is None

    @pytest.mark.asyncio
    async def test_unproven_league_raises_with_guidance(self):
        """tsl is registered state-side with no provider — the facade
        surfaces the same NotImplementedError as the state runner."""
        with pytest.raises(NotImplementedError, match="no provider yet"):
            await run_league_sync(AsyncMock(), "tsl", 2026)

    @pytest.mark.asyncio
    async def test_mark_current_false_clears_the_season_flag(self):
        """``mark_current=False`` (historical backfills) reverts the
        season's ``is_current`` flag after the sync pass."""
        session = AsyncMock()

        with patch(
            "packages.shared.ingestion.national_leagues."
            "LocalCompetitionSyncService"
        ) as service_cls, patch(
            "packages.shared.ingestion.national_leagues."
            "build_team_metadata_lookup",
            new=AsyncMock(return_value=None),
        ), patch(
            "packages.shared.ingestion.national_leagues._nrl"
        ) as nrl_factory, patch(
            _IDENTITY_PATCH, new=AsyncMock()
        ):
            nrl_factory.return_value = _mock_provider()
            service_cls.return_value.sync = AsyncMock(
                return_value={"competition_id": 77, "fixtures_synced": 0}
            )

            stats = await run_league_sync(
                session, "nrl", 2025, mark_current=False
            )

        assert stats["league"] == "nrl"
        assert stats["status"] == "success"
        assert session.execute.await_count == 1
        assert session.commit.await_count == 1


class TestNationalRunnerWiring:
    """The national runner's Phase A wiring: identity prerequisites
    scope to national keys only, and failures carry the repo-standard
    ``BackendServiceError`` shape."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("league_key", ["nrl", "nrlw", "origin"])
    async def test_national_keys_ensure_rugby_league_identity_first(
        self, league_key
    ):
        """Sport row + canonical participants are guaranteed before the
        pass, so feed nicknames resolve at the exact-name step."""
        session = AsyncMock()
        order: list[str] = []

        async def _identity(sess):
            order.append("identity")

        def _provider_factory(competition):
            order.append("provider")
            return _mock_provider()

        with patch(
            "packages.shared.ingestion.national_leagues."
            "LocalCompetitionSyncService"
        ) as service_cls, patch(
            "packages.shared.ingestion.national_leagues."
            "build_team_metadata_lookup",
            new=AsyncMock(return_value=None),
        ), patch(
            "packages.shared.ingestion.national_leagues._nrl",
            _provider_factory,
        ), patch(
            _IDENTITY_PATCH, new=AsyncMock(side_effect=_identity)
        ):
            service_cls.return_value.sync = AsyncMock(
                return_value={"competition_id": 1, "fixtures_synced": 0}
            )

            await run_league_sync(session, league_key, 2026)

        assert order == ["identity", "provider"], (
            "identity prerequisites must run before the provider engages"
        )

    @pytest.mark.asyncio
    async def test_identity_seeds_sport_and_all_19_teams(self):
        """The REAL prerequisite step (mock session): ensure_sport plus
        every canonical team identity with its aliases — the exact-name
        resolution contract."""
        from packages.shared.ingestion import national_leagues as nl
        from packages.shared.ingestion.league_seeding import TEAMS

        session = AsyncMock()
        with patch.object(
            nl, "ensure_sport", new=AsyncMock()
        ) as ensure_sport, patch.object(
            nl.ParticipantResolver, "ensure_team", new=AsyncMock()
        ) as ensure_team:
            await nl._ensure_rugby_league_identity(session)

        ensure_sport.assert_awaited_once_with(session)
        assert ensure_team.await_count == len(TEAMS) == 19
        by_name = {
            call.args[0]: call.kwargs for call in ensure_team.await_args_list
        }
        assert by_name["Bulldogs"] == {
            "aliases": ("Canterbury-Bankstown Bulldogs", "Canterbury Bulldogs")
        }
        assert by_name["Blues"] == {
            "aliases": (
                "NSW Blues",
                "New South Wales Blues",
                "New South Wales",
            )
        }

    @pytest.mark.asyncio
    async def test_sync_failure_raises_backend_service_error_502(self):
        """An operational failure (feed down, DB fault) surfaces as
        BackendServiceError with the repo-standard shape and the
        original cause preserved."""
        session = AsyncMock()
        boom = RuntimeError("fixturedownload unreachable")

        with patch(
            "packages.shared.ingestion.national_leagues."
            "LocalCompetitionSyncService"
        ) as service_cls, patch(
            "packages.shared.ingestion.national_leagues."
            "build_team_metadata_lookup",
            new=AsyncMock(return_value=None),
        ), patch(
            _IDENTITY_PATCH, new=AsyncMock()
        ):
            service_cls.return_value.sync = AsyncMock(side_effect=boom)

            with pytest.raises(BackendServiceError) as excinfo:
                await run_league_sync(session, "nrl", 2026)

        error = excinfo.value
        assert error.status_code == 502
        assert error.code == "league_sync_failed"
        assert "National Rugby League" in error.message
        assert error.details == {"league": "nrl", "season": 2026}
        assert error.__cause__ is boom

    @pytest.mark.asyncio
    async def test_unknown_league_raises_backend_service_error_400(self):
        with pytest.raises(BackendServiceError) as excinfo:
            await run_league_sync(AsyncMock(), "afl-mens", 2026)

        error = excinfo.value
        assert error.status_code == 400
        assert error.code == "unknown_league"
        assert error.details == {"league": "afl-mens", "season": 2026}
        assert isinstance(error.__cause__, ValueError)

    @pytest.mark.asyncio
    async def test_partial_fixture_errors_are_returned_not_raised(self):
        """Per-fixture failures stay on stats['errors'] — logged by the
        service, never swallowed, never a raise."""
        session = AsyncMock()
        partial = {
            "competition_id": 1,
            "fixtures_synced": 4,
            "errors": ["fixture 5: simulated upsert failure"],
        }

        with patch(
            "packages.shared.ingestion.national_leagues."
            "LocalCompetitionSyncService"
        ) as service_cls, patch(
            "packages.shared.ingestion.national_leagues."
            "build_team_metadata_lookup",
            new=AsyncMock(return_value=None),
        ), patch(
            _IDENTITY_PATCH, new=AsyncMock()
        ):
            service_cls.return_value.sync = AsyncMock(return_value=partial)

            stats = await run_league_sync(session, "nrl", 2026)

        assert stats["status"] == "success"
        assert stats["fixtures_synced"] == 4
        assert stats["errors"] == ["fixture 5: simulated upsert failure"]
