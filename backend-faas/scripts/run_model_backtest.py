"""
Run backtests for individual ML models.

Usage:
    uv run python scripts/run_model_backtest.py --season 2025
    uv run python scripts/run_model_backtest.py --season 2025 --model weather_impact
    uv run python scripts/run_model_backtest.py --season 2025 --generate-predictions
"""

import argparse
import asyncio
import json
import sys
import os

# Setup path for imports — scripts/ is inside backend-faas/
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from packages.shared.db import _get_session_factory, dispose_engine
from packages.shared.services.backtest import BacktestService


async def main():
    parser = argparse.ArgumentParser(description="Run model backtests")
    parser.add_argument("--season", type=int, required=True, help="Season year to backtest")
    parser.add_argument(
        "--model", type=str, default=None, help="Specific model to backtest (default: all)"
    )
    parser.add_argument(
        "--generate-predictions",
        action="store_true",
        help="Generate predictions for games without them",
    )
    parser.add_argument("--verbose", action="store_true", help="Verbose output")
    args = parser.parse_args()

    SessionLocal = _get_session_factory()
    had_error = False

    try:
        async with SessionLocal() as db:
            service = BacktestService()

            if args.generate_predictions:
                print(f"Generating predictions for season {args.season}...")
                results = await service.run_model_backtest(db, args.season)
            elif args.model:
                result = await service.calculate_backtest_from_model_predictions(
                    db, args.season, args.model
                )
                results = [result]
            else:
                results = await service.compare_models(db, args.season)

            # Print results as formatted table
            print(f"\n{'=' * 80}")
            print(f"Model Backtest Results - Season {args.season}")
            print(f"{'=' * 80}")
            print(
                f"{'Model':<20} {'Tips':>6} {'Correct':>8} {'Accuracy':>10} {'Profit':>10}"
            )
            print(f"{'-' * 20} {'-' * 6} {'-' * 8} {'-' * 10} {'-' * 10}")
            for r in sorted(
                results, key=lambda x: x.get("overall_accuracy", 0), reverse=True
            ):
                print(
                    f"{r['model_name']:<20} {r['total_tips']:>6} {r['total_correct']:>8} "
                    f"{r['overall_accuracy']:>9.1%} ${r['total_profit']:>9.0f}"
                )
            print(f"{'=' * 80}\n")

            if args.verbose:
                print(json.dumps(results, indent=2))

    except Exception as e:
        had_error = True
        print(f"Error: {e}")
        sys.exit(1)
    finally:
        await dispose_engine(force=had_error)


if __name__ == "__main__":
    asyncio.run(main())
