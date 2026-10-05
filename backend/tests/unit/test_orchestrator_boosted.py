"""Orchestrator wiring tests for the ``boosted_tip`` heuristic (BT-1).

Mirrors ``test_weighted_tip_orchestrator_integration.py``.  The
:class:`~packages.shared.orchestrator.ModelOrchestrator` owns the db
session, so it is responsible for loading the active ``boosted_tip``
model version's artifact and pushing the LOADED model into the
:class:`BoostedTipHeuristic` (whose ``apply`` signature has no db).

These tests pin that wiring contract (decision 3 of the BT-1 bundle:
``best_bet`` is replaced, not deleted):

* the heuristic registry contains ``boosted_tip`` and no longer
  contains ``best_bet`` (historical rows stay queryable via the API
  allowlists — that is NOT the orchestrator's concern);
* ``_ensure_boosted_tip_model`` reads the active version via
  :func:`get_active_model_artifact`, deserializes ONCE per TTL window
  (BT-2: never per ``apply``) and calls ``set_loaded_model`` /
  ``clear_model`` on the heuristic;
* DB and deserialization errors are swallowed + logged — tip
  generation falls back to the majority vote and never crashes;
* ``predict`` and ``predict_all`` invoke the loader before applying any
  heuristic; a monkeypatched loaded model receives the exact
  ``build_feature_vector`` output and its prediction maps through
  :func:`home_margin_to_tip`.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from packages.shared.heuristics.weighted_tip import (
    build_feature_vector,
    home_margin_to_tip,
    weighted_tip_fallback,
)
from packages.shared.orchestrator import (
    BOOSTED_TIP_MODEL_TTL_SECONDS,
    ModelOrchestrator,
)


def _make_game(home_team="Richmond", away_team="Carlton"):
    game = MagicMock()
    game.id = 1
    game.home_team = home_team
    game.away_team = away_team
    return game


class _FakeLoadedModel:
    """Stand-in for a deserialized ``XGBRegressor`` that records inputs."""

    def __init__(self, prediction: float = 12.5):
        self.prediction = prediction
        self.predict_calls = []

    def predict(self, features):
        self.predict_calls.append(features)
        return [self.prediction]


# ---------------------------------------------------------------------------
# Heuristic registry composition (decision 3: best_bet → boosted_tip)
# ---------------------------------------------------------------------------

class TestHeuristicRegistry:
    def setup_method(self):
        self.orch = ModelOrchestrator(session_factory=lambda: None)

    def test_registry_contains_boosted_tip(self):
        assert "boosted_tip" in self.orch.get_available_heuristics()

    def test_registry_no_longer_contains_best_bet(self):
        """New best_bet tips are never generated again; historical rows
        remain queryable through the API allowlists (kept elsewhere)."""
        assert "best_bet" not in self.orch.get_available_heuristics()

    def test_registry_order_boosted_tip_yolo_weighted_tip(self):
        assert self.orch.get_available_heuristics() == [
            "boosted_tip",
            "yolo",
            "weighted_tip",
        ]

    def test_boosted_heuristic_shares_registry_models(self):
        """The boosted heuristic is built over the SAME model list as the
        other heuristics — the registry is the feature contract."""
        assert self.orch.heuristics["boosted_tip"].models is self.orch.models

    @pytest.mark.asyncio
    async def test_best_bet_request_raises_value_error(self):
        with pytest.raises(ValueError, match="Unknown heuristic"):
            await self.orch.predict(_make_game(), "best_bet", db=MagicMock())


# ---------------------------------------------------------------------------
# _ensure_boosted_tip_model (TTL-cached artifact loader)
# ---------------------------------------------------------------------------

class TestEnsureBoostedTipModel:
    def setup_method(self):
        self.orch = ModelOrchestrator()

    @pytest.mark.asyncio
    async def test_sets_loaded_model_when_active_artifact_exists(self):
        version = MagicMock()
        artifact = b"xgb-json-bytes"
        mock_get = AsyncMock(return_value=(artifact, "json", version))
        fake_model = _FakeLoadedModel()
        db = MagicMock()
        with (
            patch(
                "packages.shared.orchestrator.get_active_model_artifact",
                new=mock_get,
            ),
            patch(
                "packages.shared.orchestrator.load_boosted_model",
                return_value=fake_model,
            ) as mock_load,
        ):
            await self.orch._ensure_boosted_tip_model(db)

        mock_get.assert_awaited_once_with(db, "boosted_tip")
        mock_load.assert_called_once_with(artifact, "json")
        boosted = self.orch.heuristics["boosted_tip"]
        assert boosted._loaded_model is fake_model
        assert boosted._model_bytes is None  # bytes path retired once loaded

    @pytest.mark.asyncio
    async def test_clears_model_when_no_active_version(self):
        # Start from a known "trained" state to prove the clear path runs.
        self.orch.heuristics["boosted_tip"].set_model(b"old-artifact")
        with patch(
            "packages.shared.orchestrator.get_active_model_artifact",
            new=AsyncMock(return_value=None),
        ):
            await self.orch._ensure_boosted_tip_model(MagicMock())

        boosted = self.orch.heuristics["boosted_tip"]
        assert boosted._model_bytes is None
        assert boosted._loaded_model is None

    @pytest.mark.asyncio
    async def test_ttl_caches_within_window(self):
        mock_get = AsyncMock(return_value=(b"artifact", "json", MagicMock()))
        with (
            patch(
                "packages.shared.orchestrator.get_active_model_artifact",
                new=mock_get,
            ),
            patch(
                "packages.shared.orchestrator.load_boosted_model",
                return_value=_FakeLoadedModel(),
            ),
        ):
            await self.orch._ensure_boosted_tip_model(MagicMock())
            await self.orch._ensure_boosted_tip_model(MagicMock())

        # Second call is served from the in-memory cache.
        assert mock_get.await_count == 1

    @pytest.mark.asyncio
    async def test_ttl_reloads_after_expiry(self):
        import time as _time

        mock_get = AsyncMock(return_value=(b"artifact", "json", MagicMock()))
        with (
            patch(
                "packages.shared.orchestrator.get_active_model_artifact",
                new=mock_get,
            ),
            patch(
                "packages.shared.orchestrator.load_boosted_model",
                return_value=_FakeLoadedModel(),
            ),
        ):
            await self.orch._ensure_boosted_tip_model(MagicMock())
            # Rewind the load timestamp past the TTL window.
            self.orch._boosted_model_loaded_at = (
                _time.monotonic() - (BOOSTED_TIP_MODEL_TTL_SECONDS + 1)
            )
            await self.orch._ensure_boosted_tip_model(MagicMock())

        assert mock_get.await_count == 2

    @pytest.mark.asyncio
    async def test_db_error_swallowed_and_fallback_serves(self):
        mock_get = AsyncMock(side_effect=RuntimeError("db down"))
        with patch(
            "packages.shared.orchestrator.get_active_model_artifact",
            new=mock_get,
        ):
            # Must not raise — tip generation must survive a model-load failure.
            await self.orch._ensure_boosted_tip_model(MagicMock())

        boosted = self.orch.heuristics["boosted_tip"]
        # Never loaded → heuristic stays on the majority-vote fallback.
        assert boosted._model_bytes is None
        assert boosted._loaded_model is None

    @pytest.mark.asyncio
    async def test_deserialize_error_swallowed_and_fallback_serves(self):
        """A corrupt artifact must never crash tip generation either."""
        mock_get = AsyncMock(return_value=(b"corrupt-bytes", "json", MagicMock()))
        with (
            patch(
                "packages.shared.orchestrator.get_active_model_artifact",
                new=mock_get,
            ),
            patch(
                "packages.shared.orchestrator.load_boosted_model",
                side_effect=ValueError("bad artifact"),
            ),
        ):
            await self.orch._ensure_boosted_tip_model(MagicMock())

        boosted = self.orch.heuristics["boosted_tip"]
        assert boosted._loaded_model is None
        assert boosted._model_bytes is None

    @pytest.mark.asyncio
    async def test_deserialization_happens_once_per_ttl_window_not_per_apply(
        self,
    ):
        """BT-2 performance contract: XGBoost deserialization is ms-scale,
        so it happens in the loader (once per TTL) — ``apply`` predicts
        with the preloaded object and never re-deserializes per tip."""
        mock_get = AsyncMock(return_value=(b"artifact", "json", MagicMock()))
        fake_model = _FakeLoadedModel()
        with (
            patch(
                "packages.shared.orchestrator.get_active_model_artifact",
                new=mock_get,
            ),
            patch(
                "packages.shared.orchestrator.load_boosted_model",
                return_value=fake_model,
            ) as mock_load,
        ):
            await self.orch._ensure_boosted_tip_model(MagicMock())

            boosted = self.orch.heuristics["boosted_tip"]
            game = _make_game()
            preds = {"elo": ("Richmond", 0.7, 20)}
            for _ in range(3):
                await boosted.apply(game, preds)

        # Deserialized exactly once, regardless of apply() count.
        assert mock_load.call_count == 1
        assert len(fake_model.predict_calls) == 3

    @pytest.mark.asyncio
    async def test_loaded_model_receives_built_feature_vector(self):
        """The preloaded model is handed the exact ``build_feature_vector``
        output for the heuristic's registry-derived model names."""
        with (
            patch(
                "packages.shared.orchestrator.get_active_model_artifact",
                new=AsyncMock(return_value=(b"artifact", "json", MagicMock())),
            ),
            patch(
                "packages.shared.orchestrator.load_boosted_model",
                return_value=_FakeLoadedModel(),
            ),
        ):
            await self.orch._ensure_boosted_tip_model(MagicMock())

        boosted = self.orch.heuristics["boosted_tip"]
        preds = {"elo": ("Richmond", 0.7, 20)}
        await boosted.apply(_make_game(), preds)

        fake_model = boosted._loaded_model
        expected = build_feature_vector(
            preds,
            "Richmond",
            "Carlton",
            model_names=boosted.model_names,
        )
        assert fake_model.predict_calls[-1] == [expected]

    @pytest.mark.asyncio
    async def test_prediction_maps_through_home_margin_to_tip(self):
        """y_pred from the (fake) loaded model maps to a tip via the shared
        home_margin_to_tip rules — winner/margin/confidence all derived."""
        with (
            patch(
                "packages.shared.orchestrator.get_active_model_artifact",
                new=AsyncMock(return_value=(b"artifact", "json", MagicMock())),
            ),
            patch(
                "packages.shared.orchestrator.load_boosted_model",
                return_value=_FakeLoadedModel(prediction=12.5),
            ),
        ):
            await self.orch._ensure_boosted_tip_model(MagicMock())

        boosted = self.orch.heuristics["boosted_tip"]
        tip = await boosted.apply(
            _make_game(), {"elo": ("Richmond", 0.7, 20)}
        )
        assert tip == home_margin_to_tip(12.5, "Richmond", "Carlton")
        assert tip[0] == "Richmond"  # positive margin → home


