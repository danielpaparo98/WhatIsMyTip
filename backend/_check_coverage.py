"""Check per-competition parity data coverage. Safe to delete."""
import asyncio

import asyncpg

DSN = "postgresql://wimt:wimt_dev_password@localhost:5433/whatismytip"

async def main() -> None:
    conn = await asyncpg.connect(DSN)
    try:
        rows = await conn.fetch(
            """SELECT c.name,
                      (SELECT count(DISTINCT e.id) FROM events e
                       JOIN seasons s ON e.season_id = s.id
                       WHERE s.competition_id = c.id) AS events,
                      (SELECT count(*) FROM league_tips t
                       WHERE t.competition_id = c.id) AS tips,
                      (SELECT count(*) FROM league_model_predictions p
                       JOIN events e ON p.event_id = e.id
                       JOIN seasons s ON e.season_id = s.id
                       WHERE s.competition_id = c.id) AS preds,
                      (SELECT count(*) FROM league_match_analyses a
                       JOIN events e ON a.event_id = e.id
                       JOIN seasons s ON e.season_id = s.id
                       WHERE s.competition_id = c.id) AS analyses
               FROM competitions c ORDER BY c.id"""
        )
        for r in rows:
            print(
                f"{r['name']:<52} events={r['events']:<5} "
                f"tips={r['tips']:<6} preds={r['preds']:<6} analyses={r['analyses']}"
            )
        # Upcoming events per competition (what the match pages show)
        print("\n== upcoming (scheduled) events per competition ==")
        rows = await conn.fetch(
            """SELECT c.name, count(*) AS upcoming
               FROM events e
               JOIN seasons s ON e.season_id = s.id
               JOIN competitions c ON s.competition_id = c.id
               WHERE e.status = 'scheduled' AND e.completed IS FALSE
               GROUP BY c.name ORDER BY c.name"""
        )
        for r in rows:
            print(f"  {r['name']:<52} upcoming={r['upcoming']}")
        # An upcoming NRL event: does it have preds/analysis/explanations?
        sample = await conn.fetchrow(
            """SELECT e.slug, c.name,
                      (SELECT count(*) FROM league_model_predictions p WHERE p.event_id = e.id) AS preds,
                      (SELECT count(*) FROM league_tips t WHERE t.event_id = e.id AND t.explanation IS NOT NULL) AS explained,
                      (SELECT count(*) FROM league_match_analyses a WHERE a.event_id = e.id) AS analysis
               FROM events e
               JOIN seasons s ON e.season_id = s.id
               JOIN competitions c ON s.competition_id = c.id
               WHERE e.status = 'scheduled' AND e.completed IS FALSE
               ORDER BY e.starts_at LIMIT 6"""
        )
        print("\n== sample upcoming events ==")
        for r in sample and [sample] or []:
            pass
        rows = await conn.fetch(
            """SELECT e.slug, c.name,
                      (SELECT count(*) FROM league_model_predictions p WHERE p.event_id = e.id) AS preds,
                      (SELECT count(*) FROM league_tips t WHERE t.event_id = e.id AND t.explanation IS NOT NULL) AS explained,
                      (SELECT count(*) FROM league_match_analyses a WHERE a.event_id = e.id) AS analysis
               FROM events e
               JOIN seasons s ON e.season_id = s.id
               JOIN competitions c ON s.competition_id = c.id
               WHERE e.status = 'scheduled' AND e.completed IS FALSE
               ORDER BY e.starts_at LIMIT 8"""
        )
        for r in rows:
            print(
                f"  {r['name']:<30} {r['slug']:<18} preds={r['preds']} "
                f"explained={r['explained']} analysis={r['analysis']}"
            )
    finally:
        await conn.close()

asyncio.run(main())
