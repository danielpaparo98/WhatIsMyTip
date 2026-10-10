"""Unit tests for the league-aware backtest API routes (D4).

Dispatch contract under test:

* absent / empty / ``afl`` league  → legacy AFL ``BacktestService`` path,
  byte-identical (int seasons, calendar-year ``current_year``, native
  ``int, ge=2000`` validation) — the multisport tables are never touched;
* registered state-league key      → ``LeagueBacktestService`` with the
  competition resolved by canonical ``competitions.name`` (the league
  registries ↔ frontend ``LEAGUE_COMPETITION_NAMES`` contract), season
  passed as a LABEL string on ``/compare``;
* national rugby-league key (``nrl``/``nrlw``/``origin``) → the same
  league path through the cross-registry ``get_league`` facade, with the
  reduced per-sport model set (GUARDRAIL FLIP, Phase 5.2: these keys
  used to 404 as state-registry unknowns — flipped in the same change
  that made them valid; detailed coverage in
  ``test_league_backtest_rugby_league.py``);
* unknown key                      → 404 repo-standard error shape;
* registered but never synced      → the service's own graceful zero
  payloads (empty ``available_years`` / zeroed heuristics) — never a 500.

The zero-payload shapes themselves are proven in
``test_league_backtest_service.py``; here the unsynced cases run the REAL
service against an empty-result DB mock so the API-level "never a 500"
guarantee is end-to-end.
"""

from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from packages.shared.services.league_heuristics import (
    LEAGUE_HEURISTICS,
    RUGBY_LEAGUE_MODELS,
)

# ---------------------------------------------------------------------------
# Helpers (same app-builder convention as test_app_api_backtest.py)
# ---------------------------------------------------------------------------


def _build_app_with_backtest_router(monkeypatch=None):
    """Build a minimal FastAPI app with the backtest router + handlers."""
    from fastapi.exceptions import RequestValidationError
    from fastapi.responses import JSONResponse

    from app.api.backtest import router
    from app.core.exceptions import BackendServiceError

    app = FastAPI()
    app.include_router(router, prefix="/api/backtest")

    @app.exception_handler(BackendServiceError)
    async def _backend_error_handler(_request, exc: BackendServiceError):
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "code": exc.code,
                "message": exc.message,
                "details": exc.details,
                "request_id": "test-request-id",
            },
        )

    @app.exception_handler(RequestValidationError)
    async def _validation_error_handler(_request, exc: RequestValidationError):
        return JSONResponse(
            status_code=422,
            content={
                "code": "validation_error",
                "message": "Invalid request",
                "errors": exc.errors(),
                "request_id": "test-request-id",
            },
        )

    if monkeypatch is not None:
        from packages.shared.config import settings
        monkeypatch.setattr(settings, "admin_api_key", "the-secret-key")

    return app


def _override_db(app, mock_session: AsyncSession) -> None:
    from app.core import db_deps

    async def _override():
        yield mock_session

    app.dependency_overrides[db_deps.get_db] = _override


class _ScalarResult:
    """``execute()`` result resolving one scalar (the competition lookup)."""

    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


class _EmptyResult:
    """``execute()`` result with no rows — the unsynced-competition case."""

    def scalar_one_or_none(self):
        return None

    def scalar(self):
        return 0

    def all(self):
        return []


def _session_returning(value) -> AsyncMock:
    """AsyncSession mock whose ``execute`` always resolves ``value``."""
    session = AsyncMock(spec=AsyncSession)
    session.execute = AsyncMock(return_value=value)
    return session


# ---------------------------------------------------------------------------
# Legacy dispatch — absent / empty / 'afl' league is byte-identical AFL
# ---------------------------------------------------------------------------


