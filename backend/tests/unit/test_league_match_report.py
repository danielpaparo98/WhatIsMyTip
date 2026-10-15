"""Unit tests for the league grand-final pre-match report (PARITY-1).

Covers the detection gate (the league twin of
``models_ml.neutral.is_grand_final``), the pure section builders, the
strict-JSON editorial client contract (fallback on ANY failure), the
service's generation gates + deterministic fallback assembly, the
``GET /api/events/{slug}/report`` endpoint contract, and the sweep's
report step.
"""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from packages.shared.services.league_heuristics import CompletedEvent
from packages.shared.services.league_match_report import (
    LeagueMatchReportService,
    LeagueReportOpenRouterClient,
    _fallback_editorial,
    _report_user_prompt,
    _result_phrase,
    _side_finals_path,
    is_league_grand_final,
)

_KICKOFF = datetime(2026, 9, 26, 14, 40)


def _event(
    event_id: int,
    *,
    season_id: int = 1,
    round_id: int | None = 5,
    starts_at: datetime | None = _KICKOFF,
    home_pid: int | None = 11,
    away_pid: int | None = 12,
    home_score: int | None = None,
    away_score: int | None = None,
    completed: bool = False,
) -> CompletedEvent:
    return CompletedEvent(
        event_id=event_id,
        season_id=season_id,
        starts_at=starts_at,
        home_side_id=(101 if home_pid is not None else None),
        away_side_id=(102 if away_pid is not None else None),
        home_participant_id=home_pid,
        away_participant_id=away_pid,
        home_score=home_score,
        away_score=away_score,
        completed=completed,
        venue="Test Oval",
        round_id=round_id,
    )


# ---------------------------------------------------------------------------
# Detection gate
# ---------------------------------------------------------------------------


class TestIsLeagueGrandFinal:
    @pytest.mark.asyncio
    async def test_final_round_of_a_complete_fixture_is_the_grand_final(self):
        db = MagicMock()
        db.execute = AsyncMock(
            side_effect=[
                SimpleNamespace(first=lambda: (5, 1)),  # (round_id, season_id)
                SimpleNamespace(one=lambda: (5, 5)),  # (max, distinct)
            ]
        )
        assert await is_league_grand_final(db, event_id=501) is True

    @pytest.mark.asyncio
    async def test_mid_season_round_is_not(self):
        db = MagicMock()
        db.execute = AsyncMock(
            side_effect=[
                SimpleNamespace(first=lambda: (3, 1)),
                SimpleNamespace(one=lambda: (5, 5)),
            ]
        )
        assert await is_league_grand_final(db, event_id=501) is False

    @pytest.mark.asyncio
    async def test_partial_fixture_never_qualifies(self):
        db = MagicMock()
        db.execute = AsyncMock(
            side_effect=[
                SimpleNamespace(first=lambda: (2, 1)),
                SimpleNamespace(one=lambda: (2, 2)),
            ]
        )
        assert await is_league_grand_final(db, event_id=501) is False

    @pytest.mark.asyncio
    async def test_missing_event_is_not(self):
        db = MagicMock()
        db.execute = AsyncMock(
            side_effect=[SimpleNamespace(first=lambda: None)]
        )
        assert await is_league_grand_final(db, event_id=501) is False


# ---------------------------------------------------------------------------
# Pure builders
# ---------------------------------------------------------------------------


