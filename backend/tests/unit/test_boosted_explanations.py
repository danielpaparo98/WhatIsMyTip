"""Unit tests for ``packages.shared.services.boosted_explanations`` (BT-1).

Covers the two SHAP explanation services backing the backtest page:

* :func:`get_active_boosted_model` — active-version metadata + global
  SHAP importances enriched with ``model``/``type`` (feature-name
  convention) and sorted by |shap_value| descending.
* :func:`get_game_shap_explanation` — per-game local SHAP payload with
  additivity (``base_value + sum(contributions) ≈ prediction``), a
  winner derived from the prediction sign, and a
  :func:`home_margin_to_tip`-derived pick for context.

Fake-session/monkeypatch style (mirrors ``test_app_api_backtest.py`` /
``test_model_retrain_service.py`` helpers, no database needed): the CRUD
reads are stubbed at ``packages.shared.crud.model_versions`` (the service
imports them lazily inside its functions, exactly like
``services/backtest.py`` does) and the game/prediction query is served by
a minimal fake ``AsyncSession``.

The happy-path test fits a *tiny real* ``XGBRegressor`` and runs the real
``shap.TreeExplainer`` so the additivity property is verified against the
actual libraries, not mocks (importorskip keeps machines without them
green).

Also pins the S3 lazy-import convention for this module in an isolated
subprocess (BT-1: xgboost/shap/numpy must never load at import time).
"""

from __future__ import annotations

import subprocess
import sys
import textwrap
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, List, Optional, Sequence, Tuple

import pytest

from packages.shared.heuristics.weighted_tip import FEATURE_NAMES, MODEL_NAMES
from packages.shared.services.boosted_explanations import (
    BOOSTED_TIP_MODEL_NAME,
    _reset_cache,
    get_active_boosted_model,
    get_game_shap_explanation,
)

BACKEND_DIR = Path(__file__).resolve().parents[2]  # backend/

_HOME = "Brisbane"
_AWAY = "Collingwood"


# ---------------------------------------------------------------------------
# Fakes (mirror the fake-session style of test_app_api_backtest.py)
# ---------------------------------------------------------------------------


class _FakeResult:
    """Minimal result supporting the single access pattern the service uses."""

    def __init__(self, rows: Sequence[Any]) -> None:
        self._rows = list(rows)

    def all(self) -> List[Any]:
        return self._rows


class _FakeSession:
    """AsyncSession stand-in that returns pre-canned (Game, ModelPrediction) rows.

    The service issues exactly one ``session.execute`` in
    :func:`get_game_shap_explanation` (the joined game/prediction query);
    CRUD reads are stubbed separately, so the statement can be ignored.
    """

    def __init__(self, rows: Sequence[Any]) -> None:
        self.rows = list(rows)
        self.executed: List[Any] = []

    async def execute(self, stmt: Any) -> _FakeResult:
        self.executed.append(stmt)
        return _FakeResult(self.rows)


def _make_version(**overrides: Any) -> SimpleNamespace:
    """A fake active ``ModelVersion`` row (attribute access only)."""
    fields: dict[str, Any] = {
        "id": 7,
        "model_name": BOOSTED_TIP_MODEL_NAME,
        "version": 3,
        "intercept": 0.0,
        "trained_at": datetime(2026, 9, 28, 5, 0, 0),
        "training_rows": 4321,
        "metrics": {"r2": 0.21, "mae": 18.4, "shap_base_value": -1.5},
        "is_active": True,
        "artifact": None,
        "artifact_format": "json",
        "shap_base_value": -1.5,
    }
    fields.update(overrides)
    return SimpleNamespace(**fields)


def _coef(feature_name: str, coefficient: float) -> SimpleNamespace:
    """A fake ``ModelCoefficient`` row (BT-1 overload: coefficient = mean|SHAP|)."""
    return SimpleNamespace(feature_name=feature_name, coefficient=coefficient)