# ---------------------------------------------------------------------------
# predict / predict_all invoke the loader
# ---------------------------------------------------------------------------

class TestPredictInvokesBoostedLoader:
    """Both prediction entry points refresh the boosted model before
    applying any heuristic."""

    def setup_method(self):
        self.orch = ModelOrchestrator()
        # Empty model list → predict runs no DB-bound model queries, so we can
        # isolate the artifact-injection behaviour cheaply.
        self.orch.models = []

    @pytest.mark.asyncio
    async def test_predict_calls_ensure(self):
        with patch.object(
            self.orch,
            "_ensure_boosted_tip_model",
            new=AsyncMock(),
        ) as mock_ensure:
            await self.orch.predict(_make_game(), "boosted_tip", db=MagicMock())
            mock_ensure.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_predict_all_calls_ensure(self):
        with patch.object(
            self.orch,
            "_ensure_boosted_tip_model",
            new=AsyncMock(),
        ) as mock_ensure:
            await self.orch.predict_all(_make_game(), db=MagicMock())
            mock_ensure.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_predict_runs_both_ensure_loaders(self):
        """boosted_tip loads alongside the weighted-tip refresh — each
        entry point refreshes both caches (mirrors the weighted wiring)."""
        with (
            patch.object(
                self.orch,
                "_ensure_weighted_tip_coefficients",
                new=AsyncMock(),
            ) as mock_wt,
            patch.object(
                self.orch,
                "_ensure_boosted_tip_model",
                new=AsyncMock(),
            ) as mock_bt,
        ):
            await self.orch.predict_all(_make_game(), db=MagicMock())

        mock_wt.assert_awaited_once()
        mock_bt.assert_awaited_once()


