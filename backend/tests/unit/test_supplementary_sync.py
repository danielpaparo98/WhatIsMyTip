"""Unit tests for the supplementary-data sync service (P3-3).

Injuries and weather were only refreshed by MANUAL scripts — the
injury model's point-in-time guard silently starved as scraped data
aged, with nobody alerted.  The sync service closes that gap and
alerts on failure/staleness.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from packages.shared.services.supplementary_sync import (
    SupplementaryDataSyncService,
)


def _injury(player="Player A", team="Adelaide", injury="knee", timeline="Test"):
    return {
        "player": player,
        "team": team,
        "injury": injury,
        "return_timeline": timeline,
    }


class _FakeSession:
    def __init__(self):
        self.executed: list = []
        self.added: list = []
        self.commits = 0

    async def execute(self, stmt, params=None):
        self.executed.append((stmt, params))
        result = MagicMock()
        # For the max(scraped_at) staleness probe return None (no data).
        result.scalar.return_value = None
        result.fetchall.return_value = []
        return result

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        self.commits += 1

    async def flush(self):
        return None

    async def rollback(self):
        return None


class _FakeFootyWire:
    def __init__(self, injuries):
        self._injuries = injuries

    async def get_injury_list(self):
        return self._injuries


class _FakeAlerting:
    def __init__(self):
        self.failures: list = []

    async def send_failure_alert(self, job_name, error, **kwargs):
        self.failures.append((job_name, error))
        return True


class TestInjurySync:
    @pytest.mark.asyncio
    async def test_injuries_upserted_and_counted(self):
        session = _FakeSession()
        service = SupplementaryDataSyncService(
            session,
            footywire_client=_FakeFootyWire([_injury(), _injury(player="Player B")]),
            weather_client=None,
        )

        stats = await service.sync()

        assert stats["injuries_upserted"] == 2
        assert stats["errors"] == []

    @pytest.mark.asyncio
    async def test_feed_failure_alerts_and_records_error(self):
        class _Broken:
            async def get_injury_list(self):
                raise RuntimeError("footywire down")

        alerting = _FakeAlerting()
        session = _FakeSession()
        service = SupplementaryDataSyncService(
            session, footywire_client=_Broken(), weather_client=None, alerting=alerting
        )

        stats = await service.sync()

        assert stats["injuries_upserted"] == 0
        assert any("footywire down" in e for e in stats["errors"])
        assert alerting.failures, "a feed failure must raise an alert"

    @pytest.mark.asyncio
    async def test_stale_data_alerts(self):
        """No successful scrape within the staleness window → alert."""

        class _StaleSession(_FakeSession):
            async def execute(self, stmt, params=None):
                result = await super().execute(stmt, params)
                # Answer the staleness probe with an old timestamp.
                result.scalar.return_value = datetime.now(timezone.utc) - timedelta(
                    days=30
                )
                return result

        alerting = _FakeAlerting()
        service = SupplementaryDataSyncService(
            _StaleSession(),
            footywire_client=_FakeFootyWire([]),
            weather_client=None,
            alerting=alerting,
            staleness_days=3,
        )

        stats = await service.sync()

        assert stats["stale"] is True
        assert alerting.failures, "stale data must raise an alert"

    @pytest.mark.asyncio
    async def test_empty_feed_with_fresh_data_does_not_alert(self):
        alerting = _FakeAlerting()
        service = SupplementaryDataSyncService(
            _FakeSession(),
            footywire_client=_FakeFootyWire([]),
            weather_client=None,
            alerting=alerting,
        )

        stats = await service.sync()

        assert stats["stale"] is False
        assert alerting.failures == []


class TestWeatherSync:
    @pytest.mark.asyncio
    async def test_upcoming_games_missing_weather_are_fetched(self):
        upcoming = SimpleNamespace(
            id=31,
            venue="MCG",
            date=datetime(2026, 9, 25, 10, 0),
            completed=False,
        )
        session = _FakeSession()

        # First execute: upcoming games query; second: weather-exists query.
        results = []

        upcoming_result = MagicMock()
        upcoming_result.scalars.return_value.all.return_value = [upcoming]
        weather_missing = MagicMock()
        weather_missing.scalar.return_value = 0  # no existing weather row

        class _WeatherSession(_FakeSession):
            async def execute(self, stmt, params=None):
                self.executed.append((stmt, params))
                if not results:
                    results.append(upcoming_result)
                    return upcoming_result
                results.append(weather_missing)
                return weather_missing

        class _FakeWeather:
            async def get_forecast(self, venue, days=8):
                return {
                    "hourly": {
                        "time": ["2026-09-25T10:00"],
                        "temperature_2m": [12.5],
                        "precipitation": [0.2],
                        "windspeed_10m": [20.0],
                        "winddirection_10m": [180],
                        "windgusts": [35.0],
                        "relative_humidity_2m": [60],
                        "weathercode": [3],
                    }
                }

        service = SupplementaryDataSyncService(
            _WeatherSession(),
            footywire_client=_FakeFootyWire([]),
            weather_client=_FakeWeather(),
        )

        stats = await service.sync()
        session = service.db

        assert stats["weather_stored"] == 1
        assert any(
            type(obj).__name__ == "MatchWeather" for obj in session.added
        ), "a MatchWeather row should have been added"

    @pytest.mark.asyncio
    async def test_game_without_venue_is_skipped(self):
        upcoming = SimpleNamespace(
            id=32,
            venue=None,
            date=datetime(2026, 9, 25, 10, 0),
            completed=False,
        )
        upcoming_result = MagicMock()
        upcoming_result.scalars.return_value.all.return_value = [upcoming]

        class _WeatherSession(_FakeSession):
            async def execute(self, stmt, params=None):
                self.executed.append((stmt, params))
                return upcoming_result

        class _FakeWeather:
            def __init__(self):
                self.calls = 0

            async def get_forecast(self, venue, days=8):
                self.calls += 1
                return {}

        weather = _FakeWeather()
        service = SupplementaryDataSyncService(
            _WeatherSession(),
            footywire_client=_FakeFootyWire([]),
            weather_client=weather,
        )

        stats = await service.sync()

        assert stats["weather_stored"] == 0
        assert weather.calls == 0