def _stub_crud(
    monkeypatch: pytest.MonkeyPatch,
    *,
    version: Optional[Any],
    coefficients: Optional[List[SimpleNamespace]] = None,
    artifact: Optional[Tuple[bytes, str, Any]] = None,
) -> None:
    """Stub the CRUD functions the service resolves lazily at call time."""
    import packages.shared.crud.model_versions as crud

    async def _fake_get_active(session: Any, model_name: str) -> Any:
        return version

    async def _fake_get_coeffs(session: Any, version_id: int) -> List[SimpleNamespace]:
        return list(coefficients or [])

    async def _fake_get_artifact(session: Any, model_name: str) -> Optional[Any]:
        return artifact

    monkeypatch.setattr(crud, "get_active_model_version", _fake_get_active)
    monkeypatch.setattr(crud, "get_model_coefficients", _fake_get_coeffs)
    monkeypatch.setattr(crud, "get_active_model_artifact", _fake_get_artifact)


def _make_preds(seed: int) -> dict[str, Tuple[str, float, int]]:
    """Deterministic 8-model prediction dict matching the feature contract."""
    preds: dict[str, Tuple[str, float, int]] = {}
    for j, name in enumerate(MODEL_NAMES):
        winner = _HOME if (seed + j) % 2 == 0 else _AWAY
        margin = (seed * 7 + j * 3) % 40 + 1
        confidence = 0.50 + (j / 16.0) + (seed % 3) * 0.01
        preds[name] = (winner, round(confidence, 3), margin)
    return preds


# ---------------------------------------------------------------------------
# get_active_boosted_model
# ---------------------------------------------------------------------------


class TestGetActiveBoostedModel:
    async def test_no_active_model_returns_none(self, monkeypatch: pytest.MonkeyPatch):
        _stub_crud(monkeypatch, version=None)

        result = await get_active_boosted_model(_FakeSession([]))

        assert result is None

    async def test_meta_and_enriched_importances(self, monkeypatch: pytest.MonkeyPatch):
        """All three type cases + |shap_value| descending sort + meta passthrough."""
        version = _make_version()
        # Deliberately NOT sorted by |coefficient| — the service must sort.
        coefficients = [
            _coef("form_conf", 0.9),  # -> ("form", "confidence")
            _coef("elo_margin_home", -3.1),  # -> ("elo", "margin")
            _coef("weather_impact_conf", 0.2),
            _coef("mystery_feature", 0.05),  # no convention suffix -> "other"
        ]
        _stub_crud(monkeypatch, version=version, coefficients=coefficients)

        result = await get_active_boosted_model(_FakeSession([]))

        assert result is not None
        assert result["model_name"] == BOOSTED_TIP_MODEL_NAME
        assert result["version"] == 3
        assert result["trained_at"] == "2026-09-28T05:00:00"
        assert result["training_rows"] == 4321
        assert result["is_active"] is True
        assert result["metrics"] == {"r2": 0.21, "mae": 18.4, "shap_base_value": -1.5}

        importances = result["importances"]
        assert [i["feature_name"] for i in importances] == [
            "elo_margin_home",
            "form_conf",
            "weather_impact_conf",
            "mystery_feature",
        ]
        assert [i["shap_value"] for i in importances] == [-3.1, 0.9, 0.2, 0.05]

        by_name = {i["feature_name"]: i for i in importances}
        assert by_name["elo_margin_home"]["model"] == "elo"
        assert by_name["elo_margin_home"]["type"] == "margin"
        assert by_name["form_conf"]["model"] == "form"
        assert by_name["form_conf"]["type"] == "confidence"
        assert by_name["weather_impact_conf"]["model"] == "weather_impact"
        assert by_name["weather_impact_conf"]["type"] == "confidence"
        assert by_name["mystery_feature"]["model"] == "mystery_feature"
        assert by_name["mystery_feature"]["type"] == "other"

    async def test_trained_at_none_survives(self, monkeypatch: pytest.MonkeyPatch):
        _stub_crud(
            monkeypatch,
            version=_make_version(trained_at=None),
            coefficients=[_coef("elo_margin_home", 1.0)],
        )

        result = await get_active_boosted_model(_FakeSession([]))

        assert result is not None
        assert result["trained_at"] is None


