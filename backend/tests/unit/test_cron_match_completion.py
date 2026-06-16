"""Unit tests for the Match Completion scheduled function.

Tests the ``main()`` FaaS entry point, which (post-Phase 3 refactor)
delegates to ``packages.shared.services.match_completion.run_match_completion``.
We mock at the service layer to verify the FaaS handler still wires up
lock acquisition, execution tracking, and the OpenWhisk-shaped
``statusCode`` / ``body`` response.
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


SERVICE_PATH = "packages.shared.services.match_completion"


class TestMatchCompletionFunction:
    """Test the match-completion scheduled function."""

    @pytest.mark.asyncio
    async def test_successful_match_completion(self):
        """A normal match-completion run completes with statusCode 200."""
        mod = _import_match_completion()

        mock_session = AsyncMock()
        mock_execution = MagicMock(id=1)

        mock_service_result = {
            "status": "success",
            "message": "Checked 5 games for completion; Marked 2 games as complete; 2 games not ready; 1 already complete; Elo cache updated",
            "games_checked": 5,
            "games_completed": 2,
            "games_already_completed": 1,
            "games_not_ready": 2,
            "errors": 0,
            "elo_cache_updated": True,
        }

        with patch.object(mod, "_get_session_factory") as mock_factory, \
             patch.object(mod, "JobLockCRUD") as mock_lock_crud_cls, \
             patch.object(mod, "JobExecutionCRUD") as mock_exec_crud_cls, \
             patch(f"{SERVICE_PATH}.run_match_completion", new_callable=AsyncMock, return_value=mock_service_result), \
             patch.object(mod, "close_redis_pool", new_callable=AsyncMock):

            mock_factory.return_value.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_factory.return_value.return_value.__aexit__ = AsyncMock(return_value=None)

            mock_lock_crud = mock_lock_crud_cls.return_value
            mock_lock_crud.acquire_lock = AsyncMock(return_value=MagicMock())
            mock_lock_crud.release_lock = AsyncMock()

            mock_exec_crud = mock_exec_crud_cls.return_value
            mock_exec_crud.create_execution = AsyncMock(return_value=mock_execution)
            mock_exec_crud.update_execution = AsyncMock()

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
        """When the service raises an exception, returns 500."""
        mod = _import_match_completion()

        mock_session = AsyncMock()
        mock_execution = MagicMock(id=1)

        with patch.object(mod, "_get_session_factory") as mock_factory, \
             patch.object(mod, "JobLockCRUD") as mock_lock_crud_cls, \
             patch.object(mod, "JobExecutionCRUD") as mock_exec_crud_cls, \
             patch(f"{SERVICE_PATH}.run_match_completion", new_callable=AsyncMock, side_effect=RuntimeError("Squiggle API timeout")), \
             patch.object(mod, "close_redis_pool", new_callable=AsyncMock):

            mock_factory.return_value.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_factory.return_value.return_value.__aexit__ = AsyncMock(return_value=None)

            mock_lock_crud = mock_lock_crud_cls.return_value
            mock_lock_crud.acquire_lock = AsyncMock(return_value=MagicMock())
            mock_lock_crud.release_lock = AsyncMock()

            mock_exec_crud = mock_exec_crud_cls.return_value
            mock_exec_crud.create_execution = AsyncMock(return_value=mock_execution)
            mock_exec_crud.update_execution = AsyncMock()

            result = await mod.main({})

        assert result["statusCode"] == 500
        assert "Squiggle API timeout" in result["body"]["error"]

    @pytest.mark.asyncio
    async def test_no_elo_update_when_zero_completed(self):
        """When no games are completed, the service does not update Elo."""
        mod = _import_match_completion()

        mock_session = AsyncMock()
        mock_execution = MagicMock(id=1)

        mock_service_result = {
            "status": "success",
            "message": "Checked 3 games for completion; Marked 0 games as complete; 2 games not ready; 1 already complete",
            "games_checked": 3,
            "games_completed": 0,
            "games_already_completed": 1,
            "games_not_ready": 2,
            "errors": 0,
            "elo_cache_updated": False,
        }

        with patch.object(mod, "_get_session_factory") as mock_factory, \
             patch.object(mod, "JobLockCRUD") as mock_lock_crud_cls, \
             patch.object(mod, "JobExecutionCRUD") as mock_exec_crud_cls, \
             patch(f"{SERVICE_PATH}.run_match_completion", new_callable=AsyncMock, return_value=mock_service_result), \
             patch.object(mod, "close_redis_pool", new_callable=AsyncMock):

            mock_factory.return_value.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_factory.return_value.return_value.__aexit__ = AsyncMock(return_value=None)

            mock_lock_crud = mock_lock_crud_cls.return_value
            mock_lock_crud.acquire_lock = AsyncMock(return_value=MagicMock())
            mock_lock_crud.release_lock = AsyncMock()

            mock_exec_crud = mock_exec_crud_cls.return_value
            mock_exec_crud.create_execution = AsyncMock(return_value=mock_execution)
            mock_exec_crud.update_execution = AsyncMock()

            result = await mod.main({})

        assert result["statusCode"] == 200
        assert "Elo cache updated" not in result["body"]["message"]
