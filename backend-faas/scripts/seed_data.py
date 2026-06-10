#!/usr/bin/env python3
"""
Database seed script for WhatIsMyTip.

Generates realistic AFL data for development and testing, including:
- Games across multiple rounds and seasons
- Model predictions (elo, form, home_advantage, value) per game
- Tips (best_bet, yolo, high_risk_high_reward) per game
- Elo cache ratings for all 18 teams
- Backtest results for historical performance
- Match analyses with AI-style talking points
- Generation progress and job execution tracking

Usage:
    # Seed with default data (2025 completed + 2026 partial)
    uv run python scripts/seed_data.py

    # Seed only a specific season
    uv run python scripts/seed_data.py --season 2025

    # Clear existing data before seeding (idempotent)
    uv run python scripts/seed_data.py --clear

    # Seed with verbose output
    uv run python scripts/seed_data.py --verbose
"""

import argparse
import asyncio
import hashlib
import os
import random
import sys
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

# Ensure the backend-faas directory is on sys.path so that
# `packages.shared` is importable when running from repo root.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from packages.shared.db import Base, get_engine
from packages.shared.models import (
    BacktestResult,
    EloCache,
    Game,
    GenerationProgress,
    Injury,
    JobExecution,
    MatchAnalysis,
    MatchWeather,
    ModelPrediction,
    Player,
    PlayerAdvancedStats,
    PlayerMatchStats,
    Tip,
)
from packages.shared.utils import generate_slug
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

AFL_TEAMS: List[str] = sorted([
    "Adelaide",
    "Brisbane",
    "Bulldogs",
    "Carlton",
    "Collingwood",
    "Essendon",
    "Fremantle",
    "Geelong",
    "Giants",
    "GoldCoast",
    "Hawthorn",
    "Melbourne",
    "NorthMelbourne",
    "PortAdelaide",
    "Richmond",
    "StKilda",
    "Sydney",
    "WestCoast",
])

# Home venue mapping for each team
TEAM_VENUES: Dict[str, str] = {
    "Adelaide": "Adelaide Oval",
    "Brisbane": "Gabba",
    "Bulldogs": "Marvel Stadium",
    "Carlton": "MCG",
    "Collingwood": "MCG",
    "Essendon": "MCG",
    "Fremantle": "Optus Stadium",
    "Geelong": "GMHBA Stadium",
    "Giants": "Giants Stadium",
    "GoldCoast": "Heritage Bank Stadium",
    "Hawthorn": "MCG",
    "Melbourne": "MCG",
    "NorthMelbourne": "Marvel Stadium",
    "PortAdelaide": "Adelaide Oval",
    "Richmond": "MCG",
    "StKilda": "Marvel Stadium",
    "Sydney": "SCG",
    "WestCoast": "Optus Stadium",
}

HEURISTICS: List[str] = ["best_bet", "yolo", "high_risk_high_reward"]
MODEL_NAMES: List[str] = ["elo", "form", "home_advantage", "value"]

# Default Elo rating for a new team
DEFAULT_ELO = 1500.0

# AFL season typically runs mid-March to end of September
SEASON_START_MONTH = 3
SEASON_START_DAY = 15
ROUNDS_PER_SEASON = 24
GAMES_PER_ROUND = 9  # 18 teams → 9 matches

# Realistic AFL score ranges
MIN_SCORE = 40
MAX_SCORE = 150

# Realistic margin ranges
MIN_MARGIN = 1
MAX_MARGIN = 65

# Casual match analysis templates
ANALYSIS_TEMPLATES = [
    (
        "{home} host {away} at {venue} and the home ground advantage could be the "
        "difference here. {winner} have been in solid form and should get the job "
        "done, but {loser} won't go down without a fight. Expect a competitive "
        "contest with the margin likely around {margin} points."
    ),
    (
        "This one shapes as a fascinating contest. {winner} come in as favourites "
        "and for good reason — their ball movement and defensive pressure have been "
        "elite. {loser} will need to bring their A-game to cause an upset at "
        "{venue}. The key matchup in the midfield could decide it."
    ),
    (
        "Both sides have shown flashes of brilliance this season, but {winner} have "
        "been more consistent overall. Playing at {venue} is always a challenge for "
        "visiting teams, and {loser} will need to overcome that to get the win. "
        "Expect a margin of around {margin} points in what should be an entertaining game."
    ),
    (
        "Don't sleep on this one — {home} versus {away} at {venue} has the makings "
        "of a classic. {winner} have the edge on paper with a {margin}-point "
        "predicted margin, but {loser} have pulled off surprises before. "
        "The forward line efficiency will be the telling factor."
    ),
    (
        "Round {round} action sees {home} take on {away} at {venue}. {winner} "
        "have been building nicely and should continue that momentum here. "
        "{loser} will be hungry for a response after recent results, so don't "
        "expect them to roll over. Could be closer than the experts think."
    ),
]


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------


