"""Unit tests for the GF-TRIGGER round-completion redesign.

Covers the three new pieces:

1. ``_detect_rounds_completed`` — a round whose last game just
   completed is reported as complete (drives everything else).
2. ``schedule_round_completion_rerun`` / ``compute_rerun_time`` — the
   one-shot night rerun scheduling (that night, not the next day).
3. ``_round_nlp_sweep`` — AI content back-fill that no longer depends
   on ``tips_created > 0`` (the bug that left the grand-final report
   ungenerated forever once tips existed).
"""

from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from zoneinfo import ZoneInfo

import pytest

from packages.shared.services.match_completion import MatchCompletionDetectorService
from packages.shared.services.tip_generation import _round_nlp_sweep

_PERTH = ZoneInfo("Australia/Perth")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_detector(db: AsyncMock) -> MatchCompletionDetectorService:
    return MatchCompletionDetectorService(
        squiggle_client=MagicMock(),
        db_session=db,
        buffer_minutes=60,
    )


class _FakeSessionFactory:
    """Zero-arg callable returning an async CM yielding a mock session."""

    def __init__(self) -> None:
        self.session = AsyncMock()

    def __call__(self):
        return self

    async def __aenter__(self):
        return self.session

    async def __aexit__(self, *exc):
        return False


# ---------------------------------------------------------------------------
# 1. Round-completion detection
# ---------------------------------------------------------------------------


class TestDetectRoundsCompleted:
    @pytest.mark.asyncio
    async def test_round_fully_complete_is_reported(self):
        db = AsyncMock()
        # The count query returns 0 remaining for the round.
        db.execute = AsyncMock(return_value=MagicMock(scalar=lambda: 0))
        detector = _make_detector(db)

        finished = SimpleNamespace(season=2026, round_id=28)
        rounds = await detector._detect_rounds_completed([finished])

        assert rounds == [{"season": 2026, "round_id": 28}]
        db.execute.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_round_with_remaining_games_not_reported(self):
        db = AsyncMock()
        db.execute = AsyncMock(return_value=MagicMock(scalar=lambda: 2))
        detector = _make_detector(db)

        rounds = await detector._detect_rounds_completed(
            [SimpleNamespace(season=2026, round_id=27)]
        )

        assert rounds == []

    @pytest.mark.asyncio
    async def test_multiple_rounds_and_dedup(self):
        db = AsyncMock()
        # Two distinct rounds, both complete.
        db.execute = AsyncMock(return_value=MagicMock(scalar=lambda: 0))
        detector = _make_detector(db)

        finished = [
            SimpleNamespace(season=2026, round_id=27),
            SimpleNamespace(season=2026, round_id=27),  # duplicate → deduped
            SimpleNamespace(season=2026, round_id=28),
        ]
        rounds = await detector._detect_rounds_completed(finished)

        assert rounds == [
            {"season": 2026, "round_id": 27},
            {"season": 2026, "round_id": 28},
        ]

    @pytest.mark.asyncio
    async def test_count_query_failure_skips_round(self):
        db = AsyncMock()
        db.execute = AsyncMock(side_effect=RuntimeError("db hiccup"))
        detector = _make_detector(db)

        rounds = await detector._detect_rounds_completed(
            [SimpleNamespace(season=2026, round_id=27)]
        )

        assert rounds == []

    @pytest.mark.asyncio
    async def test_run_match_completion_passes_rounds_through(self, monkeypatch):
        """``run_match_completion`` surfaces ``rounds_completed`` from the
        detector stats so the cron wrapper can schedule the rerun."""
        from packages.shared.services.match_completion import run_match_completion

        stats = {
            "games_checked": 1,
            "games_completed": 1,
            "games_already_completed": 0,
            "games_not_ready": 0,
            "errors": [],
            "rounds_completed": [{"season": 2026, "round_id": 28}],
        }
        mock_squiggle = MagicMock()
        mock_squiggle.close = AsyncMock()
        mock_detector = MagicMock()
        mock_detector.detect_and_process_completed_matches = AsyncMock(
            return_value=stats
        )
        monkeypatch.setattr(
            "packages.shared.services.match_completion.SquiggleClient",
            lambda: mock_squiggle,
        )
        monkeypatch.setattr(
            "packages.shared.services.match_completion.MatchCompletionDetectorService",
            lambda **kwargs: mock_detector,
        )
        elo = MagicMock()
        elo.update_cache = AsyncMock()
        monkeypatch.setattr(
            "packages.shared.services.match_completion.EloModel", elo
        )
        monkeypatch.setattr(
            "packages.shared.services.match_completion.invalidate_cache_pattern",
            AsyncMock(return_value=0),
        )

        result = await run_match_completion(AsyncMock())

        assert result["rounds_completed"] == [{"season": 2026, "round_id": 28}]