class TestLegacyDispatch:
    """The legacy AFL path is untouched by the league param."""

    def test_seasons_no_league_uses_legacy_service(self):
        mock_session = AsyncMock(spec=AsyncSession)
        app = _build_app_with_backtest_router()
        _override_db(app, mock_session)

        with patch("app.api.backtest.BacktestService") as legacy_cls, patch(
            "app.api.backtest.LeagueBacktestService"
        ) as league_cls:
            legacy_cls.return_value.get_available_seasons = AsyncMock(
                return_value=[2024, 2025]
            )
            client = TestClient(app)
            resp = client.get("/api/backtest/seasons")

        assert resp.status_code == 200
        body = resp.json()
        # AFL seasons stay calendar-year ints.
        assert body["available_years"] == [2024, 2025]
        assert body["current_year"] == datetime.now().year
        league_cls.return_value.get_available_seasons.assert_not_called()
        mock_session.execute.assert_not_called()

    def test_seasons_league_afl_uses_legacy_service(self):
        mock_session = AsyncMock(spec=AsyncSession)
        app = _build_app_with_backtest_router()
        _override_db(app, mock_session)

        with patch("app.api.backtest.BacktestService") as legacy_cls, patch(
            "app.api.backtest.LeagueBacktestService"
        ) as league_cls:
            legacy_cls.return_value.get_available_seasons = AsyncMock(
                return_value=[2025]
            )
            client = TestClient(app)
            resp = client.get("/api/backtest/seasons?league=afl")

        assert resp.status_code == 200
        assert resp.json()["available_years"] == [2025]
        league_cls.return_value.get_available_seasons.assert_not_called()
        mock_session.execute.assert_not_called()

    def test_seasons_empty_league_uses_legacy_service(self):
        mock_session = AsyncMock(spec=AsyncSession)
        app = _build_app_with_backtest_router()
        _override_db(app, mock_session)

        with patch("app.api.backtest.BacktestService") as legacy_cls, patch(
            "app.api.backtest.LeagueBacktestService"
        ) as league_cls:
            legacy_cls.return_value.get_available_seasons = AsyncMock(
                return_value=[]
            )
            client = TestClient(app)
            resp = client.get("/api/backtest/seasons?league=")

        assert resp.status_code == 200
        assert resp.json()["available_years"] == []
        league_cls.return_value.get_available_seasons.assert_not_called()
        mock_session.execute.assert_not_called()

    def test_current_season_league_afl_uses_legacy_service(self):
        from packages.shared.schemas.backtest import (
            CurrentSeasonHeuristicPerformance,
            CurrentSeasonResponse,
        )

        mock_session = AsyncMock(spec=AsyncSession)
        mock_performance = CurrentSeasonResponse(
            season=2025,
            heuristics=[
                CurrentSeasonHeuristicPerformance(
                    heuristic="best_bet",
                    total_profit=30.0,
                    total_accuracy=0.7,
                    rounds_played=5,
                    avg_profit_per_round=6.0,
                    projected_annual_profit=144.0,
                )
            ],
            rounds_completed=5,
            total_rounds=24,
        )
        app = _build_app_with_backtest_router()
        _override_db(app, mock_session)

        with patch("app.api.backtest.BacktestService") as legacy_cls, patch(
            "app.api.backtest.LeagueBacktestService"
        ) as league_cls:
            legacy_cls.return_value.get_current_season_performance = AsyncMock(
                return_value=mock_performance
            )
            client = TestClient(app)
            resp = client.get("/api/backtest/current-season?league=afl")

        assert resp.status_code == 200
        body = resp.json()
        assert body["season"] == 2025
        assert body["rounds_completed"] == 5
        league_cls.return_value.get_current_season_performance.assert_not_called()
        mock_session.execute.assert_not_called()

    def test_compare_league_afl_keeps_int_season(self):
        mock_session = AsyncMock(spec=AsyncSession)
        mock_comparison = {
            "best_bet": {
                "total_rounds": 5,
                "total_tips": 30,
                "total_correct": 21,
                "overall_accuracy": 0.7,
                "total_profit": 30.0,
                "avg_profit_per_round": 6.0,
                "best_round_accuracy": 0.83,
                "worst_round_accuracy": 0.6,
            },
        }
        app = _build_app_with_backtest_router()
        _override_db(app, mock_session)

        with patch("app.api.backtest.BacktestService") as legacy_cls, patch(
            "app.api.backtest.LeagueBacktestService"
        ) as league_cls:
            legacy_cls.return_value.compare_heuristics = AsyncMock(
                return_value=mock_comparison
            )
            client = TestClient(app)
            resp = client.get("/api/backtest/compare?league=afl&season=2025")

        assert resp.status_code == 200
        body = resp.json()
        assert body["season"] == 2025
        assert body["best_overall"]["heuristic"] == "best_bet"
        # The int reached the legacy service exactly as before.
        legacy_cls.return_value.compare_heuristics.assert_awaited_once_with(
            mock_session, 2025
        )
        league_cls.return_value.compare_season.assert_not_called()
        mock_session.execute.assert_not_called()

    def test_compare_no_league_missing_season_returns_422(self):
        app = _build_app_with_backtest_router()
        _override_db(app, AsyncMock(spec=AsyncSession))
        client = TestClient(app)
        resp = client.get("/api/backtest/compare")

        assert resp.status_code == 422

    def test_compare_league_afl_rejects_non_numeric_season(self):
        app = _build_app_with_backtest_router()
        _override_db(app, AsyncMock(spec=AsyncSession))
        client = TestClient(app)
        resp = client.get("/api/backtest/compare?league=afl&season=abc")

        assert resp.status_code == 422

    def test_compare_league_afl_rejects_pre_2000_season(self):
        app = _build_app_with_backtest_router()
        _override_db(app, AsyncMock(spec=AsyncSession))
        client = TestClient(app)
        resp = client.get("/api/backtest/compare?league=afl&season=1999")

        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# GET /seasons?league=
