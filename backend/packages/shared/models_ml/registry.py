"""Model registry — the per-sport source of truth for model sets (P2-3).

Adding a sport's model set is a *registration*, not an edit to a
hardcoded list.  Registration order is significant: it defines both the
orchestrator's model list and the weighted-tip feature ordering (the
training/prediction contract), so it must never change for a sport
whose models have been trained.
"""

from __future__ import annotations

from typing import Callable, Dict, List

from ..sport_context import SportContext
from .base import BaseModel


class ModelRegistry:
    """Ordered name → factory mapping for a sport's model set.

    Factories receive the :class:`SportContext` so context-aware models
    (e.g. Elo) can scope themselves at construction time.
    """

    def __init__(self) -> None:
        self._factories: Dict[str, Callable[[SportContext], BaseModel]] = {}

    def register(
        self, name: str, factory: Callable[[SportContext], BaseModel]
    ) -> "ModelRegistry":
        """Register a model factory under ``name`` (its ``get_name()``).

        Re-registering an existing name is rejected — silent override
        would reorder features under trained models' feet.
        """
        if name in self._factories:
            raise ValueError(
                f"Model '{name}' is already registered in this registry"
            )
        self._factories[name] = factory
        return self

    def names(self) -> List[str]:
        """Registered model names in registration (feature-contract) order."""
        return list(self._factories.keys())

    def create(self, context: SportContext) -> List[BaseModel]:
        """Instantiate the registered models for ``context``, in order."""
        return [factory(context) for factory in self._factories.values()]


def build_default_registry() -> ModelRegistry:
    """The AFL bootstrap registry — the eight existing models, in the
    exact order the legacy hardcoded ``MODEL_NAMES`` list used (the
    weighted-tip feature contract)."""

    from .elo import EloModel
    from .form import FormModel
    from .home_advantage import HomeAdvantageModel
    from .injury_impact import InjuryImpactModel
    from .matchup import MatchupModel
    from .player_form import PlayerFormModel
    from .value import ValueModel
    from .weather_impact import WeatherImpactModel

    registry = ModelRegistry()
    registry.register("elo", lambda ctx: EloModel(context=ctx))
    registry.register("form", lambda ctx: FormModel())
    registry.register("home_advantage", lambda ctx: HomeAdvantageModel())
    registry.register("value", lambda ctx: ValueModel())
    registry.register("weather_impact", lambda ctx: WeatherImpactModel())
    registry.register("injury_impact", lambda ctx: InjuryImpactModel())
    registry.register("matchup", lambda ctx: MatchupModel())
    registry.register("player_form", lambda ctx: PlayerFormModel())
    return registry


__all__ = ["ModelRegistry", "build_default_registry"]
