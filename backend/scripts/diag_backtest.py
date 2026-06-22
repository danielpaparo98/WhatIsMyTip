"""Read-only backtest diagnostics against the live Postgres.

This script NEVER contains a connection string — it reads the DSN from the
``DATABASE_URL`` environment variable (or ``sys.argv[1]``) so the secret is
only ever passed inline per-command and never written to a tracked file.

It connects with TLS encryption ON but CA verification OFF (matches the
production ``sslmode=require`` behaviour on this Windows host).

Usage (cmd.exe):
    set "DATABASE_URL=postgresql://..." && set "DB_SSL_VERIFY=false" && uv run python scripts/diag_backtest.py
Usage (PowerShell):
    $env:DATABASE_URL="postgresql://..."; uv run python scripts/diag_backtest.py
Usage (any shell, DSN as arg):
    uv run python scripts/diag_backtest.py "postgresql://user:pass@host:port/db"
"""

from __future__ import annotations

import asyncio
import os
import ssl
import sys
from textwrap import dedent

import asyncpg


def _dsn() -> str:
    dsn = os.environ.get("DATABASE_URL") or (sys.argv[1] if len(sys.argv) > 1 else "")
    if not dsn:
        raise SystemExit(
            "No DSN provided. Set DATABASE_URL or pass the DSN as argv[1]."
        )
    # asyncpg ignores the libpq ``sslmode`` query param; we manage SSL via the
    # connection argument below.  Strip it so asyncpg doesn't choke.
    return dsn.split("?")[0]


async def _connect() -> asyncpg.Connection:
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE  # TLS on, CA validation off (== sslmode=require here)
    return await asyncpg.connect(_dsn(), ssl=ctx)


def _banner(title: str) -> None:
    print("\n" + "=" * 78)
    print(title)
    print("=" * 78)


async def main() -> None:
    conn = await _connect()
    try:
        # Q1 — seasons: total / completed / scored
        _banner("Q1: games per season (total / completed / completed+scored)")
        rows = await conn.fetch(dedent("""
            SELECT season, count(*) total,
                   count(*) FILTER (WHERE completed) completed,
                   count(*) FILTER (WHERE completed AND home_score IS NOT NULL
                                            AND away_score IS NOT NULL) scored
            FROM games GROUP BY season ORDER BY season;
        """))
        print(f"{'season':>7} {'total':>6} {'completed':>10} {'scored':>7}")
        for r in rows:
            print(f"{r['season']:>7} {r['total']:>6} {r['completed']:>10} {r['scored']:>7}")

        # Q2 — tips per heuristic per round for COMPLETED games
        _banner("Q2: tips per season/round/heuristic for COMPLETED games")
        rows = await conn.fetch(dedent("""
            SELECT g.season, g.round_id, t.heuristic, count(*) n
            FROM tips t JOIN games g ON g.id = t.game_id
            WHERE g.completed
            GROUP BY g.season, g.round_id, t.heuristic
            ORDER BY g.season, g.round_id, t.heuristic;
        """))
        print(f"{'season':>7} {'round':>5} {'heuristic':>22} {'n':>4}")
        for r in rows:
            print(f"{r['season']:>7} {r['round_id']:>5} {r['heuristic']:>22} {r['n']:>4}")

        # Q2b — distinct heuristics present in tips overall
        _banner("Q2b: distinct heuristics in tips (all rows)")
        rows = await conn.fetch("SELECT heuristic, count(*) n FROM tips GROUP BY heuristic ORDER BY heuristic;")
        for r in rows:
            print(f"{r['heuristic']:>22} {r['n']:>6}")

        # Q3 — model_predictions per model per completed game (counts)
        _banner("Q3: model_predictions per model (completed games only)")
        rows = await conn.fetch(dedent("""
            SELECT g.season, mp.model_name, count(*) n
            FROM model_predictions mp JOIN games g ON g.id = mp.game_id
            WHERE g.completed
            GROUP BY g.season, mp.model_name
            ORDER BY g.season, mp.model_name;
        """))
        print(f"{'season':>7} {'model_name':>20} {'n':>5}")
        for r in rows:
            print(f"{r['season']:>7} {r['model_name']:>20} {r['n']:>5}")

        # Q4 — backtest_results persisted table
        _banner("Q4: backtest_results (first 50 rows)")
        rows = await conn.fetch(dedent("""
            SELECT season, round_id, heuristic, tips_made, tips_correct, accuracy, profit
            FROM backtest_results
            ORDER BY season, round_id, heuristic LIMIT 50;
        """))
        if not rows:
            print("(empty table)")
        for r in rows:
            print(dict(r))
        cnt = await conn.fetchval("SELECT count(*) FROM backtest_results;")
        print(f"total backtest_results rows: {cnt}")

        # Q5 — team-name hygiene for 2026
        _banner("Q5: distinct home_team / away_team for season 2026")
        rows = await conn.fetch("SELECT DISTINCT home_team FROM games WHERE season=2026 ORDER BY home_team;")
        print("home_team:", [r["home_team"] for r in rows])
        rows = await conn.fetch("SELECT DISTINCT away_team FROM games WHERE season=2026 ORDER BY away_team;")
        print("away_team:", [r["away_team"] for r in rows])

        # Q5b — cross-check: are tip selected_team values present in games teams?
        _banner("Q5b: tips.selected_team NOT in any games.home_team/away_team (2026)")
        rows = await conn.fetch(dedent("""
            SELECT DISTINCT t.selected_team
            FROM tips t JOIN games g ON g.id=t.game_id
            WHERE g.season=2026
              AND t.selected_team NOT IN (
                SELECT home_team FROM games WHERE home_team IS NOT NULL
                UNION
                SELECT away_team FROM games WHERE away_team IS NOT NULL
              );
        """))
        print("orphan selected_team (2026):", [r["selected_team"] for r in rows])

        # Q6 — sample a couple of completed 2026 games with tips to eyeball correctness join
        _banner("Q6: sample completed 2026 game + its tips (heuristic/selected_team/scores)")
        rows = await conn.fetch(dedent("""
            SELECT g.id, g.round_id, g.home_team, g.away_team,
                   g.home_score, g.away_score, g.completed,
                   t.heuristic, t.selected_team
            FROM games g
            LEFT JOIN tips t ON t.game_id = g.id
            WHERE g.season=2026 AND g.completed
            ORDER BY g.round_id, g.id, t.heuristic
            LIMIT 40;
        """))
        for r in rows:
            print(dict(r))

    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
