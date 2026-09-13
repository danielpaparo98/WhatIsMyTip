"""Unit tests for ``packages.shared.services.tip_generation`` service function.

The service function is the reusable core that both the FaaS handler
and the new ``app.cron.tip_generation.TipGenerationJob`` invoke.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from packages.shared.crud.games import GameCRUD
from packages.shared.crud.tips import TipCRUD
from packages.shared.services.tip_generation import (
    TipGenerationService,
    run_tip_generation,
)


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


class TestGenerateForRoundSessionRollback:
    """Regression guard for the ``InFailedSQLTransactionError`` cascade.

    A per-game DB error must not poison the shared ``self.db`` session for the
    rest of the round. Before this fix, ``_generate_for_game`` failures were
    logged-and-continued WITHOUT rolling back, so one aborted Postgres
    transaction caused every subsequent game to fail with
    ``current transaction is aborted, commands ignored``.
    """

    @staticmethod
    def _stats_ok() -> dict:
        return {
            "tips_created": 1,
            "tips_skipped": 0,
            "tips_updated": 0,
            "model_predictions_created": 0,
            "model_predictions_updated": 0,
        }

    @pytest.mark.asyncio
    async def test_failed_game_rolls_back_and_loop_continues(self, monkeypatch):
        session = _make_session()
        session.rollback = AsyncMock()

        # Avoid constructing the real ModelOrchestrator singleton.
        monkeypatch.setattr(
            "packages.shared.services.tip_generation._get_orchestrator",
            lambda: MagicMock(),
        )

        game_a = MagicMock(id=1, home_team="A", away_team="B")
        game_b = MagicMock(id=2, home_team="C", away_team="D")
        monkeypatch.setattr(
            GameCRUD, "get_by_round", AsyncMock(return_value=[game_a, game_b])
        )

        service = TipGenerationService(session)

        attempted: list[int] = []

        async def fake_generate(game, regenerate=False, skip_nlp=False):
            attempted.append(game.id)
            if game.id == 1:
                raise RuntimeError("simulated game failure")
            return self._stats_ok()

        monkeypatch.setattr(service, "_generate_for_game", fake_generate)

        stats = await service.generate_for_round(2026, 18)

        # The loop continued past the first game's failure (cascade prevented).
        assert attempted == [1, 2]
        # The aborted transaction was cleared before processing the next game.
        assert session.rollback.await_count == 1
        # The second game still processed successfully.
        assert stats["games_processed"] == 1
        assert stats["tips_created"] == 1
        assert len(stats["errors"]) == 1

    @pytest.mark.asyncio
    async def test_generate_batch_rolls_back_and_loop_continues(self, monkeypatch):
        session = _make_session()
        session.rollback = AsyncMock()

        monkeypatch.setattr(
            "packages.shared.services.tip_generation._get_orchestrator",
            lambda: MagicMock(),
        )

        game_a = MagicMock(id=1, home_team="A", away_team="B")
        game_b = MagicMock(id=2, home_team="C", away_team="D")

        service = TipGenerationService(session)

        attempted: list[int] = []

        async def fake_generate(game, regenerate=False):
            attempted.append(game.id)
            if game.id == 1:
                raise RuntimeError("simulated game failure")
            return self._stats_ok()

        monkeypatch.setattr(service, "_generate_for_game", fake_generate)

        stats = await service.generate_batch([game_a, game_b])

        assert attempted == [1, 2]
        assert session.rollback.await_count == 1
        assert stats["games_processed"] == 1
        assert len(stats["errors"]) == 1


class TestTeamlessGameSkip:
    """Games with unknown participants (Squiggle TBC finals placeholders)
    must never receive tips.

    Regression: the daily cron generated tips with ``selected_team = ''``
    for placeholder fixtures, and because ``get_next_upcoming_round``
    treats any existing tip as "round already done", the garbage tip
    suppressed generation for the REAL games sharing that round —
    leaving whatismytip.com's current round showing empty cards.
    """

    @staticmethod
    def _placeholder_game(game_id: int = 999):
        return SimpleNamespace(
            id=game_id,
            slug="tbc12345",
            season=2026,
            round_id=27,
            home_team=None,
            away_team=None,
            date=None,
            completed=False,
        )

    @pytest.mark.asyncio
    async def test_generate_for_game_skips_placeholder(self, monkeypatch):
        session = _make_session()
        monkeypatch.setattr(
            "packages.shared.services.tip_generation._get_orchestrator",
            lambda: MagicMock(),
        )
        orchestrator = MagicMock()
        orchestrator.predict = AsyncMock()
        monkeypatch.setattr(
            "packages.shared.services.tip_generation._shared_orchestrator",
            orchestrator,
        )
        monkeypatch.setattr(
            TipCRUD, "get_by_game", AsyncMock(return_value=[])
        )
        create_tip = AsyncMock()
        monkeypatch.setattr(TipCRUD, "create", create_tip)

        game = self._placeholder_game()
        service = TipGenerationService(session)

        stats = await service._generate_for_game(game)

        create_tip.assert_not_awaited()
        orchestrator.predict.assert_not_awaited()
        assert stats["tips_created"] == 0
        assert stats["games_skipped_no_teams"] == 1

    @pytest.mark.asyncio
    async def test_generate_for_round_skips_placeholder_but_tips_real_game(
        self, monkeypatch
    ):
        session = _make_session()
        session.rollback = AsyncMock()
        monkeypatch.setattr(
            "packages.shared.services.tip_generation._get_orchestrator",
            lambda: MagicMock(),
        )

        placeholder = SimpleNamespace(
            id=901, home_team=None, away_team=None, season=2026, round_id=27
        )
        partial = SimpleNamespace(
            id=902, home_team="Hawthorn", away_team="", season=2026, round_id=27
        )
        real = SimpleNamespace(
            id=903, home_team="Fremantle", away_team="Geelong", season=2026,
            round_id=27,
        )
        monkeypatch.setattr(
            GameCRUD, "get_by_round", AsyncMock(
                return_value=[placeholder, partial, real]
            )
        )

        service = TipGenerationService(session)

        async def fake_generate(game, regenerate=False, skip_nlp=False):
            return {
                "tips_created": 3,
                "tips_skipped": 0,
                "tips_updated": 0,
                "model_predictions_created": 0,
                "model_predictions_updated": 0,
            }

        monkeypatch.setattr(service, "_generate_for_game", fake_generate)

        stats = await service.generate_for_round(2026, 27)

        assert stats["games_processed"] == 1
        assert stats["games_skipped_no_teams"] == 2
        assert stats["tips_created"] == 3
        assert stats["errors"] == []
