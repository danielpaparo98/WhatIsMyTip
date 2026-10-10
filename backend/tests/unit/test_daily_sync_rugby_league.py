"""Unit tests for the rugby-league section of the daily sync (Phase 5.2).

The daily-sync pass carries a second sport: every live national
rugby-league competition (``nrl``, ``nrlw``, ``origin``) syncs on the
same job as the AFL sync — gated by the rugby-league ``SportContext``
(cron timezone ``Australia/Brisbane``, off-season Nov–Feb), polite via
the FixtureDownload provider's shared 24h TTL cache (at most one fetch
per slug per day), and failure-isolated per league so a rugby-league
outage can never break the AFL run.
"""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from zoneinfo import ZoneInfo

import pytest

from packages.shared.services import daily_sync
from packages.shared.services.daily_sync import (
    is_rugby_league_sync_due,
    rugby_league_sync_leagues,
    run_daily_sync,
    run_rugby_league_sync,
)

BRISBANE = ZoneInfo("Australia/Brisbane")
PERTH = ZoneInfo("Australia/Perth")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_session() -> AsyncMock:
    """Build a mock AsyncSession suitable for service-level tests."""
    session = AsyncMock()
    session.commit = AsyncMock()
    session.rollback = AsyncMock()
    session.refresh = AsyncMock()
    return session


def _settings(
    *,
    enabled: bool = True,
    season: int = 2026,
    cron_tz: str = "Australia/Perth",
) -> MagicMock:
    """A settings mock with the knobs the daily sync reads."""
    mock = MagicMock()
    mock.rugby_league_sync_enabled = enabled
    mock.current_season = season
    mock.cron_timezone = cron_tz
    return mock


def _patch_settings(monkeypatch, **kwargs) -> MagicMock:
    mock = _settings(**kwargs)
    monkeypatch.setattr(daily_sync, "settings", mock)
    return mock


def _patch_afl_sync(monkeypatch, sync_return: dict = None):
    """Mock the AFL (Squiggle) half of the pass; returns (client, service)."""
    sync_return = sync_return or {
        "games_created": 0,
        "games_updated": 0,
        "games_skipped": 0,
        "total_games": 0,
        "errors": [],
    }
    client = AsyncMock()
    monkeypatch.setattr(daily_sync, "SquiggleClient", lambda: client)
    service = MagicMock()
    service.sync_games = AsyncMock(return_value=sync_return)
    monkeypatch.setattr(daily_sync, "GameSyncService", lambda **kwargs: service)
    invalidate = AsyncMock(return_value=0)
    monkeypatch.setattr(daily_sync, "invalidate_cache_pattern", invalidate)
    return client, service


def _patch_run_league_sync(monkeypatch, return_value=None, side_effect=None):
    """Mock the national-registry runner at its source module (the
    deferred import in daily_sync resolves the patched attribute)."""
    mock = AsyncMock(return_value=return_value, side_effect=side_effect)
    monkeypatch.setattr(
        "packages.shared.ingestion.national_leagues.run_league_sync", mock
    )
    return mock


def _league_stats(fixtures: int = 5) -> dict:
    return {"fixtures_synced": fixtures, "errors": [], "status": "success"}


# ---------------------------------------------------------------------------
# League selection (which leagues the sync registers)
# ---------------------------------------------------------------------------


class TestLeagueSelection:
    def test_registry_lists_the_three_rugby_league_competitions(self):
        """The owner-approved competitions (nrl, nrlw, origin) are all
        registered by the daily sync — live entries of the national
        registry, in registry order."""
        assert rugby_league_sync_leagues() == ["nrl", "nrlw", "origin"]

    def test_selection_derives_from_live_registry_status(self, monkeypatch):
        """Leagues come and go with the registry: only entries whose
        status starts with "live" are scheduled (a planned provider is
        not attempted), mirroring the state-league sweep."""
        fake = {
            "nrl": SimpleNamespace(status="live"),
            "nrlw": SimpleNamespace(status="live"),
            "origin": SimpleNamespace(status="live"),
            "future": SimpleNamespace(status="planned"),
        }
        monkeypatch.setattr(
            "packages.shared.ingestion.national_leagues.NATIONAL_LEAGUES", fake
        )
        assert daily_sync.rugby_league_sync_leagues() == ["nrl", "nrlw", "origin"]


