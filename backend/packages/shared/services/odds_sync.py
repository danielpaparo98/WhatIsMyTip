"""Daily AFL odds sync (BT-ODDS, 2026-10 review).

Fetches live+upcoming AFL head-to-head odds from The Odds API and
upserts one snapshot per upcoming game into ``game_odds``.  Backtests
settle games that have a snapshot at their real decimal price and all
other games at the representative fallback price (see
:mod:`packages.shared.services.settlement`).

Matching: Odds API team names pass through the canonical AFL map
(``'Sydney Swans'`` → ``'Sydney'``), and each event joins to a
``games`` row by (canonical home, canonical away, kick-off day ±1d).
Home/away sides matter — a reversed fixture is a different game.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Dict, List, Tuple

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..crud.game_odds import GameOddsCRUD
from ..logger import get_logger
from ..models import Game
from ..odds_api.client import OddsAPIClient, odds_api_canonical_team
from ..teams import canonical_team

logger = get_logger(__name__)

# Window of upcoming games eligible for an odds snapshot.  One day of
# back-tolerance covers tz edge cases; ten days forward keeps the
# snapshot focused on games that will actually use it (a free-tier
# response covers ~2 rounds anyway).
_MATCH_BACKWARD_TOLERANCE = timedelta(days=1)
_MATCH_FORWARD_WINDOW = timedelta(days=10)


def _as_utc(value: datetime) -> datetime:
    """Normalise a naive-or-aware datetime to aware UTC."""
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def match_events_to_games(
    events: List[Dict],
    games: List,
) -> Tuple[List[Tuple[object, Dict]], List[Dict]]:
    """Join Odds API events to upcoming games.

    Returns:
        ``(matched, unmatched)`` where ``matched`` is a list of
        ``(game, event)`` pairs and ``unmatched`` lists events that
        found no eligible game (logged for operator visibility).
    """
    # Candidate index: (canonical home, canonical away) → [games]
    by_teams: Dict[Tuple[str, str], List] = {}
    for game in games:
        if game.home_team is None or game.away_team is None or game.date is None:
            continue
        key = (canonical_team(game.home_team), canonical_team(game.away_team))
        by_teams.setdefault(key, []).append(game)

    matched: List[Tuple[object, Dict]] = []
    unmatched: List[Dict] = []

    for event in events:
        commence = event.get("commence_time")
        if commence is None:
            unmatched.append(event)
            continue
        candidates = by_teams.get(
            (
                odds_api_canonical_team(event["home_team"]),
                odds_api_canonical_team(event["away_team"]),
            ),
            [],
        )
        best = None
        best_delta = None
        for game in candidates:
            delta = abs(_as_utc(game.date) - commence)
            if delta > _MATCH_BACKWARD_TOLERANCE:
                continue
            if best_delta is None or delta < best_delta:
                best, best_delta = game, delta
        if best is not None:
            matched.append((best, event))
        else:
            unmatched.append(event)

    return matched, unmatched


async def run_odds_sync(db: AsyncSession) -> dict:
    """Fetch AFL odds and upsert snapshots for upcoming games.

    Skips cleanly (never fails the job) when no API key is configured —
    the deployment simply runs on fallback-price settlement.

    Args:
        db: Database session

    Returns:
        Summary dict: events fetched, games matched/updated, unmatched
        events, and per-write errors.
    """
    if not settings.odds_api_key:
        logger.info(
            "odds-sync skipped: ODDS_API_KEY not configured "
            "(backtests settle at the representative fallback price)"
        )
        return {"skipped": True, "reason": "odds_api_key not configured"}

    now_utc = datetime.now(timezone.utc)

    async with OddsAPIClient() as client:
        events = await client.get_afl_head_to_head_odds()

    logger.info("odds-sync fetched %d upcoming AFL events", len(events))

    # Upcoming (not completed) games in the match window.  Comparing
    # against the UTC window rather than venue-local keeps the query
    # index-friendly and consistent with how ``games.date`` is stored.
    result = await db.execute(
        select(Game)
        .where(
            Game.completed.is_(False),
            Game.date.isnot(None),
            Game.date >= now_utc - _MATCH_BACKWARD_TOLERANCE,
            Game.date <= now_utc + _MATCH_FORWARD_WINDOW,
        )
        .order_by(Game.date)
    )
    upcoming_games = list(result.scalars().all())

    matched, unmatched = match_events_to_games(events, upcoming_games)

    updated = 0
    errors = 0
    for game, event in matched:
        try:
            await GameOddsCRUD.upsert(
                db,
                game_id=game.id,
                home_odds=event["home_odds"],
                away_odds=event["away_odds"],
                bookmaker=event["bookmaker"],
                captured_at=event["captured_at"],
            )
            updated += 1
        except Exception:  # noqa: BLE001 — one bad row must not lose the batch
            errors += 1
            logger.exception("odds-sync failed to upsert odds for game %s", game.id)

    if unmatched:
        logger.info(
            "odds-sync: %d events had no matching upcoming game "
            "(byes, finals renaming, or feed extras)",
            len(unmatched),
        )

    await db.commit()

    summary = {
        "skipped": False,
        "events_fetched": len(events),
        "upcoming_games": len(upcoming_games),
        "games_matched": len(matched),
        "games_updated": updated,
        "unmatched": len(unmatched),
        "errors": errors,
    }
    logger.info("odds-sync complete: %s", summary)
    return summary
