"""Unit tests for the ``boosted_tip`` XGBoost-backed heuristic (BT-1).

Mirrors ``test_weighted_tip_heuristic.py``.  The pure functions
(``predict_home_margin_boosted``, ``load_boosted_model``) plus the
:class:`BoostedTipHeuristic` class form the public contract that the
orchestrator (Subtask 5) and the retrain service (Subtask 3) will
import, so reuse of the weighted_tip feature contract, sign
conventions, fallback behaviour and lazy-import discipline are pinned
here.

Heavy-lib discipline: xgboost is imported lazily *inside* the helpers,
so the length-mismatch / fallback / contract tests run without it.  The
fitted-model tests call ``pytest.importorskip("xgboost")`` and skip
cleanly when xgboost is unavailable.  The lazy-import pins use an
isolated subprocess (mirroring ``test_lazy_sklearn.py``) so the result
is not polluted by other tests that legitimately import xgboost.
"""

from __future__ import annotations

import ast
import subprocess
import sys
import textwrap
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from packages.shared.heuristics import boosted_tip
from packages.shared.heuristics.boosted_tip import (
    BoostedTipHeuristic,
    load_boosted_model,
    predict_home_margin_boosted,
)
from packages.shared.heuristics.weighted_tip import (
    FEATURE_NAMES,
    MODEL_NAMES,
    build_feature_vector,
    feature_names_for,
    home_margin_to_tip,
    weighted_tip_fallback,
)

BACKEND_DIR = Path(__file__).resolve().parents[2]  # backend/

# Heavy ML libraries that must NOT be loaded by importing the always-on
# path.  Same guard list as tests/unit/test_lazy_sklearn.py.
_HEAVY_LIBS = ("sklearn", "scipy", "numpy", "xgboost", "shap")


def _make_game(home_team="Richmond", away_team="Carlton"):
    """Create a mock Game object for testing (matches sibling heuristic tests)."""
    game = MagicMock()
    game.home_team = home_team
    game.away_team = away_team
    return game


@pytest.fixture(scope="module")
def tiny_model_bytes() -> bytes:
    """A tiny fitted XGBRegressor serialized with ``save_raw(raw_format="json")``.

    16 features, 20 rows, ``n_estimators=2`` — must match the real
    feature contract (``build_feature_vector`` always yields
    ``len(FEATURE_NAMES)`` == 16 columns) or xgboost rejects every
    prediction with a shape mismatch.  ``y == x0`` with all other
    columns constant keeps the regression deterministic and
    sign-preserved.  Skips cleanly when xgboost is unavailable.
    """
    xgb = pytest.importorskip("xgboost")
    rows = [(float(x), 0.5) for x in range(-10, 10)]
    X = [[x0, x1] + [0.0] * 14 for x0, x1 in rows]  # noqa: N806 — ML design-matrix convention
    y = [x0 for x0, _ in rows]
    reg = xgb.XGBRegressor(
        n_estimators=2,
        max_depth=2,
        learning_rate=0.5,
        tree_method="hist",
        eval_metric="rmse",
        random_state=42,
    )
    reg.fit(X, y)
    model_bytes: bytes = reg.get_booster().save_raw(raw_format="json")
    return model_bytes


# ---------------------------------------------------------------------------
# Reuse contract (import, never copy)
# ---------------------------------------------------------------------------

class TestReuseContract:
    """BT-1: boosted_tip must REUSE weighted_tip's contract objects.

    Asserting object identity (``is``) proves the functions were
    imported, not copied — a copied drift between the two heuristics'
    feature contracts would silently mis-train the boosted model.
    """

    def test_reuses_build_feature_vector(self):
        assert boosted_tip.build_feature_vector is build_feature_vector

    def test_reuses_home_margin_to_tip(self):
        assert boosted_tip.home_margin_to_tip is home_margin_to_tip

    def test_reuses_weighted_tip_fallback(self):
        assert boosted_tip.weighted_tip_fallback is weighted_tip_fallback

    def test_reuses_model_names_and_feature_names(self):
        assert boosted_tip.MODEL_NAMES is MODEL_NAMES
        assert boosted_tip.FEATURE_NAMES is FEATURE_NAMES

    def test_reuses_feature_names_for(self):
        assert boosted_tip.feature_names_for is feature_names_for


