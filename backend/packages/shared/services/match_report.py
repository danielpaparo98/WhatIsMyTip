"""Grand-final pre-match report generation via a Pydantic AI research agent.

During grand-final week a research agent queries the app's own Postgres
data (model predictions, tips, backtest accuracy, player stats, injuries,
weather, ELO, form, H2H, finals path) through a set of ``@agent.tool``
functions and writes a structured :class:`GrandFinalReport`, stored in the
``match_reports`` table.

Design constraints (mirroring ``services/match_analysis.py``):

* Generation must NEVER break tip generation â€” every failure is logged
  and ``None`` is returned.  No fabricated fallback report is produced.
* Without an OpenRouter API key the service is inert (returns ``None``).
* ``is_grand_final`` is a static helper so the API layer can gate on it
  without constructing the agent or any OpenRouter client.

Pydantic AI API reference (verified 2026-09-19, pydantic-ai 2.46.0):
``Agent(model, deps_type=..., output_type=..., instructions=...)`` with
``result.output`` for structured output; tools via ``@agent.tool`` with
``ctx.deps``; limits via ``UsageLimits`` / ``ModelSettings``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict

from pydantic_ai import Agent, RunContext
from pydantic_ai.models.openrouter import OpenRouterModel, OpenRouterModelSettings
from pydantic_ai.providers.openrouter import OpenRouterProvider
from pydantic_ai.tools import ToolDefinition
from pydantic_ai.usage import UsageLimits
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..crud.elo_cache import EloCacheCRUD
from ..crud.match_report import MatchReportCRUD
from ..crud.model_predictions import ModelPredictionCRUD
from ..crud.tips import TipCRUD
from ..logger import get_logger
from ..models import (
    BacktestResult,
    Game,
    Injury,
    MatchWeather,
    Player,
    PlayerAdvancedStats,
    PlayerMatchStats,
)
from ..schemas.match_report import GrandFinalReport
from ..teams import canonical_team
from .match_context import _head_to_head, _recent_form

logger = get_logger(__name__)

_REPORT_TYPE = "grand_final_pre_match"

# Guardrails: cap the agent's research loop and output size so a run can
# never spiral (B6).  Breaching either limit raises UsageLimitExceeded,
# which is caught and reported as a failed generation (None).
#
# GF-BUDGET (2026-09-20, user request): raised again for the switch to
# `ling-3.0-flash` WITH reasoning enabled — reasoning burns extra
# tokens between tool calls, and pydantic-ai resends the full growing
# conversation on every research round-trip, so the honest budget for a
# 12-tool loop is six figures.  At ling-flash prices (~$0.02-0.06/M)
# even a 300k-token run costs well under a cent, once a season.
# GF-BUDGET v3 (2026-09-20): with reasoning ENABLED on ling-flash the
# research loop regularly exceeds 150k tokens and the old budget tripped
# UsageLimitExceeded mid-run (prod regens failed after 71s/222s).  At
# ling prices (~$0.02-0.06/M) even a full 1M-token run costs a few
# cents, once a season — so the token ceiling is generous and the REAL
# runaway guard is the request cap (60 LLM round-trips max).
_REQUEST_LIMIT = 60
_TOTAL_TOKEN_LIMIT = 1_000_000
_TEMPERATURE = 0.3
_MAX_TOKENS = 16_000
# GF-CONTENT FIX: "Exceeded maximum output retries (2)" — with full-effort
# reasoning the structured-output tool call truncated inside the token
# budget and failed validation repeatedly.  A dedicated, larger OUTPUT
# retry budget (4) gives the model more swings at valid JSON.
_AGENT_RETRIES = {"output": 4}

_FORM_LOOKBACK = 5
_STAT_CHOICES = ("goals", "disposals", "marks", "tackles", "hitouts")

# Finals labels keyed by (max_round - round_id) for the season's last four
# rounds: the max round itself is the grand final.  Any unexpected offset
# falls back to a generic label.
_FINALS_LABELS = {
    0: "Grand Final",
    1: "Preliminary Final",
    2: "Semi Final",
    3: "Finals Week 1",
}

_AGENT_INSTRUCTIONS = (
    "You are an expert AFL analyst writing the PRE-MATCH grand final report "
    "for a footy-tipping website. This report is a PREDICTION of what is "
    "anticipated to happen in the game — never a result recap. Call the "
    "research tools to ground every claim in the app's own data: season "
    "summaries, recent form, head-to-head, finals paths, stat leaders, "
    "advanced player stats, injuries, weather, ELO ratings, model "
    "predictions, public tips and season accuracy. Cite the specific "
    "signals behind each judgement and stay honest about uncertainty. "
    "Write in a fan-friendly tone. NEVER invent statistics, injuries or "
    "facts that the tools did not return — when a data source comes back "
    "empty, either omit it or say it is unavailable.\n\n"
    "GF-CONTENT RULES (strict):\n"
    "- If a data source is empty or unavailable, set the corresponding "
    "report field to null (weather_impact / x_factor) or omit the entry "
    "entirely — NEVER write filler prose like 'data was unavailable'.\n"
    "- NEVER create placeholder player entries (e.g. name='Unknown'). "
    "Only name players the tools actually returned; omit the rest.\n"
    "- key_players MUST include 2-4 players for EACH team whenever "
    "stat-leader data was returned for them — always cover BOTH the "
    "home and the away side.\n"
    "- HEADLINE: at most 8 words, an editorial hook only. The teams, "
    "venue, date and the words 'Grand Final' / 'Pre-Match Report' are "
    "ALREADY displayed around it — do not repeat them.\n"
    "- NEUTRAL VENUE: the grand final has NO home team — never imply "
    "either side has home-ground advantage (no 'home fortress', "
    "'defend home', 'home crowd' framing).\n\n"
    "RESEARCH EFFICIENCY RULES (strict):\n"
    "- The two teams are named in the task below; use those EXACT names "
    "for every tool's `team` argument. Never try alternative spellings.\n"
    "- Call each tool AT MOST once per unique argument set. If a result "
    "comes back empty or thin, do NOT retry â€” note it and move on.\n"
    "- Aim to finish research in roughly 12-16 tool calls, then write "
    "the report. Do not re-call a tool you already have the answer from."
)


@dataclass
class GFDeps:
    """Dependencies passed to every grand-final agent tool run.

    ``tool_cache`` dedupes research calls: a reasoning model left to its
    own devices re-called ``get_team_season_summary`` 48 times in one
    prod-shaped run (name-variant retries), burning the whole token
    budget.  Tools consult the cache and return the stored result with
    a "do not call again" note on repeats.

    ``tool_steps`` counts how many agent steps each tool has been
    OFFERED in; once a tool exceeds its budget (``_budgeted`` prepare)
    it is removed from the toolset entirely, so the model physically
    cannot loop on it.
    """

    db: AsyncSession
    tool_cache: dict[str, Any] = field(default_factory=dict)
    tool_steps: dict[str, int] = field(default_factory=dict)


def _budgeted(tool_name: str, max_steps: int):
    """Return a per-tool ``prepare`` hook that removes ``tool_name``
    from the toolset after ``max_steps`` agent steps.

    This is the hard loop-guard: returning ``None`` from ``prepare``
    drops the tool, so a model stuck retrying a tool with name
    variants (the observed failure mode) is forced to move on and
    write the report from the data it already has.
    """

    async def _prepare(ctx: RunContext[GFDeps], tool_def: ToolDefinition):
        used = ctx.deps.tool_steps.get(tool_name, 0)
        if used >= max_steps:
            return None
        ctx.deps.tool_steps[tool_name] = used + 1
        return tool_def

    return _prepare


def _pick_hourly(hourly: Dict[str, Any], key: str, idx: int) -> Any:
    """Pull ``key`` at index ``idx`` from an Open-Meteo hourly payload."""
    values = hourly.get(key) or []
    return values[idx] if idx < len(values) else None


# GF-CONTENT: LLM output occasionally carries double-encoded characters
# ("â€"" for an em dash, "Â°" for a degree sign).  Normalise the common
# mojibake sequences so user-facing copy renders cleanly.
_MOJIBAKE_MAP = {
    "â€”": "—",
    "â€“": "–",
    "â€œ": '"',
    "â€\x9d": '"',
    "â€˜": "'",
    "â€\x99": "'",
    "Â°": "°",
    "Â·": "·",
}


def _sanitize_mojibake(value: Any) -> Any:
    """Recursively normalise double-encoded characters in a payload.

    Mapping table for the known sequences, then a final sweep strips
    stray C0/C1 control characters (the residue of mis-decoded UTF-8,
    e.g. the artefact inside '15.7\u0097°C').
    """
    if isinstance(value, str):
        for bad, good in _MOJIBAKE_MAP.items():
            value = value.replace(bad, good)
        value = "".join(
            ch
            for ch in value
            if not (ord(ch) <= 0x08 or (0x0E <= ord(ch) <= 0x1F) or (0x7F <= ord(ch) <= 0x9F))
        )
        return value
    if isinstance(value, dict):
        return {k: _sanitize_mojibake(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_sanitize_mojibake(v) for v in value]
    return value


async def _ensure_weather(db: AsyncSession, game: Game) -> None:
    """Fetch and store the forecast for ``game`` when no row exists.

    GF-CONTENT FIX (2026-09-20): weather lived only in a MANUAL seed
    script — nothing in the cron pipeline ever fetched it — so upcoming
    games (the grand final!) had no ``match_weather`` row and the
    report's weather section went blank.  The report generation now
    self-serves: one Open-Meteo forecast call the first time it runs.
    Defensive throughout: any failure is logged and skipped.
    """
    try:
        existing = await db.execute(
            select(MatchWeather).where(MatchWeather.game_id == game.id)
        )
        if existing.scalar_one_or_none() is not None:
            return
        if not game.venue or not game.date:
            return

        from ..weather.client import WeatherClient

        async with WeatherClient() as client:
            forecast = await client.get_forecast(game.venue, days=8)

        hourly = (forecast or {}).get("hourly") or {}
        times = hourly.get("time") or []
        if not times:
            logger.info(f"No forecast data available for venue {game.venue!r}")
            return

        # Index the hourly forecast at (or nearest to) kick-off.
        kickoff = game.date.replace(tzinfo=None) if game.date.tzinfo else game.date
        try:
            idx = min(
                range(len(times)),
                key=lambda i: abs(
                    datetime.fromisoformat(times[i]).replace(tzinfo=None) - kickoff
                ),
            )
        except ValueError:
            idx = len(times) // 2

        db.add(
            MatchWeather(
                game_id=game.id,
                venue=game.venue,
                match_date=kickoff.date(),
                temperature=_pick_hourly(hourly, "temperature_2m", idx),
                precipitation=_pick_hourly(hourly, "precipitation", idx),
                wind_speed=_pick_hourly(hourly, "windspeed_10m", idx),
                wind_direction=_pick_hourly(hourly, "winddirection_10m", idx),
                wind_gusts=_pick_hourly(hourly, "windgusts_10m", idx),
                humidity=_pick_hourly(hourly, "relative_humidity_2m", idx),
                weather_code=_pick_hourly(hourly, "weathercode", idx),
                data_type="forecast",
                raw_hourly=hourly,
            )
        )
        await db.commit()
        logger.info(
            f"Stored forecast weather for game {game.id} ({game.venue!r})"
        )
    except Exception as e:  # noqa: BLE001 — weather must not break the report
        logger.warning(f"Weather ensure failed for game {game.id} (non-fatal): {e}")


def _has_known_teams(home: Any, away: Any) -> bool:
    """True when BOTH team names are known (non-null, non-blank)."""
    return bool((home or "").strip()) and bool((away or "").strip())


def _build_prompt(game: Game) -> str:
    """Build the user prompt for the grand-final report run."""
    return (
        f"Write the grand-final pre-match report for {game.home_team} vs "
        f"{game.away_team} â€” season {game.season}, round {game.round_id}, "
        f"venue {game.venue}, kick-off {game.date}. Call the research tools "
        f"to gather both teams' season summaries, recent form, "
        f"head-to-head record, finals paths, stat leaders, advanced stats, "
        f"injuries, the weather forecast, ELO ratings, model predictions, "
        f"public tips and season accuracy â€” then write the report."
    )


def _register_tools(agent: Agent[GFDeps, GrandFinalReport], game: Game) -> None:
    """Attach the research tools to ``agent`` (closures over ``game``).

    Every tool is defensive: a failing or empty data source returns an
    empty structure so one bad table never aborts the whole run.
    """
    season = game.season

    @agent.tool(prepare=_budgeted("get_team_season_summary", 3))
    async def get_team_season_summary(
        ctx: RunContext[GFDeps], team: str
    ) -> dict[str, Any]:
        """Season summary for one team: W/L record, points for/against, ladder position.

        Args:
            team: Team name as it appears in the fixtures (e.g. "Brisbane").
        """
        # DUP-GUARD (agent-loop defence): a reasoning model will retry
        # this call with name variants when the result is thin â€”
        # canonicalize the key and return cached results on repeats so
        # the loop terminates.
        canonical = canonical_team(team)
        cache_key = f"season_summary:{canonical}"
        cached = ctx.deps.tool_cache.get(cache_key)
        if cached is not None:
            return {**cached, "note": "cached result â€” do not call this tool again"}

        try:
            result = await ctx.deps.db.execute(
                select(Game).where(
                    Game.completed.is_(True),
                    Game.season == season,
                )
            )
            games = list(result.scalars().all())

            table: dict[str, dict[str, int]] = {}
            for g in games:
                home = g.home_team
                away = g.away_team
                if not home or not away:
                    continue
                home_score = g.home_score or 0
                away_score = g.away_score or 0
                for name, points_for, points_against in (
                    (home, home_score, away_score),
                    (away, away_score, home_score),
                ):
                    stats = table.setdefault(
                        name,
                        {
                            "played": 0,
                            "wins": 0,
                            "losses": 0,
                            "draws": 0,
                            "points_for": 0,
                            "points_against": 0,
                        },
                    )
                    stats["played"] += 1
                    stats["points_for"] += points_for
                    stats["points_against"] += points_against
                    if points_for > points_against:
                        stats["wins"] += 1
                    elif points_for < points_against:
                        stats["losses"] += 1
                    else:
                        stats["draws"] += 1

            # Lookup order: exact â†’ canonical â†’ case-insensitive, so a
            # name-variant argument still finds the team's row instead
            # of triggering a retry loop.
            matched_team = team
            if matched_team not in table:
                matched_team = next(
                    (name for name in table if canonical_team(name) == canonical),
                    None,
                )
            if matched_team is None:
                matched_team = next(
                    (
                        name
                        for name in table
                        if name.strip().lower() == team.strip().lower()
                    ),
                    None,
                )

            if matched_team is None:
                summary = {
                    "team": team,
                    "season": season,
                    "note": "no completed games found for this team",
                    "known_teams": sorted(table.keys())[:20],
                }
            else:
                # Ladder position: rank by wins among the teams that played
                # this season (simple heuristic, consistent with the locator).
                ranked = sorted(
                    table.items(), key=lambda kv: kv[1]["wins"], reverse=True
                )
                ladder_position = next(
                    (
                        i + 1
                        for i, (name, _stats) in enumerate(ranked)
                        if name == matched_team
                    ),
                    None,
                )
                summary = {
                    "team": matched_team,
                    "season": season,
                    "ladder_position": ladder_position,
                    "teams_in_competition": len(table),
                    **table[matched_team],
                }
            ctx.deps.tool_cache[cache_key] = summary
            return summary
        except Exception as e:  # noqa: BLE001 - defensive, must not break gen
            logger.debug(f"get_team_season_summary unavailable: {e}")
            return {"team": team, "season": season, "note": "unavailable"}

    @agent.tool(prepare=_budgeted("get_recent_form", 4))
    async def get_recent_form(
        ctx: RunContext[GFDeps], team: str, last_n: int = _FORM_LOOKBACK
    ) -> dict[str, Any]:
        """Recent win/loss form for one team (last N completed games).

        Args:
            team: Team name as it appears in the fixtures.
            last_n: How many recent completed games to look back over.
        """
        try:
            return dict(
                await _recent_form(ctx.deps.db, team, game.date, max(1, min(last_n, 10)))
            )
        except Exception as e:  # noqa: BLE001
            logger.debug(f"get_recent_form unavailable: {e}")
            return {"games": 0, "wins": 0, "losses": 0, "streak": "-", "avg_margin": 0}

    @agent.tool(prepare=_budgeted("get_head_to_head", 2))
    async def get_head_to_head(ctx: RunContext[GFDeps]) -> dict[str, Any]:
        """Head-to-head record between the two grand-final teams (last 10 meetings)."""
        try:
            return dict(
                await _head_to_head(ctx.deps.db, game.home_team, game.away_team, game.date)
            )
        except Exception as e:  # noqa: BLE001
            logger.debug(f"get_head_to_head unavailable: {e}")
            return {"games": 0, "home_wins": 0, "away_wins": 0}

    @agent.tool(prepare=_budgeted("get_finals_path", 4))
    async def get_finals_path(ctx: RunContext[GFDeps], team: str) -> list[str]:
        """The team's completed finals results this season, in round order.

        Finals are the season's last four rounds (the max round is the grand
        final); entries look like "Grand Final: beat X by N points".

        Args:
            team: Team name as it appears in the fixtures.
        """
        try:
            max_result = await ctx.deps.db.execute(
                select(func.max(Game.round_id)).where(Game.season == season)
            )
            max_round = max_result.scalar()
            if not max_round:
                return []

            result = await ctx.deps.db.execute(
                select(Game)
                .where(
                    Game.season == season,
                    Game.completed.is_(True),
                    Game.round_id >= max_round - 3,
                    Game.round_id <= max_round,
                    or_(Game.home_team == team, Game.away_team == team),
                )
                .order_by(Game.round_id.asc())
            )
            games = list(result.scalars().all())

            path: list[str] = []
            for g in games:
                offset = max_round - (g.round_id or max_round)
                label = _FINALS_LABELS.get(offset, f"Finals round {g.round_id}")
                if g.home_team == team:
                    opponent = g.away_team
                    team_score = g.home_score or 0
                    opp_score = g.away_score or 0
                else:
                    opponent = g.home_team
                    team_score = g.away_score or 0
                    opp_score = g.home_score or 0
                if team_score > opp_score:
                    verb = "beat"
                elif team_score == opp_score:
                    verb = "drew with"
                else:
                    verb = "lost to"
                path.append(
                    f"{label}: {verb} {opponent} by {abs(team_score - opp_score)} points"
                )
            return path
        except Exception as e:  # noqa: BLE001
            logger.debug(f"get_finals_path unavailable: {e}")
            return []

    @agent.tool(prepare=_budgeted("get_team_stat_leaders", 12))
    async def get_team_stat_leaders(
        ctx: RunContext[GFDeps], team: str, stat: str, top_n: int = 5
    ) -> dict[str, Any]:
        """Season aggregate leaders for one team and one stat.

        Args:
            team: Team name as it appears in the fixtures.
            stat: One of goals, disposals, marks, tackles, hitouts.
            top_n: How many leaders to return (1-10).
        """
        if stat not in _STAT_CHOICES:
            return {
                "team": team,
                "stat": stat,
                "leaders": [],
                "note": f"stat must be one of: {', '.join(_STAT_CHOICES)}",
            }
        try:
            stat_col = getattr(PlayerMatchStats, stat)
            canonical = canonical_team(team)
            # GF-CONTENT FIX (2026-09-20): `player_match_stats.team` was
            # seeded from the games table BEFORE the 0004 canonical-name
            # rewrite, so Brisbane rows may still read "Brisbane Lions".
            # Exact-match on the canonical name returned zero rows and
            # the report's Players section went blank for one side.  Match
            # the canonical form via the column OR the player's
            # current_team (always canonical).
            result = await ctx.deps.db.execute(
                select(Player.name, func.sum(stat_col).label("total"))
                .join(Game, PlayerMatchStats.game_id == Game.id)
                .join(Player, PlayerMatchStats.player_id == Player.id)
                .where(
                    Game.season == season,
                    Game.completed.is_(True),
                    or_(
                        PlayerMatchStats.team == team,
                        PlayerMatchStats.team == canonical,
                        Player.current_team == canonical,
                    ),
                )
                .group_by(Player.name)
                .order_by(func.sum(stat_col).desc())
                .limit(max(1, min(top_n, 10)))
            )
            leaders = [
                {"player": name, "total": int(total or 0)} for name, total in result.all()
            ]
            return {"team": team, "stat": stat, "leaders": leaders}
        except Exception as e:  # noqa: BLE001
            logger.debug(f"get_team_stat_leaders unavailable: {e}")
            return {"team": team, "stat": stat, "leaders": []}

    @agent.tool(prepare=_budgeted("get_player_advanced_note", 4))
    async def get_player_advanced_note(ctx: RunContext[GFDeps], team: str) -> list[dict[str, Any]]:
        """Top contested-possession / pressure-act players for one team this season.

        Args:
            team: Team name as it appears in the fixtures.
        """
        try:
            result = await ctx.deps.db.execute(
                select(
                    Player.name,
                    PlayerAdvancedStats.contested_possessions,
                    PlayerAdvancedStats.pressure_acts,
                )
                .join(Game, PlayerAdvancedStats.game_id == Game.id)
                .join(Player, PlayerAdvancedStats.player_id == Player.id)
                .where(
                    Game.season == season,
                    Player.current_team == canonical_team(team),
                    PlayerAdvancedStats.contested_possessions.isnot(None),
                )
                .order_by(PlayerAdvancedStats.contested_possessions.desc())
                .limit(5)
            )
            return [
                {
                    "player": name,
                    "contested_possessions": contested,
                    "pressure_acts": pressure,
                }
                for name, contested, pressure in result.all()
            ]
        except Exception as e:  # noqa: BLE001
            logger.debug(f"get_player_advanced_note unavailable: {e}")
            return []

    @agent.tool(prepare=_budgeted("get_injuries", 4))
    async def get_injuries(ctx: RunContext[GFDeps], team: str) -> list[dict[str, Any]]:
        """Current injury list for one team (excludes Available/Test players).

        Args:
            team: Team name as it appears in the fixtures.
        """
        try:
            # REVIEW-MINOR-4: canonicalize like the sibling tools â€” the
            # injuries pipeline predates the 0004 canonical-name rewrite,
            # so a raw LLM-supplied team string may not match stored rows.
            canonical = canonical_team(team)
            result = await ctx.deps.db.execute(
                select(Injury.player_name, Injury.injury_type, Injury.return_timeline)
                .where(
                    or_(Injury.team == team, Injury.team == canonical),
                    Injury.return_timeline.isnot(None),
                    Injury.return_timeline != "Available",
                    Injury.return_timeline != "Test",
                )
            )
            return [
                {"player": name, "injury_type": injury_type, "return_timeline": timeline}
                for name, injury_type, timeline in result.all()
            ]
        except Exception as e:  # noqa: BLE001
            logger.debug(f"get_injuries unavailable: {e}")
            return []

    @agent.tool(prepare=_budgeted("get_weather", 2))
    async def get_weather(ctx: RunContext[GFDeps]) -> dict[str, Any]:
        """Weather forecast for the grand final venue (empty when unavailable)."""
        try:
            result = await ctx.deps.db.execute(
                select(MatchWeather).where(MatchWeather.game_id == game.id)
            )
            w = result.scalar_one_or_none()
            if w is None:
                return {}
            return {
                "venue": w.venue,
                "match_date": str(w.match_date) if w.match_date else None,
                "temperature": w.temperature,
                "precipitation": w.precipitation,
                "wind_speed": w.wind_speed,
                "wind_gusts": w.wind_gusts,
                "humidity": w.humidity,
            }
        except Exception as e:  # noqa: BLE001
            logger.debug(f"get_weather unavailable: {e}")
            return {}

    @agent.tool(prepare=_budgeted("get_elo_ratings", 2))
    async def get_elo_ratings(ctx: RunContext[GFDeps]) -> dict[str, Any]:
        """Current ELO ratings for both grand-final teams plus the difference."""
        try:
            db = ctx.deps.db
            home_rating = await EloCacheCRUD.get_team_rating(db, game.home_team)
            away_rating = await EloCacheCRUD.get_team_rating(db, game.away_team)
            if home_rating is None or away_rating is None:
                return {}
            home_elo = float(home_rating)
            away_elo = float(away_rating)
            return {
                "home_team": game.home_team,
                "away_team": game.away_team,
                "home": round(home_elo),
                "away": round(away_elo),
                "diff": round(home_elo - away_elo),
            }
        except Exception as e:  # noqa: BLE001
            logger.debug(f"get_elo_ratings unavailable: {e}")
            return {}

    @agent.tool(prepare=_budgeted("get_model_predictions", 2))
    async def get_model_predictions(ctx: RunContext[GFDeps]) -> list[dict[str, Any]]:
        """The app's internal model predictions for the grand final (winner/margin/confidence)."""
        try:
            predictions = await ModelPredictionCRUD.get_by_game(ctx.deps.db, game.id)
            return [
                {
                    "model_name": p.model_name,
                    "winner": p.winner,
                    "confidence": p.confidence,
                    "margin": p.margin,
                }
                for p in predictions
            ]
        except Exception as e:  # noqa: BLE001
            logger.debug(f"get_model_predictions unavailable: {e}")
            return []

    @agent.tool(prepare=_budgeted("get_tips", 2))
    async def get_tips(ctx: RunContext[GFDeps]) -> list[dict[str, Any]]:
        """The app's published heuristic tips for the grand final."""
        try:
            tips = await TipCRUD.get_by_game(ctx.deps.db, game.id)
            return [
                {
                    "heuristic": t.heuristic,
                    "selected_team": t.selected_team,
                    "margin": t.margin,
                    "confidence": t.confidence,
                }
                for t in tips
            ]
        except Exception as e:  # noqa: BLE001
            logger.debug(f"get_tips unavailable: {e}")
            return []

    @agent.tool(prepare=_budgeted("get_season_accuracy", 2))
    async def get_season_accuracy(ctx: RunContext[GFDeps]) -> dict[str, Any]:
        """Per-heuristic tipping accuracy for the season (from backtest results)."""
        try:
            result = await ctx.deps.db.execute(
                select(BacktestResult).where(BacktestResult.season == season)
            )
            rows = list(result.scalars().all())
            if not rows:
                return {"season": season, "heuristics": {}}

            totals: dict[str, dict[str, int]] = {}
            for r in rows:
                heuristic = r.heuristic or "unknown"
                bucket = totals.setdefault(heuristic, {"tips_made": 0, "tips_correct": 0})
                bucket["tips_made"] += r.tips_made or 0
                bucket["tips_correct"] += r.tips_correct or 0

            heuristics: dict[str, dict[str, Any]] = {}
            for heuristic, bucket in totals.items():
                made = bucket["tips_made"]
                correct = bucket["tips_correct"]
                heuristics[heuristic] = {
                    "tips_made": made,
                    "tips_correct": correct,
                    "accuracy": round(correct / made, 3) if made else 0.0,
                }
            return {"season": season, "heuristics": heuristics}
        except Exception as e:  # noqa: BLE001
            logger.debug(f"get_season_accuracy unavailable: {e}")
            return {"season": season, "heuristics": {}}


