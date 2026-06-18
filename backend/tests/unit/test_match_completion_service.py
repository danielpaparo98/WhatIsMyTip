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


# ---------------------------------------------------------------------------
# ME-003: Date-window filter on ``MatchCompletionDetectorService``.
# ---------------------------------------------------------------------------


class TestDateWindowFilter:
    """ME-003: ``MatchCompletionDetectorService.detect_and_process_completed_matches``
    must call ``client.get_games`` with a date-window filter — NOT
    the bare ``year=current_year`` call that re-downloads the entire
    AFL season every 15 minutes.
    """

    @pytest.mark.asyncio
    async def test_get_games_uses_date_window_filter(self, monkeypatch):
        from types import SimpleNamespace
        from packages.shared.services.match_completion import (
            MatchCompletionDetectorService,
        )

        session = AsyncMock()
        # Build a SquiggleClient mock with an AsyncMock get_games.
        client = MagicMock()
        client.get_games = AsyncMock(return_value=[])
        client.close = AsyncMock()
        detector = MatchCompletionDetectorService(
            squiggle_client=client,
            db_session=session,
            buffer_minutes=60,
        )
        # Return one recent game so the detector actually reaches the
        # Squiggle fetch.
        recent_game = SimpleNamespace(
            id=1, squiggle_id=9001, home_team="Home", away_team="Away",
        )
        monkeypatch.setattr(
            "packages.shared.services.match_completion.GameCRUD.get_recently_finished_games",
            AsyncMock(return_value=[recent_game]),
        )
        # Stub update_game_completion to a no-op.
        async def _noop(db, *, game_id, squiggle_data):
            return SimpleNamespace(
                id=game_id, home_score=50, away_score=40,
            )
        monkeypatch.setattr(
            "packages.shared.services.match_completion.GameCRUD.update_game_completion",
            _noop,
        )

        await detector.detect_and_process_completed_matches()

        # The detector MUST have called get_games exactly once.
        assert client.get_games.await_count == 1

        call = client.get_games.await_args
        call_kwargs = call.kwargs or {}

        # ME-003 acceptance: the call must narrow the Squiggle fetch
        # with a date filter (start_date / end_date) or a status
        # filter (complete=) — anything other than the bare year=
        # regression.
        narrowing_keys = {
            "start_date", "end_date",
            "from_date", "to_date", "since", "until",
            "complete",
        }
        assert call_kwargs.keys() & narrowing_keys, (
            "ME-003: detector must narrow the Squiggle fetch with a "
            "date filter instead of the bare year= call.  Got: "
            f"{call_kwargs} (args={call.args})"
        )
        # Pure-year regression is the bug we are guarding against.
        assert not (set(call_kwargs.keys()) == {"year"}), (
            "ME-003: passing only year= to get_games is exactly the "
            "regression we are guarding against."
        )
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
