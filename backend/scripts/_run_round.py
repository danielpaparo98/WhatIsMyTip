"""Run tip generation for a specific round and print results."""
import asyncio
import os
import sys

sys.path.insert(0, ".")


async def main():
    season = int(os.environ.get("SEASON", "2026"))
    round_id = int(os.environ.get("ROUND_ID", "15"))
    regenerate = os.environ.get("REGENERATE", "false").lower() == "true"

    from sqlalchemy import select
    from packages.shared.db import _get_session_factory, dispose_engine
    from packages.shared.services.tip_generation import TipGenerationService
    from packages.shared.models import Game, ModelPrediction, Tip

    SessionLocal = _get_session_factory()

    async with SessionLocal() as db:
        result = await db.execute(
            select(Game)
            .where(Game.season == season, Game.round_id == round_id)
            .order_by(Game.date)
        )
        games = result.scalars().all()
        if not games:
            print(f"No games found for season={season} round={round_id}")
            return

        print(f"\nGames in Round {round_id}:")
        for g in games:
            status = "Complete" if getattr(g, "completed", False) else "Upcoming"
            print(f"  {g.home_team} vs {g.away_team} at {g.venue} ({g.date}) [{status}]")

        service = TipGenerationService(db, season=season, round_id=round_id)
        stats = await service.generate_for_round(
            season, round_id, regenerate=regenerate, skip_nlp=True
        )

        print(f"\nGeneration Stats:")
        print(f"  Games processed: {stats['games_processed']}")
        print(f"  Tips created: {stats['tips_created']}")
        print(f"  Tips skipped: {stats['tips_skipped']}")
        print(f"  Tips updated: {stats['tips_updated']}")
        print(f"  Model predictions created: {stats['model_predictions_created']}")
        print(f"  Model predictions updated: {stats['model_predictions_updated']}")
        print(f"  Duration: {stats['duration_seconds']:.2f}s")
        if stats.get("errors"):
            print("  Errors:")
            for e in stats["errors"]:
                print(f"    - {e}")

        await db.commit()

        # Print model predictions
        result = await db.execute(
            select(ModelPrediction, Game)
            .join(Game, ModelPrediction.game_id == Game.id)
            .where(Game.season == season, Game.round_id == round_id)
            .order_by(Game.date, ModelPrediction.model_name)
        )
        predictions = result.all()

        games_map = {}
        for pred, game in predictions:
            key = f"{game.home_team} vs {game.away_team}"
            if key not in games_map:
                games_map[key] = {"game": game, "predictions": {}}
            games_map[key]["predictions"][pred.model_name] = {
                "winner": pred.winner,
                "confidence": pred.confidence,
                "margin": pred.margin,
            }

        for game_key, data in games_map.items():
            game = data["game"]
            preds = data["predictions"]
            print(f"\n  {game_key} ({game.venue}, {game.date})")
            print(f"  {'Model':<22} {'Winner':<26} {'Confidence':>12} {'Margin':>10}")
            print(f"  {'-'*22} {'-'*26} {'-'*12} {'-'*10}")
            for model_name in sorted(preds.keys()):
                p = preds[model_name]
                conf_str = f"{p['confidence']:.1%}" if p['confidence'] else "N/A"
                margin_str = f"{p['margin']}pts" if p['margin'] else "N/A"
                print(f"  {model_name:<22} {p['winner']:<26} {conf_str:>12} {margin_str:>10}")

        # Heuristic tips
        result = await db.execute(
            select(Tip, Game)
            .join(Game, Tip.game_id == Game.id)
            .where(Game.season == season, Game.round_id == round_id)
            .order_by(Game.date, Tip.heuristic)
        )
        tips = result.all()

        for tip, game in tips:
            print(f"\n  {game.home_team} vs {game.away_team}")
            print(f"  {'Heuristic':<25} {'Selected':<26} {'Confidence':>12} {'Margin':>10}")
            print(f"  {'-'*25} {'-'*26} {'-'*12} {'-'*10}")
            conf_str = f"{tip.confidence:.1%}" if tip.confidence else "N/A"
            margin_str = f"{tip.margin}pts" if tip.margin else "N/A"
            print(f"  {tip.heuristic:<25} {tip.selected_team:<26} {conf_str:>12} {margin_str:>10}")

        # Consensus summary
        print(f"\nConsensus Summary - Round {round_id}:")
        for game_key, data in games_map.items():
            preds = data["predictions"]
            votes = {}
            for model_name, p in preds.items():
                votes[p["winner"]] = votes.get(p["winner"], 0) + 1
            if votes:
                sorted_votes = sorted(votes.items(), key=lambda x: x[1], reverse=True)
                consensus_team = sorted_votes[0][0]
                consensus_count = sorted_votes[0][1]
                total = len(preds)
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

        print("\nDone!")
        await dispose_engine(force=True)


if __name__ == "__main__":
    asyncio.run(main())