# ---------------------------------------------------------------------------
# predict_home_margin_boosted (pure function)
# ---------------------------------------------------------------------------

class TestPredictHomeMarginBoosted:
    def test_fewer_names_than_features_raises_value_error(self):
        # Raises BEFORE any xgboost import/deserialization — fail loud
        # instead of silently truncating the zip (mirrors weighted_tip).
        # Garbage bytes on purpose: validation must fire first, so this
        # test runs even without xgboost installed.
        with pytest.raises(ValueError):
            predict_home_margin_boosted(b"not-a-model", [1.0] * 16, feature_names=["a", "b"])

    def test_more_names_than_features_raises_value_error(self):
        names = feature_names_for(MODEL_NAMES) + ["extra_margin_home"]
        with pytest.raises(ValueError):
            predict_home_margin_boosted(b"not-a-model", [1.0] * 16, feature_names=names)

    def test_mismatch_error_message_mirrors_weighted_tip_style(self):
        """Same message style as weighted_tip.predict_home_margin."""
        with pytest.raises(ValueError, match="features/names length mismatch"):
            predict_home_margin_boosted(b"{}", [1.0] * 16, feature_names=["a"])

    def test_deterministic_for_same_bytes_and_features(self, tiny_model_bytes):
        features = [3.0, 0.7] + [0.0] * 14
        first = predict_home_margin_boosted(tiny_model_bytes, features)
        second = predict_home_margin_boosted(tiny_model_bytes, features)
        assert first == second

    def test_returns_plain_float(self, tiny_model_bytes):
        features = [3.0, 0.7] + [0.0] * 14
        assert isinstance(predict_home_margin_boosted(tiny_model_bytes, features), float)

    def test_positive_feature_yields_positive_margin(self, tiny_model_bytes):
        # Model trained with y == x0, so x0=20 must predict a home-signed margin.
        y_pred = predict_home_margin_boosted(tiny_model_bytes, [20.0, 0.5] + [0.0] * 14)
        assert y_pred > 0

    def test_negative_feature_yields_negative_margin(self, tiny_model_bytes):
        y_pred = predict_home_margin_boosted(tiny_model_bytes, [-20.0, 0.5] + [0.0] * 14)
        assert y_pred < 0

    def test_missing_models_zero_padding_parity(self, tiny_model_bytes):
        """Zero-padded vectors (missing models) predict identically to a
        direct ``reg.predict`` on the same row — the wrapper adds no
        transformation, it only deserializes + predicts."""
        np = pytest.importorskip("numpy")
        # build_feature_vector (reused from weighted_tip) zero-pads the
        # 8 missing models of an elo-only prediction set.
        features = build_feature_vector({"elo": ("Richmond", 0.7, 10)}, "Richmond", "Carlton")
        assert features[0] == 10.0 and features[1] == 0.7
        assert all(v == 0.0 for v in features[2:])  # zero padding

        y_pred = predict_home_margin_boosted(tiny_model_bytes, features)
        reg = load_boosted_model(tiny_model_bytes)
        expected = float(reg.predict(np.array([features]))[0])
        assert y_pred == pytest.approx(expected)

    def test_artifact_format_kwarg_accepted(self, tiny_model_bytes):
        """The stored ``artifact_format`` tag rides along; XGBoost detects
        the buffer format itself, so "json" bytes predict the same either way."""
        features = [5.0, 0.5] + [0.0] * 14
        default = predict_home_margin_boosted(tiny_model_bytes, features)
        explicit = predict_home_margin_boosted(
            tiny_model_bytes, features, artifact_format="json"
        )
        assert default == explicit


