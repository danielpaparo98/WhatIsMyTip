"""SportContext + orchestrator threading tests (P2-2)."""

from __future__ import annotations

import dataclasses

import pytest

from packages.shared.sport_context import AFL, DEFAULT_CONTEXT, SportContext


class TestSportContext:
    def test_afl_is_default(self):
        assert DEFAULT_CONTEXT is AFL
        assert AFL.sport_id == "afl"
        assert AFL.participant_model == "team"
        assert AFL.has_draws is True
        assert AFL.has_home_advantage is True
        assert AFL.scoring_unit == "points"
        assert AFL.cache_namespace == "afl"

    def test_frozen(self):
        with pytest.raises(dataclasses.FrozenInstanceError):
            AFL.sport_id = "rugby"  # type: ignore[misc]

    def test_cache_key_namespacing(self):
        assert AFL.cache_key("elo_ratings") == "wimt:afl:elo_ratings"
        assert AFL.cache_key("ratings", "2026") == "wimt:afl:ratings:2026"

    def test_tennis_style_context_never_collides_with_afl(self):
        tennis = SportContext(
            sport_id="tennis",
            display_name="Tennis",
            participant_model="individual",
            has_draws=False,
            has_home_advantage=False,
            scoring_unit="sets",
            cron_timezone="UTC",
            cache_namespace="tennis",
        )
        assert tennis.cache_key("elo_ratings") != AFL.cache_key("elo_ratings")
        assert tennis.has_draws is False
        assert tennis.has_home_advantage is False


class TestOrchestratorContext:
    def test_orchestrator_defaults_to_afl(self):
        from packages.shared.orchestrator import ModelOrchestrator

        orch = ModelOrchestrator(session_factory=lambda: None)
        assert orch.context is AFL

    def test_orchestrator_accepts_a_context(self):
        from packages.shared.orchestrator import ModelOrchestrator
        from packages.shared.sport_context import SportContext

        custom = SportContext(
            sport_id="rugby",
            display_name="Rugby",
            participant_model="team",
            has_draws=True,
            has_home_advantage=True,
            scoring_unit="points",
            cron_timezone="UTC",
            cache_namespace="rugby",
        )
        orch = ModelOrchestrator(session_factory=lambda: None, context=custom)
        assert orch.context is custom

    def test_elo_model_namespaces_redis_key(self):
        from packages.shared.models_ml.elo import EloModel

        assert EloModel(context=AFL).redis_cache_key == "wimt:afl:elo_ratings"

        custom = dataclasses.replace(AFL, cache_namespace="test-sport")
        assert EloModel(context=custom).redis_cache_key == "wimt:test-sport:elo_ratings"
