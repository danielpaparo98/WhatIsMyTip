#!/usr/bin/env python3
"""
Export seed data to CSV files.

Generates seed data using the same seed functions and writes each table
to a CSV file in backend/seed_data/.

Usage:
    uv run python scripts/export_seed_csv.py
    uv run python scripts/export_seed_csv.py --seasons 2010-2026
    uv run python scripts/export_seed_csv.py --seed 123
    uv run python scripts/export_seed_csv.py --output-dir ./my_output
"""

import argparse
import csv
import os
import random
import sys
from dataclasses import fields as dataclass_fields
from datetime import datetime, timezone
from typing import List

# Ensure backend is on sys.path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from scripts.seed_data import (
    ROUNDS_PER_SEASON,
    seed_backtest_results,
    seed_elo_cache,
    seed_games,
    seed_generation_progress,
    seed_job_executions,
    seed_match_analyses,
    seed_model_predictions,
    seed_tips,
)


def orm_to_dict(obj) -> dict:
    """Convert a SQLAlchemy ORM object to a flat dict."""
    result = {}
    for col in obj.__table__.columns:
        val = getattr(obj, col.key)
        # Convert datetime to ISO format string
        if isinstance(val, datetime):
            val = val.isoformat()
        result[col.key] = val
    return result


def write_csv(filepath: str, rows: List[dict]) -> None:
    """Write a list of dicts to a CSV file."""
    if not rows:
        # Write empty file with headers from an empty dict
        with open(filepath, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow([])
        return

    fieldnames = list(rows[0].keys())
    with open(filepath, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    print(f"  -> {os.path.basename(filepath)}: {len(rows)} records")


def _parse_seasons(seasons_str: str) -> list[int]:
    """Parse a seasons string like '2010-2026' or '2025,2026' into a list of ints."""
    if "-" in seasons_str and "," not in seasons_str:
        parts = seasons_str.split("-")
        return list(range(int(parts[0]), int(parts[1]) + 1))
    return [int(s) for s in seasons_str.split(",")]


def main() -> None:
    parser = argparse.ArgumentParser(description="Export seed data to CSV files")
    parser.add_argument(
        "--seed", type=int, default=42, help="Random seed (default: 42)"
    )
    parser.add_argument(
        "--seasons",
        default="2010-2026",
        help="Seasons to generate (e.g. '2010-2026' or '2025,2026'). Default: 2010-2026",
    )
    parser.add_argument(
        "--output-dir",
        default=None,
        help="Output directory (default: backend/seed_data/)",
    )
    args = parser.parse_args()

    output_dir = args.output_dir or os.path.join(
        os.path.dirname(__file__), "..", "seed_data"
    )
    output_dir = os.path.abspath(output_dir)
    os.makedirs(output_dir, exist_ok=True)

    rng = random.Random(args.seed)
    seasons = _parse_seasons(args.seasons)
    latest_season = max(seasons)

    print(f"Generating seed data with seed={args.seed}...")
    print(f"Seasons: {seasons[0]}-{seasons[-1]} ({len(seasons)} seasons)")
    print(f"Output directory: {output_dir}\n")

    # --- Games ---
    print("Generating games...")
    all_games = []
    for season in seasons:
        if season == latest_season:
            # Current season: partially completed
            games = seed_games(
                rng,
                season=season,
                rounds=ROUNDS_PER_SEASON,
                completed_rounds=12,
                squiggle_id_start=10000 + (season - 2010) * 1000,
            )
        else:
            # Past season: fully completed
            games = seed_games(
                rng,
                season=season,
                rounds=ROUNDS_PER_SEASON,
                completed_rounds=ROUNDS_PER_SEASON,
                squiggle_id_start=10000 + (season - 2010) * 1000,
            )
        all_games.extend(games)
    write_csv(os.path.join(output_dir, "games.csv"), [orm_to_dict(g) for g in all_games])

    # --- Model Predictions ---
    print("Generating model predictions...")
    predictions = seed_model_predictions(rng, all_games)
    write_csv(
        os.path.join(output_dir, "model_predictions.csv"),
        [orm_to_dict(p) for p in predictions],
    )

    # --- Tips ---
    print("Generating tips...")
    tips = seed_tips(rng, all_games)
    write_csv(os.path.join(output_dir, "tips.csv"), [orm_to_dict(t) for t in tips])

    # --- Elo Cache ---
    print("Generating Elo cache...")
    elo_entries = seed_elo_cache(rng, latest_season)
    write_csv(
        os.path.join(output_dir, "elo_cache.csv"),
        [orm_to_dict(e) for e in elo_entries],
    )

    # --- Backtest Results ---
    print("Generating backtest results...")
    backtest_results = []
    for season in seasons:
        if season == latest_season:
            backtest_results.extend(seed_backtest_results(rng, season, rounds=12))
        else:
            backtest_results.extend(seed_backtest_results(rng, season, rounds=ROUNDS_PER_SEASON))
    write_csv(
        os.path.join(output_dir, "backtest_results.csv"),
        [orm_to_dict(b) for b in backtest_results],
    )

    # --- Match Analyses ---
    print("Generating match analyses...")
    analyses = seed_match_analyses(rng, all_games)
    write_csv(
        os.path.join(output_dir, "match_analyses.csv"),
        [orm_to_dict(a) for a in analyses],
    )

    # --- Generation Progress ---
    print("Generating progress tracking...")
    progress = seed_generation_progress(seasons)
    write_csv(
        os.path.join(output_dir, "generation_progress.csv"),
        [orm_to_dict(p) for p in progress],
    )

    # --- Job Executions ---
    print("Generating job execution history...")
    job_execs = seed_job_executions()
    write_csv(
        os.path.join(output_dir, "job_executions.csv"),
        [orm_to_dict(j) for j in job_execs],
    )

    print(f"\nAll CSV files written to {output_dir}")


if __name__ == "__main__":
    main()