# ---------------------------------------------------------------------------


class TestLeagueSeasons:
    """``GET /api/backtest/seasons?league=`` — D4 label payload."""

    def test_seasons_for_synced_league(self):
        payload = {"available_years": ["2025", "2024"], "current_year": "2025"}
        session = _session_returning(_ScalarResult(42))
        app = _build_app_with_backtest_router()
        _override_db(app, session)

        with patch("app.api.backtest.LeagueBacktestService") as league_cls:
            league_cls.return_value.get_available_seasons = AsyncMock(
                return_value=payload
            )
            client = TestClient(app)
            resp = client.get("/api/backtest/seasons?league=wafl")

        assert resp.status_code == 200
        assert resp.json() == payload
        league_cls.return_value.get_available_seasons.assert_awaited_once_with(
            session, competition_id=42
        )

    def test_seasons_unsynced_league_returns_empty_payload(self):
        # Registered but never synced: the REAL service runs against an
        # empty DB and its own empty-state machinery answers — no 500.
        session = _session_returning(_EmptyResult())
        app = _build_app_with_backtest_router()
        _override_db(app, session)

        client = TestClient(app)
        resp = client.get("/api/backtest/seasons?league=wafl")

        assert resp.status_code == 200
        assert resp.json() == {"available_years": [], "current_year": None}

    def test_seasons_unknown_league_returns_404(self):
        mock_session = AsyncMock(spec=AsyncSession)
        app = _build_app_with_backtest_router()
        _override_db(app, mock_session)

        with patch("app.api.backtest.LeagueBacktestService") as league_cls:
            client = TestClient(app)
            resp = client.get("/api/backtest/seasons?league=not-a-league")

        assert resp.status_code == 404
        body = resp.json()
        assert body["code"] == "not_found"
        assert "not-a-league" in body["message"].lower()
        league_cls.return_value.get_available_seasons.assert_not_called()
        mock_session.execute.assert_not_called()

    def test_seasons_league_keys_are_case_sensitive(self):
        mock_session = AsyncMock(spec=AsyncSession)
        app = _build_app_with_backtest_router()
        _override_db(app, mock_session)

        with patch("app.api.backtest.LeagueBacktestService"):
            client = TestClient(app)
            resp = client.get("/api/backtest/seasons?league=WAFL")

        assert resp.status_code == 404
        assert resp.json()["code"] == "not_found"


# ---------------------------------------------------------------------------
# GET /current-season?league=
# ---------------------------------------------------------------------------


