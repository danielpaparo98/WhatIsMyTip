"""Unit tests for ``packages.shared.services.daily_sync``.

The service function is the reusable core that both the FaaS handler
(``backend/packages/cron/daily-sync/__init__.py``) and the new
``app.cron.daily_sync.DailySyncJob`` invoke.
"""

from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from packages.shared.services.daily_sync import run_daily_sync


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_session() -> AsyncMock:
    """Build a mock AsyncSession suitable for service-level tests."""
    session = AsyncMock()
    session.commit = AsyncMock()
    session.refresh = AsyncMock()
    return session


def _patch_squiggle_and_sync(monkeypatch, sync_return: dict, elo_update=None):
    """Patch the imports the service uses for the Squiggle API + sync service."""
    mock_client = AsyncMock()
    mock_client.close = AsyncMock()
    monkeypatch.setattr(
        "packages.shared.services.daily_sync.SquiggleClient",
        lambda: mock_client,
    )

    mock_sync_service = MagicMock()
    mock_sync_service.sync_games = AsyncMock(return_value=sync_return)
    monkeypatch.setattr(
        "packages.shared.services.daily_sync.GameSyncService",
        lambda **kwargs: mock_sync_service,
    )

    if elo_update is not None:
        monkeypatch.setattr(
            "packages.shared.services.daily_sync.EloModel", elo_update
        )

    return mock_client, mock_sync_service


def _patch_invalidate(monkeypatch, deleted: int = 0):
    """Patch the cache-invalidation helper."""
    invalidate = AsyncMock(return_value=deleted)
    monkeypatch.setattr(
        "packages.shared.services.daily_sync.invalidate_cache_pattern", invalidate
    )
    return invalidate


# ---------------------------------------------------------------------------
# run_daily_sync
# ---------------------------------------------------------------------------


class TestRunDailySync:
    @pytest.mark.asyncio
    async def test_happy_path_returns_stats(self, monkeypatch):
        session = _make_session()
        sync_return = {
            "games_created": 2,
            "games_updated": 5,
            "games_skipped": 2,
            "total_games": 9,
            "errors": [],
        }
        _patch_squiggle_and_sync(monkeypatch, sync_return)
        _patch_invalidate(monkeypatch, deleted=3)

        # Season set explicitly to avoid relying on system clock
        with patch("packages.shared.services.daily_sync.settings") as mock_settings:
            mock_settings.current_season = 2025
            mock_settings.cron_timezone = "Australia/Perth"
            result = await run_daily_sync(session, now=datetime(2025, 6, 15, 10, 0))

        assert result["status"] == "success"
        assert result["total_games"] == 9
        assert result["games_created"] == 2
        assert result["games_updated"] == 5
        assert result["games_skipped"] == 2
        assert "Synced 9 games" in result["message"]

    @pytest.mark.asyncio
    async def test_off_season_outside_window_skips(self, monkeypatch):
        """In AFL off-season (Oct-Feb) outside 2-4 AM, the job is a no-op."""
        session = _make_session()
        mock_client, mock_sync_service = _patch_squiggle_and_sync(
            monkeypatch, sync_return={"total_games": 0, "errors": []}
        )
        _patch_invalidate(monkeypatch, deleted=0)

        with patch("packages.shared.services.daily_sync.settings") as mock_settings:
            mock_settings.current_season = 2025
            mock_settings.cron_timezone = "Australia/Perth"
            # November at 10 AM AWST
            result = await run_daily_sync(
                session, now=datetime(2025, 11, 15, 10, 0)
            )

        # Sync service should NOT have been called
        mock_sync_service.sync_games.assert_not_awaited()
        # Result indicates a skip
        assert result["status"] == "skipped"
        assert "off-season" in result["message"].lower()

    @pytest.mark.asyncio
    async def test_off_season_in_window_runs(self, monkeypatch):
        """In AFL off-season (Oct-Feb) inside 2-4 AM, the job runs."""
        session = _make_session()
        sync_return = {
            "games_created": 0,
            "games_updated": 0,
            "games_skipped": 0,
            "total_games": 0,
            "errors": [],
        }
        _patch_squiggle_and_sync(monkeypatch, sync_return)
        _patch_invalidate(monkeypatch, deleted=0)

        with patch("packages.shared.services.daily_sync.settings") as mock_settings:
            mock_settings.current_season = 2025
            mock_settings.cron_timezone = "Australia/Perth"
            # November at 3 AM AWST
            result = await run_daily_sync(
                session, now=datetime(2025, 11, 15, 3, 0)
            )

        # Should run, not skip
        assert result["status"] == "success"

    @pytest.mark.asyncio
    async def test_in_season_always_runs(self, monkeypatch):
        """In AFL in-season (Mar-Sep), always runs regardless of hour."""
        session = _make_session()
        sync_return = {
            "games_created": 1,
            "games_updated": 0,
            "games_skipped": 0,
            "total_games": 1,
            "errors": [],
        }
        _patch_squiggle_and_sync(monkeypatch, sync_return)
        _patch_invalidate(monkeypatch, deleted=0)

        with patch("packages.shared.services.daily_sync.settings") as mock_settings:
            mock_settings.current_season = 2025
            mock_settings.cron_timezone = "Australia/Perth"
            # June at 3 PM AWST (in-season)
            result = await run_daily_sync(
                session, now=datetime(2025, 6, 15, 15, 0)
            )

        assert result["status"] == "success"
        assert result["total_games"] == 1

    @pytest.mark.asyncio
    async def test_includes_error_count_in_result(self, monkeypatch):
        """If sync returns errors, result should reflect the error count."""
        session = _make_session()
        sync_return = {
            "games_created": 1,
            "games_updated": 0,
            "games_skipped": 0,
            "total_games": 1,
            "errors": ["game 123 failed"],
        }
        _patch_squiggle_and_sync(monkeypatch, sync_return)
        _patch_invalidate(monkeypatch, deleted=0)

        with patch("packages.shared.services.daily_sync.settings") as mock_settings:
            mock_settings.current_season = 2025
            mock_settings.cron_timezone = "Australia/Perth"
            result = await run_daily_sync(session, now=datetime(2025, 6, 15, 10, 0))

        assert result["status"] == "success"
        assert result["errors"] == 1
        assert "Failed: 1" in result["message"]