def _generate_deterministic_slug(
    season: int, round_id: int, game_index: int
) -> str:
    """Generate a deterministic but realistic-looking slug.

    Uses a hash of the season/round/game combination to produce a
    reproducible 10-character alphanumeric slug.
    """
    raw = f"game-{season}-r{round_id}-g{game_index}"
    return hashlib.sha256(raw.encode()).hexdigest()[:10]


def _generate_round_fixtures(
    season: int, round_id: int
) -> List[Tuple[str, str, str]]:
    """Generate fixture pairs for a round using a deterministic rotation.

    Uses a simple rotation of team indices to produce unique matchups
    across the season. Returns list of (home_team, away_team, venue).
    """
    n = len(AFL_TEAMS)
    # Rotate using round_id to create different matchups each round
    rotated = AFL_TEAMS[round_id % n :] + AFL_TEAMS[: round_id % n]
    fixtures = []
    for i in range(GAMES_PER_ROUND):
        home = rotated[i]
        away = rotated[n - 1 - i]
        venue = TEAM_VENUES[home]
        fixtures.append((home, away, venue))
    return fixtures


def _generate_game_datetime(season: int, round_id: int, game_index: int) -> datetime:
    """Generate a realistic game datetime.

    AFL games are typically played Thursday to Sunday, with start times
    ranging from early afternoon to evening.
    """
    # Base date: season start + (round_id - 1) weeks
    base = datetime(season, SEASON_START_MONTH, SEASON_START_DAY, tzinfo=timezone.utc)
    round_start = base + timedelta(weeks=(round_id - 1))

    # Spread games across Thursday (3) to Sunday (6)
    day_offset = 3 + (game_index % 4)  # 3=Thu, 4=Fri, 5=Sat, 6=Sun
    game_date = round_start + timedelta(days=day_offset - round_start.weekday())

    # Realistic start times (AEST = UTC+10): 13:45, 15:35, 17:30, 19:20
    start_hours = [3, 5, 7, 9]  # UTC hours for the above AEST times
    start_minutes = [45, 35, 30, 20]
    time_idx = game_index % 4
    game_date = game_date.replace(
        hour=start_hours[time_idx],
        minute=start_minutes[time_idx],
        second=0,
        microsecond=0,
    )
    return game_date


def _generate_realistic_score(
    rng: random.Random, home_advantage: bool = True
) -> Tuple[int, int]:
    """Generate realistic AFL scores.

    The home team tends to score slightly higher on average.
    AFL typical scores range from 40-150 (6 goals to 22+ goals equivalent).
    """
    home_base = 85 if home_advantage else 75
    away_base = 75

    home_score = rng.randint(home_base - 35, home_base + 45)
    away_score = rng.randint(away_base - 35, away_base + 45)

    home_score = max(MIN_SCORE, min(MAX_SCORE, home_score))
    away_score = max(MIN_SCORE, min(MAX_SCORE, away_score))

    return home_score, away_score


def _generate_confidence(rng: random.Random, heuristic: str) -> float:
    """Generate a realistic confidence value based on heuristic type.

    - best_bet: higher confidence (0.55-0.85)
    - high_risk_high_reward: moderate confidence (0.35-0.65)
    - yolo: lower confidence (0.15-0.45)
    """
    ranges = {
        "best_bet": (0.55, 0.85),
        "high_risk_high_reward": (0.35, 0.65),
        "yolo": (0.15, 0.45),
    }
    lo, hi = ranges[heuristic]
    return round(rng.uniform(lo, hi), 2)


def _generate_margin(rng: random.Random, heuristic: str) -> int:
    """Generate a realistic predicted margin based on heuristic type.

    - best_bet: moderate margins (5-30)
    - high_risk_high_reward: larger margins (15-50)
    - yolo: can be anything (1-65)
    """
    ranges = {
        "best_bet": (5, 30),
        "high_risk_high_reward": (15, 50),
        "yolo": (1, 65),
    }
    lo, hi = ranges[heuristic]
    return rng.randint(lo, hi)


