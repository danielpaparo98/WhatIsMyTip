r"""Read-only walk-forward backfill progress probe (NO secrets — DSN via env).

Prints ``model_predictions`` + ``tips`` counts grouped by season for 2010-2024,
plus totals and the number of completed/scored games. Used to baseline before
the backfill and to confirm early seasons are climbing during the health-check.
Safe to run concurrently with the backfill (read-only ``SELECT``s only).

Usage (cmd.exe, env supplied inline in the child only)::

    set "DATABASE_URL=postgresql+asyncpg://..." && set "DB_SSL_VERIFY=false" ^
        && set "REDIS_URL=disabled" && set "ENVIRONMENT=staging" && ^
        cd /d backend && uv run python scripts\_wf_counts.py

This is a throwaway ops helper — not tracked, deleted after the run.
"""

import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from sqlalchemy import func, select

from packages.shared.db import _get_session_factory, dispose_engine
from packages.shared.models import Game, ModelPrediction, Tip


async def main() -> None:
    sf = _get_session_factory()
    try:
        async with sf() as db:
            games = (
                await db.execute(
                    select(func.count(Game.id)).where(
                        Game.season.between(2010, 2024),
                        Game.completed.is_(True),
                    )
                )
            ).scalar()
            preds = (
                await db.execute(
                    select(Game.season, func.count(ModelPrediction.id))
                    .join(ModelPrediction, ModelPrediction.game_id == Game.id)
                    .where(Game.season.between(2010, 2024))
                    .group_by(Game.season)
                    .order_by(Game.season)
                )
            ).all()
            tips = (
                await db.execute(
                    select(Game.season, func.count(Tip.id))
                    .join(Tip, Tip.game_id == Game.id)
                    .where(Game.season.between(2010, 2024))
                    .group_by(Game.season)
                    .order_by(Game.season)
                )
            ).all()
            pred_total = sum(r[1] for r in preds)
            tip_total = sum(r[1] for r in tips)
            print(f"PROBE games_completed_2010_2024={games}")
            print(f"PROBE pred_total={pred_total} tip_total={tip_total}")
            print(
                "PROBE PRED_BY_SEASON "
                + " ".join(f"{r[0]}:{r[1]}" for r in preds)
            )
            print(
                "PROBE TIP_BY_SEASON  "
                + " ".join(f"{r[0]}:{r[1]}" for r in tips)
            )
    finally:
        await dispose_engine(force=True)


if __name__ == "__main__":
    asyncio.run(main())
