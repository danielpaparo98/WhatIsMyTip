"""
Run predictions for the next upcoming round.
Reports all model predictions and heuristic tips with scores.

Usage:
    uv run python scripts/run_next_round_predictions.py
    uv run python scripts/run_next_round_predictions.py --regenerate
"""

import argparse
import asyncio
import json
import os
import sys

sys.path.insert(0, ".")


async def main():
    parser = argparse.ArgumentParser(description="Run predictions for next upcoming round")
    parser.add_argument("--regenerate", action="store_true", help="Regenerate existing tips")
    parser.add_argument("--verbose", action="store_true", help="Show detailed output")
    args = parser.parse_args()

    from sqlalchemy import text, select
    from packages.shared.db import _get_session_factory, dispose_engine
    from packages.shared.services.tip_generation import TipGenerationService
    from packages.shared.models import Game, ModelPrediction, Tip
    from packages.shared.orchestrator import ModelOrchestrator

    print("Connecting to database...")
    SessionLocal = _get_session_factory()

    async with SessionLocal() as db:
        # Find the next upcoming round
        result = await db.execute(text("""
            SELECT season, round_id, COUNT(*) as game_count,
                   MIN(date) as first_game, MAX(date) as last_game
            FROM games
            WHERE completed = false OR completed IS NULL
            GROUP BY season, round_id
            ORDER BY season DESC, round_id ASC
            LIMIT 5
        """))
        upcoming = result.fetchall()

        if not upcoming:
            print("No upcoming games found!")
            return

        print(f"\n{'='*80}")
        print("Upcoming Rounds:")
        print(f"{'='*80}")
        for row in upcoming:
            print(f"  Season {row[0]}, Round {row[1]}: {row[2]} games ({row[3]} to {row[4]})")

        # Use the first upcoming round
        season = upcoming[0][0]
        round_id = upcoming[0][1]

        print(f"\n{'='*80}")
        print(f"Generating predictions for Season {season}, Round {round_id}")
        print(f"{'='*80}")

        # Get the games for this round
        result = await db.execute(
            select(Game)
            .where(Game.season == season, Game.round_id == round_id)
            .order_by(Game.date)
        )
        games = result.scalars().all()

        print(f"\nGames in Round {round_id}:")
        for g in games:
            status = "Complete" if getattr(g, 'completed', False) else "Upcoming"
            print(f"  {g.home_team} vs {g.away_team} at {g.venue} ({g.date}) [{status}]")

        # Run the full tip generation pipeline
        service = TipGenerationService(db, season=season, round_id=round_id)
        stats = await service.generate_for_round(
            season, round_id, regenerate=args.regenerate, skip_nlp=True
        )

        print(f"\n{'='*80}")
        print(f"Generation Stats:")
        print(f"{'='*80}")
        print(f"  Games processed: {stats['games_processed']}")
        print(f"  Tips created: {stats['tips_created']}")
        print(f"  Tips skipped: {stats['tips_skipped']}")
        print(f"  Model predictions created: {stats['model_predictions_created']}")
        print(f"  Duration: {stats['duration_seconds']:.2f}s")

        if stats["errors"]:
            print(f"\n  Errors:")
            for err in stats["errors"]:
                print(f"    - {err}")

        # Now fetch and display all predictions
        await db.commit()  # Ensure all writes are committed

        print(f"\n{'='*80}")
        print(f"MODEL PREDICTIONS - Round {round_id}, Season {season}")
        print(f"{'='*80}")

        # Get all model predictions for this round
        result = await db.execute(
            select(ModelPrediction, Game)
            .join(Game, ModelPrediction.game_id == Game.id)
            .where(Game.season == season, Game.round_id == round_id)
            .order_by(Game.date, ModelPrediction.model_name)
        )
        predictions = result.all()

        # Group by game
        games_map = {}
        for pred, game in predictions:
            key = f"{game.home_team} vs {game.away_team}"
            if key not in games_map:
                games_map[key] = {
                    "game": game,
                    "predictions": {}
                }
            games_map[key]["predictions"][pred.model_name] = {
                "winner": pred.winner,
                "confidence": pred.confidence,
                "margin": pred.margin
            }

        # Display predictions per game
        for game_key, data in games_map.items():
            game = data["game"]
            preds = data["predictions"]
            print(f"\n  {game_key} ({game.venue}, {game.date})")
            print(f"  {'Model':<20} {'Winner':<15} {'Confidence':>12} {'Margin':>8}")
            print(f"  {'-'*20} {'-'*15} {'-'*12} {'-'*8}")
            for model_name in sorted(preds.keys()):
                p = preds[model_name]
                conf_str = f"{p['confidence']:.1%}" if p['confidence'] else "N/A"
                margin_str = f"{p['margin']}pts" if p['margin'] else "N/A"
                print(f"  {model_name:<20} {p['winner']:<15} {conf_str:>12} {margin_str:>8}")

        # Get heuristic tips
        print(f"\n{'='*80}")
        print(f"HEURISTIC TIPS - Round {round_id}, Season {season}")
        print(f"{'='*80}")

        result = await db.execute(
            select(Tip, Game)
            .join(Game, Tip.game_id == Game.id)
            .where(Game.season == season, Game.round_id == round_id)
            .order_by(Game.date, Tip.heuristic)
        )
        tips = result.all()

        for tip, game in tips:
            print(f"\n  {game.home_team} vs {game.away_team}")
            print(f"  {'Heuristic':<25} {'Selected':<15} {'Confidence':>12} {'Margin':>8}")
            print(f"  {'-'*25} {'-'*15} {'-'*12} {'-'*8}")
            conf_str = f"{tip.confidence:.1%}" if tip.confidence else "N/A"
            margin_str = f"{tip.margin}pts" if tip.margin else "N/A"
            print(f"  {tip.heuristic:<25} {tip.selected_team:<15} {conf_str:>12} {margin_str:>8}")

        # Summary: consensus picks
        print(f"\n{'='*80}")
        print(f"CONSENSUS SUMMARY - Round {round_id}")
        print(f"{'='*80}")

        for game_key, data in games_map.items():
            preds = data["predictions"]
            # Count votes
            votes = {}
            for model_name, p in preds.items():
                winner = p["winner"]
                votes[winner] = votes.get(winner, 0) + 1

            # Find consensus
            if votes:
                sorted_votes = sorted(votes.items(), key=lambda x: x[1], reverse=True)
                consensus_team = sorted_votes[0][0]
                consensus_count = sorted_votes[0][1]
                total = len(preds)

                # Average confidence and margin for consensus team
                avg_conf = sum(
                    p["confidence"] for p in preds.values()
                    if p["winner"] == consensus_team and p["confidence"]
                ) / max(consensus_count, 1)
                avg_margin = sum(
                    p["margin"] for p in preds.values()
                    if p["winner"] == consensus_team and p["margin"]
                ) / max(consensus_count, 1)

                print(f"\n  {game_key}")
                print(f"  Consensus: {consensus_team} ({consensus_count}/{total} models agree)")
                print(f"  Avg confidence: {avg_conf:.1%}, Avg margin: {avg_margin:.0f}pts")

                if len(sorted_votes) > 1:
                    for team, count in sorted_votes[1:]:
                        print(f"  Minority: {team} ({count}/{total} models)")

    print(f"\n{'='*80}")
    print("Done! Predictions uploaded to database.")
    print(f"{'='*80}\n")


if __name__ == "__main__":
    asyncio.run(main())
