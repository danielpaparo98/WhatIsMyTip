"""Integration tests for the Backtest API (``/api/backtest``).

Covers the seven declared routes:

* ``GET  /api/backtest/``              — deprecated empty results stub
* ``GET  /api/backtest/compare``       — heuristic comparison for a season
* ``GET  /api/backtest/model-compare`` — model comparison for a season
* ``GET  /api/backtest/table``         — round-by-round table
* ``GET  /api/backtest/seasons``       — list available seasons
* ``GET  /api/backtest/current-season``— YTD performance
* ``POST /api/backtest/run``           — admin-only model backtest

The seed fixture inserts one ``BacktestResult`` row
(season=2025, round=1, best_bet, accuracy=1.0, profit=10.0) and one
``Game`` with a ``best_bet`` tip + elo prediction.

``POST /api/backtest/run`` is admin-gated (R2 in the inventory).
The real ``BacktestService.run_model_backtest`` walks every game
in the season and is slow — we stub it in the happy-path test
(matching the unit-test pattern in ``test_app_api_backtest.py``).
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from tests.integration.conftest import ADMIN_HEADERS


# ---------------------------------------------------------------------------
# GET /api/backtest/
# ---------------------------------------------------------------------------


class TestBacktestListStub:
    """``GET /api/backtest/`` — deprecated empty stub (R12)."""

    def test_backtest_list_returns_empty_stub(self, client):
        """Deprecated route always returns ``{results: [], count: 0}``."""
        resp = client.get("/api/backtest/")
        assert resp.status_code == 200
        body = resp.json()
        assert body == {"results": [], "count": 0}


# ---------------------------------------------------------------------------
# GET /api/backtest/compare
# ---------------------------------------------------------------------------


class TestBacktestCompare:
    """``GET /api/backtest/compare?season=<year>`` — heuristic comparison."""

    def test_compare_heuristics_season_with_data(self, client):
        """Returns per-heuristic stats + ``best_overall`` for a seeded season."""
        resp = client.get("/api/backtest/compare?season=2025")
        assert resp.status_code == 200
        body = resp.json()
        assert body["season"] == 2025
        assert "comparison" in body
        assert "best_overall" in body

        comparison = body["comparison"]
        # Seed includes best_bet.
        assert "best_bet" in comparison
        best = comparison["best_bet"]
        assert best["overall_accuracy"] == 1.0
        assert best["total_profit"] == 10.0
        assert best["total_tips"] == 1

        # ``best_overall`` points at the highest-accuracy heuristic.
        assert body["best_overall"]["heuristic"] == "best_bet"
        assert body["best_overall"]["accuracy"] == 1.0
        assert body["best_overall"]["profit"] == 10.0

    def test_compare_heuristics_empty_season_returns_zero_best(self, client):
        """A season with no backtest data returns zero-valued metrics.

        The service walks games in the season and joins them with tips;
        with no games, ``calculate_backtest_from_tips`` returns a dict
        of zero-valued metrics, so ``comparison`` is a dict of
        heuristic → zeros (one entry per available heuristic).  The
        route's ``if comparison:`` branch then picks the highest-
        accuracy heuristic — which happens to be ``best_bet`` with
        zeros, so ``best_overall`` carries that name with zero values.
        We pin the shape: every per-heuristic metric is zero.
        """
        resp = client.get("/api/backtest/compare?season=2099")
        assert resp.status_code == 200
        body = resp.json()
        assert body["season"] == 2099
        for heuristic_stats in body["comparison"].values():
            assert heuristic_stats["overall_accuracy"] == 0.0
            assert heuristic_stats["total_profit"] == 0.0
            assert heuristic_stats["total_tips"] == 0
        # best_overall always carries a numeric accuracy/profit pair.
        assert body["best_overall"]["accuracy"] == 0.0
        assert body["best_overall"]["profit"] == 0.0

    def test_compare_heuristics_missing_season_returns_422(self, client):
        """``season`` is required → 422 when missing."""
        resp = client.get("/api/backtest/compare")
        assert resp.status_code == 422

    def test_compare_heuristics_invalid_season_returns_422(self, client):
        """``season=1999`` is below the ge=2000 floor."""
        resp = client.get("/api/backtest/compare?season=1999")
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# GET /api/backtest/model-compare
# ---------------------------------------------------------------------------


class TestBacktestModelCompare:
    """``GET /api/backtest/model-compare?season=<year>`` — model comparison."""

    def test_compare_models_season(self, client):
        """Returns per-model stats + ``best_overall`` for the seeded season."""
        resp = client.get("/api/backtest/model-compare?season=2025")
        assert resp.status_code == 200
        body = resp.json()
        assert body["season"] == 2025
        assert "comparison" in body
        assert "best_overall" in body
        # comparison is a list (sorted desc by accuracy).
        assert isinstance(body["comparison"], list)

    def test_compare_models_missing_season_returns_422(self, client):
        """``season`` is required."""
        resp = client.get("/api/backtest/model-compare")
        assert resp.status_code == 422

    def test_compare_models_invalid_season_returns_422(self, client):
        """``season=1999`` is below the floor."""
        resp = client.get("/api/backtest/model-compare?season=1999")
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# GET /api/backtest/table
# ---------------------------------------------------------------------------


class TestBacktestTable:
    """``GET /api/backtest/table?season=<year>`` — round-by-round table."""

    def test_get_table_season_returns_seeded_round(self, client):
        """Returns ``season`` + ``heuristics`` list with the seeded round."""
        resp = client.get("/api/backtest/table?season=2025")
        assert resp.status_code == 200
        body = resp.json()
        assert body["season"] == 2025
        assert "heuristics" in body
        assert isinstance(body["heuristics"], list)
        # Each heuristic has the seeded round.
        if body["heuristics"]:
            for h in body["heuristics"]:
                assert h["season"] == 2025
                assert isinstance(h["rounds"], list)
                for r in h["rounds"]:
                    assert r["round_id"] == 1

    def test_get_table_missing_season_returns_422(self, client):
        """``season`` is required."""
        resp = client.get("/api/backtest/table")
        assert resp.status_code == 422

    def test_get_table_invalid_season_returns_422(self, client):
        """``season=1999`` is below the floor."""
        resp = client.get("/api/backtest/table?season=1999")
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# GET /api/backtest/seasons
# ---------------------------------------------------------------------------


class TestBacktestSeasons:
    """``GET /api/backtest/seasons`` — list available seasons."""

    def test_get_seasons_returns_seeded_year(self, client):
        """The seeded season appears in ``available_years``."""
        resp = client.get("/api/backtest/seasons")
        assert resp.status_code == 200
        body = resp.json()
        assert "available_years" in body
        assert "current_year" in body
        assert 2025 in body["available_years"]
        # current_year is just ``datetime.now().year``.
        assert body["current_year"] >= 2026

    def test_get_seasons_descending_order(self, client):
        """``available_years`` is returned in descending order."""
        resp = client.get("/api/backtest/seasons")
        body = resp.json()
        years = body["available_years"]
        assert years == sorted(years, reverse=True)


# ---------------------------------------------------------------------------
# GET /api/backtest/current-season
# ---------------------------------------------------------------------------


class TestBacktestCurrentSeason:
    """``GET /api/backtest/current-season`` — YTD performance."""

    def test_get_current_season_returns_shape(self, client):
        """Returns a ``CurrentSeasonResponse`` shape."""
        resp = client.get("/api/backtest/current-season")
        assert resp.status_code == 200
        body = resp.json()
        # The schema has ``season``, ``heuristics``, ``rounds_completed``,
        # ``total_rounds``.  The exact values depend on the current
        # season config — we only assert the shape.
        assert "season" in body
        assert "heuristics" in body
        assert isinstance(body["heuristics"], list)
        assert "rounds_completed" in body
        assert "total_rounds" in body


# ---------------------------------------------------------------------------
# POST /api/backtest/run  (admin-only — R2)
# ---------------------------------------------------------------------------


class TestBacktestRunAuth:
    """``POST /api/backtest/run`` — admin-only auth contract."""

    def test_run_backtest_without_api_key_returns_401(self, client):
        """No ``X-API-Key`` → 401 ``invalid_api_key``."""
        resp = client.post(
            "/api/backtest/run",
            json={"season": 2025},
        )
        assert resp.status_code == 401
        body = resp.json()
        assert body["code"] == "invalid_api_key"

    def test_run_backtest_with_wrong_api_key_returns_401(self, client):
        """Wrong key → 401 (constant-time compare)."""
        resp = client.post(
            "/api/backtest/run",
            json={"season": 2025},
            headers={"X-API-Key": "wrong-key"},
        )
        assert resp.status_code == 401

    def test_run_backtest_with_empty_api_key_returns_401(self, client):
        """Empty key → 401."""
        resp = client.post(
            "/api/backtest/run",
            json={"season": 2025},
            headers={"X-API-Key": ""},
        )
        assert resp.status_code == 401


class TestBacktestRunValidation:
    """``POST /api/backtest/run`` — body validation contract."""

    def test_run_backtest_empty_body_returns_422(self, client):
        """Missing required ``season`` → 422 ``validation_error``."""
        resp = client.post(
            "/api/backtest/run",
            json={},
            headers=ADMIN_HEADERS,
        )
        assert resp.status_code == 422
        body = resp.json()
        assert body["code"] == "validation_error"

    def test_run_backtest_invalid_season_returns_422(self, client):
        """``season=1999`` violates Pydantic ``ge=2000``."""
        resp = client.post(
            "/api/backtest/run",
            json={"season": 1999},
            headers=ADMIN_HEADERS,
        )
        assert resp.status_code == 422

    def test_run_backtest_round_below_one_returns_422(self, client):
        """``round_id=0`` violates Pydantic ``ge=1`` (the field is aliased)."""
        resp = client.post(
            "/api/backtest/run",
            json={"season": 2025, "round_id": 0},
            headers=ADMIN_HEADERS,
        )
        assert resp.status_code == 422


class TestBacktestRunSuccess:
    """``POST /api/backtest/run`` — 200 happy-path with stubbed service.

    The real ``BacktestService.run_model_backtest`` walks every game in
    the season and can take minutes.  We stub it to keep this test fast
    and deterministic (matching the unit-test pattern).
    """

    def test_run_backtest_returns_200_with_contract_shape(self, client):
        """Happy path: 200 with ``{season, round, heuristic, count, results}``."""
        fake_results = [
            {"game_id": 1, "winner": "Brisbane", "correct": True},
        ]

        with patch("app.api.backtest.BacktestService") as mock_service_cls:
            mock_service = mock_service_cls.return_value
            mock_service.run_model_backtest = AsyncMock(return_value=fake_results)

            resp = client.post(
                "/api/backtest/run",
                json={"season": 2025},
                headers=ADMIN_HEADERS,
            )

        assert resp.status_code == 200
        body = resp.json()
        assert body["season"] == 2025
        assert body["round"] is None  # not supplied in the request
        assert body["heuristic"] is None
        assert body["count"] == 1
        assert body["results"] == fake_results

        # The service was called with the right season.
        mock_service.run_model_backtest.assert_awaited_once()
        call_args = mock_service.run_model_backtest.call_args
        assert call_args.kwargs["season"] == 2025

    def test_run_backtest_with_round_and_heuristic(self, client):
        """``round_id`` and ``heuristic`` are echoed back even though unused."""
        with patch("app.api.backtest.BacktestService") as mock_service_cls:
            mock_service = mock_service_cls.return_value
            mock_service.run_model_backtest = AsyncMock(return_value=[])

            resp = client.post(
                "/api/backtest/run",
                json={"season": 2025, "round_id": 1, "heuristic": "best_bet"},
                headers=ADMIN_HEADERS,
            )

        assert resp.status_code == 200
        body = resp.json()
        assert body["round"] == 1
        assert body["heuristic"] == "best_bet"
        assert body["count"] == 0

    def test_run_backtest_includes_request_id(self, client):
        """The 200 response carries the request_id."""
        with patch("app.api.backtest.BacktestService") as mock_service_cls:
            mock_service = mock_service_cls.return_value
            mock_service.run_model_backtest = AsyncMock(return_value=[])

            resp = client.post(
                "/api/backtest/run",
                json={"season": 2025},
                headers=ADMIN_HEADERS,
            )

        assert resp.status_code == 200
        assert "X-Request-ID" in resp.headers
