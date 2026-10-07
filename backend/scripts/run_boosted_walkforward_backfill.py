"""Ad-hoc boosted-tip walk-forward backfill runner (BT-1).

Backfills ``boosted_tip`` tips for completed rounds: each round is tipped
by a model trained ONLY on strictly-earlier completed games (honest
walk-forward), falling back to majority vote below the 100-row training
threshold.  Idempotent — existing boosted tips are skipped.

Usage (from ``backend/``)::

    uv run python scripts/run_boosted_walkforward_backfill.py --dry-run
    uv run python scripts/run_boosted_walkforward_backfill.py --seasons 2026
    uv run python scripts/run_boosted_walkforward_backfill.py --seasons 2024,2025,2026
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys

from packages.shared.config import settings
from packages.shared.db import _get_session_factory
from packages.shared.logger import get_logger
from packages.shared.services.boosted_walkforward import (
    run_boosted_walkforward_backfill,
)

logger = get_logger(__name__)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Walk-forward backfill of boosted_tip tips (BT-1)."
    )
    parser.add_argument(
        "--seasons",
        type=str,
        default="",
        help="Comma-separated seasons (default: the configured current season).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Compute the per-round plan and print it without writing tips.",
    )
    return parser.parse_args()


async def main() -> None:
    args = _parse_args()
    seasons = (
        [int(s.strip()) for s in args.seasons.split(",") if s.strip()]
        if args.seasons
        else [settings.current_season]
    )

    session_factory = _get_session_factory()
    async with session_factory() as session:
        summary = await run_boosted_walkforward_backfill(
            session, seasons, dry_run=args.dry_run
        )

    print(json.dumps(summary, indent=2, default=str))

    per_round = summary.get("per_round", [])
    fallback_rounds = sum(1 for r in per_round if r["mode"] == "fallback")
    print(
        f"\n{summary['status']}: {summary['tips_created']} tips created, "
        f"{summary['tips_skipped_existing']} skipped (existing), "
        f"{summary['rounds_processed']} rounds "
        f"({fallback_rounds} fallback / "
        f"{summary['rounds_processed'] - fallback_rounds} trained)"
    )


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as exc:  # noqa: BLE001 — scripts surface errors plainly
        print(f"Backfill failed: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
