"""
Check what seasons and data are available in the production database.
Uses the shared db module which reads credentials from .env file.
"""

import asyncio
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


async def check_data():
    from sqlalchemy import text
    from packages.shared.db import _get_session_factory, dispose_engine

    SessionLocal = _get_session_factory()

    try:
        async with SessionLocal() as db:
            # Check available seasons
            result = await db.execute(text("SELECT DISTINCT season FROM games ORDER BY season"))
            seasons = [row[0] for row in result.fetchall()]
            print(f"\nAvailable seasons: {seasons}")

            # Check completed games count per season
            for season in seasons:
                result = await db.execute(
                    text(
                        "SELECT COUNT(*) FROM games WHERE season = :season AND completed = true"
                    ),
                    {"season": season},
                )
                completed = result.scalar()
                result = await db.execute(
                    text("SELECT COUNT(*) FROM games WHERE season = :season"),
                    {"season": season},
                )
                total = result.scalar()
                print(f"  Season {season}: {completed} completed / {total} total games")

            # Check existing model predictions
            result = await db.execute(
                text(
                    "SELECT model_name, COUNT(*) FROM model_predictions GROUP BY model_name ORDER BY model_name"
                )
            )
            predictions = result.fetchall()
            print(f"\nExisting model predictions:")
            if predictions:
                for row in predictions:
                    print(f"  {row[0]}: {row[1]} predictions")
            else:
                print("  (none)")

            # Check tips
            result = await db.execute(
                text(
                    "SELECT heuristic, COUNT(*) FROM tips GROUP BY heuristic ORDER BY heuristic"
                )
            )
            tips = result.fetchall()
            print(f"\nExisting tips:")
            if tips:
                for row in tips:
                    print(f"  {row[0]}: {row[1]} tips")
            else:
                print("  (none)")

            # Check if weather data exists
            try:
                result = await db.execute(text("SELECT COUNT(*) FROM match_weather"))
                weather_count = result.scalar()
                print(f"\nWeather records: {weather_count}")
            except Exception as e:
                print(f"\nWeather table error: {e}")

            # Check if player data exists
            try:
                result = await db.execute(text("SELECT COUNT(*) FROM players"))
                player_count = result.scalar()
                print(f"Players: {player_count}")
            except Exception as e:
                print(f"Players table error: {e}")

            try:
                result = await db.execute(text("SELECT COUNT(*) FROM player_match_stats"))
                stats_count = result.scalar()
                print(f"Player match stats: {stats_count}")
            except Exception as e:
                print(f"Player match stats error: {e}")

            try:
                result = await db.execute(text("SELECT COUNT(*) FROM injuries"))
                injury_count = result.scalar()
                print(f"Injuries: {injury_count}")
            except Exception as e:
                print(f"Injuries table error: {e}")

            try:
                result = await db.execute(text("SELECT COUNT(*) FROM player_advanced_stats"))
                adv_count = result.scalar()
                print(f"Advanced stats: {adv_count}")
            except Exception as e:
                print(f"Advanced stats table error: {e}")

    finally:
        await dispose_engine(force=True)


asyncio.run(check_data())