class TestPreloadedModelOverload:
    """BT-2: the pure function accepts raw bytes OR a preloaded model, so
    the orchestrator's TTL loader can deserialize once per window and
    ``apply`` never re-deserializes per tip."""

    def test_accepts_preloaded_model_object(self, tiny_model_bytes):
        """A loaded model passed directly predicts identically to the
        bytes path (same artifact, one deserialization fewer)."""
        features = [3.0, 0.7] + [0.0] * 14
        reg = load_boosted_model(tiny_model_bytes)
        via_object = predict_home_margin_boosted(reg, features)
        via_bytes = predict_home_margin_boosted(tiny_model_bytes, features)
        assert via_object == pytest.approx(via_bytes)

    def test_preloaded_object_is_not_reloaded(self):
        """Non-bytes input is treated as an already-deserialized model —
        ``load_boosted_model`` must NOT be called for it."""
        fake_model = MagicMock()
        fake_model.predict.return_value = [7.5]
        with patch(
            "packages.shared.heuristics.boosted_tip.load_boosted_model"
        ) as mock_load:
            y_pred = predict_home_margin_boosted(fake_model, [1.0] * 16)
        mock_load.assert_not_called()
        fake_model.predict.assert_called_once_with([[1.0] * 16])
        assert y_pred == pytest.approx(7.5)

    def test_bytearray_still_treated_as_bytes(self, tiny_model_bytes):
        """bytearray/memoryview artifacts still go through the loader."""
        features = [3.0, 0.7] + [0.0] * 14
        via_bytearray = predict_home_margin_boosted(bytearray(tiny_model_bytes), features)
        assert via_bytearray == pytest.approx(
            predict_home_margin_boosted(tiny_model_bytes, features)
        )

    def test_length_mismatch_still_raises_for_preloaded_model(self):
        """Validation happens before the predict call either way."""
        fake_model = MagicMock()
        with pytest.raises(ValueError, match="features/names length mismatch"):
            predict_home_margin_boosted(fake_model, [1.0, 2.0], feature_names=["a"])


# ---------------------------------------------------------------------------
# load_boosted_model (deserialization helper)
# ---------------------------------------------------------------------------

class TestLoadBoostedModel:
    def test_roundtrip_json_bytes(self, tiny_model_bytes):
        """``save_raw(raw_format="json")`` bytes → ``load_boosted_model``
        → a predict-capable model with the original behaviour."""
        np = pytest.importorskip("numpy")
        reg = load_boosted_model(tiny_model_bytes)
        # Trained with y == x0: direct predict on x0=20 must be positive.
        direct = float(reg.predict(np.array([[20.0, 0.5] + [0.0] * 14]))[0])
        assert direct > 0
        # And the pure function agrees with the reloaded model.
        via_pure = predict_home_margin_boosted(tiny_model_bytes, [20.0, 0.5] + [0.0] * 14)
        assert via_pure == pytest.approx(direct)


# ---------------------------------------------------------------------------
# BoostedTipHeuristic class
# ---------------------------------------------------------------------------