# ---------------------------------------------------------------------------
# Schedule gate (which cadence — Brisbane time, Nov–Feb off-season)
# ---------------------------------------------------------------------------


class TestRugbyLeagueScheduleGate:
    def test_in_season_runs_any_hour(self):
        """October is rugby-league FINALS month (the AFL off-season does
        not apply): any hour of day runs."""
        assert is_rugby_league_sync_due(
            datetime(2026, 10, 15, 14, 0, tzinfo=BRISBANE)
        )

    def test_off_season_outside_window_skips(self):
        """Nov–Feb outside the reduced 2–4 AM window is a skip."""
        assert not is_rugby_league_sync_due(
            datetime(2026, 1, 15, 14, 0, tzinfo=BRISBANE)
        )

    def test_off_season_inside_window_runs(self):
        """Nov–Feb inside the reduced 2–4 AM window still runs."""
        assert is_rugby_league_sync_due(
            datetime(2026, 1, 15, 3, 0, tzinfo=BRISBANE)
        )

    def test_window_boundaries_match_the_shared_hours(self):
        """Start hour inclusive, end hour exclusive — the same
        2 AM–4 AM reduced-window semantics as the AFL gate."""
        assert is_rugby_league_sync_due(
            datetime(2026, 2, 10, 2, 0, tzinfo=BRISBANE)
        )
        assert not is_rugby_league_sync_due(
            datetime(2026, 2, 10, 4, 0, tzinfo=BRISBANE)
        )

    def test_gate_evaluates_brisbane_not_the_app_timezone(self):
        """The SAME instant judged in the two timezones must differ:
        01:00 AWST Perth is 03:00 AEST Brisbane — in-window in the
        rugby-league cron timezone even though the Perth hour is not."""
        instant = datetime(2026, 1, 15, 1, 0, tzinfo=PERTH)
        assert instant.astimezone(BRISBANE).hour == 3  # sanity: +2h east
        assert is_rugby_league_sync_due(instant)

    def test_naive_now_is_interpreted_in_the_sport_timezone(self):
        """A naive ``now`` means Brisbane local (the sport context's
        zone) — never the host machine's zone."""
        assert is_rugby_league_sync_due(datetime(2026, 1, 15, 3, 0))
        assert not is_rugby_league_sync_due(datetime(2026, 1, 15, 14, 0))


# ---------------------------------------------------------------------------
# run_rugby_league_sync — the sweep (selection + politeness + isolation)
# ---------------------------------------------------------------------------


