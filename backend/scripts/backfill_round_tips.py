"""Idempotently backfill heuristic tips for COMPLETED+SCORED games.

These games already have ``model_predictions`` rows (the 8 ML models) but no
``tips`` rows, which is why the live backtest API (which computes from
``tips ⨯ games``) returns blanks.

Approach
--------
We reuse the project's own :class:`ModelOrchestrator` heuristic objects and
:func:`TipCRUD.create`, applying each heuristic to the **stored**
``model_predictions`` for a game instead of re-running the 8 ML models.
Re-running the models over the remote managed-Postgres would be extremely
slow (≈10k ``model.predict()`` calls, each doing several DB round-trips) and
would produce *identical* results, because the models are deterministic over
the same historical data.  Reusing stored predictions is therefore both
faster and equivalent.

Safety
------
* Only INSERTS tips for ``(game, heuristic)`` pairs that don't already exist.
  Existing tips are never deleted or overwritten.
* Targets only ``completed`` games with non-null ``home_score``/``away_score``
  (exactly what the backtest query joins on).
* ``--dry-run`` reports what would be created without writing.

Secrets
-------
This file NEVER contains a connection string.  It reads ``DATABASE_URL`` from
the environment (the project engine honours ``DB_SSL_VERIFY=false``).

Usage (cmd.exe)::
    set "DATABASE_URL=postgresql://..." && set "DB_SSL_VERIFY=false" ^
    && uv run python scripts/backfill_round_tips.py --seasons 2025,2026 --dry-run
    (drop --dry-run to apply)
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from collections import Counter
from typing import Dict, Iterable, List, Set, Tuple

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from packages.shared.db import _get_session_factory, dispose_engine
from packages.shared.models import Game, ModelPrediction, Tip
from packages.shared.orchestrator import ModelOrchestrator

Prediction = Tuple[str, float, int]


def _parse_seasons(raw: str) -> List[int]:
    out: List[int] = []
    for part in raw.split(","):
        part = part.strip()
        if part:
            out.append(int(part))
    return sorted(set(out))


async def _load_predictions_by_game(
    db: AsyncSession, game_ids: Iterable[int]
) -> Dict[int, Dict[str, Prediction]]:
    game_ids = list(game_ids)
    if not game_ids:
        return {}
    result = await db.execute(
        select(ModelPrediction).where(ModelPrediction.game_id.in_(game_ids))
    )
    by_game: Dict[int, Dict[str, Prediction]] = {}
    for p in result.scalars().all():
        by_game.setdefault(p.game_id, {})[p.model_name] = (
            p.winner,
            float(p.confidence or 0.0),
            int(p.margin or 0),
        )
    return by_game


async def _load_existing_tip_heuristics(
    db: AsyncSession, game_ids: Iterable[int]
) -> Dict[int, Set[str]]:
    game_ids = list(game_ids)
    if not game_ids:
        return {}
    result = await db.execute(
        select(Tip.game_id, Tip.heuristic).where(Tip.game_id.in_(game_ids))
    )
    by_game: Dict[int, Set[str]] = {}
    for game_id, heuristic in result.all():
        by_game.setdefault(game_id, set()).add(heuristic)
    return by_game


async def _backfill_season(
    db: AsyncSession,
    orch: ModelOrchestrator,
    season: int,
    dry_run: bool,
) -> Dict[str, int]:
    # Completed + scored games are exactly what the backtest query uses.
    result = await db.execute(
        select(Game)
        .where(
            Game.season == season,
            Game.completed.is_(True),
            Game.home_score.is_not(None),
            Game.away_score.is_not(None),
        )
        .order_by(Game.round_id, Game.date)
    )
    games = list(result.scalars().all())
    game_ids = [g.id for g in games]

    preds_by_game = await _load_predictions_by_game(db, game_ids)
    existing_by_game = await _load_existing_tip_heuristics(db, game_ids)

    heuristic_names = orch.get_available_heuristics()
    created = Counter()
    skipped_existing = 0
    skipped_no_preds = 0
    games_needing_work = 0

    for game in games:
        have = existing_by_game.get(game.id, set())
        missing = [h for h in heuristic_names if h not in have]
        if not missing:
            skipped_existing += 1
            continue

        model_predictions = preds_by_game.get(game.id, {})
        if not model_predictions:
            # Cannot derive a tip without any model prediction for this game.
            skipped_no_preds += 1
            continue

        games_needing_work += 1
        for heuristic in missing:
            hobj = orch.heuristics[heuristic]
            try:
                winner, confidence, margin = await hobj.apply(game, model_predictions)
            except Exception as exc:  # noqa: BLE001 — best effort, continue
                print(
                    f"  ! apply failed season={season} game={game.id} "
                    f"heuristic={heuristic}: {exc}"
                )
                continue

            created[heuristic] += 1

            if dry_run:
                continue

            # Bulk-add (commit once per season below) rather than calling
            # TipCRUD.create per row, which would issue ~1 transaction per
            # tip over the remote DB.  We still use the project's own Tip
            # ORM model and only insert rows for (game, heuristic) pairs we
            # already confirmed are missing, so this remains idempotent.
            db.add(
                Tip(
                    game_id=game.id,
                    heuristic=heuristic,
                    selected_team=winner,
                    margin=int(margin or 0),
                    confidence=float(confidence or 0.0),
                    explanation="",  # historic backfill — no AI explanation
                )
            )

    if not dry_run and games_needing_work:
        # Single commit per season — fast and atomic per season.
        await db.commit()
        # Best-effort cache invalidation so the tips API serves fresh rows.
        try:
            from packages.shared.cache import invalidate_cache_pattern, medium_cache

            await invalidate_cache_pattern(medium_cache, "tips")
            await invalidate_cache_pattern(medium_cache, "backtest")
        except Exception as exc:  # noqa: BLE001
            print(f"  ! cache invalidation skipped: {exc}")

    total_games = len(games)
    print(
        f"\n[season {season}] games={total_games} "
        f"already_complete={skipped_existing} "
        f"missing_preds={skipped_no_preds} "
        f"worked={games_needing_work}"
    )
    for h in heuristic_names:
        print(f"    {h:<14} tips_created={'(dry-run) ' if dry_run else ''}{created[h]}")

    return {
        "games": total_games,
        "tips_created": sum(created.values()),
        **{f"created_{h}": created[h] for h in heuristic_names},
    }


async def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill tips for completed games")
    parser.add_argument(
        "--seasons", default="2025,2026", help="Comma-separated season years"
    )
    parser.add_argument("--dry-run", action="store_true", help="Report only; write nothing")
    args = parser.parse_args()

    seasons = _parse_seasons(args.seasons)
    print(f"Backfill tips for seasons={seasons} dry_run={args.dry_run}")

    SessionLocal = _get_session_factory()
    orch = ModelOrchestrator()

    totals: Dict[str, int] = {}
    try:
        async with SessionLocal() as db:
            # Load the active weighted_tip coefficients (cached, best-effort).
            try:
                await orch._ensure_weighted_tip_coefficients(db)
            except Exception as exc:  # noqa: BLE001
                print(f"  ! weighted_tip coefficient load failed (using fallback): {exc}")

            for season in seasons:
                stats = await _backfill_season(db, orch, season, args.dry_run)
                totals[season] = stats["tips_created"]  # type: ignore[assignment]
    finally:
        await dispose_engine(force=True)

    print("\n=== summary ===")
    for season, n in totals.items():
        print(f"  season {season}: {n} tips {'would be ' if args.dry_run else ''}created")
    print("done.")


if __name__ == "__main__":
    asyncio.run(main())
