"""Registry-driven model sets (P2-3).

Adding a sport's model set must be a *registration*, not an edit to a
hardcoded list.  The registry is the single source of truth for which
models exist per sport, in what order — and that order IS the
weighted-tip feature contract, so AFL registration order must produce
exactly the legacy ``MODEL_NAMES`` list.
"""

from __future__ import annotations

import pytest

from packages.shared.heuristics.weighted_tip import (
    FEATURE_NAMES,
    MODEL_NAMES,
    WeightedTipHeuristic,
    build_feature_vector,
)
from packages.shared.models_ml.elo import EloModel
from packages.shared.models_ml.prediction import Prediction
from packages.shared.models_ml.registry import ModelRegistry, build_default_registry
from packages.shared.orchestrator import ModelOrchestrator
from packages.shared.sport_context import AFL, SportContext

class TestDefaultRegistry:
    def test_default_registry_matches_legacy_model_names(self):
        """The AFL bootstrap set must produce the exact legacy order —
        it is the weighted-tip training contract."""
        assert build_default_registry().names() == MODEL_NAMES

    def test_creates_live_models_in_order(self):
        models = build_default_registry().create(AFL)
        assert [m.get_name() for m in models] == MODEL_NAMES
        assert all(hasattr(m, "predict") for m in models)

    def test_elo_factory_receives_context(self):
        models = build_default_registry().create(AFL)
        elo = next(m for m in models if m.get_name() == "elo")
        assert elo.context is AFL


class TestCustomRegistry:
    @staticmethod
    def _two_model_registry() -> ModelRegistry:
        reg = ModelRegistry()
        reg.register("elo", lambda ctx: EloModel(context=ctx))
        reg.register("form", lambda ctx: build_default_registry().create(ctx)[1])
        return reg

    def test_registration_order_is_names_order(self):
        reg = self._two_model_registry()
        assert reg.names() == ["elo", "form"]

    def test_duplicate_registration_rejected(self):
        reg = ModelRegistry()
        reg.register("elo", lambda ctx: EloModel(context=ctx))
        with pytest.raises(ValueError):
            reg.register("elo", lambda ctx: EloModel(context=ctx))

    def test_orchestrator_builds_from_registry(self):
        orch = ModelOrchestrator(
            session_factory=lambda: None, registry=self._two_model_registry()
        )
        assert [m.get_name() for m in orch.models] == ["elo", "form"]
        # heuristics are still constructed over the registry's models
        assert set(orch.get_available_heuristics()) == {
            "best_bet",
            "yolo",
            "weighted_tip",
        }


class TestWeightedTipDerivesFromModels:
    def test_heuristic_model_names_come_from_injected_models(self):
        orch = ModelOrchestrator(
            session_factory=lambda: None,
            registry=TestCustomRegistry._two_model_registry(),
        )
        heuristic = orch.heuristics["weighted_tip"]
        assert heuristic.model_names == ["elo", "form"]

    def test_default_heuristic_matches_legacy_names(self):
        heuristic = WeightedTipHeuristic(
            build_default_registry().create(AFL)
        )
        assert heuristic.model_names == MODEL_NAMES

    def test_build_feature_vector_honours_explicit_names(self):
        preds = {
            "elo": Prediction("Home", 0.8, 10),
            "form": Prediction("Away", 0.6, 4),
        }
        features = build_feature_vector(
            preds, "Home", "Away", model_names=["form"]
        )
        # only the requested model's two features
        assert len(features) == 2
        assert features[0] == -4.0  # away pick → negative signed margin
        assert features[1] == 0.6

    def test_afl_feature_contract_unchanged(self):
        """16 features, same names, same order — training data stays valid."""
        assert len(FEATURE_NAMES) == 16
        assert FEATURE_NAMES[0] == "elo_margin_home"
        assert FEATURE_NAMES[1] == "elo_conf"
        assert FEATURE_NAMES[-2] == "player_form_margin_home"
        assert FEATURE_NAMES[-1] == "player_form_conf"