class TestRunRugbyLeagueSync:
    @pytest.mark.asyncio
    async def test_syncs_each_registered_league_on_the_current_season(
        self, monkeypatch
    ):
        """The sweep drives the national registry runner once per live
        league with the configured season — via ``run_league_sync``, so
        the NrlProvider's 24h TTL cache (≤ 1 fetch/slug/day) is the
        only network path."""
        session = _make_session()
        _patch_settings(monkeypatch, season=2026)
        mock = _patch_run_league_sync(monkeypatch, return_value=_league_stats(5))

        result = await run_rugby_league_sync(
            session, now=datetime(2026, 6, 15, 10, 0, tzinfo=BRISBANE)
        )

        assert mock.await_count == 3
        assert [call.args[1] for call in mock.await_args_list] == [
            "nrl",
            "nrlw",
            "origin",
        ]
        assert all(call.args[2] == 2026 for call in mock.await_args_list)
        assert result["status"] == "success"
        assert result["leagues_synced"] == ["nrl", "nrlw", "origin"]
        assert result["fixtures_synced"] == 15
        assert result["season"] == 2026

    @pytest.mark.asyncio
    async def test_one_league_failure_does_not_break_the_others(self, monkeypatch):
        """Failure isolation: nrl blowing up records the failure and the
        remaining leagues still sync (status partial, never a raise)."""
        session = _make_session()
        _patch_settings(monkeypatch)
        mock = _patch_run_league_sync(
            monkeypatch,
            side_effect=[
                RuntimeError("feed down"),
                _league_stats(2),
                _league_stats(3),
            ],
        )

        result = await run_rugby_league_sync(
            session, now=datetime(2026, 6, 15, 10, 0, tzinfo=BRISBANE)
        )

        assert mock.await_count == 3
        assert result["status"] == "partial"
        assert result["leagues_failed"] == ["nrl"]
        assert result["leagues_synced"] == ["nrlw", "origin"]
        assert result["fixtures_synced"] == 5
        assert result["errors"] == ["nrl: feed down"]
        # The failed league's transaction is rolled back so the session
        # stays usable for the remaining leagues.
        session.rollback.assert_awaited()

    @pytest.mark.asyncio
    async def test_every_league_failing_still_reports_without_raising(
        self, monkeypatch
    ):
        """A total rugby-league outage is a 'partial' aggregate — the
        sweep is total by contract and never propagates."""
        session = _make_session()
        _patch_settings(monkeypatch)
        mock = _patch_run_league_sync(monkeypatch, side_effect=RuntimeError("down"))

        result = await run_rugby_league_sync(
            session, now=datetime(2026, 6, 15, 10, 0, tzinfo=BRISBANE)
        )

        assert mock.await_count == 3
        assert result["status"] == "partial"
        assert result["leagues_failed"] == ["nrl", "nrlw", "origin"]
        assert result["leagues_synced"] == []

    @pytest.mark.asyncio
    async def test_off_season_outside_window_skips_without_syncing(
        self, monkeypatch
    ):
        """Nov–Feb outside the reduced window: the gate short-circuits
        before any league is attempted."""
        session = _make_session()
        _patch_settings(monkeypatch)
        mock = _patch_run_league_sync(monkeypatch, return_value=_league_stats())

        result = await run_rugby_league_sync(
            session, now=datetime(2026, 1, 15, 14, 0, tzinfo=BRISBANE)
        )

        mock.assert_not_awaited()
        assert result["status"] == "off_season_skip"
        assert "off-season" in result["message"].lower()
        # The attempted list is still reported for ops visibility.
        assert result["leagues"] == ["nrl", "nrlw", "origin"]

    @pytest.mark.asyncio
    async def test_explicit_leagues_and_season_override_the_derivation(
        self, monkeypatch
    ):
        """Backfill ergonomics: an explicit league list/season overrides
        the registry derivation and settings season."""
        session = _make_session()
        _patch_settings(monkeypatch)
        mock = _patch_run_league_sync(monkeypatch, return_value=_league_stats(1))

        result = await run_rugby_league_sync(
            session,
            now=datetime(2026, 6, 15, 10, 0, tzinfo=BRISBANE),
            season=2024,
            leagues=["origin"],
        )

        mock.assert_awaited_once()
        assert mock.await_args.args == (session, "origin", 2024)
        assert result["leagues"] == ["origin"]
        assert result["season"] == 2024

    @pytest.mark.asyncio
    async def test_result_reports_the_sport_identity(self, monkeypatch):
        """The aggregate carries the sport + the cron timezone the gate
        is evaluated in (the SportContext decision, not the app tz)."""
        session = _make_session()
        _patch_settings(monkeypatch)
        _patch_run_league_sync(monkeypatch, return_value=_league_stats())

        result = await run_rugby_league_sync(
            session, now=datetime(2026, 6, 15, 10, 0, tzinfo=BRISBANE)
        )

        assert result["sport"] == "rugby-league"
        assert result["cron_timezone"] == "Australia/Brisbane"


# ---------------------------------------------------------------------------
# run_daily_sync — registration alongside the AFL run
# ---------------------------------------------------------------------------


