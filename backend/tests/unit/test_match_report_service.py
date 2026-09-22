"""Unit tests for the grand-final match report service.

No database, network or OpenRouter access: the DB session is an
``AsyncMock(spec=AsyncSession)``, the agent boundary is patched at the
module level (``Agent`` / ``OpenRouterModel`` / ``OpenRouterProvider``),
and the CRUD boundary is patched inside the service module — the same
mocking style as ``test_app_api_games.py`` / ``test_openrouter_null_choices.py``.
"""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from packages.shared.config import settings
from packages.shared.schemas.match_report import GrandFinalReport
from packages.shared.services.match_report import (
    GFDeps,
    MatchReportService,
    _anchor_prediction_to_weighted_tip,
    _normalize_sides,
)

SERVICE_MODULE = "packages.shared.services.match_report"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_game_mock(**overrides) -> MagicMock:
    """Build a ``Game``-shaped MagicMock sitting in GF week (pre-match)."""
    defaults = {
        "id": 101,
        "slug": "gf2026abc",
        "round_id": 27,
        "season": 2026,
        "home_team": "Brisbane",
        "away_team": "Collingwood",
        "home_score": None,
        "away_score": None,
        "venue": "MCG",
        "date": datetime(2026, 9, 26, 10, 0, tzinfo=timezone.utc),
        "completed": False,
    }
    defaults.update(overrides)
    game = MagicMock()
    for k, v in defaults.items():
        setattr(game, k, v)
    return game


def _session_with_scalar(value) -> AsyncMock:
    """Session whose single ``execute`` returns ``scalar() == value``."""
    session = AsyncMock(spec=AsyncSession)
    result = MagicMock()
    result.scalar.return_value = value
    session.execute = AsyncMock(return_value=result)
    return session


def _valid_report() -> GrandFinalReport:
    """A minimal-but-valid ``GrandFinalReport`` (all required fields)."""
    return GrandFinalReport(
        headline="Lions chase back-to-back against Magpies",
        executive_summary="A clash of the season's two most complete sides.",
        season_story={
            "home": {
                "team": "Brisbane",
                "narrative": "Dominant at the Gabba all year.",
                "finals_path": ["Qualifying Final: beat Geelong by 22 points"],
            },
            "away": {
                "team": "Collingwood",
                "narrative": "Ground out four tight finals wins.",
                "finals_path": [],
            },
        },
        keys_to_the_game=["Stoppage battle", "Inside-50 efficiency"],
        key_players={
            "home": [{"name": "L. Neale", "team": "Brisbane", "note": "32 touches a game"}],
            "away": [],
        },
        injury_watch={"home": [], "away": []},
        model_consensus={
            "summary": "Three of four models lean Brisbane",
            "models_picking_home": 3,
            "models_picking_away": 1,
            "season_accuracy_note": "best_bet tipped 76% for the season",
        },
        weather_impact="Clear and 18C — no Impact.",
        x_factor="Crowd noise at stoppages.",
        prediction={"winner": "Brisbane", "margin": 11, "confidence": 0.62},
        talking_points=["The midfield arm wrestle decides this."],
    )


# ---------------------------------------------------------------------------
# is_grand_final
# ---------------------------------------------------------------------------


class TestIsGrandFinal:
    """``is_grand_final`` compares the game's round to the season max round."""

    @pytest.mark.asyncio
    async def test_true_when_round_is_season_max(self):
        session = _session_with_scalar(27)
        game = _make_game_mock(round_id=27, season=2026)

        assert await MatchReportService.is_grand_final(session, game) is True
        session.execute.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_false_when_round_is_not_season_max(self):
        session = _session_with_scalar(27)
        game = _make_game_mock(round_id=10, season=2026)

        assert await MatchReportService.is_grand_final(session, game) is False

    @pytest.mark.asyncio
    async def test_false_when_season_has_no_games(self):
        session = _session_with_scalar(None)
        game = _make_game_mock(round_id=27, season=2026)

        assert await MatchReportService.is_grand_final(session, game) is False

    @pytest.mark.asyncio
    async def test_false_without_round_or_season(self):
        session = AsyncMock(spec=AsyncSession)
        game = _make_game_mock(round_id=None, season=None)

        assert await MatchReportService.is_grand_final(session, game) is False
        # No query needed when the game lacks round/season.
        session.execute.assert_not_awaited()

    def test_is_static_helper(self):
        # The API layer calls this without instantiating the service —
        # no OpenRouter client may be constructed for the check.
        assert isinstance(MatchReportService.__dict__["is_grand_final"], staticmethod)


# ---------------------------------------------------------------------------
# generate_and_store_report — gates
# ---------------------------------------------------------------------------


