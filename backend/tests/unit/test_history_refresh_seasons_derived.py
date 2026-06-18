"""Tests for SEC-LO-003: auto-derive ``history_refresh_seasons``.

The Phase 4 ``historic_refresh_seasons`` setting was a hard-coded
``"2010-2025"`` string.  That means every January 1st the
``HistoricDataRefreshService`` silently skips the most recent (and
most useful) season, and the operator has to remember to bump the
string in the env file or the config.

The fix: derive the default season range from
``settings.current_season`` so the cron job always covers the most
recent 16 AFL seasons automatically.  Operators can still override
the range by setting ``HISTORIC_REFRESH_SEASONS`` explicitly.
"""

from __future__ import annotations

import pytest

from packages.shared.config import settings
from packages.shared.services.historic_data_refresh import (
    HistoricDataRefreshService,
    derive_default_seasons,
)


class TestDeriveDefaultSeasons:
    """``derive_default_seasons`` returns a 16-year window ending at
    ``settings.current_season`` (exclusive)."""

    def test_uses_settings_current_season(self, monkeypatch) -> None:
        monkeypatch.setattr(settings, "current_season", 2026)
        result = derive_default_seasons(current_season=2026)
        # 16 years, ending at current_season (exclusive).
        assert result == list(range(2010, 2026))

    def test_window_is_16_years(self) -> None:
        result = derive_default_seasons(current_season=2030)
        assert len(result) == 16
        # And it ends at current_season (exclusive).
        assert result[-1] == 2029

    def test_window_starts_at_minus_16(self) -> None:
        result = derive_default_seasons(current_season=2030)
        assert result[0] == 2014  # 2030 - 16

    def test_explicit_override(self) -> None:
        """Caller can override current_season without touching settings."""
        result = derive_default_seasons(current_season=2050)
        assert result == list(range(2034, 2050))


class TestServiceDefaultSeasons:
    """The service uses the derived default when no seasons are passed."""

    def test_service_uses_derived_default(self, monkeypatch) -> None:
        monkeypatch.setattr(settings, "current_season", 2026)

        # Stub out a DB session — we only check the default seasons
        # value, not the network calls.
        class _FakeSession:
            pass

        svc = HistoricDataRefreshService(db_session=_FakeSession())  # type: ignore[arg-type]
        assert svc.seasons == list(range(2010, 2026))

    def test_explicit_seasons_win_over_default(self, monkeypatch) -> None:
        monkeypatch.setattr(settings, "current_season", 2026)

        class _FakeSession:
            pass

        explicit = [2015, 2016, 2017]
        svc = HistoricDataRefreshService(
            db_session=_FakeSession(),  # type: ignore[arg-type]
            seasons=explicit,
        )
        assert svc.seasons == explicit