def _generate_explanation(
    rng: random.Random,
    winner: str,
    loser: str,
    margin: int,
    heuristic: str,
) -> str:
    """Generate a realistic tip explanation."""
    templates = {
        "best_bet": [
            f"{winner} have strong form and a favourable matchup. Expecting a {margin}-point victory in what should be a reliable pick.",
            f"The models are aligned on {winner} here with good confidence. {margin}-point margin reflects their recent dominant performances.",
            f"{winner} at home with strong statistical backing. A {margin}-point win looks likely based on the data.",
        ],
        "high_risk_high_reward": [
            f"Going with {winner} as a value pick — the odds are generous given their underlying numbers. Could win by {margin}+ points.",
            f"{winner} are underrated by the market. If they bring their A-game, a {margin}-point margin is very achievable.",
            f"The contrarian play here is {winner}. Their recent form suggests they can cover the {margin}-point margin with ease.",
        ],
        "yolo": [
            f"YOLO pick: {winner} by {margin}. No guts, no glory! Sometimes you just have to trust the vibes.",
            f"Throwing a dart — {winner} to win by {margin}. It's bold, but that's what the YOLO pick is all about.",
            f"Wildcard selection: {winner}. The form guide says no, but the heart says yes. Margin: {margin} points.",
        ],
    }
    return rng.choice(templates[heuristic])


def _generate_model_confidence(rng: random.Random, model_name: str) -> float:
    """Generate realistic model confidence by model type."""
    ranges = {
        "elo": (0.50, 0.80),
        "form": (0.45, 0.75),
        "home_advantage": (0.55, 0.85),
        "value": (0.40, 0.70),
    }
    lo, hi = ranges[model_name]
    return round(rng.uniform(lo, hi), 2)


def _generate_model_margin(rng: random.Random, model_name: str) -> int:
    """Generate realistic predicted margin by model type."""
    ranges = {
        "elo": (3, 25),
        "form": (5, 35),
        "home_advantage": (8, 40),
        "value": (2, 20),
    }
    lo, hi = ranges[model_name]
    return rng.randint(lo, hi)


def _generate_elo_rating(rng: random.Random, team: str) -> float:
    """Generate a realistic Elo rating for a team.

    Top teams: 1600-1700, mid teams: 1450-1550, lower teams: 1350-1450.
    """
    # Use a deterministic seed based on team name for consistency
    team_seed = int(hashlib.md5(team.encode()).hexdigest(), 16) % 100
    base_ratings = {
        "Adelaide": 1480,
        "Brisbane": 1620,
        "Bulldogs": 1510,
        "Carlton": 1560,
        "Collingwood": 1650,
        "Essendon": 1490,
        "Fremantle": 1530,
        "Geelong": 1600,
        "Giants": 1540,
        "GoldCoast": 1420,
        "Hawthorn": 1460,
        "Melbourne": 1520,
        "NorthMelbourne": 1380,
        "PortAdelaide": 1580,
        "Richmond": 1440,
        "StKilda": 1470,
        "Sydney": 1630,
        "WestCoast": 1360,
    }
    base = base_ratings.get(team, DEFAULT_ELO)
    # Add some randomness
    return round(base + rng.uniform(-20, 20), 1)


# ---------------------------------------------------------------------------
# Seed functions
# ---------------------------------------------------------------------------


async def clear_all_data(session: AsyncSession) -> None:
    """Clear all data from all tables (in dependency order)."""
    tables = [
        "injuries",
        "player_advanced_stats",
        "player_match_stats",
        "match_analyses",
        "match_weather",
        "tips",
        "model_predictions",
        "backtest_results",
        "generation_progress",
        "job_executions",
        "job_locks",
        "elo_cache",
        "players",
        "games",
    ]
    for table in tables:
        await session.execute(text(f"DELETE FROM {table}"))
    await session.commit()


