"""Quick query to show predictions for the next round."""
import asyncio
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from sqlalchemy import text
from packages.shared.db import _get_session_factory


async def main():
    SessionLocal = _get_session_factory()
    async with SessionLocal() as db:
        # Get next round info
        result = await db.execute(text("""
            SELECT season, round_id FROM games
            WHERE completed = false OR completed IS NULL
            ORDER BY season DESC, round_id ASC LIMIT 1
        """))
        row = result.first()
        if not row:
            print("No upcoming rounds!")
            return
        season, round_id = row

        # Get model predictions
        result = await db.execute(text("""
            SELECT g.home_team, g.away_team, g.venue, g.date,
                   mp.model_name, mp.winner, mp.confidence, mp.margin
            FROM model_predictions mp
            JOIN games g ON mp.game_id = g.id
            WHERE g.season = :season AND g.round_id = :round
            ORDER BY g.date, mp.model_name
        """), {"season": season, "round": round_id})

        predictions = result.fetchall()

        # Get tips
        result = await db.execute(text("""
            SELECT g.home_team, g.away_team,
                   t.heuristic, t.selected_team, t.margin, t.confidence
            FROM tips t
            JOIN games g ON t.game_id = g.id
            WHERE g.season = :season AND g.round_id = :round
            ORDER BY g.date, t.heuristic
        """), {"season": season, "round": round_id})

        tips = result.fetchall()

        print(f"\n{'='*90}")
        print(f"ROUND {round_id}, SEASON {season} - MODEL PREDICTIONS")
        print(f"{'='*90}")

        # Group by game
        games = {}
        for p in predictions:
            key = f"{p[0]} vs {p[1]}"
            if key not in games:
                games[key] = {"venue": p[2], "date": p[3], "preds": []}
            games[key]["preds"].append((p[4], p[5], p[6], p[7]))

        for game_key, data in games.items():
            print(f"\n  {game_key} ({data['venue']}, {data['date']})")
            print(f"  {'Model':<20} {'Winner':<18} {'Confidence':>12} {'Margin':>8}")
            print(f"  {'-'*20} {'-'*18} {'-'*12} {'-'*8}")
            for name, winner, conf, margin in data["preds"]:
                print(f"  {name:<20} {winner:<18} {conf:>11.1%} {margin:>7}pts")

        # Tips
        print(f"\n{'='*90}")
        print(f"HEURISTIC TIPS")
        print(f"{'='*90}")

        game_tips = {}
        for t in tips:
            key = f"{t[0]} vs {t[1]}"
            if key not in game_tips:
                game_tips[key] = []
            game_tips[key].append((t[2], t[3], t[4], t[5]))

        for game_key, tip_list in game_tips.items():
            print(f"\n  {game_key}")
            for heuristic, team, margin, conf in tip_list:
                print(f"    {heuristic:<25} → {team:<18} (conf: {conf:.1%}, margin: {margin}pts)")

        # Consensus
        print(f"\n{'='*90}")
        print(f"CONSENSUS PICKS")
        print(f"{'='*90}")
        for game_key, data in games.items():
            votes = {}
            for name, winner, conf, margin in data["preds"]:
                votes[winner] = votes.get(winner, 0) + 1
            sorted_votes = sorted(votes.items(), key=lambda x: x[1], reverse=True)
            consensus = sorted_votes[0]
            total = len(data["preds"])
            print(f"  {game_key}: {consensus[0]} ({consensus[1]}/{total} models)")
            if len(sorted_votes) > 1:
                for team, count in sorted_votes[1:]:
                    print(f"    Minority: {team} ({count}/{total})")

    print(f"\n{'='*90}\n")


if __name__ == "__main__":
    asyncio.run(main())
