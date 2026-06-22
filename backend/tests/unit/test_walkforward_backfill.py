"""Unit tests for the walk-forward backfill script.

The script backfills historical ``model_predictions`` (8 per game) and
heuristic ``tips`` (3 per game) chronologically, using a single
``ModelOrchestrator.predict_all`` pass per game and idempotent upserts so the
run is resumable. These tests pin that contract without touching a database:

* games are processed in date order;
* a fully-populated game is skipped (resumability);
* exactly one ``predict_all`` call and one upsert per model/heuristic;
* ``--dry-run`` writes nothing;
* idempotency — a second run over a fully-populated fixture writes no new rows.
"""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _game(game_id, day, season=2024, round_id=1):
    """Build a minimal Game object for backfill tests."""
    from packages.shared.models import Game

    return Game(
        id=game_id,
        slug=f"g{game_id}",
        home_team="Brisbane",
        away_team="Collingwood",
        venue="Gabba",
        date=datetime(2024, 3, day, 18, 0, tzinfo=timezone.utc),
        round_id=round_id,
        season=season,
        completed=True,
        home_score=100,
        away_score=80,
    )


def _predict_all_result(n_models=8):
    """Fake ``ModelOrchestrator.predict_all`` payload (8 models, 3 heuristics)."""
    preds = {
        f"model_{i}": ("Brisbane", 0.5 + i * 0.01, 10 + i)
        for i in range(n_models)
    }
    return {
        "best_bet": {"model_predictions": preds, "tip": ("Brisbane", 0.60, 12)},
        "yolo": {"model_predictions": preds, "tip": ("Collingwood", 0.55, 8)},
        "weighted_tip": {"model_predictions": preds, "tip": ("Brisbane", 0.70, 15)},
    }


# ---------------------------------------------------------------------------
# fetch_game_ids — chronological selection + filters
# ---------------------------------------------------------------------------

class TestFetchGameIds:
    @pytest.mark.asyncio
    async def test_returns_ids_in_row_order(self):
        from scripts.run_walkforward_backfill import fetch_game_ids

        db = AsyncMock()
        result = MagicMock()
        result.all.return_value = [(30,), (10,), (20,)]
        db.execute.return_value = result

        ids = await fetch_game_ids(db, 2024, 2024)
        assert ids == [30, 10, 20]

    @pytest.mark.asyncio
    async def test_query_orders_by_date_ascending(self):
        from scripts.run_walkforward_backfill import fetch_game_ids

        db = AsyncMock()
        result = MagicMock()
        result.all.return_value = []
        db.execute.return_value = result

        await fetch_game_ids(db, 2024, 2024)
        compiled = str(db.execute.call_args.args[0].compile()).lower()
        assert "order by" in compiled
        assert "date" in compiled

    @pytest.mark.asyncio
    async def test_query_filters_completed_and_scored(self):
        from scripts.run_walkforward_backfill import fetch_game_ids

        db = AsyncMock()
        result = MagicMock()
        result.all.return_value = []
        db.execute.return_value = result

        await fetch_game_ids(db, 2010, 2024)
        compiled = str(db.execute.call_args.args[0].compile()).lower()
        # completed games only
        assert "completed" in compiled
        # home_score must be present (scored games only)
        assert "home_score" in compiled

    @pytest.mark.asyncio
    async def test_limit_applied_when_provided(self):
        from scripts.run_walkforward_backfill import fetch_game_ids

        db = AsyncMock()
        result = MagicMock()
        result.all.return_value = [(1,)]
        db.execute.return_value = result

        await fetch_game_ids(db, 2024, 2024, limit=1)
        compiled = str(db.execute.call_args.args[0].compile()).lower()
        assert "limit" in compiled


# ---------------------------------------------------------------------------
# backfill_game — single model pass, one upsert per model/heuristic
# ---------------------------------------------------------------------------