def seed_games(
    rng: random.Random,
    season: int,
    rounds: int = ROUNDS_PER_SEASON,
    completed_rounds: Optional[int] = None,
    squiggle_id_start: int = 10000,
) -> List[Game]:
    """Generate Game objects for a season.

    Args:
        rng: Random instance for reproducibility.
        season: Season year.
        rounds: Number of rounds to generate.
        completed_rounds: Number of rounds that are completed (have scores).
            If None, all rounds are completed.
        squiggle_id_start: Starting squiggle_id for the season.

    Returns:
        List of Game ORM objects (not yet added to session).
    """
    if completed_rounds is None:
        completed_rounds = rounds

    games: List[Game] = []
    squiggle_id = squiggle_id_start
    game_id = (season - 2010) * 10000  # Unique ID space per season

    for round_id in range(1, rounds + 1):
        is_completed = round_id <= completed_rounds
        fixtures = _generate_round_fixtures(season, round_id)

        for game_index, (home, away, venue) in enumerate(fixtures):
            game_id += 1
            squiggle_id += 1
            game_date = _generate_game_datetime(season, round_id, game_index)

            home_score = None
            away_score = None
            if is_completed:
                home_score, away_score = _generate_realistic_score(
                    rng, home_advantage=True
                )

            game = Game(
                id=game_id,
                slug=_generate_deterministic_slug(season, round_id, game_index),
                squiggle_id=squiggle_id,
                round_id=round_id,
                season=season,
                home_team=home,
                away_team=away,
                home_score=home_score,
                away_score=away_score,
                venue=venue,
                date=game_date,
                completed=is_completed,
                predictions_generated=is_completed,
                tips_generated=is_completed,
                last_synced_at=datetime.now(timezone.utc) if is_completed else None,
                sync_version=1 if is_completed else 0,
            )
            games.append(game)

    return games


def seed_model_predictions(
    rng: random.Random, games: List[Game]
) -> List[ModelPrediction]:
    """Generate model predictions for each game."""
    predictions: List[ModelPrediction] = []
    prediction_id = 1

    for game in games:
        for model_name in MODEL_NAMES:
            confidence = _generate_model_confidence(rng, model_name)
            margin = _generate_model_margin(rng, model_name)

            # Each model picks a winner (home advantage biases toward home)
            home_bias = {"elo": 0.55, "form": 0.50, "home_advantage": 0.65, "value": 0.50}
            if rng.random() < home_bias[model_name]:
                winner = game.home_team
            else:
                winner = game.away_team

            predictions.append(
                ModelPrediction(
                    id=prediction_id,
                    game_id=game.id,
                    model_name=model_name,
                    winner=winner,
                    confidence=confidence,
                    margin=margin,
                )
            )
            prediction_id += 1

    return predictions


def seed_tips(rng: random.Random, games: List[Game]) -> List[Tip]:
    """Generate tips for each game using all heuristics."""
    tips: List[Tip] = []
    tip_id = 1

    for game in games:
        # Determine a consensus winner (from model predictions perspective)
        # For completed games, the tip tends to favor the actual winner
        if game.completed and game.home_score is not None and game.away_score is not None:
            actual_winner = game.home_team if game.home_score > game.away_score else game.away_team
        else:
            actual_winner = rng.choice([game.home_team, game.away_team])

        for heuristic in HEURISTICS:
            confidence = _generate_confidence(rng, heuristic)
            margin = _generate_margin(rng, heuristic)

            # best_bet usually picks the consensus/actual winner
            # yolo sometimes picks the underdog
            if heuristic == "yolo" and rng.random() < 0.35:
                selected = game.away_team if actual_winner == game.home_team else game.home_team
            elif heuristic == "high_risk_high_reward" and rng.random() < 0.25:
                selected = game.away_team if actual_winner == game.home_team else game.home_team
            else:
                selected = actual_winner

            explanation = _generate_explanation(
                rng, selected,
                game.away_team if selected == game.home_team else game.home_team,
                margin,
                heuristic,
            )

            tips.append(
                Tip(
                    id=tip_id,
                    game_id=game.id,
                    heuristic=heuristic,
                    selected_team=selected,
                    margin=margin,
                    confidence=confidence,
                    explanation=explanation,
                )
            )
            tip_id += 1

    return tips


def seed_elo_cache(rng: random.Random, season: int) -> List[EloCache]:
    """Generate Elo cache entries for all teams."""
    entries: List[EloCache] = []

    for idx, team in enumerate(AFL_TEAMS):
        rating = _generate_elo_rating(rng, team)
        entries.append(
            EloCache(
                id=idx + 1,
                team_name=team,
                rating=rating,
                games_played=rng.randint(18, 24),
                last_updated=datetime.now(timezone.utc),
                season=season,
            )
        )

    return entries


