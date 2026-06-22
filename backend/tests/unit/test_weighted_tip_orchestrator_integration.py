"""Integration-style unit tests for weighted-tip coefficient injection.

The :class:`~packages.shared.orchestrator.ModelOrchestrator` owns the db
session, so it is responsible for loading the active ``weighted_tip``
model version's coefficients and pushing them into the
:class:`WeightedTipHeuristic` (whose ``apply`` signature has no db).

These tests pin that wiring contract:

* ``_ensure_weighted_tip_coefficients`` reads the active version via
  :func:`get_active_coefficients` and calls ``set_coefficients`` /
  ``clear_coefficients`` on the heuristic.
* A TTL cache avoids re-reading the DB on every tip; after the TTL the
  weights are reloaded.
* A DB error must never crash tip generation — the heuristic stays on
  its majority-vote fallback.
* ``predict`` and ``predict_all`` invoke the loader before applying any
  heuristic.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from packages.shared.orchestrator import ModelOrchestrator


def _make_game(home_team="Richmond", away_team="Carlton"):
    game = MagicMock()
    game.id = 1
    game.home_team = home_team
    game.away_team = away_team
    return game


# ---------------------------------------------------------------------------
# _ensure_weighted_tip_coefficients
# ---------------------------------------------------------------------------

class TestEnsureWeightedTipCoefficients:
    def setup_method(self):
        self.orch = ModelOrchestrator()

    @pytest.mark.asyncio
    async def test_sets_coefficients_when_active_version_exists(self):
        coeffs = {"elo_margin_home": 0.5, "elo_conf": 1.0}
        mock_get = AsyncMock(return_value=(2.0, coeffs))
        db = MagicMock()
        with patch(
            "packages.shared.orchestrator.get_active_coefficients", new=mock_get
        ):
            await self.orch._ensure_weighted_tip_coefficients(db)

        mock_get.assert_awaited_once_with(db, "weighted_tip")
        wt = self.orch.heuristics["weighted_tip"]
        assert wt._intercept == 2.0
        assert wt._coefficients == coeffs

    @pytest.mark.asyncio
    async def test_clears_coefficients_when_no_active_version(self):
        # Start from a known "trained" state to prove the clear path runs.
        self.orch.heuristics["weighted_tip"].set_coefficients(
            1.0, {"elo_margin_home": 1.0}
        )
        with patch(
            "packages.shared.orchestrator.get_active_coefficients",
            new=AsyncMock(return_value=None),
        ):
            await self.orch._ensure_weighted_tip_coefficients(MagicMock())

        wt = self.orch.heuristics["weighted_tip"]
        assert wt._intercept is None
        assert wt._coefficients is None

    @pytest.mark.asyncio
    async def test_ttl_caches_within_window(self):
        coeffs = {"elo_margin_home": 0.5}
        mock_get = AsyncMock(return_value=(1.0, coeffs))
        with patch(
            "packages.shared.orchestrator.get_active_coefficients", new=mock_get
        ):
            await self.orch._ensure_weighted_tip_coefficients(MagicMock())
            await self.orch._ensure_weighted_tip_coefficients(MagicMock())

        # Second call is served from the in-memory cache.
        assert mock_get.await_count == 1

    @pytest.mark.asyncio
    async def test_ttl_reloads_after_expiry(self):
        from packages.shared.orchestrator import (
            WEIGHTED_TIP_COEFFICIENT_TTL_SECONDS,
        )
        import time as _time

        coeffs = {"elo_margin_home": 0.5}
        mock_get = AsyncMock(return_value=(1.0, coeffs))
        with patch(
            "packages.shared.orchestrator.get_active_coefficients", new=mock_get
        ):
            await self.orch._ensure_weighted_tip_coefficients(MagicMock())
            # Rewind the load timestamp past the TTL window.
            self.orch._wt_coeffs_loaded_at = (
                _time.monotonic() - (WEIGHTED_TIP_COEFFICIENT_TTL_SECONDS + 1)
            )
            await self.orch._ensure_weighted_tip_coefficients(MagicMock())

        assert mock_get.await_count == 2

    @pytest.mark.asyncio
    async def test_db_error_keeps_fallback_and_does_not_raise(self):
        mock_get = AsyncMock(side_effect=RuntimeError("db down"))
        with patch(
            "packages.shared.orchestrator.get_active_coefficients", new=mock_get
        ):
            # Must not raise — tip generation must survive a model-load failure.
            await self.orch._ensure_weighted_tip_coefficients(MagicMock())

        wt = self.orch.heuristics["weighted_tip"]
        # Never loaded → heuristic stays on the majority-vote fallback.
        assert wt._coefficients is None
        assert wt._intercept is None


# ---------------------------------------------------------------------------
# predict / predict_all invoke the loader
# ---------------------------------------------------------------------------

class TestPredictInvokesLoader:
    """Both prediction entry points refresh coefficients before applying heuristics."""

    def setup_method(self):
        self.orch = ModelOrchestrator()
        # Empty model list → predict runs no DB-bound model queries, so we can
        # isolate the coefficient-injection behaviour cheaply.
        self.orch.models = []

    @pytest.mark.asyncio
    async def test_predict_calls_ensure(self):
        with patch.object(
            self.orch,
            "_ensure_weighted_tip_coefficients",
            new=AsyncMock(),
        ) as mock_ensure:
            await self.orch.predict(_make_game(), "weighted_tip", db=MagicMock())
            mock_ensure.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_predict_all_calls_ensure(self):
        with patch.object(
            self.orch,
            "_ensure_weighted_tip_coefficients",
            new=AsyncMock(),
        ) as mock_ensure:
            await self.orch.predict_all(_make_game(), db=MagicMock())
            mock_ensure.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_predict_with_unknown_heuristic_still_loads_then_raises(self):
        """The loader runs first (harmless for an invalid heuristic), then the
        validity check raises ValueError — exactly as before the rename."""
        with patch.object(
            self.orch,
            "_ensure_weighted_tip_coefficients",
            new=AsyncMock(),
        ) as mock_ensure:
            with pytest.raises(ValueError):
                await self.orch.predict(_make_game(), "not_a_heuristic", db=None)
            mock_ensure.assert_awaited_once()
