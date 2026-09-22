"""Contract tests for the typed prediction result (P2-1).

``Prediction`` must be a drop-in for the legacy ``(winner, confidence,
margin)`` tuple at every consumer that unpacks or indexes it, while
exposing the sport-generic attribute names.  ``ABSTAINED`` must be a
singleton with the explicit-abstention semantics.
"""

from __future__ import annotations

import inspect
import typing

import pytest

from packages.shared.models_ml import (
    EloModel,
    FormModel,
    HomeAdvantageModel,
    InjuryImpactModel,
    MatchupModel,
    PlayerFormModel,
    ValueModel,
    WeatherImpactModel,
)
from packages.shared.models_ml.base import BaseModel
from packages.shared.models_ml.prediction import (
    ABSTAINED,
    Abstained,
    Prediction,
    is_abstained,
)
from packages.shared.orchestrator import ModelOrchestrator

ALL_MODEL_CLASSES = [
    EloModel,
    FormModel,
    HomeAdvantageModel,
    ValueModel,
    WeatherImpactModel,
    InjuryImpactModel,
    MatchupModel,
    PlayerFormModel,
]


class TestPredictionInterop:
    def test_unpacks_like_legacy_tuple(self):
        pick, probability, projection = Prediction("Home", 0.8, 12)
        assert pick == "Home"
        assert probability == 0.8
        assert projection == 12

    def test_indexes_like_legacy_tuple(self):
        pred = Prediction("Away", 0.6, 4)
        assert pred[0] == "Away"
        assert pred[1] == 0.6
        assert pred[2] == 4

    def test_two_fields_with_default_projection(self):
        pick, probability = Prediction("Home", 0.7)[:2]
        assert (pick, probability) == ("Home", 0.7)
        assert Prediction("Home", 0.7).score_projection is None

    def test_attribute_names_are_sport_generic(self):
        pred = Prediction("Home", 0.8, 12)
        assert pred.pick == "Home"
        assert pred.probability == 0.8
        assert pred.score_projection == 12


class TestAbstained:
    def test_singleton_identity(self):
        assert Abstained() is ABSTAINED
        assert ABSTAINED is ABSTAINED

    def test_is_abstained(self):
        assert is_abstained(ABSTAINED) is True
        assert is_abstained(None) is True  # legacy abstention shape
        assert is_abstained(Prediction("Home", 0.5)) is False
        assert is_abstained("Home") is False

    def test_falsy(self):
        assert not ABSTAINED


class TestModelABCContract:
    @staticmethod
    def _result_types(func) -> set:
        """Member classes of the return annotation (works for both
        ``Union[Prediction, Abstained]`` and bare ``Prediction``)."""
        hint = typing.get_type_hints(func)["return"]
        args = set(typing.get_args(hint))
        return args or {hint}

    def test_predict_annotated_prediction_or_abstained(self):
        args = self._result_types(BaseModel.predict)
        assert Prediction in args
        assert Abstained in args

    @pytest.mark.parametrize("model_cls", ALL_MODEL_CLASSES, ids=lambda c: c.__name__)
    def test_every_model_declares_the_contract(self, model_cls):
        """All 8 models declare ``Prediction`` on their overridden
        ``predict`` (a type-safe narrowing of the ABC's union — a model
        that never abstains explicitly may drop Abstained from its own
        annotation, but none may narrow back to a raw tuple)."""
        args = self._result_types(model_cls.predict)
        assert Prediction in args, (
            f"{model_cls.__name__}.predict must declare Prediction"
        )
        assert not any(
            isinstance(a, type) and a is tuple for a in args
        )

    def test_models_return_prediction_instances(self):
        """Source-level guard: every model's predict() constructs
        Prediction objects — no bare tuple returns remain."""
        for model_cls in ALL_MODEL_CLASSES:
            src = inspect.getsource(model_cls.predict)
            assert "return Prediction(" in src, (
                f"{model_cls.__name__}.predict still returns a bare tuple"
            )
            assert "ABSTAINED" in src or "Prediction(" in src


class TestOrchestratorAbstentionSeam:
    @staticmethod
    def _bare_orchestrator(models):
        orch = ModelOrchestrator.__new__(ModelOrchestrator)
        orch.models = models

        class _FakeSessionCM:
            async def __aenter__(self):
                return MagicMock()

            async def __aexit__(self, *exc):
                return False

        orch._session_factory = _FakeSessionCM
        return orch

    @pytest.mark.asyncio
    async def test_abstained_model_excluded_but_not_failed(self):
        from types import SimpleNamespace
        from unittest.mock import AsyncMock, MagicMock

        good = SimpleNamespace(
            get_name=lambda: "good",
            predict=AsyncMock(return_value=Prediction("Home", 0.8, 10)),
        )
        abstainer = SimpleNamespace(
            get_name=lambda: "abstainer",
            predict=AsyncMock(return_value=ABSTAINED),
        )

        orch = self._bare_orchestrator([good, abstainer])
        predictions, failed = await orch._gather_model_predictions(
            SimpleNamespace(id=1), ctx="test"
        )

        assert set(predictions) == {"good"}
        assert failed == []  # chose to abstain — did not fail

    @pytest.mark.asyncio
    async def test_raising_model_still_reported_failed(self):
        from types import SimpleNamespace
        from unittest.mock import AsyncMock, MagicMock

        crasher = SimpleNamespace(
            get_name=lambda: "crasher",
            predict=AsyncMock(side_effect=RuntimeError("boom")),
        )

        orch = self._bare_orchestrator([crasher])
        predictions, failed = await orch._gather_model_predictions(
            SimpleNamespace(id=1), ctx="test"
        )

        assert predictions == {}
        assert failed == ["crasher"]


from types import SimpleNamespace  # noqa: E402
from unittest.mock import AsyncMock, MagicMock  # noqa: E402
