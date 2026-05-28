"""Unit tests for the Tip Generation scheduled function.

Tests the ``main()`` entry point by mocking the database session factory,
CRUD operations, services, and Redis pool. No external dependencies required.

Note: The cron function directories use hyphens (e.g. ``tip-generation/``)
which are not valid Python identifiers.  We use ``importlib`` to load the
module from its file path.
"""

import importlib.util
import os
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


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


class TestTipGenerationFunction:
    """Test the tip-generation scheduled function."""

    @pytest.mark.asyncio
    async def test_successful_tip_generation(self):
        """A normal tip generation run completes with statusCode 200."""
        mod = _import_tip_generation()

        mock_session = AsyncMock()
        mock_execution = MagicMock(id=1)

        mock_gen_stats = {
            "games_processed": 9,
            "tips_created": 27,
            "tips_skipped": 0,
            "tips_updated": 0,
            "model_predictions_created": 36,
            "model_predictions_updated": 0,
            "errors": [],
        }

        with patch.object(mod, "_get_session_factory") as mock_factory, \
             patch.object(mod, "JobLockCRUD") as mock_lock_crud_cls, \
             patch.object(mod, "JobExecutionCRUD") as mock_exec_crud_cls, \
             patch.object(mod, "GameCRUD") as mock_game_crud, \
             patch.object(mod, "TipGenerationService") as mock_gen_cls, \
             patch.object(mod, "ExplanationService") as mock_expl_cls, \
             patch.object(mod, "close_redis_pool", new_callable=AsyncMock):

            mock_factory.return_value.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_factory.return_value.return_value.__aexit__ = AsyncMock(return_value=None)

            mock_lock_crud = mock_lock_crud_cls.return_value
            mock_lock_crud.acquire_lock = AsyncMock(return_value=MagicMock())
            mock_lock_crud.release_lock = AsyncMock()

            mock_exec_crud = mock_exec_crud_cls.return_value
            mock_exec_crud.create_execution = AsyncMock(return_value=mock_execution)
            mock_exec_crud.update_execution = AsyncMock()

            mock_game_crud.get_next_upcoming_round = AsyncMock(return_value=(2025, 5))

            mock_gen = mock_gen_cls.return_value
            mock_gen.generate_for_round = AsyncMock(return_value=mock_gen_stats)

            mock_expl = mock_expl_cls.return_value
            mock_expl.generate_for_round = AsyncMock(return_value=3)
            mock_expl.close = AsyncMock()

            result = await mod.main({})

        assert result["statusCode"] == 200
        assert "Generated tips" in result["body"]["message"]

    @pytest.mark.asyncio
    async def test_no_upcoming_round(self):
        """When no upcoming round is found, returns 200 with info message."""
        mod = _import_tip_generation()

        mock_session = AsyncMock()
        mock_execution = MagicMock(id=1)

        with patch.object(mod, "_get_session_factory") as mock_factory, \
             patch.object(mod, "JobLockCRUD") as mock_lock_crud_cls, \
             patch.object(mod, "JobExecutionCRUD") as mock_exec_crud_cls, \
             patch.object(mod, "GameCRUD") as mock_game_crud, \
             patch.object(mod, "close_redis_pool", new_callable=AsyncMock):

            mock_factory.return_value.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_factory.return_value.return_value.__aexit__ = AsyncMock(return_value=None)

            mock_lock_crud = mock_lock_crud_cls.return_value
            mock_lock_crud.acquire_lock = AsyncMock(return_value=MagicMock())
            mock_lock_crud.release_lock = AsyncMock()

            mock_exec_crud = mock_exec_crud_cls.return_value
            mock_exec_crud.create_execution = AsyncMock(return_value=mock_execution)
            mock_exec_crud.update_execution = AsyncMock()

            mock_game_crud.get_next_upcoming_round = AsyncMock(return_value=None)

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
        """When generation raises an exception, returns 500."""
        mod = _import_tip_generation()

        mock_session = AsyncMock()
        mock_execution = MagicMock(id=1)

        with patch.object(mod, "_get_session_factory") as mock_factory, \
             patch.object(mod, "JobLockCRUD") as mock_lock_crud_cls, \
             patch.object(mod, "JobExecutionCRUD") as mock_exec_crud_cls, \
             patch.object(mod, "GameCRUD") as mock_game_crud, \
             patch.object(mod, "TipGenerationService") as mock_gen_cls, \
             patch.object(mod, "close_redis_pool", new_callable=AsyncMock):

            mock_factory.return_value.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_factory.return_value.return_value.__aexit__ = AsyncMock(return_value=None)

            mock_lock_crud = mock_lock_crud_cls.return_value
            mock_lock_crud.acquire_lock = AsyncMock(return_value=MagicMock())
            mock_lock_crud.release_lock = AsyncMock()

            mock_exec_crud = mock_exec_crud_cls.return_value
            mock_exec_crud.create_execution = AsyncMock(return_value=mock_execution)
            mock_exec_crud.update_execution = AsyncMock()

            mock_game_crud.get_next_upcoming_round = AsyncMock(return_value=(2025, 5))

            mock_gen = mock_gen_cls.return_value
            mock_gen.generate_for_round = AsyncMock(side_effect=RuntimeError("Model failed"))

            result = await mod.main({})

        assert result["statusCode"] == 500
        assert "Model failed" in result["body"]["error"]

    @pytest.mark.asyncio
    async def test_explanation_failure_does_not_fail_job(self):
        """When explanation generation fails, the job still succeeds."""
        mod = _import_tip_generation()

        mock_session = AsyncMock()
        mock_execution = MagicMock(id=1)

        mock_gen_stats = {
            "games_processed": 9,
            "tips_created": 27,
            "tips_skipped": 0,
            "tips_updated": 0,
            "model_predictions_created": 36,
            "model_predictions_updated": 0,
            "errors": [],
        }

        with patch.object(mod, "_get_session_factory") as mock_factory, \
             patch.object(mod, "JobLockCRUD") as mock_lock_crud_cls, \
             patch.object(mod, "JobExecutionCRUD") as mock_exec_crud_cls, \
             patch.object(mod, "GameCRUD") as mock_game_crud, \
             patch.object(mod, "TipGenerationService") as mock_gen_cls, \
             patch.object(mod, "ExplanationService") as mock_expl_cls, \
             patch.object(mod, "close_redis_pool", new_callable=AsyncMock):

            mock_factory.return_value.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_factory.return_value.return_value.__aexit__ = AsyncMock(return_value=None)

            mock_lock_crud = mock_lock_crud_cls.return_value
            mock_lock_crud.acquire_lock = AsyncMock(return_value=MagicMock())
            mock_lock_crud.release_lock = AsyncMock()

            mock_exec_crud = mock_exec_crud_cls.return_value
            mock_exec_crud.create_execution = AsyncMock(return_value=mock_execution)
            mock_exec_crud.update_execution = AsyncMock()

            mock_game_crud.get_next_upcoming_round = AsyncMock(return_value=(2025, 5))

            mock_gen = mock_gen_cls.return_value
            mock_gen.generate_for_round = AsyncMock(return_value=mock_gen_stats)

            # Explanation fails
            mock_expl = mock_expl_cls.return_value
            mock_expl.generate_for_round = AsyncMock(side_effect=RuntimeError("AI unavailable"))
            mock_expl.close = AsyncMock()

            result = await mod.main({})

        assert result["statusCode"] == 200
        assert "Explanation generation failed" in result["body"]["message"]
