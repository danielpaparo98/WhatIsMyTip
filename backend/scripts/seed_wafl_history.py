"""Full WAFL history seed: 5 seasons of data + model runs, NO LLMs.

Usage (demo DB via DATABASE_URL):
    uv run python scripts/seed_wafl_history.py --seasons 2022 2023 2024 2025 2026

Pipeline per season:
  1. run_wafl_sync — fixtures/results onto the events tables.
  2. PROJECTION: mirror events into the legacy ``games`` table shape —
     the prediction models still read the legacy schema until the
     service cutover.  Safe only in an AFL-free (demo) database: the
     legacy table has no competition column (that is exactly why 0010
     exists).  This script is a DEMO/PILOT tool, not a production path.
Then:
  3. EloModel.update_cache — WAFL Elo ratings from the projected games.
  4. Model sweeps + heuristics for every completed game with
     ``skip_nlp=True`` — the OpenRouter explanations/reports are never
     constructed, so no LLM is invoked.
  5. BacktestService per season — model/heuristic accuracy + the fake
     even-money profit (reported, not a real staking claim).
"""

from __future__ import annotations

import argparse
import asyncio
import time
from typing import List, Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncSession

from packages.shared.db import get_engine
from packages.shared.crud.games import GameCRUD
from packages.shared.logger import get_logger
from packages.shared.models_ml.elo import EloModel
from packages.shared.services.backtest import BacktestService
from packages.shared.services.local_competition_sync import run_wafl_sync
from packages.shared.services.tip_generation import TipGenerationService

logger = get_logger(__name__)

# events → legacy games projection (one row per event; scores from the
# event_participant sides).
_PROJECTION_SQL = text(
    """
    INSERT INTO games (slug, round_id, season, home_team, away_team,
                       home_score, away_score, venue, date, completed,
                       last_synced_at, sync_version)
    SELECT 'w' || substring(md5(e.id::text), 1, 10),
           e.round_id,
           s.label::int,
           hp.name,
           ap.name,
           h.score,
           a.score,
           e.venue,
           e.starts_at,
           e.completed,
           e.last_synced_at,
           e.sync_version
    FROM events e
    JOIN seasons s  ON s.id = e.season_id
    JOIN event_participants h ON h.event_id = e.id AND h.side = 'home'
    JOIN participants hp ON hp.id = h.participant_id
    JOIN event_participants a ON a.event_id = e.id AND a.side = 'away'
    JOIN participants ap ON ap.id = a.participant_id
    WHERE s.label = ANY(:labels)
    ON CONFLICT DO NOTHING
    """
)

# Slug is deterministic per event id, so re-runs must not duplicate.
_EXISTING_SQL = text(
    "SELECT count(*) FROM games WHERE slug LIKE 'w%'"
)


async def _project_events_to_games(session: AsyncSession, labels: List[str]) -> int:
    before = (await session.execute(_EXISTING_SQL)).scalar() or 0
    await session.execute(_PROJECTION_SQL, {"labels": labels})
    await session.commit()
    after = (await session.execute(_EXISTING_SQL)).scalar() or 0
    return after - before


async def main(seasons: List[int]) -> None:
    engine = get_engine()
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    # ------------------------------------------------------------------
    # 1. Multi-season sync (newest last so is_current lands on it).
    # ------------------------------------------------------------------
    async with session_factory() as session:
        newest = max(seasons)
        for year in sorted(seasons):
            stats = await run_wafl_sync(
                session, season=year, mark_current=(year == newest)
            )
            print(
                f"[sync] {year}: {stats['fixtures_synced']} fixtures, "
                f"{len(stats['errors'])} errors"
            )
            for error in stats["errors"][:5]:
                print(f"       ERROR: {error}")

    # ------------------------------------------------------------------
    # 2. Project events → legacy games shape (models read legacy).
    # ------------------------------------------------------------------
    async with session_factory() as session:
        projected = await _project_events_to_games(
            session, [str(y) for y in seasons]
        )
    print(f"[project] {projected} WAFL games mirrored into legacy `games`")

    # ------------------------------------------------------------------
    # 3. Elo ratings from the projected history.
    # ------------------------------------------------------------------
    async with session_factory() as session:
        await EloModel.update_cache(session)
    print("[elo] ratings cache updated")

    # ------------------------------------------------------------------
    # 4. Model sweeps + heuristics (skip_nlp=True — no LLM).
    # ------------------------------------------------------------------
    sweep_start = time.time()
    async with session_factory() as session:
        service = TipGenerationService(session)
        for year in sorted(seasons):
            games = await GameCRUD.get_by_season(session, year)
            done = [g for g in games if g.completed]
            if not done:
                print(f"[tips] {year}: no completed games")
                continue
            stats = await service.generate_batch(done, regenerate=False)
            print(
                f"[tips] {year}: {stats['games_processed']} games, "
                f"{stats['tips_created']} tips, "
                f"{stats['model_predictions_created']} predictions, "
                f"{len(stats['errors'])} errors"
            )
    print(f"[tips] sweep took {time.time() - sweep_start:.0f}s")

    # ------------------------------------------------------------------
    # 5. Backtest — per-season model/heuristic accuracy.
    # ------------------------------------------------------------------
    backtest = BacktestService()
    async with session_factory() as session:
        for year in sorted(seasons):
            print(f"\n=== {year} model comparison (accuracy by model) ===")
            rows = await backtest.compare_models(session, year)
            for row in rows:
                print(
                    f"  {row['model_name']:<16} "
                    f"acc={row['overall_accuracy']:.3f}  "
                    f"tips={row['total_tips']}"
                )
            print(f"  --- heuristics (fake even-money profit) ---")
            for heuristic in ("best_bet", "weighted_tip", "yolo"):
                try:
                    hb = await backtest.calculate_backtest_from_tips(
                        session, year, heuristic
                    )
                except Exception as e:  # noqa: BLE001
                    print(f"  {heuristic:<16} backtest failed: {e}")
                    continue
                if hb.get("total_tips"):
                    print(
                        f"  {heuristic:<16} "
                        f"acc={hb['overall_accuracy']:.3f}  "
                        f"tips={hb['total_tips']}  "
                        f"profit={hb['total_profit']:.0f} (NOT real staking)"
                    )

    await engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--seasons",
        type=int,
        nargs="+",
        default=[2022, 2023, 2024, 2025, 2026],
        help="Season years to seed (default: 2022-2026)",
    )
    args = parser.parse_args()
    asyncio.run(main(args.seasons))
