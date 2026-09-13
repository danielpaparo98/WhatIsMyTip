"""Session-isolation and abstain semantics for :class:`ModelOrchestrator`.

Guards two production incidents found in the 2026-09 comprehensive review:

* **ORCH-H1** — all 8 models previously ran under ONE shared
  ``AsyncSession`` via ``asyncio.gather``.  SQLAlchemy explicitly forbids
  concurrent use of a single ``AsyncSession``; the resulting errors were
  swallowed by the per-model ``except`` and the models silently degraded
  to home-team defaults.  Each model task now receives its OWN session
  from a session factory.
* **ORCH-M7** — a failing model previously voted
  ``(home_team, 0.5, 0)``, systematically biasing consensus toward home
  sides during partial outages.  Failed models now **abstain**: they are
  excluded from ``model_predictions`` (all heuristics have documented
  missing-model handling: best_bet/yolo empty-checks, weighted_tip
  zero-fill in ``build_feature_vector``) and are reported via the
  ``failed_models`` key of the ``predict_all`` payload.
"""

from typing import Any, Dict, List, Optional, Tuple

import pytest

from packages.shared.orchestrator import ModelOrchestrator


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------

class FakeSession:
    """Minimal async-context-manager stand-in for AsyncSession."""

    async def __aenter__(self) -> "FakeSession":
        return self

    async def __aexit__(self, *exc: Any) -> bool:
        return False


class FakeSessionFactory:
    """Callable factory yielding a NEW FakeSession per call (like
    ``async_sessionmaker``).  Tracks every session it created."""

    def __init__(self):
        self.created: List[FakeSession] = []

    def __call__(self) -> FakeSession:
        session = FakeSession()
        self.created.append(session)
        return session


class FakeModel:
    """Records the session it was handed; returns a canned prediction or raises."""

    def __init__(
        self,
        name: str,
        result: Optional[Tuple[str, float, int]] = None,
        error: Optional[Exception] = None,
    ):
        self._name = name
        self._result = result or (name + "_winner", 0.7, 10)
        self._error = error
        self.sessions: List[Any] = []

    def get_name(self) -> str:
        return self._name

    async def predict(self, game, db) -> Tuple[str, float, int]:
        self.sessions.append(db)
        if self._error is not None:
            raise self._error
        return self._result


def _make_game(home_team: str = "Richmond", away_team: str = "Carlton"):
    from unittest.mock import MagicMock

    game = MagicMock()
    game.id = 1
    game.home_team = home_team
    game.away_team = away_team
    return game


def _make_orchestrator(models: List[FakeModel]) -> Tuple[ModelOrchestrator, FakeSessionFactory]:
    factory = FakeSessionFactory()
    orch = ModelOrchestrator(session_factory=factory)
    orch.models = models
    return orch, factory


# ---------------------------------------------------------------------------
# ORCH-H1: session isolation
# ---------------------------------------------------------------------------

class TestSessionIsolation:
    @pytest.mark.asyncio
    async def test_each_model_receives_its_own_session(self):
        """8 models must get 8 DISTINCT sessions, none of them the caller's db."""
        models = [FakeModel(f"m{i}") for i in range(8)]
        orch, factory = _make_orchestrator(models)

        caller_db = object()  # sentinel: must never reach a model
        await orch.predict_all(_make_game(), db=caller_db)

        all_sessions = [s for m in models for s in m.sessions]
        assert len(all_sessions) == 8
        assert all(s is not caller_db for s in all_sessions)
        assert len({id(s) for s in all_sessions}) == 8
        assert len(factory.created) == 8

    @pytest.mark.asyncio
    async def test_predict_single_heuristic_uses_fresh_sessions(self):
        models = [FakeModel(f"m{i}") for i in range(4)]
        orch, _ = _make_orchestrator(models)

        caller_db = object()
        await orch.predict(_make_game(), "best_bet", db=caller_db)

        all_sessions = [s for m in models for s in m.sessions]
        assert len(all_sessions) == 4
        assert all(s is not caller_db for s in all_sessions)

    @pytest.mark.asyncio
    async def test_default_factory_path_yields_working_sessions(self, monkeypatch):
        """B1 regression (2026-09 pre-deploy review).

        The DEFAULT production path resolves the shared
        ``async_sessionmaker`` from packages.shared.db.  The orchestrator
        treats ``self._session_factory()`` as an async context manager —
        so the default resolver must return the *session* (a real async
        CM), NOT the maker (which has no ``__aenter__``/``__aexit__``).
        The original implementation returned the maker itself, so every
        model raised ``TypeError`` on the default path, was swallowed by
        the abstain handler, and ALL tips silently degraded to heuristic
        fallbacks.  This test pins the default path with a fake maker.
        """
        created: List[FakeSession] = []

        def fake_maker() -> FakeSession:
            session = FakeSession()
            created.append(session)
            return session

        monkeypatch.setattr(
            "packages.shared.db._get_session_factory", lambda: fake_maker
        )

        models = [FakeModel(f"m{i}") for i in range(3)]
        orch = ModelOrchestrator()  # NO session_factory — default path
        orch.models = models

        results = await orch.predict_all(_make_game())

        # Every model must have actually run (not silently abstained):
        preds = results["best_bet"]["model_predictions"]
        failed = results["best_bet"]["failed_models"]
        assert len(preds) == 3, f"models silently failed on the default path: {failed}"
        assert failed == []
        assert len(created) == 3


