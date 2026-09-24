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

        async def fake_generate(game, regenerate=False, skip_nlp=False):
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


class TestSingleModelSweep:
    """All tips and model predictions for a game must come from ONE
    orchestrator sweep (P0-2).

    Regression: ``_generate_for_game`` called ``orchestrator.predict``
    once per heuristic (each running ALL models — 3×8 = 24 model runs),
    then looped ``orchestrator.models`` calling ``model.predict`` again
    for persistence (8 more runs).  32 executions where 8 suffice, with
    Elo-style stateful models possibly answering differently between
    the sweep that decided the tip and the run that got persisted.
    """

    @staticmethod
    def _make_game():
        return SimpleNamespace(
            id=55,
            slug="abc-55555",
            season=2026,
            round_id=9,
            home_team="Home",
            away_team="Away",
            date=__import__("datetime").datetime.fromisoformat(
                "2026-05-10T10:00:00+00:00"
            ),
            completed=False,
        )

    @pytest.mark.asyncio
    async def test_one_predict_all_call_persists_everything(self, monkeypatch):
        session = _make_session()

        # Two fake models — their .predict must NEVER be called by the service.
        model_a, model_b = MagicMock(name="ModelA"), MagicMock(name="ModelB")
        model_a.get_name.return_value = "model_a"
        model_b.get_name.return_value = "model_b"
        model_a.predict = AsyncMock()
        model_b.predict = AsyncMock()

        orchestrator = MagicMock()
        orchestrator.get_available_heuristics.return_value = ["best_bet", "yolo"]
        orchestrator.models = [model_a, model_b]
        orchestrator.predict = AsyncMock()
        orchestrator.predict_all = AsyncMock(
            return_value={
                "best_bet": {
                    "model_predictions": {
                        "model_a": ("Home", 0.80, 15),
                        "model_b": ("Away", 0.60, 4),
                    },
                    "tip": ("Home", 0.80, 15),
                    "failed_models": [],
                },
                "yolo": {
                    "model_predictions": {
                        "model_a": ("Home", 0.80, 15),
                        "model_b": ("Away", 0.60, 4),
                    },
                    "tip": ("Away", 0.60, 4),
                    "failed_models": [],
                },
            }
        )
        monkeypatch.setattr(
            "packages.shared.services.tip_generation._get_orchestrator",
            lambda: orchestrator,
        )

        create_tip = AsyncMock()
        create_pred = AsyncMock()
        monkeypatch.setattr(TipCRUD, "get_by_game", AsyncMock(return_value=[]))
        monkeypatch.setattr(TipCRUD, "create", create_tip)
        monkeypatch.setattr(
            "packages.shared.crud.model_predictions.ModelPredictionCRUD.get_by_game",
            AsyncMock(return_value=[]),
        )
        monkeypatch.setattr(
            "packages.shared.crud.model_predictions.ModelPredictionCRUD.create",
            create_pred,
        )

        service = TipGenerationService(session)
        stats = await service._generate_for_game(self._make_game(), skip_nlp=True)

        # The whole game runs on a single sweep.
        orchestrator.predict_all.assert_awaited_once()
        orchestrator.predict.assert_not_awaited()
        model_a.predict.assert_not_called()
        model_b.predict.assert_not_called()

        # One tip per heuristic, persisted from the sweep's tip tuples.
        assert create_tip.await_count == 2
        heuristics_tipped = {
            call.kwargs["heuristic"] for call in create_tip.await_args_list
        }
        assert heuristics_tipped == {"best_bet", "yolo"}

        # One stored prediction per model, from the SAME sweep.
        assert create_pred.await_count == 2
        stored_models = {
            call.kwargs["model_name"] for call in create_pred.await_args_list
        }
        assert stored_models == {"model_a", "model_b"}

        assert stats["tips_created"] == 2
        assert stats["model_predictions_created"] == 2