class TestPureBuilders:
    def test_result_phrase_covers_all_three_outcomes(self):
        assert _result_phrase("A", 60, "B", 40) == "defeated B by 20 points"
        assert _result_phrase("A", 30, "B", 55) == "lost to B by 25 points"
        assert _result_phrase("A", 44, "B", 44) == "drew with B"

    def test_finals_path_labels_the_last_four_rounds_with_opponent_names(self):
        season = [
            _event(1, round_id=1, home_score=80, away_score=50, completed=True),
            _event(2, round_id=2, home_score=60, away_score=70, completed=True),
            _event(3, round_id=3, home_score=90, away_score=90, completed=True),
            _event(
                4, round_id=4, home_pid=13, away_pid=11,
                home_score=40, away_score=95, completed=True,
            ),
            _event(
                5, round_id=5, home_pid=14, away_pid=11,
                home_score=55, away_score=88, completed=True,
            ),
        ]
        names = {11: "Souths", 12: "Peel", 13: "Claremont", 14: "Subiaco"}
        path = _side_finals_path(season, 11, "Souths", names, max_round=5)
        assert path == [
            "Round 2: lost to Peel by 10 points",
            "Round 3: drew with Peel",
            "Round 4: defeated Claremont by 55 points",
            "Round 5: defeated Subiaco by 33 points",
        ]

    def test_fallback_editorial_quotes_the_evidence_and_the_verdict(self):
        prediction = {"winner": "Souths", "margin": 12, "confidence": 0.5}
        block = _fallback_editorial(
            "Souths",
            "Peel",
            ["Souths: season win rate 80% (8-0-2), ranked #1"],
            prediction,
        )
        assert "Souths" in block["headline"]
        assert "Souths" in block["executive_summary"]
        assert block["x_factor"] is None
        assert block["keys_to_the_game"]
        assert block["talking_points"]

    def test_report_user_prompt_carries_evidence_and_teams(self):
        prompt = _report_user_prompt(
            {
                "competition_name": "Test League",
                "home_name": "Souths",
                "away_name": "Peel",
                "venue": "Test Oval",
                "round_id": 5,
                "season_label": "2026",
                "evidence_lines": ["Souths: season win rate 80%"],
            }
        )
        assert "Test League" in prompt
        assert "Souths" in prompt and "Peel" in prompt
        assert "Souths: season win rate 80%" in prompt
        assert "x_factor" in prompt


# ---------------------------------------------------------------------------
# Editorial client — strict-JSON contract, fallback on ANY failure
# ---------------------------------------------------------------------------


class TestReportClient:
    @pytest.mark.asyncio
    async def test_no_api_key_returns_none(self):
        with patch(
            "packages.shared.config.settings.openrouter_api_key", ""
        ):
            client = LeagueReportOpenRouterClient("Test League")
            assert await client.generate_report_sections({}) is None

    @pytest.mark.asyncio
    async def test_valid_json_parses(self):
        with patch(
            "packages.shared.config.settings.openrouter_api_key", "k"
        ):
            client = LeagueReportOpenRouterClient("Test League")
            client.client = MagicMock()
            client.client.chat.completions.create = AsyncMock(
                return_value=SimpleNamespace(
                    choices=[
                        SimpleNamespace(
                            message=SimpleNamespace(
                                content=(
                                    '{"headline": "Decider looms", '
                                    '"executive_summary": "Two sides, '
                                    'one trophy.", "home_narrative": "H.", '
                                    '"away_narrative": "A.", '
                                    '"keys_to_the_game": ["k1"], '
                                    '"x_factor": null, '
                                    '"talking_points": ["t1"]}'
                                )
                            )
                        )
                    ]
                )
            )
            out = await client.generate_report_sections({})
            assert out is not None
            assert out["headline"] == "Decider looms"
            assert out["x_factor"] is None

    @pytest.mark.asyncio
    async def test_missing_required_key_returns_none(self):
        with patch(
            "packages.shared.config.settings.openrouter_api_key", "k"
        ):
            client = LeagueReportOpenRouterClient("Test League")
            client.client = MagicMock()
            client.client.chat.completions.create = AsyncMock(
                return_value=SimpleNamespace(
                    choices=[
                        SimpleNamespace(
                            message=SimpleNamespace(
                                content='{"headline": "No rest"}'
                            )
                        )
                    ]
                )
            )
            assert await client.generate_report_sections({}) is None

    @pytest.mark.asyncio
    async def test_garbage_content_returns_none(self):
        with patch(
            "packages.shared.config.settings.openrouter_api_key", "k"
        ):
            client = LeagueReportOpenRouterClient("Test League")
            client.client = MagicMock()
            client.client.chat.completions.create = AsyncMock(
                return_value=SimpleNamespace(
                    choices=[
                        SimpleNamespace(
                            message=SimpleNamespace(content="not json at all")
                        )
                    ]
                )
            )
            assert await client.generate_report_sections({}) is None