# ---------------------------------------------------------------------------
# get_game_shap_explanation — happy path with a tiny real XGBRegressor
# ---------------------------------------------------------------------------


def _train_tiny_boosted_model() -> Tuple[bytes, SimpleNamespace]:
    """Fit a tiny deterministic XGBRegressor; return (json_bytes, version_row).

    Uses random synthetic 16-feature data — only shape/additivity matter
    here, not predictive quality (importorskip guards the libs).
    """
    xgboost = pytest.importorskip("xgboost")
    pytest.importorskip("shap")
    import random

    rng = random.Random(42)
    n_rows = 60
    X = [[rng.uniform(-40, 40) if k % 2 == 0 else rng.uniform(0.5, 0.95)  # noqa: N806 — ML design-matrix convention
          for k in range(len(FEATURE_NAMES))] for _ in range(n_rows)]
    y = [sum(0.05 * v for v in row) for row in X]

    model = xgboost.XGBRegressor(
        n_estimators=8,
        max_depth=2,
        learning_rate=0.5,
        tree_method="hist",
        random_state=42,
    )
    model.fit(X, y)
    artifact_bytes: bytes = model.get_booster().save_raw(raw_format="json")
    version = _make_version(artifact=artifact_bytes, artifact_format="json")
    return artifact_bytes, version


