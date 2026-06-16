"""Unit tests for the Tip Generation scheduled function.

Tests the ``main()`` FaaS entry point, which (post-Phase 3 refactor)
delegates to ``packages.shared.services.tip_generation.run_tip_generation``.
We mock at the service layer to verify the FaaS handler still wires up
lock acquisition, execution tracking, and the OpenWhisk-shaped
``statusCode`` / ``body`` response.
"""

import importlib.util
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Load the tip-generation module from its file path (hyphen in directory name)
_MODULE_PATH = os.path.join(
    os.path.dirname(__file__), "..", "..", "packages", "cron", "tip-generation", "__init__.py"
)
_spec = importlib.util.spec_from_file_location("tip_generation", _MODULE_PATH)
tip_generation = importlib.util.module_from_spec(_spec)


def _import_tip_generation():
    """Import (or re-import) the tip-generation module."""
    _spec.loader.exec_module(tip_generation)
    return tip_generation


SERVICE_PATH = "packages.shared.services.tip_generation"


class TestTipGenerationFunction:
    """Test the tip-generation scheduled function."""

    @pytest.mark.asyncio
    async def test_successful_tip_generation(self):
        """A normal tip generation run completes with statusCode 200."""
        mod = _import_tip_generation()

        mock_session = AsyncMock()
        mock_execution = MagicMock(id=1)

        mock_service_result = {
            "status": "success",
            "message": "Generated tips for next upcoming round; Processed 9 games; Created 27 tips; Skipped 0 existing tips; Created 36 model predictions; Generated 27 AI explanations",
            "games_processed": 9,
            "tips_created": 27,
            "tips_skipped": 0,
            "tips_updated": 0,
            "model_predictions_created": 36,
            "model_predictions_updated": 0,
            "errors": 0,
            "explanations_generated": 27,
        }

        with patch.object(mod, "_get_session_factory") as mock_factory, \
             patch.object(mod, "JobLockCRUD") as mock_lock_crud_cls, \
             patch.object(mod, "JobExecutionCRUD") as mock_exec_crud_cls, \
             patch(f"{SERVICE_PATH}.run_tip_generation", new_callable=AsyncMock, return_value=mock_service_result), \
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
        assert "Generated tips" in result["body"]["message"]

    @pytest.mark.asyncio
    async def test_no_upcoming_round(self):
        """When no upcoming round is found, returns 200 with info message."""
        mod = _import_tip_generation()

        mock_session = AsyncMock()
        mock_execution = MagicMock(id=1)

        mock_service_result = {
            "status": "success",
            "message": "No upcoming rounds found that need tips",
            "games_processed": 0,
            "tips_created": 0,
            "tips_skipped": 0,
            "tips_updated": 0,
            "model_predictions_created": 0,
            "model_predictions_updated": 0,
            "errors": 0,
            "explanations_generated": 0,
        }

        with patch.object(mod, "_get_session_factory") as mock_factory, \
             patch.object(mod, "JobLockCRUD") as mock_lock_crud_cls, \
             patch.object(mod, "JobExecutionCRUD") as mock_exec_crud_cls, \
             patch(f"{SERVICE_PATH}.run_tip_generation", new_callable=AsyncMock, return_value=mock_service_result), \
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
        assert "no upcoming" in result["body"]["message"].lower()

    @pytest.mark.asyncio
    async def test_lock_acquisition_failure(self):
        """When lock cannot be acquired, returns 200 'already running'."""
        mod = _import_tip_generation()

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
    async def test_generation_error_returns_500(self):
        """When the service raises an exception, returns 500."""
        mod = _import_tip_generation()

        mock_session = AsyncMock()
        mock_execution = MagicMock(id=1)

        with patch.object(mod, "_get_session_factory") as mock_factory, \
             patch.object(mod, "JobLockCRUD") as mock_lock_crud_cls, \
             patch.object(mod, "JobExecutionCRUD") as mock_exec_crud_cls, \
             patch(f"{SERVICE_PATH}.run_tip_generation", new_callable=AsyncMock, side_effect=RuntimeError("Model failed")), \
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
        assert "Model failed" in result["body"]["error"]
