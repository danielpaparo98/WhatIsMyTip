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

    def test_afl_off_season_months(self):
        """P3-4: the AFL calendar (Oct–Feb off-season) lives on the
        context, not hardcoded in the sync service."""
        for month in (10, 11, 12, 1, 2):
            assert AFL.is_off_season_month(month)
        for month in (3, 4, 5, 6, 7, 8, 9):
            assert not AFL.is_off_season_month(month)

    def test_year_round_sport_has_no_off_season(self):
        year_round = dataclasses.replace(AFL, sport_id="golf", off_season_months=frozenset())
        assert year_round.off_season_months == frozenset()
        assert not any(year_round.is_off_season_month(m) for m in range(1, 13))

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


class TestEloLearnedHomeAdvantageScoping:
    """P2-5: the learned home-advantage store must be keyed per sport —
    one sport's measured HA must never leak into another's predictions."""

    def test_store_is_keyed_by_sport(self):
        from packages.shared.models_ml.elo import EloModel

        assert isinstance(EloModel._LEARNED_HOME_ADVANTAGE, dict), (
            "_LEARNED_HOME_ADVANTAGE must be a per-sport dict, not a shared float"
        )

    def test_values_do_not_leak_across_sports(self):
        from packages.shared.models_ml.elo import EloModel

        tennis = dataclasses.replace(
            AFL, sport_id="tennis", cache_namespace="tennis"
        )
        afl_model = EloModel(context=AFL)
        tennis_model = EloModel(context=tennis)

        # Simulate the AFL rating walk learning a measured HA.
        type(afl_model)._LEARNED_HOME_ADVANTAGE["afl"] = 63.0
        try:
            assert type(tennis_model)._LEARNED_HOME_ADVANTAGE.get("tennis") is None
            assert type(afl_model)._LEARNED_HOME_ADVANTAGE.get("afl") == 63.0
        finally:
            type(afl_model)._LEARNED_HOME_ADVANTAGE.pop("afl", None)

    def test_instance_read_scopes_by_context(self):
        """predict() reads the learned HA for ITS context, not a global."""
        import inspect

        from packages.shared.models_ml.elo import EloModel

        src = inspect.getsource(EloModel.predict)
        assert "self.context.sport_id" in src, (
            "EloModel.predict must scope its learned-HA lookup by sport"
        )

    def test_update_cache_accepts_context(self):
        from inspect import signature

        from packages.shared.models_ml.elo import EloModel

        assert "context" in signature(EloModel.update_cache).parameters