class MatchReportService:
    """Generate and store the grand-final pre-match report."""

    def __init__(self) -> None:
        # Built lazily per generation call (requires an OpenRouter key);
        # never at construction time so the API layer can use the static
        # helpers freely.
        self._agent: Agent[GFDeps, GrandFinalReport] | None = None
        # OPS: last agent failure reason (surfaced by the admin
        # regenerate endpoint's "skipped" response).
        self.last_error: str | None = None

    @staticmethod
    async def is_grand_final(db: AsyncSession, game: Game) -> bool:
        """True when ``game`` is the last round of its season (grand final).

        Same heuristic as the ``GET /api/games?latest=true`` locator:
        the grand final is the game whose round_id equals the season's
        max round_id.
        """
        if game.round_id is None or game.season is None:
            return False
        result = await db.execute(
            select(func.max(Game.round_id)).where(Game.season == game.season)
        )
        max_round = result.scalar()
        return bool(max_round is not None and game.round_id == max_round)

    async def generate_and_store_report(
        self, db: AsyncSession, game: Game
    ) -> dict[str, Any] | None:
        """Generate the grand-final pre-match report and store it.

        Args:
            db: Database session
            game: Game to generate the report for

        Returns:
            The stored report payload dict, or None when the game is not
            an upcoming grand final with known teams, no OpenRouter key is
            configured, or generation fails.  Never raises.
        """
        try:
            # Gate 1: grand final only.
            if not await self.is_grand_final(db, game):
                logger.info(f"Game {game.id} is not the grand final; skipping match report")
                return None

            # Gate 2: participants must be known (TBC fixtures are skipped).
            if not _has_known_teams(game.home_team, game.away_team):
                logger.info(f"Game {game.id} has TBC teams; skipping match report")
                return None

            # Gate 3: PRE-match report only.
            if game.completed:
                logger.info(f"Game {game.id} is completed; match reports are pre-match only")
                return None

            # Gate 4: skip when a report is already stored.
            existing = await MatchReportCRUD.get_by_game_id(db, game.id)
            if existing is not None:
                logger.info(f"Match report already exists for game {game.id}")
                return existing.report

            # Gate 5: without an OpenRouter key we cannot run the agent â€”
            # skip rather than fabricate a fallback report.
            if not (settings.openrouter_api_key or "").strip():
                logger.warning(
                    "OpenRouter API key not configured; skipping grand-final match report"
                )
                return None

            # GF-CONTENT: self-serve the forecast so the weather section
            # has real data (nothing in the cron pipeline fetches it).
            await _ensure_weather(db, game)

            report = await self._run_agent(db, game)
            if report is None:
                return None

            row = await MatchReportCRUD.create_or_update(
                db, game.id, _REPORT_TYPE, report.model_dump(mode="json")
            )
            logger.info(f"Generated grand-final pre-match report for game {game.id}")
            return row.report

        except Exception as e:
            logger.error(
                f"Failed to generate match report for game {game.id}: {e}",
                exc_info=True,
            )
            return None

    async def _run_agent(self, db: AsyncSession, game: Game) -> GrandFinalReport | None:
        """Run the research agent once and return its structured output."""
        try:
            agent = self._build_agent(game)
            self._agent = agent
            result = await agent.run(
                _build_prompt(game),
                deps=GFDeps(db=db),
                usage_limits=UsageLimits(
                    request_limit=_REQUEST_LIMIT,
                    total_tokens_limit=_TOTAL_TOKEN_LIMIT,
                ),
                model_settings=OpenRouterModelSettings(
                    temperature=_TEMPERATURE,
                    max_tokens=_MAX_TOKENS,
                    # GF-BUDGET (2026-09-20, user request): reasoning is
                    # ALLOWED, capped at LOW effort — at default effort
                    # the model burned most of the output budget
                    # thinking and truncated the structured-output tool
                    # call mid-JSON ("Exceeded maximum output retries").
                    # Low effort keeps the thinking, fits the answer.
                    openrouter_reasoning={"effort": "low"},
                ),
            )
            # Store the sanitised payload (the JSONB blob is what the
            # API serves; normalise mojibake once at write time).
            return _sanitize_mojibake(result.output)
        except Exception as e:
            # OPS: keep the reason on the service so the admin endpoint
            # can surface WHY a regeneration was skipped without
            # trawling container logs.
            self.last_error = str(e)
            logger.error(
                f"Grand-final report agent run failed for game {game.id}: {e}",
                exc_info=True,
            )
            return None

    def _build_agent(self, game: Game) -> Agent[GFDeps, GrandFinalReport]:
        """Construct the Pydantic AI agent (verified 2.46.0 API) for ``game``.

        Tools close over ``game`` (season, id, date) so they can query the
        right rows; the agent itself is built per generation call, so no
        OpenRouter client exists until a generation actually runs.
        """
        model = OpenRouterModel(
            # GF-BUDGET (2026-09-20, user request): the report agent uses
            # its own cheap-but-capable model (ling-3.0-flash, 262k
            # context) instead of the shared nightly-explanations model.
            settings.match_report_model,
            provider=OpenRouterProvider(api_key=settings.openrouter_api_key),
        )
        agent = Agent(
            model,
            deps_type=GFDeps,
            output_type=GrandFinalReport,
            instructions=_AGENT_INSTRUCTIONS,
            retries=_AGENT_RETRIES,
        )
        _register_tools(agent, game)
        return agent

    async def close(self) -> None:
        """Release per-run resources.

        The agent (and its underlying OpenRouter HTTP client) is built per
        generation call, so there is nothing durable to close; the method
        exists for interface parity with ``MatchAnalysisService`` â€” callers
        (tip generation, admin regenerate) await it unconditionally.
        """
        self._agent = None