class TestBoostedTipHeuristic:
    def setup_method(self):
        self.heuristic = BoostedTipHeuristic(models=[])

    def test_get_name(self):
        assert self.heuristic.get_name() == "boosted_tip"

    def test_model_names_default_to_registry_when_no_models(self):
        assert self.heuristic.model_names == list(MODEL_NAMES)

    def test_model_names_derived_from_injected_models(self):
        m1, m2 = MagicMock(), MagicMock()
        m1.get_name.return_value = "m1"
        m2.get_name.return_value = "m2"
        heuristic = BoostedTipHeuristic(models=[m1, m2])
        assert heuristic.model_names == ["m1", "m2"]

    @pytest.mark.asyncio
    async def test_apply_without_model_uses_fallback(self):
        """Pre-retrain parity: identical to weighted_tip_fallback output."""
        game = _make_game()
        preds = {
            "elo": ("Richmond", 0.7, 10),
            "form": ("Richmond", 0.6, 12),
            "value": ("Carlton", 0.5, 5),
        }
        result = await self.heuristic.apply(game, preds)
        assert result == weighted_tip_fallback(preds, "Richmond", "Carlton")
        winner, conf, _ = result
        assert winner == "Richmond"
        assert conf == pytest.approx(0.55)

    @pytest.mark.asyncio
    async def test_apply_learned_path_positive_margin_picks_home(self, tiny_model_bytes):
        """Winner sign mapping: positive predicted home margin → home team."""
        game = _make_game()
        preds = {"elo": ("Richmond", 0.7, 20)}
        self.heuristic.set_model(tiny_model_bytes)
        winner, _conf, margin = await self.heuristic.apply(game, preds)
        # elo_margin_home = +20 dominates the zero-padded vector → y_pred > 0.
        assert winner == "Richmond"
        assert margin >= 1

    @pytest.mark.asyncio
    async def test_apply_learned_path_negative_margin_picks_away(self, tiny_model_bytes):
        """Winner sign mapping: negative predicted home margin → away team."""
        game = _make_game()
        preds = {"elo": ("Carlton", 0.7, 20)}
        self.heuristic.set_model(tiny_model_bytes)
        winner, _conf, margin = await self.heuristic.apply(game, preds)
        # elo_margin_home = -20 → y_pred < 0 → away (Carlton).
        assert winner == "Carlton"
        assert margin >= 1

    @pytest.mark.asyncio
    async def test_apply_confidence_and_margin_via_home_margin_to_tip(
        self, tiny_model_bytes
    ):
        """The learned path maps through home_margin_to_tip exactly:
        margin = max(1, round(|y_pred|)), conf = clamp(0.5 + |y_pred|*0.015)."""
        game = _make_game()
        preds = {"elo": ("Richmond", 0.7, 20)}
        self.heuristic.set_model(tiny_model_bytes)
        winner, conf, margin = await self.heuristic.apply(game, preds)

        y_pred = predict_home_margin_boosted(
            tiny_model_bytes,
            build_feature_vector(preds, "Richmond", "Carlton"),
        )
        expected = home_margin_to_tip(y_pred, "Richmond", "Carlton")
        assert (winner, conf, margin) == expected

    @pytest.mark.asyncio
    async def test_clear_model_reverts_to_fallback(self, tiny_model_bytes):
        game = _make_game()
        preds = {"elo": ("Richmond", 0.7, 20)}
        self.heuristic.set_model(tiny_model_bytes)
        winner_learned, conf_learned, _ = await self.heuristic.apply(game, preds)
        assert winner_learned == "Richmond"  # learned path, not the 0.55 fallback
        assert conf_learned != pytest.approx(0.55)

        self.heuristic.clear_model()
        winner, conf, margin = await self.heuristic.apply(game, preds)
        assert (winner, conf, margin) == weighted_tip_fallback(preds, "Richmond", "Carlton")

    @pytest.mark.asyncio
    async def test_set_clear_set_lifecycle(self, tiny_model_bytes):
        """set → fallback-independent; clear → fallback; set again → learned."""
        game = _make_game()
        preds = {"elo": ("Carlton", 0.7, 30)}

        # 1. No model → fallback majority vote → Carlton.
        assert (await self.heuristic.apply(game, preds))[0] == "Carlton"

        # 2. Set model → learned path (away-favouring input still → Carlton,
        #    but via y_pred, not votes).
        self.heuristic.set_model(tiny_model_bytes)
        assert (await self.heuristic.apply(game, preds))[0] == "Carlton"

        # 3. Clear → back to fallback.
        self.heuristic.clear_model()
        winner, conf, _ = await self.heuristic.apply(game, preds)
        assert winner == "Carlton"
        assert conf == pytest.approx(0.55)

        # 4. Set again → learned path resumes.
        self.heuristic.set_model(tiny_model_bytes)
        winner, conf, _ = await self.heuristic.apply(game, preds)
        assert conf != pytest.approx(0.55)

    @pytest.mark.asyncio
    async def test_empty_predictions_with_model_still_predicts(self, tiny_model_bytes):
        """A set model predicts on the all-zero feature vector; without a
        model the cold-start fallback applies (alphabetically first)."""
        game = _make_game()
        self.heuristic.set_model(tiny_model_bytes)
        winner, conf, margin = await self.heuristic.apply(game, {})
        # All-zero vector → y_pred ~ base value; mapping still yields a valid tip.
        assert margin >= 1
        assert 0.50 <= conf <= 0.95
        assert winner in ("Richmond", "Carlton")

    @pytest.mark.asyncio
    async def test_empty_predictions_without_model_cold_start_neutral(self):
        """Empty predictions, no model → alphabetically first team (0.55, 6)."""
        game = _make_game()  # min("Richmond", "Carlton") = "Carlton"
        winner, conf, margin = await self.heuristic.apply(game, {})
        assert winner == "Carlton"
        assert conf == pytest.approx(0.55)
        assert margin == 6

    @pytest.mark.asyncio
    async def test_empty_predictions_without_model_neutral_when_home_first(self):
        """Proves neutrality: home alphabetically FIRST → home picked."""
        game = _make_game(home_team="Adelaide", away_team="Brisbane")
        winner, conf, margin = await self.heuristic.apply(game, {})
        assert winner == "Adelaide"
        assert conf == pytest.approx(0.55)
        assert margin == 6

    def test_set_model_copies_bytes_defensively(self, tiny_model_bytes):
        """The stored artifact is a private immutable-ish bytes copy, so a
        later mutation of the caller's buffer cannot corrupt predictions."""
        buffer = bytearray(tiny_model_bytes)
        self.heuristic.set_model(bytes(buffer))
        buffer[0] = (buffer[0] + 1) % 256  # mutate after set
        assert self.heuristic._model_bytes == tiny_model_bytes