# ---------------------------------------------------------------------------
# Service gates + fallback assembly
# ---------------------------------------------------------------------------


class _CannedResult:
    def __init__(self, *, first=None, scalar_one_or_none=None, scalars=None):
        self._first = first
        self._scalar_one_or_none = scalar_one_or_none
        self._scalars = scalars

    def first(self):
        return self._first

    def scalar_one_or_none(self):
        return self._scalar_one_or_none

    def scalars(self):
        return iter(self._scalars) if self._scalars is not None else iter([])


def _sides():
    return {
        "home": {"side_id": 101, "participant_id": 11, "name": "Souths"},
        "away": {"side_id": 102, "participant_id": 12, "name": "Peel"},
    }


class TestGenerateAndStoreReport:
    @pytest.mark.asyncio
    async def test_not_a_grand_final_returns_none(self):
        service = LeagueMatchReportService()
        with patch(
            "packages.shared.services.league_match_report.is_league_grand_final",
            AsyncMock(return_value=False),
        ):
            assert (
                await service.generate_and_store_report(
                    MagicMock(), event_id=1
                )
                is None
            )

    @pytest.mark.asyncio
    async def test_completed_event_returns_none(self):
        service = LeagueMatchReportService()
        event_row = SimpleNamespace(
            completed=True,
            starts_at=_KICKOFF,
            round_id=5,
            venue="Test Oval",
        )
        db = MagicMock()
        db.execute = AsyncMock(
            side_effect=[
                _CannedResult(first=(event_row, "2026", 1, 2)),
            ]
        )
        with patch(
            "packages.shared.services.league_match_report.is_league_grand_final",
            AsyncMock(return_value=True),
        ):
            assert (
                await service.generate_and_store_report(db, event_id=1) is None
            )

    @pytest.mark.asyncio
    async def test_existing_report_returns_none(self):
        service = LeagueMatchReportService()
        event_row = SimpleNamespace(
            completed=False,
            starts_at=_KICKOFF,
            round_id=5,
            venue="Test Oval",
        )
        db = MagicMock()
        db.execute = AsyncMock(
            side_effect=[
                _CannedResult(first=(event_row, "2026", 1, 2)),
                _CannedResult(scalar_one_or_none=SimpleNamespace(id=9)),
            ]
        )
        with patch(
            "packages.shared.services.league_match_report.is_league_grand_final",
            AsyncMock(return_value=True),
        ):
            assert (
                await service.generate_and_store_report(db, event_id=1) is None
            )
        db.add.assert_not_called()

    @pytest.mark.asyncio
    async def test_fallback_assembly_stores_a_valid_report(self):
        service = LeagueMatchReportService()
        event_row = SimpleNamespace(
            completed=False,
            starts_at=_KICKOFF,
            round_id=5,
            venue="Test Oval",
        )
        db = MagicMock()
        db.execute = AsyncMock(
            side_effect=[
                _CannedResult(first=(event_row, "2026", 1, 2)),
                _CannedResult(scalar_one_or_none=None),
                _CannedResult(scalars=[1]),  # competition season ids
                _CannedResult(scalars=[]),  # derived tips: empty
            ]
        )
        db.add = MagicMock()
        db.commit = AsyncMock()

        with (
            patch(
                "packages.shared.services.league_match_report.is_league_grand_final",
                AsyncMock(return_value=True),
            ),
            patch.object(
                service, "_fetch_sides", AsyncMock(return_value=_sides())
            ),
            patch.object(
                service,
                "_participant_names",
                AsyncMock(return_value={11: "Souths", 12: "Peel"}),
            ),
            patch.object(
                service,
                "_fetch_model_predictions",
                AsyncMock(
                    return_value=[
                        ("elo", "Souths", 0.7, 12),
                        ("form", "Souths", 0.6, 8),
                        ("value", "Peel", 0.55, 5),
                    ]
                ),
            ),
            patch.object(
                service,
                "_season_accuracy_note",
                AsyncMock(return_value="The strongest model graded at 70%."),
            ),
            patch.object(
                service, "_weather_impact", AsyncMock(return_value=None)
            ),
            patch.object(
                service,
                "_competition_name",
                AsyncMock(return_value="Test League"),
            ),
            patch.object(
                LeagueReportOpenRouterClient,
                "generate_report_sections",
                AsyncMock(return_value=None),
            ),
            patch(
                "packages.shared.services.league_heuristics.LeagueHeuristicsService._fetch_completed_events",
                AsyncMock(
                    return_value=[
                        _event(
                            1,
                            round_id=4,
                            home_pid=11,
                            away_pid=12,
                            home_score=70,
                            away_score=50,
                            completed=True,
                        )
                    ]
                ),
            ),
        ):
            payload = await service.generate_and_store_report(
                db, event_id=501
            )

        assert payload is not None
        assert payload["headline"]
        assert payload["prediction"]["winner"] == "Souths"
        assert payload["model_consensus"]["models_picking_home"] == 2
        assert payload["model_consensus"]["models_picking_away"] == 1
        assert "70%" in payload["model_consensus"]["season_accuracy_note"]
        assert payload["key_players"] == {"home": [], "away": []}
        assert payload["injury_watch"] == {"home": [], "away": []}
        assert payload["weather_impact"] is None
        assert payload["season_story"]["home"]["team"] == "Souths"
        assert payload["season_story"]["away"]["finals_path"] == [
            "Round 4: lost to Souths by 20 points"
        ]
        db.add.assert_called_once()
        db.commit.assert_awaited_once()
        stored = db.add.call_args.args[0]
        assert stored.event_id == 501
        assert stored.report_type == "grand_final_pre_match"

    @pytest.mark.asyncio
    async def test_internal_failure_returns_none_and_never_raises(self):
        service = LeagueMatchReportService()
        with patch(
            "packages.shared.services.league_match_report.is_league_grand_final",
            AsyncMock(return_value=True),
        ), patch.object(
            LeagueMatchReportService,
            "_fetch_sides",
            AsyncMock(side_effect=RuntimeError("db down")),
        ):
            db = MagicMock()
            db.execute = AsyncMock(
                side_effect=[
                    _CannedResult(
                        first=(
                            SimpleNamespace(
                                completed=False,
                                starts_at=_KICKOFF,
                                round_id=5,
                                venue="Test Oval",
                            ),
                            "2026",
                            1,
                            2,
                        )
                    ),
                    _CannedResult(scalar_one_or_none=None),
                ]
            )
            assert (
                await service.generate_and_store_report(db, event_id=1) is None
            )


