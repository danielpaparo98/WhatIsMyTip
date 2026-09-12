"""Real-Postgres integration test for ``ModelOrchestrator.predict_all``.

Regression guard for the ORCH-H1 review finding: all 8 models previously
ran under ONE shared ``AsyncSession`` inside ``asyncio.gather``.  Under a
real SQLAlchemy/asyncpg session that concurrent use raises
``InvalidRequestError`` / asyncpg ``InterfaceError`` — but unit tests
used fakes, so CI never saw it.  Failures were then swallowed and the
affected models silently voted ``(home_team, 0.5, 0)``.

This test runs the REAL orchestrator with the REAL 8 models against a
real Postgres (podman testcontainer) and asserts:

* ``predict_all`` completes without raising;
* every model either contributes a prediction or is reported in
  ``failed_models`` (nothing silently substituted);
* two orchestrator runs on different games can execute concurrently
  without session collisions.
"""

from __future__ import annotations

import asyncio
from datetime import datetime

import pytest

from packages.shared.models import Game
from packages.shared.orchestrator import ModelOrchestrator

MODEL_COUNT = 8  # elo, form, home_advantage, value, weather, injury, matchup, player_form


def _make_game(game_id: int, slug: str, home: str, away: str) -> Game:
    return Game(
        id=game_id,
        slug=slug,
        squiggle_id=9000 + game_id,
        round_id=1,
        season=2025,
        home_team=home,
        away_team=away,
        venue="Gabba",
        # TIMESTAMP WITHOUT TIME ZONE column — naive datetime required
        # (see conftest seed-row note).
        date=datetime(2025, 3, 20, 8, 30),
        completed=False,
    )


@pytest.mark.asyncio
async def test_predict_all_real_models_real_postgres(seeded_db):
    """All 8 real models run against real Postgres; every model either
    predicts or abstains-and-is-reported.  Under the old shared-session
    code this test fails (concurrent AsyncSession use + silent home-team
    fallbacks)."""
    factory = seeded_db  # async_sessionmaker bound to the testcontainer

    # Persist a game row (models query the games table for history).
    async with factory() as session:
        game = _make_game(2, "orchtest0002", "Brisbane", "Collingwood")
        session.add(game)
        await session.commit()

    orchestrator = ModelOrchestrator(session_factory=factory)

    async with factory() as session:
        # Re-fetch so the ORM instance is bound to this session's identity map.
        game = await session.get(Game, 2)
        results = await orchestrator.predict_all(game, db=session)

    assert set(results.keys()) == {"best_bet", "yolo", "weighted_tip"}

    for heuristic_name, payload in results.items():
        preds = payload["model_predictions"]
        failed = payload["failed_models"]
        # Every model accounted for: contributed OR reported as failed.
        assert len(preds) + len(failed) == MODEL_COUNT, (
            f"{heuristic_name}: {len(preds)} predictions + {len(failed)} failed "
            f"!= {MODEL_COUNT} models"
        )
        # No phantom home-team default votes from the old fallback:
        for name, (winner, confidence, margin) in preds.items():
            assert confidence != 0.5 or margin != 0 or winner != game.home_team, (
                f"{heuristic_name}/{name}: looks like the old silent "
                f"(home_team, 0.5, 0) fallback leaked into predictions"
            )
        # The heuristic always produces a tip tuple.
        assert len(payload["tip"]) == 3


@pytest.mark.asyncio
async def test_concurrent_predict_all_no_session_collision(seeded_db):
    """Two concurrent orchestrator runs (different games) must not collide.

    With the old code this interleaved 16 model queries on 2 shared
    sessions and blew up under real asyncpg; with session-per-task it is
    safe."""
    factory = seeded_db

    async with factory() as session:
        session.add(_make_game(3, "orchtest0003", "Carlton", "Essendon"))
        session.add(_make_game(4, "orchtest0004", "Richmond", "Sydney"))
        await session.commit()

    async def run_one(game_id: int):
        orchestrator = ModelOrchestrator(session_factory=factory)
        async with factory() as session:
            game = await session.get(Game, game_id)
            return await orchestrator.predict_all(game, db=session)

    results_a, results_b = await asyncio.gather(run_one(3), run_one(4))

    for results in (results_a, results_b):
        assert set(results.keys()) == {"best_bet", "yolo", "weighted_tip"}
        payload = results["best_bet"]
        assert len(payload["model_predictions"]) + len(payload["failed_models"]) == MODEL_COUNT