class TestSetLoadedModel:
    """BT-2 orchestrator contract: the TTL loader pushes an already-
    deserialized model; apply() predicts with the object directly."""

    def setup_method(self):
        self.heuristic = BoostedTipHeuristic(models=[])

    @staticmethod
    def _fake_model(prediction: float = 12.5) -> MagicMock:
        model = MagicMock()
        model.predict.return_value = [prediction]
        return model

    def test_set_loaded_model_stores_object_without_deserialization(self):
        fake_model = self._fake_model()
        with patch(
            "packages.shared.heuristics.boosted_tip.load_boosted_model"
        ) as mock_load:
            self.heuristic.set_loaded_model(fake_model)
        mock_load.assert_not_called()
        assert self.heuristic._loaded_model is fake_model
        assert self.heuristic._model_bytes is None

    @pytest.mark.asyncio
    async def test_apply_with_loaded_model_maps_through_home_margin_to_tip(self):
        game = _make_game()
        preds = {"elo": ("Richmond", 0.7, 20)}
        fake_model = self._fake_model(prediction=-7.5)
        self.heuristic.set_loaded_model(fake_model)

        winner, conf, margin = await self.heuristic.apply(game, preds)

        expected = home_margin_to_tip(-7.5, "Richmond", "Carlton")
        assert (winner, conf, margin) == expected
        assert winner == "Carlton"  # negative margin → away

    @pytest.mark.asyncio
    async def test_apply_with_loaded_model_passes_built_feature_vector(self):
        game = _make_game()
        preds = {"elo": ("Richmond", 0.7, 20)}
        fake_model = self._fake_model()
        self.heuristic.set_loaded_model(fake_model)

        await self.heuristic.apply(game, preds)

        expected = build_feature_vector(
            preds, "Richmond", "Carlton", model_names=self.heuristic.model_names
        )
        fake_model.predict.assert_called_once_with([expected])

    @pytest.mark.asyncio
    async def test_set_loaded_model_replaces_bytes_state(self, tiny_model_bytes):
        """set_model(bytes) then set_loaded_model(obj): the object wins and
        the bytes state is retired (single source of truth)."""
        game = _make_game()
        preds = {"elo": ("Richmond", 0.7, 20)}
        self.heuristic.set_model(tiny_model_bytes)
        fake_model = self._fake_model(prediction=42.0)
        self.heuristic.set_loaded_model(fake_model)

        assert self.heuristic._model_bytes is None
        winner, _conf, margin = await self.heuristic.apply(game, preds)
        # 42.0 could only have come from the fake object, not the real bytes.
        assert margin == home_margin_to_tip(42.0, "Richmond", "Carlton")[2]

    def test_set_model_after_loaded_model_clears_object(self, tiny_model_bytes):
        """set_loaded_model(obj) then set_model(bytes): bytes win."""
        self.heuristic.set_loaded_model(self._fake_model())
        self.heuristic.set_model(tiny_model_bytes)
        assert self.heuristic._loaded_model is None
        assert self.heuristic._model_bytes == tiny_model_bytes

    @pytest.mark.asyncio
    async def test_clear_model_clears_loaded_model(self):
        """clear_model reverts to the majority-vote fallback even from the
        preloaded-object state (orchestrator no-active-version path)."""
        game = _make_game()
        preds = {"elo": ("Carlton", 0.7, 30)}
        self.heuristic.set_loaded_model(self._fake_model(prediction=-100.0))
        self.heuristic.clear_model()

        winner, conf, _margin = await self.heuristic.apply(game, preds)
        assert (winner, conf) == weighted_tip_fallback(preds, "Richmond", "Carlton")[:2]


