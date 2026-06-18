"""Unit tests for ``packages.shared.services.tip_generation`` service function.

The service function is the reusable core that both the FaaS handler
and the new ``app.cron.tip_generation.TipGenerationJob`` invoke.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from packages.shared.services.tip_generation import run_tip_generation


def _make_session() -> AsyncMock:
    session = AsyncMock()
    session.commit = AsyncMock()
    session.refresh = AsyncMock()
    return session


def _patch_generation(monkeypatch, return_value: dict):
    """Patch the TipGenerationService class."""
    service = MagicMock()
    service.generate_for_next_upcoming_round = AsyncMock(return_value=return_value)
    monkeypatch.setattr(
        "packages.shared.services.tip_generation.TipGenerationService",
        lambda **kwargs: service,
    )
    return service


def _patch_explanation(monkeypatch, return_value: int):
    expl = MagicMock()
    expl.generate_for_round = AsyncMock(return_value=return_value)
    expl.close = AsyncMock()
    monkeypatch.setattr(
        "packages.shared.services.tip_generation.ExplanationService",
        lambda: expl,
    )
    return expl


def _patch_invalidate(monkeypatch):
    invalidate = AsyncMock(return_value=0)
    monkeypatch.setattr(
        "packages.shared.services.tip_generation.invalidate_cache_pattern",
        invalidate,
    )
    return invalidate


class TestRunTipGeneration:
    @pytest.mark.asyncio
    async def test_happy_path_with_explanations(self, monkeypatch):
        session = _make_session()
        gen_stats = {
            "games_processed": 9,
            "tips_created": 27,
            "tips_skipped": 0,
            "tips_updated": 0,
            "model_predictions_created": 36,
            "model_predictions_updated": 0,
            "errors": [],
        }
        gen = _patch_generation(monkeypatch, gen_stats)
        expl = _patch_explanation(monkeypatch, return_value=27)
        _patch_invalidate(monkeypatch)

        result = await run_tip_generation(session)

        gen.generate_for_next_upcoming_round.assert_awaited_once()
        expl.generate_for_round.assert_awaited_once()
        expl.close.assert_awaited_once()
        assert result["status"] == "success"
        assert result["tips_created"] == 27
        assert result["explanations_generated"] == 27

    @pytest.mark.asyncio
    async def test_no_upcoming_round_is_success_with_zero_counts(self, monkeypatch):
        session = _make_session()
        gen_stats = {
            "games_processed": 0,
            "tips_created": 0,
            "tips_skipped": 0,
            "tips_updated": 0,
            "model_predictions_created": 0,
            "model_predictions_updated": 0,
            "errors": [],
            "message": "No upcoming rounds found that need tips",
        }
        _patch_generation(monkeypatch, gen_stats)
        _patch_explanation(monkeypatch, return_value=0)
        _patch_invalidate(monkeypatch)

        result = await run_tip_generation(session)

        assert result["status"] == "success"
        assert result["tips_created"] == 0
        assert result["explanations_generated"] == 0
        # 'no upcoming' should appear in message
        assert "no upcoming" in result["message"].lower()

    @pytest.mark.asyncio
    async def test_explanation_failure_does_not_fail_job(self, monkeypatch):
        session = _make_session()
        gen_stats = {
            "games_processed": 9,
            "tips_created": 27,
            "tips_skipped": 0,
            "tips_updated": 0,
            "model_predictions_created": 36,
            "model_predictions_updated": 0,
            "errors": [],
        }
        _patch_generation(monkeypatch, gen_stats)
        expl = MagicMock()
        expl.generate_for_round = AsyncMock(side_effect=RuntimeError("OpenRouter 500"))
        expl.close = AsyncMock()
        monkeypatch.setattr(
            "packages.shared.services.tip_generation.ExplanationService",
            lambda: expl,
        )
        _patch_invalidate(monkeypatch)

        result = await run_tip_generation(session)

        # Job is still successful
        assert result["status"] == "success"
        assert result["tips_created"] == 27
        # Explanation was attempted
        expl.generate_for_round.assert_awaited_once()
        # 'explanation failure' should be noted
        assert "explanation" in result["message"].lower()
        assert result["explanations_generated"] == 0
