"""Unit tests for the league NLP sweep (T-1, code review).

The sweep is the nightly AI-content engine for every league — its
scoping (live leagues, lookahead window, per-run fixture budget),
counting, per-item failure containment (rollback + continue) and
error capture are pinned here with the DB and the three content
services fully patched out.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from packages.shared.services import league_nlp_sweep


def _fixture_rows(n: int) -> list[tuple[int, int]]:
    """``(competition_id, event_id)`` rows the upcoming query returns."""
    return [(1, 100 + i) for i in range(n)]


def _patched_harness(
    *,
    fixtures: list[tuple[int, int]],
    explanations: object | None = None,
    analyses: object | None = None,
    reports: object | None = None,
):
    """Patch the competition/window fetchers and the three services.

    The service doubles count their calls so the budget and the
    containment behaviour are observable.
    """
    explanation_double = MagicMock()
    explanation_double.generate_explanation_for_tip = AsyncMock(
        side_effect=explanations
        if isinstance(explanations, Exception)
        else (lambda *a, **kw: "text") if explanations is None else explanations
    )
    analysis_double = MagicMock()
    analysis_double.generate_and_store_analysis = AsyncMock(
        return_value=True
    )
    analysis_double.close = AsyncMock()
    report_double = MagicMock()
    report_double.generate_and_store_report = AsyncMock(return_value=True)

    return (
        patch.object(
            league_nlp_sweep,
            "_live_competition_ids",
            AsyncMock(return_value=[1]),
        ),
        patch.object(
            league_nlp_sweep,
            "_fetch_upcoming_fixtures",
            AsyncMock(return_value=SimpleNamespace(all=lambda: fixtures)),
        ),
        patch(
            "packages.shared.services.explanation.LeagueTipExplanationService",
            MagicMock(return_value=explanation_double),
        ),
        patch(
            "packages.shared.services.league_match_analysis.LeagueMatchAnalysisService",
            MagicMock(return_value=analysis_double),
        ),
        patch(
            "packages.shared.services.league_match_report.LeagueMatchReportService",
            MagicMock(return_value=report_double),
        ),
    ), explanation_double, analysis_double, report_double


class TestRunLeagueNlpSweep:
    @pytest.mark.asyncio
    async def test_empty_competition_list_is_a_zero_fast_path(self):
        db = MagicMock()
        with patch.object(
            league_nlp_sweep,
            "_live_competition_ids",
            AsyncMock(return_value=[]),
        ):
            counts = await league_nlp_sweep.run_league_nlp_sweep(db)
        assert counts["explanations_created"] == 0
        assert counts["match_analyses_created"] == 0
        assert counts["match_reports_created"] == 0
        assert counts["error"] is None

    @pytest.mark.asyncio
    async def test_per_run_fixture_budget_is_respected(self):
        """R-1 (code review): 45 upcoming fixtures → the first
        ``_LEAGUE_SWEEP_MAX_FIXTURES`` (40) processed, in order."""
        fixtures = _fixture_rows(45)
        patchers, _, analysis_double, report_double = _patched_harness(
            fixtures=fixtures
        )
        with patchers[0], patchers[1], patchers[2], patchers[3], patchers[4]:
            counts = await league_nlp_sweep.run_league_nlp_sweep(MagicMock())
        processed = [
            call.kwargs["event_id"]
            for call in report_double.generate_and_store_report.await_args_list
        ]
        assert processed == [100 + i for i in range(40)]
        assert len(processed) == league_nlp_sweep._LEAGUE_SWEEP_MAX_FIXTURES
        assert counts["match_reports_created"] == 40

    @pytest.mark.asyncio
    async def test_per_item_failure_rolls_back_and_continues(self):
        """F-1 (code review): a poisoned transaction must be rolled back
        and the sweep must keep going — an explanation failure for the
        first event does not stop the second event's report."""
        fixtures = _fixture_rows(2)
        patchers, explanation_double, _, report_double = _patched_harness(
            fixtures=fixtures
        )
        explanation_double.generate_explanation_for_tip = AsyncMock(
            side_effect=RuntimeError("llm down")
        )
        db = MagicMock()
        db.rollback = AsyncMock()
        with patchers[0], patchers[1], patchers[2], patchers[3], patchers[4]:
            counts = await league_nlp_sweep.run_league_nlp_sweep(db)
        # Both events still reached the report step.
        assert report_double.generate_and_store_report.await_count == 2
        assert counts["match_reports_created"] == 2
        # Explanations failed 4x per event (the derived heuristics) and
        # every failure rolled the session back.
        assert explanation_double.generate_explanation_for_tip.await_count == 8
        assert db.rollback.await_count == 8

    @pytest.mark.asyncio
    async def test_counts_reflect_generated_content(self):
        fixtures = _fixture_rows(2)
        patchers, _, analysis_double, report_double = _patched_harness(
            fixtures=fixtures
        )
        db = MagicMock()
        db.rollback = AsyncMock()
        with patchers[0], patchers[1], patchers[2], patchers[3], patchers[4]:
            counts = await league_nlp_sweep.run_league_nlp_sweep(db)
        # 2 events x 4 derived heuristics of explanations.
        assert counts["explanations_created"] == 8
        assert counts["match_analyses_created"] == 2
        assert counts["match_reports_created"] == 2
        assert counts["error"] is None
        # F-3: the analysis service's client is closed in the finally.
        analysis_double.close.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_unexpected_top_level_failure_is_captured_not_raised(self):
        with patch.object(
            league_nlp_sweep,
            "_live_competition_ids",
            AsyncMock(side_effect=RuntimeError("db gone")),
        ):
            counts = await league_nlp_sweep.run_league_nlp_sweep(MagicMock())
        assert counts["error"] == "db gone"