class TestLeagueCurrentSeason:
    """``GET /api/backtest/current-season?league=`` — AFL shape, labels."""

    def test_current_season_for_synced_league(self):
        payload = {
            "season": "2025",
            "heuristics": [
                {
                    "heuristic": heuristic,
                    "total_profit": 10.0,
                    "total_accuracy": 0.6,
                    "rounds_played": 2,
                    "avg_profit_per_round": 5.0,
                    "projected_annual_profit": 95.0,
                    "odds_coverage": 0.0,
                }
                for heuristic in LEAGUE_HEURISTICS
            ],
            "rounds_completed": 2,
            "total_rounds": 20,
        }
        session = _session_returning(_ScalarResult(7))
        app = _build_app_with_backtest_router()
        _override_db(app, session)

        with patch("app.api.backtest.LeagueBacktestService") as league_cls:
            league_cls.return_value.get_current_season_performance = AsyncMock(
                return_value=payload
            )
            client = TestClient(app)
            resp = client.get("/api/backtest/current-season?league=vfl")

        assert resp.status_code == 200
        body = resp.json()
        # Same shape the AFL current-season payload renders, str-labelled.
        assert body["season"] == "2025"
        assert len(body["heuristics"]) == len(LEAGUE_HEURISTICS)
        assert body["rounds_completed"] == 2
        assert body["total_rounds"] == 20
        league_cls.return_value.get_current_season_performance.assert_awaited_once_with(
            session, competition_id=7, models=None
        )

    def test_current_season_unsynced_league_zero_payload(self):
        session = _session_returning(_EmptyResult())
        app = _build_app_with_backtest_router()
        _override_db(app, session)

        client = TestClient(app)
        resp = client.get("/api/backtest/current-season?league=wafl")

        assert resp.status_code == 200
        body = resp.json()
        assert body["season"] is None
        assert body["rounds_completed"] == 0
        assert body["total_rounds"] == 0
        assert [h["heuristic"] for h in body["heuristics"]] == list(
            LEAGUE_HEURISTICS
        )
        # Zeroed current-season entries (the CurrentSeasonResponse shape —
        # note: rounds_played, not the comparison shape's total_tips).
        assert all(
            h["rounds_played"] == 0 and h["total_profit"] == 0.0
            for h in body["heuristics"]
        )

    def test_current_season_unknown_league_returns_404(self):
        mock_session = AsyncMock(spec=AsyncSession)
        app = _build_app_with_backtest_router()
        _override_db(app, mock_session)

        with patch("app.api.backtest.LeagueBacktestService") as league_cls:
            client = TestClient(app)
            resp = client.get("/api/backtest/current-season?league=not-a-league")

        assert resp.status_code == 404
        assert resp.json()["code"] == "not_found"
        league_cls.return_value.get_current_season_performance.assert_not_called()
        mock_session.execute.assert_not_called()


# ---------------------------------------------------------------------------
# GET /compare?league=&season=
# ---------------------------------------------------------------------------


class TestLeagueCompare:
    """``GET /api/backtest/compare?league=&season=`` — season is a LABEL."""

    def test_compare_with_league_season_label(self):
        payload = {
            "season": "2025",
            "comparison": {
                "home_advantage": {
                    "total_rounds": 3,
                    "total_tips": 30,
                    "total_correct": 18,
                    "overall_accuracy": 0.6,
                    "total_profit": 14.0,
                    "avg_profit_per_round": 4.67,
                    "best_round_accuracy": 0.8,
                    "worst_round_accuracy": 0.4,
                    "odds_coverage": 0.0,
                },
            },
            "best_overall": {
                "heuristic": "home_advantage",
                "accuracy": 0.6,
                "profit": 14.0,
            },
        }
        session = _session_returning(_ScalarResult(42))
        app = _build_app_with_backtest_router()
        _override_db(app, session)

        with patch("app.api.backtest.LeagueBacktestService") as league_cls:
            league_cls.return_value.compare_season = AsyncMock(
                return_value=payload
            )
            client = TestClient(app)
            resp = client.get("/api/backtest/compare?league=wafl&season=2025")

        assert resp.status_code == 200
        body = resp.json()
        assert body["season"] == "2025"
        assert body["best_overall"]["heuristic"] == "home_advantage"
        league_cls.return_value.compare_season.assert_awaited_once_with(
            session, competition_id=42, season_label="2025", models=None
        )

    def test_compare_non_numeric_label_passes_through(self):
        payload = {
            "season": "2025-26",
            "comparison": {},
            "best_overall": {"heuristic": None, "accuracy": 0.0, "profit": 0.0},
        }
        session = _session_returning(_ScalarResult(42))
        app = _build_app_with_backtest_router()
        _override_db(app, session)

        with patch("app.api.backtest.LeagueBacktestService") as league_cls:
            league_cls.return_value.compare_season = AsyncMock(
                return_value=payload
            )
            client = TestClient(app)
            resp = client.get(
                "/api/backtest/compare?league=wafl&season=2025-26"
            )

        assert resp.status_code == 200
        assert resp.json()["season"] == "2025-26"
        league_cls.return_value.compare_season.assert_awaited_once_with(
            session, competition_id=42, season_label="2025-26", models=None
        )

    def test_compare_unsynced_league_zero_comparison(self):
        session = _session_returning(_EmptyResult())
        app = _build_app_with_backtest_router()
        _override_db(app, session)

        client = TestClient(app)
        resp = client.get("/api/backtest/compare?league=wafl&season=2025")

        assert resp.status_code == 200
        body = resp.json()
        assert body["season"] == "2025"
        assert body["best_overall"] == {
            "heuristic": None,
            "accuracy": 0.0,
            "profit": 0.0,
        }
        assert set(body["comparison"]) == set(LEAGUE_HEURISTICS)
        assert all(
            stats["total_tips"] == 0 for stats in body["comparison"].values()
        )

    def test_compare_missing_season_returns_422_for_league(self):
        app = _build_app_with_backtest_router()
        _override_db(app, AsyncMock(spec=AsyncSession))
        client = TestClient(app)
        resp = client.get("/api/backtest/compare?league=wafl")

        assert resp.status_code == 422

    def test_compare_unknown_league_returns_404(self):
        mock_session = AsyncMock(spec=AsyncSession)
        app = _build_app_with_backtest_router()
        _override_db(app, mock_session)

        with patch("app.api.backtest.LeagueBacktestService") as league_cls, patch(
            "app.api.backtest.BacktestService"
        ) as legacy_cls:
            client = TestClient(app)
            resp = client.get(
                "/api/backtest/compare?league=not-a-league&season=2025"
            )

        assert resp.status_code == 404
        assert resp.json()["code"] == "not_found"
        league_cls.return_value.compare_season.assert_not_called()
        legacy_cls.return_value.compare_heuristics.assert_not_called()
        mock_session.execute.assert_not_called()