# ---------------------------------------------------------------------------
# End-to-end through predict / predict_all
# ---------------------------------------------------------------------------

class TestBoostedTipEndToEnd:
    def setup_method(self):
        self.orch = ModelOrchestrator()
        self.orch.models = []

    @pytest.mark.asyncio
    async def test_predict_serves_learned_boosted_tip(self):
        """Loaded artifact → predict('boosted_tip') returns the model's
        tip mapped through home_margin_to_tip."""
        with (
            patch(
                "packages.shared.orchestrator.get_active_model_artifact",
                new=AsyncMock(return_value=(b"artifact", "json", MagicMock())),
            ),
            patch(
                "packages.shared.orchestrator.load_boosted_model",
                return_value=_FakeLoadedModel(prediction=-7.5),
            ),
        ):
            winner, conf, margin = await self.orch.predict(
                _make_game(), "boosted_tip", db=MagicMock()
            )

        assert (winner, conf, margin) == home_margin_to_tip(-7.5, "Richmond", "Carlton")
        assert winner == "Carlton"  # negative margin → away

    @pytest.mark.asyncio
    async def test_predict_all_includes_boosted_tip_key(self):
        results = await self.orch.predict_all(_make_game(), db=MagicMock())
        assert "boosted_tip" in results
        assert "best_bet" not in results
        assert set(results.keys()) == {"boosted_tip", "yolo", "weighted_tip"}

    @pytest.mark.asyncio
    async def test_pre_retrain_fallback_serves_majority_vote_without_errors(
        self,
    ):
        """No active artifact (pre-retrain) → boosted_tip == the shared
        majority-vote fallback; tip generation never errors."""
        with patch(
            "packages.shared.orchestrator.get_active_model_artifact",
            new=AsyncMock(return_value=None),
        ):
            winner, conf, margin = await self.orch.predict(
                _make_game(), "boosted_tip", db=MagicMock()
            )

        assert (winner, conf, margin) == weighted_tip_fallback(
            {}, "Richmond", "Carlton"
        )
        assert (winner, conf, margin) == ("Carlton", 0.55, 6)