# ---------------------------------------------------------------------------
# 2. Night rerun scheduling
# ---------------------------------------------------------------------------


class TestComputeRerunTime:
    def test_before_rerun_hour_schedules_same_night(self):
        from app.cron.tip_generation import compute_rerun_time

        now = datetime(2026, 9, 19, 21, 30, tzinfo=_PERTH)
        assert compute_rerun_time(now, 22) == datetime(
            2026, 9, 19, 22, 0, tzinfo=_PERTH
        )

    def test_after_rerun_hour_still_that_night(self):
        from app.cron.tip_generation import compute_rerun_time

        now = datetime(2026, 9, 19, 23, 15, tzinfo=_PERTH)
        result = compute_rerun_time(now, 22)
        # NOT tomorrow 22:00 — 30 minutes after detection.
        assert result == now + timedelta(minutes=30)
        assert result.date() == now.date()

    def test_exactly_at_hour_schedules_delay(self):
        from app.cron.tip_generation import compute_rerun_time

        now = datetime(2026, 9, 19, 22, 0, tzinfo=_PERTH)
        assert compute_rerun_time(now, 22) == now + timedelta(minutes=30)


class TestScheduleRoundCompletionRerun:
    def test_no_rounds_returns_none_without_scheduling(self):
        from app.cron.tip_generation import schedule_round_completion_rerun

        scheduler = MagicMock()
        with patch("app.cron.tip_generation.get_scheduler", return_value=scheduler):
            result = schedule_round_completion_rerun(_FakeSessionFactory(), [])

        assert result is None
        scheduler.add_job.assert_not_called()

    def test_no_running_scheduler_returns_none(self):
        from app.cron.tip_generation import schedule_round_completion_rerun

        scheduler = MagicMock()
        scheduler.running = False
        with patch("app.cron.tip_generation.get_scheduler", return_value=scheduler):
            result = schedule_round_completion_rerun(
                _FakeSessionFactory(),
                [{"season": 2026, "round_id": 28}],
            )

        assert result is None
        scheduler.add_job.assert_not_called()

    def test_rounds_schedule_one_shot_at_rerun_hour(self):
        from app.cron.tip_generation import (
            RERUN_JOB_ID,
            schedule_round_completion_rerun,
        )

        scheduler = MagicMock()
        scheduler.running = True
        scheduler.timezone = _PERTH
        now = datetime(2026, 9, 19, 20, 0, tzinfo=_PERTH)

        with patch("app.cron.tip_generation.get_scheduler", return_value=scheduler):
            result = schedule_round_completion_rerun(
                _FakeSessionFactory(),
                [{"season": 2026, "round_id": 28}],
                now=now,
            )

        assert result == datetime(2026, 9, 19, 22, 0, tzinfo=_PERTH)
        scheduler.add_job.assert_called_once()
        kwargs = scheduler.add_job.call_args.kwargs
        assert kwargs["id"] == RERUN_JOB_ID
        assert kwargs["replace_existing"] is True
        assert kwargs["max_instances"] == 1
        # DateTrigger is passed positionally (job fn, trigger, ...).
        trigger = scheduler.add_job.call_args.args[1]
        assert trigger.run_date == result


# ---------------------------------------------------------------------------
# 3. MatchCompletionJob wires detection → scheduling
# ---------------------------------------------------------------------------


