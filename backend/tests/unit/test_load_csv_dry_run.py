"""Unit tests for the --dry-run flag of load_csv_to_db.py.

The ``--dry-run`` flag lets a developer inspect what a real load would
do (row counts per table, file existence, season discovery) without
needing a live database connection.  This is critical for:

  * the make-data.sh smoke test (no DB -> no migration -> quick check)
  * CI checks that the CSVs are well-formed
  * developer workflows when a Postgres instance is not running

This is a NEW flag added in Phase 7.
"""

from __future__ import annotations

import csv
import os
import sys
from unittest.mock import patch

import pytest

# Ensure backend is on sys.path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_csv(path: str, fieldnames: list, rows: list) -> None:
    """Write a small CSV file at *path*."""
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _setup_sample_data(tmp_path) -> str:
    """Create a sample ./data/ tree with multi-season subdirs."""
    # Per-season subdirs (as produced by scrape_to_csv with --season 2024 2025)
    for season in (2024, 2025):
        season_dir = tmp_path / str(season)
        season_dir.mkdir()
        _write_csv(
            str(season_dir / "match_details.csv"),
            ["game_id", "home_team", "away_team", "match_date", "season"],
            [
                {
                    "game_id": f"{season}030101",
                    "home_team": "A",
                    "away_team": "B",
                    "match_date": f"{season}-03-01",
                    "season": str(season),
                }
            ] * 5,
        )
        _write_csv(
            str(season_dir / "players.csv"),
            ["id", "name"],
            [{"id": str(i), "name": f"Player {i}"} for i in range(1, 11)],
        )
        _write_csv(
            str(season_dir / "player_match_stats.csv"),
            ["game_id", "player_id", "team", "kicks"],
            [
                {
                    "game_id": f"{season}030101",
                    "player_id": str(i),
                    "team": "A",
                    "kicks": str(i * 2),
                }
                for i in range(1, 11)
            ],
        )
        _write_csv(
            str(season_dir / "match_weather.csv"),
            ["game_id", "venue", "match_date", "temperature"],
            [
                {
                    "game_id": f"{season}030101",
                    "venue": "MCG",
                    "match_date": f"{season}-03-01",
                    "temperature": "18.5",
                }
            ] * 3,
        )

    # Global injuries.csv at the top level (not per-season)
    _write_csv(
        str(tmp_path / "injuries.csv"),
        [
            "player_name",
            "team",
            "injury_type",
            "return_timeline",
            "source",
            "scraped_at",
        ],
        [
            {
                "player_name": "Daicos, Nick",
                "team": "Collingwood",
                "injury_type": "Hamstring",
                "return_timeline": "Test",
                "source": "footywire",
                "scraped_at": "2026-06-16T00:00:00+00:00",
            }
        ],
    )

    return str(tmp_path)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestDryRunSummary:
    """dry_run_summary() must read CSVs and return a row-count dict
    without touching the database."""

    def test_returns_row_count_per_table(self, tmp_path):
        from scripts.load_csv_to_db import dry_run_summary

        data_dir = _setup_sample_data(tmp_path)
        summary = dry_run_summary(data_dir, seasons=[2024, 2025])

        # Per-season CSVs (2 seasons -> multiplied counts)
        assert summary["seasons"] == [2024, 2025]
        # match_details: 5 rows x 2 seasons
        assert summary["match_details"] == 10
        # players: 10 rows x 2 seasons
        assert summary["players"] == 20
        # player_match_stats: 10 rows x 2 seasons
        assert summary["player_match_stats"] == 20
        # match_weather: 3 rows x 2 seasons
        assert summary["match_weather"] == 6
        # injuries: 1 row global
        assert summary["injuries"] == 1

    def test_returns_zero_for_missing_files(self, tmp_path):
        """Empty data dir -> all tables 0, no seasons discovered."""
        from scripts.load_csv_to_db import dry_run_summary

        summary = dry_run_summary(str(tmp_path), seasons=None)
        assert summary["seasons"] == []
        assert summary["match_details"] == 0
        assert summary["players"] == 0
        assert summary["player_match_stats"] == 0
        assert summary["match_weather"] == 0
        assert summary["injuries"] == 0

    def test_single_season_no_subdir(self, tmp_path):
        """Single-season runs put CSVs at the top level (no per-season subdir)."""
        from scripts.load_csv_to_db import dry_run_summary

        _write_csv(
            str(tmp_path / "match_details.csv"),
            ["game_id", "home_team", "away_team", "match_date", "season"],
            [
                {
                    "game_id": "2025x",
                    "home_team": "A",
                    "away_team": "B",
                    "match_date": "2025-03-01",
                    "season": "2025",
                }
            ],
        )
        _write_csv(
            str(tmp_path / "injuries.csv"),
            ["player_name", "team", "injury_type"],
            [{"player_name": "X", "team": "Y", "injury_type": "Z"}],
        )
        summary = dry_run_summary(str(tmp_path), seasons=[2025])
        assert summary["match_details"] == 1
        assert summary["injuries"] == 1
        assert summary["players"] == 0
        assert summary["player_match_stats"] == 0

    def test_no_database_connection_required(self, tmp_path):
        """dry_run_summary must not call get_engine or create a session."""
        from scripts import load_csv_to_db

        data_dir = _setup_sample_data(tmp_path)

        # Patch get_engine to raise if anyone tries to use it.
        def _must_not_run(*_args, **_kwargs):
            raise AssertionError(
                "dry_run_summary must not open a DB connection"
            )

        original = load_csv_to_db.get_engine
        load_csv_to_db.get_engine = _must_not_run
        try:
            load_csv_to_db.dry_run_summary(data_dir, seasons=[2024, 2025])
        finally:
            load_csv_to_db.get_engine = original


class TestDryRunCli:
    """The CLI ``--dry-run`` flag must short-circuit the DB load."""

    def test_dry_run_flag_registered(self):
        """--dry-run must be a registered argparse action in main()."""
        # Patch argparse.ArgumentParser to capture the parser instance
        from scripts import load_csv_to_db

        captured_parsers = []

        original_parser = None
        from argparse import ArgumentParser

        original_init = ArgumentParser.__init__

        def _capturing_init(self, *args, **kwargs):
            original_init(self, *args, **kwargs)
            captured_parsers.append(self)

        with patch.object(ArgumentParser, "__init__", _capturing_init):
            saved = sys.argv
            try:
                sys.argv = ["load_csv_to_db", "--help"]
                try:
                    load_csv_to_db.main()
                except SystemExit:
                    pass
            finally:
                sys.argv = saved

        # Find the parser that has --dry-run registered
        found = False
        for p in captured_parsers:
            # We can't easily check the registered actions, but we
            # can try parsing --dry-run
            try:
                ns = p.parse_args(["--dry-run"])
                if getattr(ns, "dry_run", False):
                    found = True
                    break
            except SystemExit:
                pass
        assert found, "--dry-run not registered on the main() parser"

    def test_dry_run_short_circuits_db_load(self, tmp_path, capsys):
        """Running main() with --dry-run must NOT call load_csv_data."""
        from scripts import load_csv_to_db

        data_dir = _setup_sample_data(tmp_path)

        saved = sys.argv
        try:
            sys.argv = [
                "load_csv_to_db",
                "--input-dir",
                data_dir,
                "--dry-run",
            ]
            try:
                load_csv_to_db.main()
            except SystemExit:
                pass
        finally:
            sys.argv = saved

        captured = capsys.readouterr()
        assert "DRY RUN" in captured.out
        # The table-name column headers should be present
        assert "match_details" in captured.out
        assert "players" in captured.out
