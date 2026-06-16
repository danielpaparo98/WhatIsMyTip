"""Unit tests for ``packages.shared.services.match_completion`` service function.

The service function is the reusable core that both the FaaS handler
and the new ``app.cron.match_completion.MatchCompletionJob`` invoke.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from packages.shared.services.match_completion import run_match_completion


def _make_session() -> AsyncMock:
    session = AsyncMock()
    session.commit = AsyncMock()
    session.refresh = AsyncMock()
    return session


def _patch_detector(monkeypatch, return_value: dict):
    """Patch the MatchCompletionDetectorService class."""
    mock_squiggle_client = MagicMock()
    mock_squiggle_client.close = AsyncMock()

    mock_service = MagicMock()
    mock_service.detect_and_process_completed_matches = AsyncMock(return_value=return_value)

    monkeypatch.setattr(
        "packages.shared.services.match_completion.SquiggleClient",
        lambda: mock_squiggle_client,
    )
    monkeypatch.setattr(
        "packages.shared.services.match_completion.MatchCompletionDetectorService",
        lambda **kwargs: mock_service,
    )
    return mock_service


def _patch_elo(monkeypatch):
    elo = MagicMock()
    elo.update_cache = AsyncMock()
    monkeypatch.setattr(
        "packages.shared.services.match_completion.EloModel", elo
    )
    return elo


def _patch_invalidate(monkeypatch):
    invalidate = AsyncMock(return_value=0)
    monkeypatch.setattr(
        "packages.shared.services.match_completion.invalidate_cache_pattern",
        invalidate,
    )
    return invalidate


class TestRunMatchCompletion:
    @pytest.mark.asyncio
    async def test_happy_path_returns_stats(self, monkeypatch):
        session = _make_session()
        stats = {
            "games_checked": 5,
            "games_completed": 2,
            "games_already_completed": 1,
            "games_not_ready": 2,
            "errors": [],
        }
        detector = _patch_detector(monkeypatch, stats)
        elo = _patch_elo(monkeypatch)
        _patch_invalidate(monkeypatch)

        result = await run_match_completion(session)

        detector.detect_and_process_completed_matches.assert_awaited_once()
        assert result["status"] == "success"
        assert result["games_checked"] == 5
        assert result["games_completed"] == 2
        # Elo cache updated when there are new completions
        elo.update_cache.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_no_completed_games_skips_elo_update(self, monkeypatch):
        session = _make_session()
        stats = {
            "games_checked": 3,
            "games_completed": 0,
            "games_already_completed": 1,
            "games_not_ready": 2,
            "errors": [],
        }
        _patch_detector(monkeypatch, stats)
        elo = _patch_elo(monkeypatch)
        _patch_invalidate(monkeypatch)

        result = await run_match_completion(session)

        # No Elo update when nothing was completed
        elo.update_cache.assert_not_awaited()
        assert result["status"] == "success"
        assert result["games_completed"] == 0

    @pytest.mark.asyncio
    async def test_elo_update_failure_does_not_fail_job(self, monkeypatch):
        session = _make_session()
        stats = {
            "games_checked": 1,
            "games_completed": 1,
            "games_already_completed": 0,
            "games_not_ready": 0,
            "errors": [],
        }
        _patch_detector(monkeypatch, stats)
        elo = MagicMock()
        elo.update_cache = AsyncMock(side_effect=RuntimeError("Elo compute failed"))
        monkeypatch.setattr(
            "packages.shared.services.match_completion.EloModel", elo
        )
        _patch_invalidate(monkeypatch)

        result = await run_match_completion(session)

        # The job is still successful
        assert result["status"] == "success"
        # Elo update attempted
        elo.update_cache.assert_awaited_once()
        # 'elo_cache_updated' should be False
        assert result["elo_cache_updated"] is False

    @pytest.mark.asyncio
    async def test_error_count_in_result(self, monkeypatch):
        session = _make_session()
        stats = {
            "games_checked": 5,
            "games_completed": 1,
            "games_already_completed": 1,
            "games_not_ready": 2,
            "errors": ["game X failed"],
        }
        _patch_detector(monkeypatch, stats)
        _patch_elo(monkeypatch)
        _patch_invalidate(monkeypatch)

        result = await run_match_completion(session)

        assert result["errors"] == 1
        assert "Failed: 1" in result["message"]