# ---------------------------------------------------------------------------
# ORCH-M7: abstain semantics
# ---------------------------------------------------------------------------

class TestFailedModelAbstains:
    @pytest.mark.asyncio
    async def test_failed_model_excluded_from_predictions(self):
        models = [
            FakeModel("good_one"),
            FakeModel("bad_one", error=RuntimeError("boom")),
            FakeModel("good_two"),
        ]
        orch, _ = _make_orchestrator(models)

        results = await orch.predict_all(_make_game())

        preds = results["best_bet"]["model_predictions"]
        assert "bad_one" not in preds
        assert "good_one" in preds and "good_two" in preds

    @pytest.mark.asyncio
    async def test_failed_model_not_replaced_by_home_default(self):
        """The old bug: a failed model voted (home_team, 0.5, 0)."""
        home_team = "Richmond"
        models = [
            FakeModel("bad_one", error=RuntimeError("boom")),
            # Only surviving model picks the AWAY team — the final vote must
            # reflect that, not be outvoted by a phantom home vote.
            FakeModel("away_picker", result=("Carlton", 0.8, 20)),
        ]
        orch, _ = _make_orchestrator(models)
        game = _make_game(home_team=home_team, away_team="Carlton")

        results = await orch.predict_all(game)

        preds = results["best_bet"]["model_predictions"]
        # No phantom (home, 0.5, 0) entry:
        assert home_team not in preds.values()
        # The lone healthy model's away vote decides best_bet:
        assert results["best_bet"]["tip"][0] == "Carlton"

    @pytest.mark.asyncio
    async def test_failed_models_reported_in_payload(self):
        models = [
            FakeModel("good_one"),
            FakeModel("bad_one", error=RuntimeError("boom")),
            FakeModel("bad_two", error=ValueError("nope")),
        ]
        orch, _ = _make_orchestrator(models)

        results = await orch.predict_all(_make_game())

        assert sorted(results["best_bet"]["failed_models"]) == ["bad_one", "bad_two"]

    @pytest.mark.asyncio
    async def test_all_models_fail_heuristics_still_return(self):
        """Total outage must not crash tip generation — heuristics return
        their documented empty-predictions fallbacks."""
        models = [FakeModel(f"m{i}", error=RuntimeError("outage")) for i in range(3)]
        orch, _ = _make_orchestrator(models)
        game = _make_game(home_team="Richmond", away_team="Carlton")

        results = await orch.predict_all(game)

        assert set(results.keys()) == {"best_bet", "yolo", "weighted_tip"}
        # Documented empty-predictions fallbacks:
        assert results["best_bet"]["tip"] == ("Richmond", 0.55, 15)
        assert results["yolo"]["tip"] == ("Richmond", 0.6, 20)
        assert results["weighted_tip"]["tip"] == ("Carlton", 0.55, 6)
        assert sorted(results["best_bet"]["failed_models"]) == ["m0", "m1", "m2"]

    @pytest.mark.asyncio
    async def test_no_failures_reports_empty_list(self):
        models = [FakeModel(f"m{i}") for i in range(3)]
        orch, _ = _make_orchestrator(models)

        results = await orch.predict_all(_make_game())

        assert results["best_bet"]["failed_models"] == []