class TestBackfillGame:
    @staticmethod
    def _orchestrator():
        orchestrator = MagicMock()
        orchestrator.predict_all = AsyncMock(return_value=_predict_all_result())
        return orchestrator

    @pytest.mark.asyncio
    async def test_single_predict_all_pass(self):
        from scripts.run_walkforward_backfill import backfill_game

        orchestrator = self._orchestrator()
        db = AsyncMock()
        game = _game(1, 1)

        with (
            patch("scripts.run_walkforward_backfill.ModelPredictionCRUD") as mp_crud,
            patch("scripts.run_walkforward_backfill.TipCRUD") as tip_crud,
        ):
            mp_crud.create_or_update = AsyncMock()
            tip_crud.upsert = AsyncMock()
            await backfill_game(orchestrator, db, game)

        # Exactly ONE model pass — the whole point of the script.
        orchestrator.predict_all.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_one_upsert_per_model_and_heuristic(self):
        from scripts.run_walkforward_backfill import backfill_game

        orchestrator = self._orchestrator()
        db = AsyncMock()
        game = _game(1, 1)

        with (
            patch("scripts.run_walkforward_backfill.ModelPredictionCRUD") as mp_crud,
            patch("scripts.run_walkforward_backfill.TipCRUD") as tip_crud,
        ):
            mp_crud.create_or_update = AsyncMock()
            tip_crud.upsert = AsyncMock()
            result = await backfill_game(orchestrator, db, game)

        # 8 model predictions upserted exactly once each.
        assert mp_crud.create_or_update.await_count == 8
        # 3 heuristic tips upserted exactly once each.
        assert tip_crud.upsert.await_count == 3

        assert result["predictions"] == 8
        assert result["tips"] == 3

    @pytest.mark.asyncio
    async def test_tip_explanation_is_empty(self):
        """No NLP/explanation work is done during backfill (fast + free)."""
        from scripts.run_walkforward_backfill import backfill_game

        orchestrator = self._orchestrator()
        db = AsyncMock()
        game = _game(1, 1)

        with (
            patch("scripts.run_walkforward_backfill.ModelPredictionCRUD") as mp_crud,
            patch("scripts.run_walkforward_backfill.TipCRUD") as tip_crud,
        ):
            mp_crud.create_or_update = AsyncMock()
            tip_crud.upsert = AsyncMock()
            await backfill_game(orchestrator, db, game)

        for call in tip_crud.upsert.await_args_list:
            assert call.kwargs.get("explanation") == ""


# ---------------------------------------------------------------------------
# process_games — ordering, skip, dry-run, idempotency
# ---------------------------------------------------------------------------

