"""Unit tests for ``packages.shared.services.boosted_explanations`` (BT-1).

Covers the global SHAP explanation service backing the backtest page's
"Active Boosted Model (XGBoost)" section:

* :func:`get_active_boosted_model` — active-version metadata + global
  SHAP importances enriched with ``model``/``type`` (feature-name
  convention) and sorted by |shap_value| descending.

(The per-game local SHAP service and its explainer cache were removed by
design decision — only global importances remain.)

Fake-session/monkeypatch style (mirrors ``test_app_api_backtest.py`` /
``test_model_retrain_service.py`` helpers, no database needed): the CRUD
reads are stubbed at ``packages.shared.crud.model_versions`` (the service
imports them lazily inside its functions, exactly like
``services/backtest.py`` does).

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
from typing import Any, List, Optional

import pytest

from packages.shared.services.boosted_explanations import (
    BOOSTED_TIP_MODEL_NAME,
    get_active_boosted_model,
)

BACKEND_DIR = Path(__file__).resolve().parents[2]  # backend/


# ---------------------------------------------------------------------------
# Fakes (mirror the fake-session style of test_app_api_backtest.py)
# ---------------------------------------------------------------------------


class _FakeSession:
    """AsyncSession stand-in — CRUD reads are stubbed, so nothing executes."""

    async def execute(self, stmt: Any) -> Any:  # pragma: no cover - not reached
        raise AssertionError("get_active_boosted_model must not query via the session")


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
) -> None:
    """Stub the CRUD functions the service resolves lazily at call time."""
    import packages.shared.crud.model_versions as crud

    async def _fake_get_active(session: Any, model_name: str) -> Any:
        return version

    async def _fake_get_coeffs(session: Any, version_id: int) -> List[SimpleNamespace]:
        return list(coefficients or [])

    monkeypatch.setattr(crud, "get_active_model_version", _fake_get_active)
    monkeypatch.setattr(crud, "get_model_coefficients", _fake_get_coeffs)


# ---------------------------------------------------------------------------
# get_active_boosted_model
# ---------------------------------------------------------------------------


class TestGetActiveBoostedModel:
    async def test_no_active_model_returns_none(self, monkeypatch: pytest.MonkeyPatch):
        _stub_crud(monkeypatch, version=None)

        result = await get_active_boosted_model(_FakeSession())

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

        result = await get_active_boosted_model(_FakeSession())

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

        result = await get_active_boosted_model(_FakeSession())

        assert result is not None
        assert result["trained_at"] is None


# ---------------------------------------------------------------------------
# Lazy-import pin (S3) — mirrors tests/unit/test_lazy_sklearn.py
# ---------------------------------------------------------------------------


def test_importing_boosted_explanations_module_does_not_load_heavy_libs():
    """BT-1: the SHAP service must not pull xgboost/shap/numpy at import time.

    ``app.api.backtest`` imports this module eagerly, so an accidental
    top-level ``import xgboost``/``import shap``/``import numpy`` would
    make every always-on API worker pay the full resident-memory cost.
    Pinned here (rather than appended to ``test_lazy_sklearn.py``) to
    respect the parallel-batch file-ownership boundary.
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