class TestGenerateAndStoreReportGates:
    """Every gate returns None (and never raises)."""

    @pytest.mark.asyncio
    async def test_non_grand_final_returns_none(self):
        session = AsyncMock(spec=AsyncSession)
        game = _make_game_mock()
        service = MatchReportService()

        with patch.object(
            MatchReportService, "is_grand_final", new=AsyncMock(return_value=False)
        ):
            result = await service.generate_and_store_report(session, game)

        assert result is None

    @pytest.mark.asyncio
    async def test_completed_game_returns_none(self):
        session = AsyncMock(spec=AsyncSession)
        game = _make_game_mock(completed=True)
        service = MatchReportService()

        with patch.object(
            MatchReportService, "is_grand_final", new=AsyncMock(return_value=True)
        ):
            result = await service.generate_and_store_report(session, game)

        assert result is None

    @pytest.mark.asyncio
    async def test_tbc_teams_return_none(self):
        session = AsyncMock(spec=AsyncSession)
        game = _make_game_mock(home_team="", away_team=None)
        service = MatchReportService()

        with patch.object(
            MatchReportService, "is_grand_final", new=AsyncMock(return_value=True)
        ):
            result = await service.generate_and_store_report(session, game)

        assert result is None

    @pytest.mark.asyncio
    async def test_missing_api_key_returns_none_without_building_agent(self, monkeypatch):
        monkeypatch.setattr(settings, "openrouter_api_key", "")
        session = AsyncMock(spec=AsyncSession)
        game = _make_game_mock()

        with (
            patch.object(
                MatchReportService, "is_grand_final", new=AsyncMock(return_value=True)
            ),
            patch(f"{SERVICE_MODULE}.MatchReportCRUD") as mock_crud,
            patch(f"{SERVICE_MODULE}.Agent") as mock_agent_cls,
        ):
            mock_crud.get_by_game_id = AsyncMock(return_value=None)
            service = MatchReportService()
            result = await service.generate_and_store_report(session, game)

        assert result is None
        # The agent must never be constructed without a key.
        mock_agent_cls.assert_not_called()

    @pytest.mark.asyncio
    async def test_existing_report_returns_stored_without_running_agent(
        self, monkeypatch
    ):
        monkeypatch.setattr(settings, "openrouter_api_key", "sk-or-v1-test")
        session = AsyncMock(spec=AsyncSession)
        game = _make_game_mock()
        stored_payload = {"headline": "stored report"}
        row = SimpleNamespace(report=stored_payload)

        with (
            patch.object(
                MatchReportService, "is_grand_final", new=AsyncMock(return_value=True)
            ),
            patch(f"{SERVICE_MODULE}.MatchReportCRUD") as mock_crud,
            patch(f"{SERVICE_MODULE}.Agent") as mock_agent_cls,
        ):
            mock_crud.get_by_game_id = AsyncMock(return_value=row)
            service = MatchReportService()
            result = await service.generate_and_store_report(session, game)

        assert result == stored_payload
        mock_agent_cls.assert_not_called()


# ---------------------------------------------------------------------------
# generate_and_store_report — agent run + persistence
# ---------------------------------------------------------------------------