class TestMatchCompletionJobSchedulesRerun:
    @pytest.mark.asyncio
    async def test_run_schedules_rerun_when_round_completes(self, monkeypatch):
        from app.cron.match_completion import MatchCompletionJob

        factory = _FakeSessionFactory()
        completion_result = {
            "status": "success",
            "rounds_completed": [{"season": 2026, "round_id": 28}],
        }
        monkeypatch.setattr(
            "app.cron.match_completion.run_match_completion",
            AsyncMock(return_value=completion_result),
        )
        schedule = MagicMock(return_value=None)
        monkeypatch.setattr(
            "app.cron.match_completion.schedule_round_completion_rerun", schedule
        )

        job = MatchCompletionJob(factory)
        result = await job.run()

        assert result == completion_result
        schedule.assert_called_once_with(
            factory, [{"season": 2026, "round_id": 28}]
        )

    @pytest.mark.asyncio
    async def test_run_skips_scheduling_without_completed_rounds(self, monkeypatch):
        from app.cron.match_completion import MatchCompletionJob

        factory = _FakeSessionFactory()
        monkeypatch.setattr(
            "app.cron.match_completion.run_match_completion",
            AsyncMock(return_value={"status": "success", "rounds_completed": []}),
        )
        schedule = MagicMock()
        monkeypatch.setattr(
            "app.cron.match_completion.schedule_round_completion_rerun", schedule
        )

        await MatchCompletionJob(factory).run()

        schedule.assert_not_called()

    @pytest.mark.asyncio
    async def test_scheduler_failure_does_not_fail_job(self, monkeypatch):
        from app.cron.match_completion import MatchCompletionJob

        factory = _FakeSessionFactory()
        monkeypatch.setattr(
            "app.cron.match_completion.run_match_completion",
            AsyncMock(
                return_value={
                    "status": "success",
                    "rounds_completed": [{"season": 2026, "round_id": 28}],
                }
            ),
        )
        monkeypatch.setattr(
            "app.cron.match_completion.schedule_round_completion_rerun",
            MagicMock(side_effect=RuntimeError("scheduler gone")),
        )

        result = await MatchCompletionJob(factory).run()

        assert result["status"] == "success"


# ---------------------------------------------------------------------------
# 4. NLP sweep — AI content no longer gated on tips_created
# ---------------------------------------------------------------------------


def _game(sid: int, home: str = "Brisbane", away: str = "Collingwood"):
    return SimpleNamespace(id=sid, home_team=home, away_team=away)


