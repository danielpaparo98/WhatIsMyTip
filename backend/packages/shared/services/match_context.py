"""Build a rich, balanced match context for AI explanations & talking points.

Previously the LLM only received ``home_team``/``away_team``/``venue``/``date``
plus the per-model ``(winner, confidence, margin)`` tuples — it had no
access to the actual signals the 8 ML models consume (ELO ratings, recent
form, weather, injuries, head-to-head record).  This module gathers every
available data source for a game into a single dict so the explanation and
talking-point prompts can *interpret* the result rather than restate it.

Every data source is wrapped defensively: if a table is empty or a query
fails (cold start, partial scrape), that section is simply omitted so
generation never breaks.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..crud.elo_cache import EloCacheCRUD
from ..logger import get_logger
from ..models import Game, Injury, MatchWeather
from ..teams import canonical_team

logger = get_logger(__name__)

_FORM_LOOKBACK = 5
_H2H_LOOKBACK = 10


def _ensure_aware(dt: Optional[datetime]) -> datetime:
    if dt is None:
        return datetime.now(timezone.utc)
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


async def _recent_form(
    db: AsyncSession, team: str, before_date: Optional[datetime], limit: int
) -> Dict[str, Any]:
    """Recent win/loss form for ``team`` (last ``limit`` completed games)."""
    cutoff = _ensure_aware(before_date)
    result = await db.execute(
        select(Game)
        .where(
            Game.completed.is_(True),
            or_(Game.home_team == team, Game.away_team == team),
            Game.date < cutoff,
        )
        .order_by(Game.date.desc())
        .limit(limit)
    )
    games: List[Game] = list(result.scalars().all())
    if not games:
        return {"games": 0, "wins": 0, "losses": 0, "streak": "-", "avg_margin": 0}

    wins = 0
    margins: List[int] = []
    flags: List[str] = []
    for g in games:  # most-recent first
        if g.home_team == team:
            diff = (g.home_score or 0) - (g.away_score or 0)
        else:
            diff = (g.away_score or 0) - (g.home_score or 0)
        margins.append(diff)
        won = diff > 0
        if won:
            wins += 1
        flags.append("W" if won else "L")

    return {
        "games": len(games),
        "wins": wins,
        "losses": len(games) - wins,
        "streak": "".join(flags),
        "avg_margin": round(sum(margins) / len(margins), 1),
    }


async def _head_to_head(
    db: AsyncSession, home_team: str, away_team: str, before_date: Optional[datetime]
) -> Dict[str, Any]:
    """Head-to-head record between the two teams (last ``_H2H_LOOKBACK``)."""
    cutoff = _ensure_aware(before_date)
    result = await db.execute(
        select(Game)
        .where(
            Game.completed.is_(True),
            or_(
                and_(Game.home_team == home_team, Game.away_team == away_team),
                and_(Game.home_team == away_team, Game.away_team == home_team),
            ),
            Game.date < cutoff,
        )
        .order_by(Game.date.desc())
        .limit(_H2H_LOOKBACK)
    )
    games: List[Game] = list(result.scalars().all())
    if not games:
        return {"games": 0, "home_wins": 0, "away_wins": 0}

    home_wins = away_wins = 0
    for g in games:
        home_side_won = (g.home_score or 0) > (g.away_score or 0)
        winner_team = g.home_team if home_side_won else g.away_team
        if canonical_team(winner_team) == canonical_team(home_team):
            home_wins += 1
        else:
            away_wins += 1

    return {"games": len(games), "home_wins": home_wins, "away_wins": away_wins}


async def build_match_context(db: AsyncSession, game: Game) -> Dict[str, Any]:
    """Gather every available data source for ``game`` into one dict.

    The returned dict always contains the core match fields; optional
    sections (``elo``, ``weather``, ``injuries``, ``form``,
    ``head_to_head``) are added only when the underlying data exists.
    """
    home_canonical = canonical_team(game.home_team)
    away_canonical = canonical_team(game.away_team)

    ctx: Dict[str, Any] = {
        "home_team": game.home_team,
        "away_team": game.away_team,
        "venue": game.venue,
        "date": game.date.isoformat() if game.date else None,
        "season": game.season,
        "round": game.round_id,
    }

    # ── ELO ratings ────────────────────────────────────────────────
    try:
        home_elo = await EloCacheCRUD.get_team_rating(db, game.home_team)
        away_elo = await EloCacheCRUD.get_team_rating(db, game.away_team)
        if home_elo is not None and away_elo is not None:
            ctx["elo"] = {
                "home": round(float(home_elo)),
                "away": round(float(away_elo)),
                "diff": round(float(home_elo) - float(away_elo)),
            }
    except Exception as e:  # noqa: BLE001 - defensive, must not break gen
        logger.debug(f"ELO context unavailable for game {game.id}: {e}")

    # ── Weather ────────────────────────────────────────────────────
    try:
        wresult = await db.execute(
            select(MatchWeather).where(MatchWeather.game_id == game.id)
        )
        w = wresult.scalar_one_or_none()
        if w is not None:
            ctx["weather"] = {
                "temperature": w.temperature,
                "precipitation": w.precipitation,
                "wind_speed": w.wind_speed,
                "humidity": w.humidity,
                "conditions": w.weather_code,
            }
    except Exception as e:  # noqa: BLE001
        logger.debug(f"Weather context unavailable for game {game.id}: {e}")

    # ── Injuries (per team, excluding "Available"/"Test") ─────────
    try:
        iresult = await db.execute(
            select(Injury.team, Injury.player_name, Injury.return_timeline)
            .where(
                Injury.team.in_([game.home_team, game.away_team]),
                Injury.return_timeline.isnot(None),
                Injury.return_timeline != "Available",
                Injury.return_timeline != "Test",
            )
        )
        home_outs: List[str] = []
        away_outs: List[str] = []
        for team, name, timeline in iresult.all():
            label = f"{name}" + (f" ({timeline})" if timeline else "")
            if canonical_team(team) == home_canonical:
                home_outs.append(label)
            elif canonical_team(team) == away_canonical:
                away_outs.append(label)
        if home_outs or away_outs:
            ctx["injuries"] = {"home": home_outs, "away": away_outs}
    except Exception as e:  # noqa: BLE001
        logger.debug(f"Injury context unavailable for game {game.id}: {e}")

    # ── Recent form (last N completed games each) ─────────────────
    try:
        ctx["form"] = {
            "home": await _recent_form(db, game.home_team, game.date, _FORM_LOOKBACK),
            "away": await _recent_form(db, game.away_team, game.date, _FORM_LOOKBACK),
        }
    except Exception as e:  # noqa: BLE001
        logger.debug(f"Form context unavailable for game {game.id}: {e}")

    # ── Head-to-head ───────────────────────────────────────────────
    try:
        ctx["head_to_head"] = await _head_to_head(
            db, game.home_team, game.away_team, game.date
        )
    except Exception as e:  # noqa: BLE001
        logger.debug(f"Head-to-head context unavailable for game {game.id}: {e}")

    return ctx


__all__ = ["build_match_context"]