class TestGenerateAndStoreReportAgentRun:
    """With all gates passed, the agent output is stored via the CRUD."""

    @staticmethod
    def _agent_mock(report: GrandFinalReport | None) -> MagicMock:
        """Build an ``Agent`` class mock whose ``run`` returns ``report``."""
        mock_agent_cls = MagicMock()
        mock_agent_cls.return_value.run = AsyncMock(
            return_value=SimpleNamespace(output=report)
        )
        return mock_agent_cls

    @pytest.mark.asyncio
    async def test_success_stores_report_payload(self, monkeypatch):
        monkeypatch.setattr(settings, "openrouter_api_key", "sk-or-v1-test")
        session = AsyncMock(spec=AsyncSession)
        game = _make_game_mock()
        report = _valid_report()
        stored_payload = report.model_dump(mode="json")
        row = SimpleNamespace(report=stored_payload)
        mock_agent_cls = self._agent_mock(report)

        with (
            patch.object(
                MatchReportService, "is_grand_final", new=AsyncMock(return_value=True)
            ),
            patch(f"{SERVICE_MODULE}.MatchReportCRUD") as mock_crud,
            patch(f"{SERVICE_MODULE}.Agent", mock_agent_cls),
            patch(f"{SERVICE_MODULE}.OpenRouterModel"),
            patch(f"{SERVICE_MODULE}.OpenRouterProvider"),
        ):
            mock_crud.get_by_game_id = AsyncMock(return_value=None)
            mock_crud.create_or_update = AsyncMock(return_value=row)

            service = MatchReportService()
            result = await service.generate_and_store_report(session, game)

        assert result == stored_payload
        mock_crud.create_or_update.assert_awaited_once()

        # create_or_update(db, game_id, report_type, report dict) — called
        # positionally with the exact report_type and payload per spec.
        args = mock_crud.create_or_update.await_args.args
        assert len(args) == 4
        assert args[1] == 101
        assert args[2] == "grand_final_pre_match"
        assert args[3] == stored_payload

    @pytest.mark.asyncio
    async def test_agent_run_receives_deps_and_limits(self, monkeypatch):
        monkeypatch.setattr(settings, "openrouter_api_key", "sk-or-v1-test")
        session = AsyncMock(spec=AsyncSession)
        game = _make_game_mock()
        report = _valid_report()
        row = SimpleNamespace(report=report.model_dump(mode="json"))
        mock_agent_cls = self._agent_mock(report)

        with (
            patch.object(
                MatchReportService, "is_grand_final", new=AsyncMock(return_value=True)
            ),
            patch(f"{SERVICE_MODULE}.MatchReportCRUD") as mock_crud,
            patch(f"{SERVICE_MODULE}.Agent", mock_agent_cls),
            patch(f"{SERVICE_MODULE}.OpenRouterModel") as mock_model_cls,
            patch(f"{SERVICE_MODULE}.OpenRouterProvider"),
        ):
            mock_crud.get_by_game_id = AsyncMock(return_value=None)
            mock_crud.create_or_update = AsyncMock(return_value=row)

            service = MatchReportService()
            await service.generate_and_store_report(session, game)

        run_kwargs = mock_agent_cls.return_value.run.await_args.kwargs
        deps = run_kwargs["deps"]
        assert isinstance(deps, GFDeps)
        assert deps.db is session
        assert run_kwargs["usage_limits"].total_tokens_limit == 1_000_000
        assert run_kwargs["usage_limits"].request_limit == 60
        assert run_kwargs["model_settings"]["temperature"] == 0.3
        assert run_kwargs["model_settings"]["max_tokens"] == 16_000
        # GF-BUDGET: reasoning allowed but capped at LOW effort so the
        # structured-output tool call fits the token budget.
        assert run_kwargs["model_settings"]["openrouter_reasoning"] == {
            "effort": "low"
        }
        # GF-CONTENT: dedicated output-retry budget (constructor kwarg).
        assert mock_agent_cls.call_args.kwargs["retries"] == {"output": 4}
        # GF-BUDGET: the report agent runs its own cheap model
        # (ling-3.0-flash), not the shared nightly-explanations model.
        assert mock_model_cls.call_args.args[0] == "inclusionai/ling-3.0-flash"

    @pytest.mark.asyncio
    async def test_agent_failure_returns_none_and_stores_nothing(self, monkeypatch):
        monkeypatch.setattr(settings, "openrouter_api_key", "sk-or-v1-test")
        session = AsyncMock(spec=AsyncSession)
        game = _make_game_mock()
        mock_agent_cls = self._agent_mock(_valid_report())
        mock_agent_cls.return_value.run = AsyncMock(
            side_effect=RuntimeError("provider outage")
        )

        with (
            patch.object(
                MatchReportService, "is_grand_final", new=AsyncMock(return_value=True)
            ),
            patch(f"{SERVICE_MODULE}.MatchReportCRUD") as mock_crud,
            patch(f"{SERVICE_MODULE}.Agent", mock_agent_cls),
            patch(f"{SERVICE_MODULE}.OpenRouterModel"),
            patch(f"{SERVICE_MODULE}.OpenRouterProvider"),
        ):
            mock_crud.get_by_game_id = AsyncMock(return_value=None)
            mock_crud.create_or_update = AsyncMock()

            service = MatchReportService()
            result = await service.generate_and_store_report(session, game)

        assert result is None
        mock_crud.create_or_update.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_unexpected_exception_is_swallowed(self, monkeypatch):
        monkeypatch.setattr(settings, "openrouter_api_key", "sk-or-v1-test")
        session = AsyncMock(spec=AsyncSession)
        game = _make_game_mock()
        service = MatchReportService()

        with patch.object(
            MatchReportService,
            "is_grand_final",
            new=AsyncMock(side_effect=RuntimeError("db down")),
        ):
            result = await service.generate_and_store_report(session, game)

        assert result is None

    @pytest.mark.asyncio
    async def test_close_clears_cached_agent(self, monkeypatch):
        monkeypatch.setattr(settings, "openrouter_api_key", "sk-or-v1-test")
        service = MatchReportService()
        service._agent = object()

        await service.close()

        assert service._agent is None

