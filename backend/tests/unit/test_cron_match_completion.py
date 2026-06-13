"""Unit tests for the Match Completion scheduled function.

Tests the ``main()`` entry point by mocking the database session factory,
CRUD operations, services, and Redis pool. No external dependencies required.

Note: The cron function directories use hyphens (e.g. ``match-completion/``) which
are not valid Python identifiers.  We use ``importlib`` to load the module
from its file path.
"""

import importlib.util
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Load the match-completion module from its file path (hyphen in directory name)
_MODULE_PATH = os.path.join(
    os.path.dirname(__file__), "..", "..", "packages", "cron", "match-completion", "__init__.py"
)
_spec = importlib.util.spec_from_file_location("match_completion", _MODULE_PATH)
match_completion = importlib.util.module_from_spec(_spec)


def _import_match_completion():
    """Import (or re-import) the match-completion module."""
    _spec.loader.exec_module(match_completion)
    return match_completion


class TestMatchCompletionFunction:
    """Test the match-completion scheduled function."""

    @pytest.mark.asyncio
    async def test_successful_match_completion(self):
        """A normal match-completion run completes with statusCode 200."""
        mod = _import_match_completion()

        mock_session = AsyncMock()
        mock_execution = MagicMock(id=1)

        mock_stats = {
            "games_checked": 5,
            "games_completed": 2,
            "games_already_completed": 1,
            "games_not_ready": 2,
            "errors": [],
        }

        with patch.object(mod, "_get_session_factory") as mock_factory, \
             patch.object(mod, "JobLockCRUD") as mock_lock_crud_cls, \
             patch.object(mod, "JobExecutionCRUD") as mock_exec_crud_cls, \
             patch.object(mod, "SquiggleClient") as mock_squiggle_cls, \
             patch.object(mod, "MatchCompletionDetectorService") as mock_detector_cls, \
             patch.object(mod, "EloModel") as mock_elo, \
             patch.object(mod, "close_redis_pool", new_callable=AsyncMock):

            mock_factory.return_value.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_factory.return_value.return_value.__aexit__ = AsyncMock(return_value=None)

            mock_lock_crud = mock_lock_crud_cls.return_value
            mock_lock_crud.acquire_lock = AsyncMock(return_value=MagicMock())
            mock_lock_crud.release_lock = AsyncMock()

            mock_exec_crud = mock_exec_crud_cls.return_value
            mock_exec_crud.create_execution = AsyncMock(return_value=mock_execution)
            mock_exec_crud.update_execution = AsyncMock()

            mock_squiggle = mock_squiggle_cls.return_value
            mock_squiggle.close = AsyncMock()

            mock_detector = mock_detector_cls.return_value
            mock_detector.detect_and_process_completed_matches = AsyncMock(return_value=mock_stats)

            mock_elo.update_cache = AsyncMock()

            result = await mod.main({})

        assert result["statusCode"] == 200
        assert "Checked 5 games" in result["body"]["message"]
        assert "Marked 2 games as complete" in result["body"]["message"]
        assert "Elo cache updated" in result["body"]["message"]

    @pytest.mark.asyncio
    async def test_lock_acquisition_failure(self):
        """When lock cannot be acquired, returns 200 'already running'."""
        mod = _import_match_completion()

        mock_session = AsyncMock()

        with patch.object(mod, "_get_session_factory") as mock_factory, \
             patch.object(mod, "JobLockCRUD") as mock_lock_crud_cls, \
             patch.object(mod, "close_redis_pool", new_callable=AsyncMock):

            mock_factory.return_value.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_factory.return_value.return_value.__aexit__ = AsyncMock(return_value=None)

            mock_lock_crud = mock_lock_crud_cls.return_value
            mock_lock_crud.acquire_lock = AsyncMock(return_value=None)  # Lock failed

            result = await mod.main({})

        assert result["statusCode"] == 200
        assert "already running" in result["body"]["message"].lower()

    @pytest.mark.asyncio
    async def test_error_returns_500(self):
        """When match completion raises an exception, returns 500."""
        mod = _import_match_completion()

        mock_session = AsyncMock()
        mock_execution = MagicMock(id=1)

        with patch.object(mod, "_get_session_factory") as mock_factory, \
             patch.object(mod, "JobLockCRUD") as mock_lock_crud_cls, \
             patch.object(mod, "JobExecutionCRUD") as mock_exec_crud_cls, \
             patch.object(mod, "SquiggleClient") as mock_squiggle_cls, \
             patch.object(mod, "MatchCompletionDetectorService") as mock_detector_cls, \
             patch.object(mod, "close_redis_pool", new_callable=AsyncMock):

            mock_factory.return_value.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_factory.return_value.return_value.__aexit__ = AsyncMock(return_value=None)

            mock_lock_crud = mock_lock_crud_cls.return_value
            mock_lock_crud.acquire_lock = AsyncMock(return_value=MagicMock())
            mock_lock_crud.release_lock = AsyncMock()

            mock_exec_crud = mock_exec_crud_cls.return_value
            mock_exec_crud.create_execution = AsyncMock(return_value=mock_execution)
            mock_exec_crud.update_execution = AsyncMock()

            mock_squiggle = mock_squiggle_cls.return_value
            mock_squiggle.close = AsyncMock()

            mock_detector = mock_detector_cls.return_value
            mock_detector.detect_and_process_completed_matches = AsyncMock(
                side_effect=RuntimeError("Squiggle API timeout")
            )

            result = await mod.main({})

        assert result["statusCode"] == 500
        assert "Squiggle API timeout" in result["body"]["error"]

    @pytest.mark.asyncio
    async def test_no_elo_update_when_zero_completed(self):
        """When no games are completed, Elo cache is not updated."""
        mod = _import_match_completion()

        mock_session = AsyncMock()
        mock_execution = MagicMock(id=1)

        mock_stats = {
            "games_checked": 3,
            "games_completed": 0,
            "games_already_completed": 1,
            "games_not_ready": 2,
            "errors": [],
        }

        with patch.object(mod, "_get_session_factory") as mock_factory, \
             patch.object(mod, "JobLockCRUD") as mock_lock_crud_cls, \
             patch.object(mod, "JobExecutionCRUD") as mock_exec_crud_cls, \
             patch.object(mod, "SquiggleClient") as mock_squiggle_cls, \
             patch.object(mod, "MatchCompletionDetectorService") as mock_detector_cls, \
             patch.object(mod, "EloModel") as mock_elo, \
             patch.object(mod, "close_redis_pool", new_callable=AsyncMock):

            mock_factory.return_value.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_factory.return_value.return_value.__aexit__ = AsyncMock(return_value=None)

            mock_lock_crud = mock_lock_crud_cls.return_value
            mock_lock_crud.acquire_lock = AsyncMock(return_value=MagicMock())
            mock_lock_crud.release_lock = AsyncMock()

            mock_exec_crud = mock_exec_crud_cls.return_value
            mock_exec_crud.create_execution = AsyncMock(return_value=mock_execution)
            mock_exec_crud.update_execution = AsyncMock()

            mock_squiggle = mock_squiggle_cls.return_value
            mock_squiggle.close = AsyncMock()

            mock_detector = mock_detector_cls.return_value
            mock_detector.detect_and_process_completed_matches = AsyncMock(return_value=mock_stats)

            result = await mod.main({})

        assert result["statusCode"] == 200
        assert "Elo cache updated" not in result["body"]["message"]
        mock_elo.update_cache.assert_not_called()