class TestProcessGames:
    @pytest.mark.asyncio
    async def test_games_processed_in_date_order(self, monkeypatch):
        from scripts.run_walkforward_backfill import process_games

        # Handed in already-sorted by date (the SQL ORDER BY guarantees this).
        games = [_game(i, day=i) for i in (1, 2, 3, 4, 5)]
        seen = []

        async def fake_backfill(orch, db, game):
            seen.append(game.id)
            return {"predictions": 8, "tips": 3, "game_id": game.id}

        monkeypatch.setattr(
            "scripts.run_walkforward_backfill.backfill_game", fake_backfill
        )
        monkeypatch.setattr(
            "scripts.run_walkforward_backfill.existing_counts",
            AsyncMock(return_value=(0, 0)),
        )

        await process_games(
            MagicMock(), AsyncMock(), games, sleep=0, dry_run=False, log_every=0
        )
        assert seen == [1, 2, 3, 4, 5]

    @pytest.mark.asyncio
    async def test_fully_populated_game_is_skipped(self, monkeypatch):
        from scripts.run_walkforward_backfill import process_games

        games = [_game(1, 1)]
        backfill = AsyncMock(
            return_value={"predictions": 8, "tips": 3, "game_id": 1}
        )
        monkeypatch.setattr(
            "scripts.run_walkforward_backfill.backfill_game", backfill
        )
        monkeypatch.setattr(
            "scripts.run_walkforward_backfill.existing_counts",
            AsyncMock(return_value=(8, 3)),  # already complete
        )

        stats = await process_games(
            MagicMock(), AsyncMock(), games, sleep=0, dry_run=False, log_every=0
        )
        assert stats["skipped"] == 1
        assert stats["processed"] == 0
        backfill.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_dry_run_writes_nothing(self, monkeypatch):
        from scripts.run_walkforward_backfill import process_games

        games = [_game(1, 1), _game(2, 2)]
        backfill = AsyncMock()
        monkeypatch.setattr(
            "scripts.run_walkforward_backfill.backfill_game", backfill
        )
        monkeypatch.setattr(
            "scripts.run_walkforward_backfill.existing_counts",
            AsyncMock(return_value=(0, 0)),
        )

        stats = await process_games(
            MagicMock(), AsyncMock(), games, sleep=0, dry_run=True, log_every=0
        )
        backfill.assert_not_awaited()
        # Dry-run reports what WOULD be processed, but writes nothing.
        assert stats["processed"] == 2
        assert stats["predictions"] == 0
        assert stats["tips"] == 0

    @pytest.mark.asyncio
    async def test_idempotent_second_run_writes_no_new_rows(self, monkeypatch):
        """A second pass over a fully-populated fixture writes no new rows."""
        from scripts.run_walkforward_backfill import process_games

        games = [_game(1, 1), _game(2, 2), _game(3, 3)]
        backfill = AsyncMock(
            return_value={"predictions": 8, "tips": 3, "game_id": 1}
        )
        monkeypatch.setattr(
            "scripts.run_walkforward_backfill.backfill_game", backfill
        )
        # Second run sees everything already populated.
        monkeypatch.setattr(
            "scripts.run_walkforward_backfill.existing_counts",
            AsyncMock(return_value=(8, 3)),
        )

        stats = await process_games(
            MagicMock(), AsyncMock(), games, sleep=0, dry_run=False, log_every=0
        )
        assert stats["skipped"] == 3
        assert stats["processed"] == 0
        assert stats["predictions"] == 0
        assert stats["tips"] == 0
        backfill.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_partial_game_is_backfilled(self, monkeypatch):
        """A game missing some predictions/tips is not skipped."""
        from scripts.run_walkforward_backfill import process_games

        games = [_game(1, 1)]
        backfill = AsyncMock(
            return_value={"predictions": 8, "tips": 3, "game_id": 1}
        )
        monkeypatch.setattr(
            "scripts.run_walkforward_backfill.backfill_game", backfill
        )
        monkeypatch.setattr(
            "scripts.run_walkforward_backfill.existing_counts",
            AsyncMock(return_value=(5, 2)),  # partial
        )

        stats = await process_games(
            MagicMock(), AsyncMock(), games, sleep=0, dry_run=False, log_every=0
        )
        assert stats["processed"] == 1
        assert stats["skipped"] == 0
        backfill.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_failure_on_one_game_does_not_abort_run(self, monkeypatch):
        from scripts.run_walkforward_backfill import process_games

        games = [_game(1, 1), _game(2, 2)]

        async def flaky_backfill(orch, db, game):
            if game.id == 1:
                raise RuntimeError("boom")
            return {"predictions": 8, "tips": 3, "game_id": game.id}

        monkeypatch.setattr(
            "scripts.run_walkforward_backfill.backfill_game", flaky_backfill
        )
        monkeypatch.setattr(
            "scripts.run_walkforward_backfill.existing_counts",
            AsyncMock(return_value=(0, 0)),
        )

        stats = await process_games(
            MagicMock(), AsyncMock(), games, sleep=0, dry_run=False, log_every=0
        )
        assert stats["failed"] == 1
        assert stats["processed"] == 1


# ---------------------------------------------------------------------------
# is_complete — pure helper
# ---------------------------------------------------------------------------

class TestIsComplete:
    def test_complete_when_full(self):
        from scripts.run_walkforward_backfill import is_complete

        assert is_complete(8, 3) is True

    def test_incomplete_when_missing_predictions(self):
        from scripts.run_walkforward_backfill import is_complete

        assert is_complete(7, 3) is False

    def test_incomplete_when_missing_tips(self):
        from scripts.run_walkforward_backfill import is_complete

        assert is_complete(8, 2) is False

    def test_over_populated_still_complete(self):
        from scripts.run_walkforward_backfill import is_complete

        assert is_complete(9, 3) is True