# ---------------------------------------------------------------------------
# GF-SIDES FIX + GF-VERDICT FIX (2026-09-21, user report)
# ---------------------------------------------------------------------------


def _swapped_report() -> GrandFinalReport:
    """Agent output with the sides INVERTED: Brisbane (the fixture's
    away team) sits in ``home`` slots with Fremantle players under it,
    and Fremantle players sit in ``away``."""
    return GrandFinalReport(
        headline="Lions roar into the decider",
        executive_summary="Brisbane's forward line against Fremantle's wall.",
        season_story={
            "home": {  # INVERTED: Brisbane is the fixture AWAY team
                "team": "Brisbane",
                "narrative": "Brisbane story",
                "finals_path": ["PF: beat Hawthorn"],
            },
            "away": {  # INVERTED: Fremantle is the fixture HOME team
                "team": "Fremantle",
                "narrative": "Fremantle story",
                "finals_path": ["PF: beat Sydney"],
            },
        },
        keys_to_the_game=["Stoppage battle"],
        key_players={
            "home": [  # INVERTED: Brisbane players in the home slot
                {"name": "Dunkley", "team": "Brisbane", "note": "Contested beast"},
            ],
            "away": [  # INVERTED: Fremantle players in the away slot
                {"name": "Amiss, Jye", "team": "Fremantle", "note": "32 goals"},
                {"name": "Treacy, Josh", "team": "Fremantle", "note": "92 marks"},
            ],
        },
        injury_watch={"home": [], "away": []},
        model_consensus={
            "summary": "Models lean Brisbane",
            "models_picking_home": 6,
            "models_picking_away": 2,
            "season_accuracy_note": "best_bet 63%",
        },
        weather_impact=None,
        x_factor=None,
        # The agent's own invented blend (NOT the weighted tip):
        prediction={"winner": "Fremantle", "margin": 15, "confidence": 0.55},
        talking_points=["Midfield arm wrestle."],
    )


class TestNormalizeSides:
    def test_inverted_sides_are_remapped_to_the_fixture(self):
        game = SimpleNamespace(home_team="Fremantle", away_team="Brisbane")
        report = _swapped_report()

        fixed = _normalize_sides(game, report)

        # Season story now matches the fixture.
        assert fixed.season_story.home.team == "Fremantle"
        assert fixed.season_story.away.team == "Brisbane"
        # Players remapped: Fremantle under home, Brisbane under away.
        assert [p.name for p in fixed.key_players.home] == [
            "Amiss, Jye",
            "Treacy, Josh",
        ]
        assert [p.name for p in fixed.key_players.away] == ["Dunkley"]

    def test_entries_matching_neither_side_are_dropped(self):
        from packages.shared.schemas.match_report import PlayerSpotlight

        game = SimpleNamespace(home_team="Fremantle", away_team="Brisbane")
        report = _swapped_report()
        report.key_players.home = []
        report.key_players.away = [
            PlayerSpotlight(name="Mystery", team="Gold Coast", note="n")
        ]

        fixed = _normalize_sides(game, report)
        assert fixed.key_players.home == []
        assert fixed.key_players.away == []


class TestAnchorPredictionToWeightedTip:
    @pytest.mark.asyncio
    async def test_prediction_overridden_with_weighted_tip(self):
        """GF-VERDICT: the site's verdict IS the weighted tip (user's
        preferred heuristic) - not the agent's invented blend."""
        game = SimpleNamespace(id=101, home_team="Fremantle", away_team="Brisbane")
        report = _swapped_report()
        weighted = SimpleNamespace(
            heuristic="weighted_tip",
            selected_team="Brisbane",
            margin=21,
            confidence=0.72,
        )

        with patch(
            "packages.shared.crud.tips.TipCRUD.get_by_game",
            AsyncMock(return_value=[weighted]),
        ):
            fixed = await _anchor_prediction_to_weighted_tip(
                AsyncMock(spec=AsyncSession), game, report
            )

        assert fixed.prediction.winner == "Brisbane"
        assert fixed.prediction.margin == 21
        assert fixed.prediction.confidence == 0.72

    @pytest.mark.asyncio
    async def test_no_weighted_tip_keeps_agent_verdict(self):
        game = SimpleNamespace(id=101, home_team="Fremantle", away_team="Brisbane")
        report = _swapped_report()
        before = report.prediction.model_dump()

        with patch(
            "packages.shared.crud.tips.TipCRUD.get_by_game",
            AsyncMock(return_value=[]),
        ):
            fixed = await _anchor_prediction_to_weighted_tip(
                AsyncMock(spec=AsyncSession), game, report
            )

        assert fixed.prediction.model_dump() == before