# ---------------------------------------------------------------------------
# GUARDRAIL FLIP (Phase 5.2): the national rugby-league keys are VALID.
# ---------------------------------------------------------------------------


class TestNationalLeagueGuardrailFlip:
    """``league=nrl|nrlw|origin`` resolves through the cross-registry
    ``get_league`` facade and dispatches to the league service with the
    reduced per-sport model set.  These keys 404'd before the Phase 5.2
    backtest change; this flip lands in the SAME change that makes them
    valid.  Full behavioural coverage (unsynced zero payloads, season
    labels, e2e over the normalized history) lives in
    ``test_league_backtest_rugby_league.py``."""

    @pytest.mark.parametrize("league", ["nrl", "nrlw", "origin"])
    def test_national_league_keys_are_valid(self, league):
        session = _session_returning(_ScalarResult(42))
        app = _build_app_with_backtest_router()
        _override_db(app, session)

        with patch("app.api.backtest.LeagueBacktestService") as league_cls:
            league_cls.return_value.get_current_season_performance = AsyncMock(
                return_value={"season": "2026", "heuristics": []}
            )
            client = TestClient(app)
            resp = client.get(f"/api/backtest/current-season?league={league}")

        assert resp.status_code == 200
        # ...and grades the REDUCED rugby-league model set, not the D3 trio.
        league_cls.return_value.get_current_season_performance.assert_awaited_once_with(
            session, competition_id=42, models=RUGBY_LEAGUE_MODELS
        )


# ---------------------------------------------------------------------------
# Registry-contract sanity: the API resolves via the league registries
# ---------------------------------------------------------------------------


class TestRegistryContract:
    """The resolution source is the league registries via the facade."""

    def test_every_registered_key_resolves_to_a_distinct_name(self):
        from packages.shared.ingestion.national_leagues import NATIONAL_LEAGUES
        from packages.shared.ingestion.state_leagues import STATE_LEAGUES

        names = [config.name for config in STATE_LEAGUES.values()]
        assert len(names) == len(set(names))
        # The API resolves by canonical competitions.name WITHOUT sport
        # scoping, so a cross-registry name clash would collide the two
        # competitions (surfacing as the 404 multiple-results guard).
        names += [config.name for config in NATIONAL_LEAGUES.values()]
        assert len(names) == len(set(names))
        # And the frontend-facing mapping is the same contract.
        assert STATE_LEAGUES["wafl"].name == "West Australian Football League"

    def test_national_keys_resolve_through_the_facade(self):
        from packages.shared.ingestion.national_leagues import get_league

        for key in ("nrl", "nrlw", "origin"):
            assert get_league(key).name == {
                "nrl": "National Rugby League",
                "nrlw": "NRL Women's Premiership",
                "origin": "State of Origin",
            }[key]