class TestRoundNlpSweep:
    @pytest.mark.asyncio
    async def test_no_round_is_a_noop(self):
        counts = await _round_nlp_sweep(AsyncMock(), None, None)
        assert counts == {"match_analyses_created": 0, "match_reports_created": 0}

    @pytest.mark.asyncio
    async def test_missing_analysis_and_gf_report_generated(self):
        """The core regression: tips already exist (nothing created via
        the tips path), yet the sweep still generates the missing match
        analysis AND the grand-final report."""
        games = [_game(1)]
        analysis_service = MagicMock()
        analysis_service.generate_and_store_analysis = AsyncMock(
            return_value="talking points"
        )
        analysis_service.close = AsyncMock()
        report_service = MagicMock()
        report_service.generate_and_store_report = AsyncMock(
            return_value={"report": True}
        )
        report_service.close = AsyncMock()

        with (
            patch(
                "packages.shared.services.tip_generation.GameCRUD"
            ) as mock_game_crud,
            patch(
                "packages.shared.crud.match_analysis.MatchAnalysisCRUD"
            ) as mock_analysis_crud,
            patch(
                "packages.shared.crud.match_report.MatchReportCRUD"
            ) as mock_report_crud,
            patch(
                "packages.shared.services.match_analysis.MatchAnalysisService",
                return_value=analysis_service,
            ),
            patch(
                "packages.shared.services.match_report.MatchReportService",
                return_value=report_service,
            ) as mock_report_service_cls,
        ):
            mock_game_crud.get_by_round = AsyncMock(return_value=games)
            mock_analysis_crud.get_by_game_id = AsyncMock(return_value=None)
            mock_report_crud.get_by_game_id = AsyncMock(return_value=None)
            mock_report_service_cls.is_grand_final = AsyncMock(return_value=True)

            counts = await _round_nlp_sweep(AsyncMock(), 2026, 28)

        assert counts["match_analyses_created"] == 1
        assert counts["match_reports_created"] == 1
        analysis_service.generate_and_store_analysis.assert_awaited_once()
        report_service.generate_and_store_report.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_existing_content_is_not_regenerated(self):
        games = [_game(1)]
        analysis_service = MagicMock()
        analysis_service.generate_and_store_analysis = AsyncMock()
        analysis_service.close = AsyncMock()
        report_service = MagicMock()
        report_service.generate_and_store_report = AsyncMock()
        report_service.close = AsyncMock()

        with (
            patch(
                "packages.shared.services.tip_generation.GameCRUD"
            ) as mock_game_crud,
            patch(
                "packages.shared.crud.match_analysis.MatchAnalysisCRUD"
            ) as mock_analysis_crud,
            patch(
                "packages.shared.crud.match_report.MatchReportCRUD"
            ) as mock_report_crud,
            patch(
                "packages.shared.services.match_analysis.MatchAnalysisService",
                return_value=analysis_service,
            ),
            patch(
                "packages.shared.services.match_report.MatchReportService",
                return_value=report_service,
            ) as mock_report_service_cls,
        ):
            mock_game_crud.get_by_round = AsyncMock(return_value=games)
            mock_analysis_crud.get_by_game_id = AsyncMock(
                return_value=SimpleNamespace(id=1)  # analysis exists
            )
            mock_report_crud.get_by_game_id = AsyncMock(
                return_value=SimpleNamespace(id=1)  # report exists
            )
            mock_report_service_cls.is_grand_final = AsyncMock(return_value=True)

            counts = await _round_nlp_sweep(AsyncMock(), 2026, 28)

        assert counts == {"match_analyses_created": 0, "match_reports_created": 0}
        analysis_service.generate_and_store_analysis.assert_not_awaited()
        report_service.generate_and_store_report.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_non_gf_game_skips_report(self):
        games = [_game(1)]
        analysis_service = MagicMock()
        analysis_service.generate_and_store_analysis = AsyncMock(
            return_value=None  # generation declined
        )
        analysis_service.close = AsyncMock()
        report_service = MagicMock()
        report_service.generate_and_store_report = AsyncMock()
        report_service.close = AsyncMock()

        with (
            patch(
                "packages.shared.services.tip_generation.GameCRUD"
            ) as mock_game_crud,
            patch(
                "packages.shared.crud.match_analysis.MatchAnalysisCRUD"
            ) as mock_analysis_crud,
            patch(
                "packages.shared.crud.match_report.MatchReportCRUD"
            ),
            patch(
                "packages.shared.services.match_analysis.MatchAnalysisService",
                return_value=analysis_service,
            ),
            patch(
                "packages.shared.services.match_report.MatchReportService",
                return_value=report_service,
            ) as mock_report_service_cls,
        ):
            mock_game_crud.get_by_round = AsyncMock(return_value=games)
            mock_analysis_crud.get_by_game_id = AsyncMock(return_value=None)
            mock_report_service_cls.is_grand_final = AsyncMock(return_value=False)

            counts = await _round_nlp_sweep(AsyncMock(), 2026, 27)

        assert counts["match_reports_created"] == 0
        report_service.generate_and_store_report.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_tbc_games_are_skipped(self):
        games = [SimpleNamespace(id=1, home_team=None, away_team="Brisbane")]

        with patch(
            "packages.shared.services.tip_generation.GameCRUD"
        ) as mock_game_crud:
            mock_game_crud.get_by_round = AsyncMock(return_value=games)
            counts = await _round_nlp_sweep(AsyncMock(), 2026, 28)

        assert counts == {"match_analyses_created": 0, "match_reports_created": 0}

    @pytest.mark.asyncio
    async def test_service_failure_does_not_raise(self):
        games = [_game(1)]
        analysis_service = MagicMock()
        analysis_service.generate_and_store_analysis = AsyncMock(
            side_effect=RuntimeError("OpenRouter down")
        )
        analysis_service.close = AsyncMock()

        with (
            patch(
                "packages.shared.services.tip_generation.GameCRUD"
            ) as mock_game_crud,
            patch(
                "packages.shared.crud.match_analysis.MatchAnalysisCRUD"
            ) as mock_analysis_crud,
            patch(
                "packages.shared.crud.match_report.MatchReportCRUD"
            ),
            patch(
                "packages.shared.services.match_analysis.MatchAnalysisService",
                return_value=analysis_service,
            ),
            patch(
                "packages.shared.services.match_report.MatchReportService"
            ) as mock_report_service_cls,
        ):
            mock_game_crud.get_by_round = AsyncMock(return_value=games)
            mock_analysis_crud.get_by_game_id = AsyncMock(return_value=None)
            mock_report_service_cls.is_grand_final = AsyncMock(return_value=False)

            counts = await _round_nlp_sweep(AsyncMock(), 2026, 27)

        # Analysis failed, but the sweep itself never raises.
        assert counts["match_analyses_created"] == 0