# ---------------------------------------------------------------------------
# GET /api/events/{slug}/report
# ---------------------------------------------------------------------------


def _build_app() -> FastAPI:
    from fastapi.responses import JSONResponse

    from app.api.events import router
    from app.core.exceptions import BackendServiceError

    app = FastAPI()
    app.include_router(router, prefix="/api/events")

    # Mirror the global exception handler from main.py so 404/4xx errors
    # are converted to the documented JSON shape.
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

    return app


def _override_db(app: FastAPI, session) -> None:
    from app.core.db_deps import get_db

    app.dependency_overrides[get_db] = lambda: session


def _make_event_dict():
    return {
        "id": 501,
        "slug": "nrl-gf00001",
        "round_id": 27,
        "venue": "Accor Stadium",
        "starts_at": "2026-09-27T19:00:00",
        "status": "scheduled",
        "completed": False,
        "competition": "National Rugby League",
        "season": "2026",
        "participants": [
            {
                "side": "home",
                "participant_name": "Melbourne Storm",
                "score": None,
                "is_winner": None,
            },
            {
                "side": "away",
                "participant_name": "Penrith Panthers",
                "score": None,
                "is_winner": None,
            },
        ],
    }


class TestGetEventReportEndpoint:
    def test_not_a_grand_final_is_a_404(self):
        mock_session = AsyncMock(spec=AsyncSession)
        app = _build_app()
        _override_db(app, mock_session)
        with patch("app.api.events.EventsCRUD") as mock_crud, patch(
            "packages.shared.services.league_match_report.LeagueMatchReportService.is_grand_final",
            new=AsyncMock(return_value=False),
        ):
            mock_crud.get_by_slug_with_participants = AsyncMock(
                return_value=_make_event_dict()
            )
            client = TestClient(app)
            resp = client.get("/api/events/nrl-gf00001/report")
        assert resp.status_code == 404
        assert "grand final" in resp.json()["message"]

    def test_stored_report_round_trips(self):
        mock_session = AsyncMock(spec=AsyncSession)
        report_row = SimpleNamespace(
            id=7,
            event_id=501,
            report_type="grand_final_pre_match",
            report={
                "headline": "Storm and Panthers collide",
                "executive_summary": "The decider.",
                "season_story": {
                    "home": {
                        "team": "Melbourne Storm",
                        "narrative": "n",
                        "finals_path": [],
                    },
                    "away": {
                        "team": "Penrith Panthers",
                        "narrative": "n",
                        "finals_path": [],
                    },
                },
                "keys_to_the_game": ["k"],
                "key_players": {"home": [], "away": []},
                "injury_watch": {"home": [], "away": []},
                "model_consensus": {
                    "summary": "s",
                    "models_picking_home": 3,
                    "models_picking_away": 2,
                    "season_accuracy_note": "n",
                },
                "weather_impact": None,
                "x_factor": None,
                "prediction": {
                    "winner": "Melbourne Storm",
                    "margin": 6,
                    "confidence": 0.6,
                },
                "talking_points": ["t"],
            },
            created_at=datetime(2026, 9, 20, 3, 0),
        )
        get_result = MagicMock()
        get_result.scalar_one_or_none.return_value = report_row
        mock_session.execute = AsyncMock(side_effect=[get_result])

        app = _build_app()
        _override_db(app, mock_session)
        with patch("app.api.events.EventsCRUD") as mock_crud, patch(
            "packages.shared.services.league_match_report.LeagueMatchReportService.is_grand_final",
            new=AsyncMock(return_value=True),
        ):
            mock_crud.get_by_slug_with_participants = AsyncMock(
                return_value=_make_event_dict()
            )
            client = TestClient(app)
            resp = client.get("/api/events/nrl-gf00001/report")
        assert resp.status_code == 200
        body = resp.json()
        assert body["event_id"] == 501
        assert body["report_type"] == "grand_final_pre_match"
        assert body["report"]["headline"] == "Storm and Panthers collide"
        assert body["report"]["prediction"]["winner"] == "Melbourne Storm"

    def test_ungenerated_report_degrades_to_404(self):
        mock_session = AsyncMock(spec=AsyncSession)
        get_result = MagicMock()
        get_result.scalar_one_or_none.return_value = None
        mock_session.execute = AsyncMock(side_effect=[get_result])

        app = _build_app()
        _override_db(app, mock_session)
        with patch("app.api.events.EventsCRUD") as mock_crud, patch(
            "packages.shared.services.league_match_report.LeagueMatchReportService.is_grand_final",
            new=AsyncMock(return_value=True),
        ):
            mock_crud.get_by_slug_with_participants = AsyncMock(
                return_value=_make_event_dict()
            )
            client = TestClient(app)
            resp = client.get("/api/events/nrl-gf00001/report")
        assert resp.status_code == 404
        assert "not yet generated" in resp.json()["message"]