class TestGetGameShapExplanation:
    async def test_no_active_artifact_returns_none(self, monkeypatch: pytest.MonkeyPatch):
        # Active version exists but stores no artifact (e.g. pre-migration row).
        _reset_cache()
        _stub_crud(monkeypatch, version=_make_version(artifact=None), artifact=None)

        result = await get_game_shap_explanation(_FakeSession([]), game_id=1)

        assert result is None

    async def test_missing_game_returns_none(self, monkeypatch: pytest.MonkeyPatch):
        """Game absent (or zero prediction rows) -> None (API maps to 404)."""
        _reset_cache()
        _, version = _train_tiny_boosted_model()
        _stub_crud(
            monkeypatch,
            version=version,
            artifact=(version.artifact, "json", version),
        )

        result = await get_game_shap_explanation(_FakeSession([]), game_id=999)

        assert result is None

    async def test_happy_path_additivity_and_payload(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        """Real XGBRegressor + TreeExplainer: shape, sorting, additivity, winner."""
        _reset_cache()
        artifact_bytes, version = _train_tiny_boosted_model()
        _stub_crud(
            monkeypatch,
            version=version,
            artifact=(artifact_bytes, "json", version),
        )

        game = SimpleNamespace(
            id=42, home_team=_HOME, away_team=_AWAY, season=2026
        )
        preds = _make_preds(seed=1)
        rows = [(game, SimpleNamespace(model_name=name, winner=w, confidence=c, margin=m))
                for name, (w, c, m) in preds.items()]
        session = _FakeSession(rows)

        result = await get_game_shap_explanation(session, game_id=42)

        assert result is not None
        assert result["game_id"] == 42
        assert result["home_team"] == _HOME
        assert result["away_team"] == _AWAY

        contributions = result["contributions"]
        assert list(contributions.keys()) and set(contributions) == set(FEATURE_NAMES)
        values = list(contributions.values())
        assert values == sorted(values, key=lambda v: abs(v), reverse=True)

        # SHAP additivity: base + Σ contributions == model output (float error only).
        assert result["base_value"] + sum(contributions.values()) == pytest.approx(
            result["prediction"], abs=1e-4
        )

        # winner is the sign of the raw margin prediction; pick carries the tip.
        expected_winner = "home" if result["prediction"] >= 0 else "away"
        assert result["winner"] == expected_winner
        assert result["pick"]["winner"] == (
            _HOME if result["prediction"] >= 0 else _AWAY
        )
        assert result["pick"]["margin"] >= 1
        assert 0.50 <= result["pick"]["confidence"] <= 0.95

    async def test_winner_away_on_negative_prediction(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        """A model biased to negative margins must flip winner/pick to away."""
        _reset_cache()
        xgboost = pytest.importorskip("xgboost")
        pytest.importorskip("shap")
        import random

        rng = random.Random(7)
        n_rows = 60
        X = [[rng.uniform(-40, 40) if k % 2 == 0 else rng.uniform(0.5, 0.95)  # noqa: N806 — ML convention
              for k in range(len(FEATURE_NAMES))] for _ in range(n_rows)]
        # Constant strongly-negative target -> negative predictions.
        y = [-30.0 - 0.01 * (i % 5) for i in range(n_rows)]

        model = xgboost.XGBRegressor(
            n_estimators=8, max_depth=2, learning_rate=0.5,
            tree_method="hist", random_state=42,
        )
        model.fit(X, y)
        artifact_bytes: bytes = model.get_booster().save_raw(raw_format="json")
        version = _make_version(artifact=artifact_bytes, artifact_format="json")
        _stub_crud(
            monkeypatch, version=version, artifact=(artifact_bytes, "json", version)
        )

        game = SimpleNamespace(id=5, home_team=_HOME, away_team=_AWAY, season=2026)
        rows = [(game, SimpleNamespace(model_name=name, winner=w, confidence=c, margin=m))
                for name, (w, c, m) in _make_preds(seed=2).items()]

        result = await get_game_shap_explanation(_FakeSession(rows), game_id=5)

        assert result is not None
        assert result["prediction"] < 0
        assert result["winner"] == "away"
        assert result["pick"]["winner"] == _AWAY


# ---------------------------------------------------------------------------
# Explainer cache (rebuild only on version-id change or byte drift)
# ---------------------------------------------------------------------------


class TestExplainerCache:
    async def test_second_call_reuses_explainer_and_reset_rebuilds(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        """TreeExplainer is constructed once per (version, bytes); reset rebuilds."""
        _reset_cache()
        shap = pytest.importorskip("shap")
        artifact_bytes, version = _train_tiny_boosted_model()

        calls = {"n": 0}
        real_tree_explainer = shap.TreeExplainer

        def counting_tree_explainer(model: Any) -> Any:
            calls["n"] += 1
            return real_tree_explainer(model)

        monkeypatch.setattr(shap, "TreeExplainer", counting_tree_explainer)
        _stub_crud(
            monkeypatch, version=version, artifact=(artifact_bytes, "json", version)
        )

        game = SimpleNamespace(id=42, home_team=_HOME, away_team=_AWAY, season=2026)
        rows = [(game, SimpleNamespace(model_name=name, winner=w, confidence=c, margin=m))
                for name, (w, c, m) in _make_preds(seed=3).items()]

        first = await get_game_shap_explanation(_FakeSession(rows), game_id=42)
        assert calls["n"] == 1
        second = await get_game_shap_explanation(_FakeSession(rows), game_id=42)
        assert calls["n"] == 1  # cached — no second TreeExplainer construction
        assert second == first

        _reset_cache()
        await get_game_shap_explanation(_FakeSession(rows), game_id=42)
        assert calls["n"] == 2  # reset forced a rebuild

    async def test_byte_drift_same_version_id_rebuilds(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        """Same version id but different artifact bytes must invalidate the cache."""
        _reset_cache()
        shap = pytest.importorskip("shap")
        xgboost = pytest.importorskip("xgboost")
        artifact_bytes, version = _train_tiny_boosted_model()

        calls = {"n": 0}
        real_tree_explainer = shap.TreeExplainer

        def counting_tree_explainer(model: Any) -> Any:
            calls["n"] += 1
            return real_tree_explainer(model)

        monkeypatch.setattr(shap, "TreeExplainer", counting_tree_explainer)

        game = SimpleNamespace(id=42, home_team=_HOME, away_team=_AWAY, season=2026)
        rows = [(game, SimpleNamespace(model_name=name, winner=w, confidence=c, margin=m))
                for name, (w, c, m) in _make_preds(seed=4).items()]
        session = _FakeSession(rows)

        _stub_crud(
            monkeypatch, version=version, artifact=(artifact_bytes, "json", version)
        )
        await get_game_shap_explanation(session, game_id=42)
        assert calls["n"] == 1

        # Same version id, retrained bytes (e.g. manual re-run overwrote the blob).
        other = xgboost.XGBRegressor(
            n_estimators=4, max_depth=1, learning_rate=0.9,
            tree_method="hist", random_state=1,
        )
        other.fit(
            [[0.0] * len(FEATURE_NAMES), [10.0] * len(FEATURE_NAMES)],
            [1.0, 20.0],
        )
        drifted_bytes: bytes = other.get_booster().save_raw(raw_format="json")
        assert drifted_bytes != artifact_bytes
        _stub_crud(
            monkeypatch, version=version, artifact=(drifted_bytes, "json", version)
        )
        await get_game_shap_explanation(session, game_id=42)
        assert calls["n"] == 2  # byte drift invalidated the cached explainer

    async def test_version_id_change_rebuilds(self, monkeypatch: pytest.MonkeyPatch):
        """A new active version id must not be served the previous explainer."""
        _reset_cache()
        shap = pytest.importorskip("shap")
        artifact_bytes, version = _train_tiny_boosted_model()

        calls = {"n": 0}
        real_tree_explainer = shap.TreeExplainer

        def counting_tree_explainer(model: Any) -> Any:
            calls["n"] += 1
            return real_tree_explainer(model)

        monkeypatch.setattr(shap, "TreeExplainer", counting_tree_explainer)

        game = SimpleNamespace(id=42, home_team=_HOME, away_team=_AWAY, season=2026)
        rows = [(game, SimpleNamespace(model_name=name, winner=w, confidence=c, margin=m))
                for name, (w, c, m) in _make_preds(seed=5).items()]
        session = _FakeSession(rows)

        _stub_crud(
            monkeypatch, version=version, artifact=(artifact_bytes, "json", version)
        )
        await get_game_shap_explanation(session, game_id=42)
        assert calls["n"] == 1

        # Weekly retrain promotes version 4 with fresh (identical-content) bytes.
        new_version = _make_version(id=8, version=4, artifact=artifact_bytes)
        _stub_crud(
            monkeypatch,
            version=new_version,
            artifact=(artifact_bytes, "json", new_version),
        )
        await get_game_shap_explanation(session, game_id=42)
        assert calls["n"] == 2  # id change invalidated the cached explainer


# ---------------------------------------------------------------------------
# Lazy-import pin (S3) — mirrors tests/unit/test_lazy_sklearn.py
# ---------------------------------------------------------------------------


def test_importing_boosted_explanations_module_does_not_load_heavy_libs():
    """BT-1: the SHAP service must not pull xgboost/shap/numpy at import time.

    ``app.api.backtest`` (task 08) will import this module eagerly, so an
    accidental top-level ``import xgboost``/``import shap``/``import numpy``
    would make every always-on API worker pay the full resident-memory cost.
    Pinned here (rather than appended to ``test_lazy_sklearn.py``) to respect
    the parallel-batch file-ownership boundary.
    """
    code = textwrap.dedent(
        """
        import sys
        import packages.shared.services.boosted_explanations
        heavy = ("sklearn", "scipy", "numpy", "xgboost", "shap")
        offenders = sorted(lib for lib in heavy if lib in sys.modules)
        assert not offenders, (
            "Importing boosted_explanations loaded heavy ML libs at module "
            f"load time: {offenders}"
        )
        """
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        cwd=str(BACKEND_DIR),
        timeout=120,
    )
    assert result.returncode == 0, (
        "Isolated import check failed — a heavy ML lib was loaded at import "
        "time (or the import itself errored):\n"
        f"--- stdout ---\n{result.stdout}"
        f"--- stderr ---\n{result.stderr}"
    )