# ---------------------------------------------------------------------------
# Lazy heavy imports (S3 convention, BT-1)
# ---------------------------------------------------------------------------

class TestLazyHeavyImports:
    def _assert_import_does_not_load_heavy_libs(self, import_stmts: str) -> None:
        """Run *import_stmts* in a fresh interpreter and assert no heavy ML lib loads."""
        code = textwrap.dedent(
            f"""
            import sys
            {import_stmts}
            offenders = sorted(lib for lib in {_HEAVY_LIBS!r} if lib in sys.modules)
            assert not offenders, (
                "Importing the always-on path loaded heavy ML libs at module "
                f"load time: {{offenders}}"
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
            f"--- code ---\n{code}"
            f"--- stdout ---\n{result.stdout}"
            f"--- stderr ---\n{result.stderr}"
        )

    def test_importing_boosted_tip_module_does_not_load_heavy_libs(self):
        """BT-1: importing ``heuristics.boosted_tip`` must not pull
        xgboost/shap/sklearn/numpy — only the function bodies may."""
        self._assert_import_does_not_load_heavy_libs(
            "import packages.shared.heuristics.boosted_tip"
        )

    def test_importing_heuristics_package_does_not_load_heavy_libs(self):
        """The package ``__init__`` (which now exports BoostedTipHeuristic)
        is imported eagerly by the orchestrator — it must stay lean too."""
        self._assert_import_does_not_load_heavy_libs("import packages.shared.heuristics")

    def test_module_top_level_imports_are_light(self):
        """Static AST pin: no module-level import of a heavy ML lib in
        ``boosted_tip.py``.  Immune to sys.modules pollution from other tests."""
        source = Path(boosted_tip.__file__).read_text(encoding="utf-8")
        tree = ast.parse(source)
        for node in tree.body:  # module top level only
            if isinstance(node, ast.Import):
                for alias in node.names:
                    root = alias.name.split(".")[0]
                    assert root not in _HEAVY_LIBS, f"module-level import of {root}"
            elif isinstance(node, ast.ImportFrom):
                root = (node.module or "").split(".")[0]
                assert root not in _HEAVY_LIBS, f"module-level import of {root}"
