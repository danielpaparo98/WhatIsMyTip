"""Idempotently canonicalise team-name aliases in tips/model_predictions.

Background
----------
Migration ``0004_canonical_team_names`` rewrote every known team alias to its
canonical compact form across ``games``, ``tips`` and ``elo_cache``.  It did
**not** touch ``model_predictions.winner``.  Stored model predictions were
generated before canonicalisation, so some still hold raw aliases
(``Greater Western Sydney``, ``Western Bulldogs``, ``Gold Coast`` …).

Consequence: the live backtest join compares
``tips.selected_team`` (and ``model_predictions.winner``) against the
canonical ``games.home_team`` / ``games.away_team``.  Alias tips therefore
never match the actual winner even when the pick is correct, which
understates accuracy/profit.

This script rewrites every known alias to its canonical form in
``tips.selected_team`` and ``model_predictions.winner``, using the project's
own ``packages.shared.teams.TEAM_NAME_SETS`` (single source of truth).

Safety
------
* Only maps *known* alias -> canonical (canonical values are untouched;
  unknown values are untouched).  Idempotent: a second run is a no-op.
* ``--dry-run`` reports counts without writing.

Secrets
-------
Reads the DSN from ``DATABASE_URL`` (or argv[1]); never stores it.

Usage (cmd.exe)::
    set "DATABASE_URL=postgresql://..." && set "DB_SSL_VERIFY=false" ^
    && uv run python scripts/canonicalize_team_names.py --dry-run
"""

from __future__ import annotations

import argparse
import asyncio
import os
import ssl
import sys
from typing import Dict, List, Tuple

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import asyncpg

from packages.shared.teams import TEAM_NAME_SETS


def _alias_pairs() -> List[Tuple[str, str]]:
    """Return (alias, canonical) pairs for every non-identity mapping."""
    pairs: List[Tuple[str, str]] = []
    for canonical, aliases in TEAM_NAME_SETS.items():
        for alias in aliases:
            if alias != canonical:
                pairs.append((alias, canonical))
    return pairs


async def _connect() -> asyncpg.Connection:
    dsn = os.environ.get("DATABASE_URL") or (sys.argv[1] if len(sys.argv) > 1 else "")
    if not dsn:
        raise SystemExit("No DSN. Set DATABASE_URL or pass it as argv[1].")
    dsn = dsn.split("?")[0]
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE  # TLS on, CA validation off (== sslmode=require here)
    return await asyncpg.connect(dsn, ssl=ctx)


async def main() -> None:
    parser = argparse.ArgumentParser(description="Canonicalise team-name aliases")
    parser.add_argument("--dry-run", action="store_true", help="Report only; write nothing")
    args = parser.parse_args()

    pairs = _alias_pairs()
    conn = await _connect()
    total_tips = 0
    total_preds = 0
    try:
        print(f"{'alias':<26} {'->':<3} {'canonical':<14} {'tips':>6} {'preds':>6}")
        print("-" * 60)
        for alias, canonical in pairs:
            tips_n = await conn.fetchval(
                "SELECT count(*) FROM tips WHERE selected_team = $1", alias
            )
            preds_n = await conn.fetchval(
                "SELECT count(*) FROM model_predictions WHERE winner = $1", alias
            )
            if tips_n or preds_n:
                print(f"{alias:<26} {'->':<3} {canonical:<14} {tips_n:>6} {preds_n:>6}")
            if args.dry_run:
                total_tips += tips_n
                total_preds += preds_n
                continue
            if tips_n:
                await conn.execute(
                    "UPDATE tips SET selected_team = $1 WHERE selected_team = $2",
                    canonical, alias,
                )
            if preds_n:
                await conn.execute(
                    "UPDATE model_predictions SET winner = $1 WHERE winner = $2",
                    canonical, alias,
                )
            total_tips += tips_n
            total_preds += preds_n
        print("-" * 60)
        print(
            f"{'TOTAL' if args.dry_run else 'UPDATED':<30} tips={total_tips} "
            f"model_predictions={total_preds}"
        )
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
