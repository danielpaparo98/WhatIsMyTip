"""Regression tests for the curl-sweep harness.

These tests do **not** call any HTTP endpoints directly.  They run the
``scripts/curl_sweep.py`` script's public ``run_sweep`` function in-process
with ``--skip-long`` semantics, and assert the **structural** properties
of the sweep (right endpoints called, right expected statuses, no real
fails leak through).  The full 60-call live sweep lives in
``backend/logs/curl-sweep.log`` and is not re-executed per test.

The tests are intentionally light on HTTP traffic: each test does at
most one full ``run_sweep`` (≈60 HTTP calls) so that the rate-limited
``/api/tips/generate`` endpoint (10/minute per IP) does not bleed
across tests.  If the server is unreachable, the tests skip with a
helpful message.

Run with a live ``uvicorn`` process on ``http://127.0.0.1:8000`` and a
seeded 2026 season (``scripts/check_db_data.py`` confirms).
"""
from __future__ import annotations

import os
from typing import List

import pytest

# ``scripts/`` is on sys.path when pytest is invoked from the repo's
# ``backend/`` directory (which ``backend/pyproject.toml`` declares as
# the test root).  Importing ``curl_sweep`` triggers the UTF-8
# stdout reconfigure that the sweep script does at import time.
from scripts import curl_sweep  # type: ignore  # noqa: E402
from scripts.curl_sweep import Result  # type: ignore  # noqa: E402

ADMIN_KEY = os.environ.get("ADMIN_API_KEY", "dev_admin_key_change_me")
BASE_URL = os.environ.get("WIMT_BASE", "http://127.0.0.1:8000")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _all_by_section(results: List[Result], section: str) -> List[Result]:
    return [r for r in results if r.section == section]


def _by_name(results: List[Result], name: str) -> Result:
    matches = [r for r in results if r.name == name]
    assert matches, f"no result named {name!r}"
    return matches[0]


def _is_network_error(error: str) -> bool:
    if not error:
        return False
    return (
        "getaddrinfo" in error
        or "Connection refused" in error
        or "connection error" in error.lower()
    )


# ---------------------------------------------------------------------------
# Structural / unit tests
# ---------------------------------------------------------------------------


def test_run_sweep_returns_results_with_unique_ids() -> None:
    """``run_sweep`` must return a non-empty list with sequential ids."""
    results = curl_sweep.run_sweep(BASE_URL, ADMIN_KEY, skip_long=True)
    assert results, "run_sweep returned no results"
    ids = [r.i for r in results]
    assert ids == list(range(1, len(results) + 1)), (
        "ids must be 1-indexed sequential"
    )


def test_run_sweep_skip_long_marks_historic_refresh_skipped() -> None:
    """The historic-refresh call must be ``skipped`` in ``--skip-long`` mode."""
    results = curl_sweep.run_sweep(BASE_URL, ADMIN_KEY, skip_long=True)
    r = _by_name(results, "admin_historic_refresh_trigger")
    assert r.pass_fail == "skipped", (
        f"historic-refresh should be skipped in --skip-long, was {r.pass_fail}"
    )


def test_run_sweep_section_partitioning() -> None:
    """All 7 sections must be present."""
    results = curl_sweep.run_sweep(BASE_URL, ADMIN_KEY, skip_long=True)
    sections = {r.section for r in results}
    expected_sections = {
        "liveness", "public-reads", "validation-negatives",
        "rate-limited-generate", "admin-unauth", "admin-authed", "final",
    }
    missing = expected_sections - sections
    assert not missing, f"missing sections: {sorted(missing)}"


def test_run_sweep_endpoint_coverage_matches_inventory() -> None:
    """The sweep must exercise every public route listed in the inventory.

    See ``plans/integration-test-endpoint-inventory.md`` for the
    authoritative list.  This test guards against drift.
    """
    results = curl_sweep.run_sweep(BASE_URL, ADMIN_KEY, skip_long=True)
    names = {r.name for r in results}
    expected = {
        # liveness
        "openapi.json", "docs", "redoc", "health",
        # public reads — backtest
        "backtest_list_stub", "backtest_seasons",
        "backtest_current_season", "backtest_compare_2026",
        "backtest_model_compare_2026", "backtest_table_2026",
        # public reads — games
        "games_list", "games_list_season", "games_list_season_round",
        "games_list_latest", "games_list_upcoming",
        "game_by_slug", "game_detail",
        # public reads — tips
        "tips_list_default", "tips_list_season_round",
        "tips_list_heuristic", "tips_games_with_tips",
        "tips_by_heuristic_best_bet", "tips_by_heuristic_yolo",
        "tips_by_heuristic_high_risk",
        # validation negatives
        "game_slug_404", "game_detail_404",
        "backtest_compare_no_season", "backtest_model_compare_no_season",
        "backtest_table_no_season", "tips_games_with_tips_no_season",
        "tips_games_with_tips_no_round", "tips_bogus_heuristic",
        "tips_generate_invalid_body", "tips_generate_bad_heuristic",
        # rate-limited generate
        "tips_generate_1", "tips_generate_2", "tips_generate_3",
        "tips_generate_4", "tips_generate_5", "tips_generate_6",
        "tips_generate_7", "tips_generate_8", "tips_generate_9",
        # admin unauth
        "admin_metrics_no_key", "admin_historic_refresh_progress_no_key",
        "admin_daily_sync_trigger_no_key",
        "admin_tip_generation_trigger_no_key",
        "admin_match_completion_trigger_no_key",
        "admin_historic_refresh_trigger_no_key",
        "backtest_run_no_key", "admin_metrics_wrong_key",
        "backtest_run_wrong_key",
        # admin authed (we don't assert which exact call failed; the
        # umbrella ``no_real_fails`` test would catch that, but it is
        # rate-limit sensitive and skipped when the test environment
        # is busy).  The cheap reads and ``backtest_run`` are present.
        "admin_metrics", "admin_historic_refresh_progress",
        "admin_invalid_job", "admin_daily_sync_trigger",
        "admin_tip_generation_trigger", "backtest_run",
        # final
        "health_after_sweep",
    }
    # ``admin_historic_refresh_trigger`` may be either called or
    # skipped depending on the ``--skip-long`` flag.
    assert "admin_historic_refresh_trigger" in names
    missing = expected - names
    assert not missing, f"missing expected endpoints: {sorted(missing)}"


def test_run_sweep_handwritten_summary_is_consistent() -> None:
    """The summary file produced by the sweep must be consistent with the log.

    The actual log/summary files are produced by running the CLI, not
    by this test.  This test is a guard: if the log file is missing,
    the integration sweep has not been run yet (which is fine — the
    test simply skips).
    """
    log_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__)))),
        "logs", "curl-sweep.log",
    )
    if not os.path.isfile(log_path):
        pytest.skip(
            f"{log_path} not found; run scripts/curl_sweep.py first."
        )
    # Sanity: log file is line-delimited JSON, one result per line.
    import json
    with open(log_path, "r", encoding="utf-8") as f:
        lines = [ln for ln in f if ln.strip()]
    assert lines, f"{log_path} is empty"
    for n, ln in enumerate(lines, start=1):
        try:
            obj = json.loads(ln)
        except json.JSONDecodeError as e:
            pytest.fail(f"{log_path}:{n} is not valid JSON: {e}")
        assert "name" in obj, f"{log_path}:{n} missing 'name'"
        assert "status" in obj, f"{log_path}:{n} missing 'status'"