def seed_backtest_results(
    rng: random.Random, season: int, rounds: int
) -> List[BacktestResult]:
    """Generate backtest results for each heuristic and round."""
    results: List[BacktestResult] = []
    result_id = 1

    for round_id in range(1, rounds + 1):
        for heuristic in HEURISTICS:
            tips_made = GAMES_PER_ROUND
            # Accuracy varies by heuristic
            accuracy_ranges = {
                "best_bet": (0.55, 0.75),
                "high_risk_high_reward": (0.40, 0.65),
                "yolo": (0.25, 0.50),
            }
            lo, hi = accuracy_ranges[heuristic]
            accuracy = round(rng.uniform(lo, hi), 2)
            tips_correct = int(tips_made * accuracy)

            # Profit varies: best_bet usually positive, yolo volatile
            profit_ranges = {
                "best_bet": (-2, 8),
                "high_risk_high_reward": (-5, 12),
                "yolo": (-8, 15),
            }
            plo, phi = profit_ranges[heuristic]
            profit = round(rng.uniform(plo, phi), 1)

            results.append(
                BacktestResult(
                    id=result_id,
                    heuristic=heuristic,
                    season=season,
                    round_id=round_id,
                    tips_made=tips_made,
                    tips_correct=tips_correct,
                    accuracy=accuracy,
                    profit=profit,
                )
            )
            result_id += 1

    return results


def seed_match_analyses(
    rng: random.Random, games: List[Game]
) -> List[MatchAnalysis]:
    """Generate match analysis talking points for each game."""
    analyses: List[MatchAnalysis] = []
    analysis_id = 1

    for game in games:
        # Determine winner for the analysis narrative
        if game.completed and game.home_score is not None and game.away_score is not None:
            winner = game.home_team if game.home_score > game.away_score else game.away_team
            loser = game.away_team if winner == game.home_team else game.home_team
            margin = abs(game.home_score - game.away_score)
        else:
            winner = rng.choice([game.home_team, game.away_team])
            loser = game.away_team if winner == game.home_team else game.home_team
            margin = rng.randint(5, 30)

        template = rng.choice(ANALYSIS_TEMPLATES)
        analysis_text = template.format(
            home=game.home_team,
            away=game.away_team,
            venue=game.venue,
            winner=winner,
            loser=loser,
            margin=margin,
            round=game.round_id,
        )

        analyses.append(
            MatchAnalysis(
                id=analysis_id,
                game_id=game.id,
                analysis_text=analysis_text,
            )
        )
        analysis_id += 1

    return analyses


def seed_generation_progress(seasons: List[int]) -> List[GenerationProgress]:
    """Generate generation progress tracking entries."""
    entries: List[GenerationProgress] = []
    progress_id = 1
    now = datetime.now(timezone.utc)

    operations = [
        ("historical_generation", "completed"),
        ("season_sync", "completed"),
        ("tip_generation", "completed"),
    ]

    for season in seasons:
        for op_type, status in operations:
            total = ROUNDS_PER_SEASON * GAMES_PER_ROUND
            started = now - timedelta(days=30)
            entries.append(
                GenerationProgress(
                    id=progress_id,
                    operation_type=op_type,
                    season=season,
                    total_items=total,
                    completed_items=total if status == "completed" else 0,
                    status=status,
                    error_message=None,
                    started_at=started,
                    completed_at=started + timedelta(minutes=45) if status == "completed" else None,
                    updated_at=started + timedelta(minutes=45) if status == "completed" else started,
                )
            )
            progress_id += 1

    return entries


def seed_job_executions() -> List[JobExecution]:
    """Generate sample job execution history."""
    executions: List[JobExecution] = []
    exec_id = 1
    now = datetime.now(timezone.utc)

    jobs = [
        ("daily_sync", "completed", 10, 0),
        ("tip_generation", "completed", 9, 0),
        ("match_completion", "completed", 3, 0),
        ("historic_refresh", "completed", 216, 2),
        ("daily_sync", "completed", 9, 0),
        ("tip_generation", "completed", 9, 0),
        ("match_completion", "completed", 2, 0),
        ("daily_sync", "completed", 9, 0),
    ]

    for job_name, status, processed, failed in jobs:
        started = now - timedelta(hours=exec_id * 4)
        duration = random.randint(30, 300)
        completed = started + timedelta(seconds=duration)

        executions.append(
            JobExecution(
                id=exec_id,
                job_name=job_name,
                status=status,
                started_at=started,
                completed_at=completed,
                duration_seconds=duration,
                items_processed=processed,
                items_failed=failed,
                error_message=None,
                result_summary=f"Processed {processed} items successfully",
            )
        )
        exec_id += 1

    return executions


# ---------------------------------------------------------------------------
# Main orchestration
# ---------------------------------------------------------------------------