class TestDailySyncRugbyLeagueRegistration:
    @pytest.mark.asyncio
    async def test_daily_sync_registers_rugby_league_alongside_afl(
        self, monkeypatch
    ):
        """One job, both sports: the AFL result is unchanged and the
        rugby-league aggregate rides the same pass."""
        session = _make_session()
        _patch_settings(monkeypatch)
        _, afl_service = _patch_afl_sync(
            monkeypatch,
            sync_return={
                "games_created": 0,
                "games_updated": 0,
                "games_skipped": 3,
                "total_games": 3,
                "errors": [],
            },
        )
        mock = _patch_run_league_sync(monkeypatch, return_value=_league_stats(4))

        result = await run_daily_sync(
            session, now=datetime(2026, 6, 15, 10, 0, tzinfo=PERTH)
        )

        assert result["status"] == "success"
        assert result["total_games"] == 3
        assert afl_service.sync_games.await_count == 1
        assert mock.await_count == 3
        assert result["rugby_league"]["status"] == "success"
        assert "Rugby league" in result["message"]

    @pytest.mark.asyncio
    async def test_rugby_league_failure_does_not_break_the_afl_run(
        self, monkeypatch
    ):
        """The binding isolation requirement: a rugby-league outage is
        recorded, and the AFL sync in the same job still runs and keeps
        its own success status."""
        session = _make_session()
        _patch_settings(monkeypatch)
        _, afl_service = _patch_afl_sync(
            monkeypatch,
            sync_return={
                "games_created": 0,
                "games_updated": 0,
                "games_skipped": 7,
                "total_games": 7,
                "errors": [],
            },
        )
        _patch_run_league_sync(monkeypatch, side_effect=RuntimeError("feed down"))

        result = await run_daily_sync(
            session, now=datetime(2026, 6, 15, 10, 0, tzinfo=PERTH)
        )

        assert afl_service.sync_games.await_count == 1
        assert result["status"] == "success"
        assert result["errors"] == 0  # AFL error count, unchanged semantics
        assert result["rugby_league"]["status"] == "partial"
        assert result["rugby_league"]["leagues_failed"] == ["nrl", "nrlw", "origin"]

    @pytest.mark.asyncio
    async def test_afl_off_season_does_not_starve_rugby_league(self, monkeypatch):
        """The skip gate is per-sport: October 10 AM Perth is AFL
        off-season (job skips the AFL section) but rugby-league finals
        (Brisbane noon) — the leagues MUST still sync."""
        session = _make_session()
        _patch_settings(monkeypatch)
        _, afl_service = _patch_afl_sync(monkeypatch)
        mock = _patch_run_league_sync(monkeypatch, return_value=_league_stats(4))

        result = await run_daily_sync(
            session, now=datetime(2026, 10, 15, 10, 0, tzinfo=PERTH)
        )

        assert result["status"] == "skipped"
        assert "off-season" in result["message"].lower()
        afl_service.sync_games.assert_not_awaited()
        assert mock.await_count == 3
        assert result["rugby_league"]["status"] == "success"

    @pytest.mark.asyncio
    async def test_disabled_flag_skips_only_the_rugby_section(self, monkeypatch):
        """``rugby_league_sync_enabled=false`` removes the rugby-league
        section per pass — the AFL half is untouched."""
        session = _make_session()
        _patch_settings(monkeypatch, enabled=False)
        _, afl_service = _patch_afl_sync(
            monkeypatch,
            sync_return={
                "games_created": 0,
                "games_updated": 0,
                "games_skipped": 0,
                "total_games": 0,
                "errors": [],
            },
        )
        mock = _patch_run_league_sync(monkeypatch, return_value=_league_stats())

        result = await run_daily_sync(
            session, now=datetime(2026, 6, 15, 10, 0, tzinfo=PERTH)
        )

        mock.assert_not_awaited()
        assert afl_service.sync_games.await_count == 1
        assert result["status"] == "success"
        assert result["rugby_league"]["status"] == "disabled"
        assert "Rugby league" not in result["message"]

    @pytest.mark.asyncio
    async def test_rugby_section_crash_still_returns_the_afl_result(
        self, monkeypatch
    ):
        """Defence in depth: even if the section itself raises, the job
        reports the AFL result and a 'failed' rugby-league aggregate."""
        session = _make_session()
        _patch_settings(monkeypatch)
        _, afl_service = _patch_afl_sync(
            monkeypatch,
            sync_return={
                "games_created": 0,
                "games_updated": 0,
                "games_skipped": 0,
                "total_games": 0,
                "errors": [],
            },
        )
        monkeypatch.setattr(
            daily_sync,
            "run_rugby_league_sync",
            AsyncMock(side_effect=RuntimeError("boom")),
        )

        result = await run_daily_sync(
            session, now=datetime(2026, 6, 15, 10, 0, tzinfo=PERTH)
        )

        assert afl_service.sync_games.await_count == 1
        assert result["status"] == "success"
        assert result["rugby_league"]["status"] == "failed"
        assert result["rugby_league"]["errors"]