async def seed_database(
    seasons: Optional[List[int]] = None,
    clear: bool = False,
    verbose: bool = False,
    seed: int = 42,
) -> Dict[str, int]:
    """Seed the database with realistic AFL data.

    Args:
        seasons: List of seasons to seed. Defaults to [2025, 2026].
        clear: Whether to clear existing data first.
        verbose: Whether to print progress.
        seed: Random seed for reproducibility.

    Returns:
        Dict mapping table names to number of records created.
    """
    if seasons is None:
        seasons = list(range(2010, 2027))

    rng = random.Random(seed)
    counts: Dict[str, int] = {}

    engine = get_engine()
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with session_factory() as session:
        try:
            if clear:
                if verbose:
                    print("🗑️  Clearing existing data...")
                await clear_all_data(session)
                if verbose:
                    print("✅ Existing data cleared.")

            # --- Games ---
            if verbose:
                print(f"🏈 Generating games for seasons {seasons}...")

            all_games: List[Game] = []
            for season in seasons:
                if season == max(seasons):
                    # Current season: partially completed (up to round 12)
                    current_round = 12
                    games = seed_games(
                        rng,
                        season=season,
                        rounds=ROUNDS_PER_SEASON,
                        completed_rounds=current_round,
                        squiggle_id_start=10000 + (season - 2010) * 1000,
                    )
                else:
                    # Past season: fully completed
                    games = seed_games(
                        rng,
                        season=season,
                        rounds=ROUNDS_PER_SEASON,
                        completed_rounds=ROUNDS_PER_SEASON,
                        squiggle_id_start=10000 + (season - 2010) * 1000,
                    )
                all_games.extend(games)

            session.add_all(all_games)
            await session.flush()
            counts["games"] = len(all_games)

            # --- Model Predictions ---
            if verbose:
                print("🤖 Generating model predictions...")
            predictions = seed_model_predictions(rng, all_games)
            session.add_all(predictions)
            await session.flush()
            counts["model_predictions"] = len(predictions)

            # --- Tips ---
            if verbose:
                print("💡 Generating tips...")
            tips = seed_tips(rng, all_games)
            session.add_all(tips)
            await session.flush()
            counts["tips"] = len(tips)

            # --- Elo Cache ---
            if verbose:
                print("📊 Generating Elo cache...")
            elo_entries = seed_elo_cache(rng, max(seasons))
            session.add_all(elo_entries)
            await session.flush()
            counts["elo_cache"] = len(elo_entries)

            # --- Backtest Results ---
            if verbose:
                print("📈 Generating backtest results...")
            backtest_results: List[BacktestResult] = []
            for season in seasons:
                if season == max(seasons):
                    backtest_results.extend(
                        seed_backtest_results(rng, season, rounds=12)
                    )
                else:
                    backtest_results.extend(
                        seed_backtest_results(rng, season, rounds=ROUNDS_PER_SEASON)
                    )
            session.add_all(backtest_results)
            await session.flush()
            counts["backtest_results"] = len(backtest_results)

            # --- Match Analyses ---
            if verbose:
                print("📝 Generating match analyses...")
            analyses = seed_match_analyses(rng, all_games)
            session.add_all(analyses)
            await session.flush()
            counts["match_analyses"] = len(analyses)

            # --- Generation Progress ---
            if verbose:
                print("⏳ Generating progress tracking...")
            progress = seed_generation_progress(seasons)
            session.add_all(progress)
            await session.flush()
            counts["generation_progress"] = len(progress)

            # --- Job Executions ---
            if verbose:
                print("⚙️  Generating job execution history...")
            job_execs = seed_job_executions()
            session.add_all(job_execs)
            await session.flush()
            counts["job_executions"] = len(job_execs)

            # Commit everything
            await session.commit()

            if verbose:
                print("\n✅ Seed complete! Summary:")
                for table, count in counts.items():
                    print(f"   {table}: {count} records")

            return counts

        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()

    await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed the WhatIsMyTip database")
    parser.add_argument(
        "--season",
        type=int,
        nargs="*",
        default=None,
        help="Season(s) to seed (default: 2025 and 2026)",
    )
    parser.add_argument(
        "--clear",
        action="store_true",
        help="Clear existing data before seeding",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Print progress to stdout",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=42,
        help="Random seed for reproducibility (default: 42)",
    )
    args = parser.parse_args()

    seasons = args.season if args.season else None
    asyncio.run(
        seed_database(
            seasons=seasons,
            clear=args.clear,
            verbose=args.verbose,
            seed=args.seed,
        )
    )


if __name__ == "__main__":
    main()
