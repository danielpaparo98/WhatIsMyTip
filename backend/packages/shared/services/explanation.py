"""Service for generating AI explanations for tips using OpenRouter.

Two sport paths share ONE OpenRouter pipeline:

* AFL (the bootstrap): :class:`ExplanationService` explains legacy
  ``tips`` rows (per-model predictions, weather/injury context) and
  persists the explanation onto the tip row.
* Rugby league (Phase 5.2, nrl-expansion-12): :class:`RugbyLeagueOpenRouterClient`
  + :class:`LeagueTipExplanationService` explain ``league_tips`` rows
  through the SAME request path — a sport-aware prompt/fallback subclass
  overrides only the wording hooks, never the LLM machinery.  Evidence
  is restricted to the reduced rugby-league model set (elo, form,
  home_advantage, matchup) and computed by the SAME pure functions that
  made the pick, so the explanation can never cite a signal the pick
  could not see (weather/injuries/player_form/value are not gathered
  and are rejected at the registry).  Explanations are not persisted
  (``league_tips`` carries no explanation column by design) — they are
  cached under the rugby-league cache namespace instead, so one tip
  pays for at most one OpenRouter call.
"""


from datetime import datetime
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..cache import RedisCache
from ..logger import get_logger
from ..models import (
    Competition,
    Event,
    EventParticipant,
    Game,
    LeagueTip,
    Participant,
    Season,
    Tip,
)
from ..openrouter import OpenRouterClient
from ..orchestrator import ModelOrchestrator
from ..sport_context import RUGBY_LEAGUE
from .league_heuristics import (
    FORM_WINDOW,
    HEURISTIC_ELO,
    HEURISTIC_FORM,
    HEURISTIC_HOME_ADVANTAGE,
    HEURISTIC_MATCHUP,
    CompletedEvent,
    LeagueHeuristicsService,
    compute_elo_ratings,
    head_to_head_wins,
    is_drawn,
    league_model_set_for_sport,
    require_league_model,
    sort_chronologically,
    wins_in_last_n,
)
from .match_context import build_match_context

logger = get_logger(__name__)


class ExplanationService:
    """Service for generating AI explanations for tips."""

    def __init__(self):
        self.client = OpenRouterClient()
        self.orchestrator = ModelOrchestrator()

    async def generate_and_store_explanation(
        self, db: AsyncSession, tip: Tip, game: Game
    ) -> str:
        """Generate an AI explanation for a tip and store it in the database.

        Args:
            db: Database session
            tip: Tip to generate explanation for
            game: Associated game

        Returns:
            Generated explanation string
        """
        # Get model predictions for richer context
        model_predictions = {}
        try:
            for model in self.orchestrator.models:
                winner, confidence, margin = await model.predict(game, db)
                model_predictions[model.get_name()] = (winner, confidence, margin)
        except Exception as e:
            logger.warning(
                f"Could not get model predictions for explanation context: {e}"
            )

        # Build prediction dict
        prediction = {
            "winner": tip.selected_team,
            "confidence": tip.confidence,
            "margin": tip.margin,
        }

        # Build game dict
        game_dict = {
            "home_team": game.home_team,
            "away_team": game.away_team,
            "venue": game.venue,
            "date": game.date.isoformat(),
        }

        # Gather the full match context (ELO, form, weather, injuries, H2H)
        # so the explanation can interpret the result rather than restate it.
        match_context = await build_match_context(db, game)

        # Generate explanation via OpenRouter
        explanation = await self.client.generate_explanation(
            game=game_dict,
            prediction=prediction,
            heuristic=tip.heuristic,
            model_predictions=model_predictions if model_predictions else None,
            match_context=match_context,
        )

        # Update the tip in the database
        tip.explanation = explanation
        db.add(tip)
        await db.commit()

        return explanation

    async def generate_for_game_tips(
        self, db: AsyncSession, game_id: int
    ) -> int:
        """Generate explanations for all tips for a given game.

        Args:
            db: Database session
            game_id: Game ID to generate explanations for

        Returns:
            Number of explanations generated
        """
        from ..crud.games import GameCRUD
        from ..crud.tips import TipCRUD

        # Get the game
        game = await GameCRUD.get_by_id(db, game_id)
        if not game:
            logger.warning(f"Game {game_id} not found, skipping explanation generation")
            return 0

        # Get tips for this game
        tips = await TipCRUD.get_by_game(db, game_id)

        count = 0
        for tip in tips:
            # Only generate explanation if not already present
            if not tip.explanation:
                try:
                    await self.generate_and_store_explanation(db, tip, game)
                    count += 1
                    logger.debug(
                        f"Generated explanation for tip {tip.id} "
                        f"(game {game_id}, heuristic {tip.heuristic})"
                    )
                except Exception as e:
                    logger.error(
                        f"Failed to generate explanation for tip {tip.id}: {e}",
                        exc_info=True,
                    )
                    # Continue with other tips even if one fails

        if count > 0:
            logger.info(
                f"Generated {count} explanations for game {game_id} "
                f"({game.home_team} vs {game.away_team})"
            )

        return count

    async def generate_for_round(
        self, db: AsyncSession, season: int, round_id: int
    ) -> int:
        """Generate explanations for all tips in a round.

        Args:
            db: Database session
            season: Season year
            round_id: Round number

        Returns:
            Number of explanations generated
        """
        from ..crud.games import GameCRUD

        # Get games for the round
        games = await GameCRUD.get_by_round(db, season, round_id)

        total_count = 0
        for game in games:
            count = await self.generate_for_game_tips(db, game.id)
            total_count += count

        if total_count > 0:
            logger.info(
                f"Generated {total_count} explanations for "
                f"season {season}, round {round_id}"
            )

        return total_count

    async def close(self):
        """Close the service and release resources."""
        await self.client.close()


# ---------------------------------------------------------------------------
# Rugby-league explanations (Phase 5.2, nrl-expansion-12)
#
# Pure prompt/evidence layer + a client subclass + a thin DB service.
# AFL behaviour above is untouched — this is a registry-style ADDITION,
# mirroring how league_heuristics registers the reduced model set.
# ---------------------------------------------------------------------------

#: Cache TTL for a generated explanation (12h).  Picks are refreshed by
#: the daily sync; a bounded half-day staleness keeps OpenRouter spend at
#: ~one call per (competition, event, model) per half-day window.
LEAGUE_EXPLANATION_CACHE_TTL_SECONDS = 43200

#: Explanations live under the rugby-league cache namespace — keys are
#: built with :meth:`SportContext.cache_key` (``wimt:rugby-league:...``)
#: so AFL keys are never touched; the RedisCache instance therefore
#: carries NO additional prefix.
LEAGUE_EXPLANATION_CACHE = RedisCache(
    default_ttl=LEAGUE_EXPLANATION_CACHE_TTL_SECONDS,
    prefix="",
)

#: Wording for a draw-no-pick tip (``selected_participant_id`` NULL).
_LEAGUE_DRAW_PICK_PHRASE = "a draw (level at full-time)"

_MIN_TIME = datetime.min


def rugby_league_system_prompt(competition_name: str) -> str:
    """The sport-aware system prompt: rugby league, one competition.

    Deliberately mentions ONLY the reduced factor set — the excluded
    AFL-scrape models are never named (no anchor for the LLM to lean on).
    """
    return (
        f"You are a rugby league tipping expert analysing a "
        f"{competition_name} match. Your task is to generate concise, "
        f"human-readable explanations for rugby league tips that "
        f"INTERPRET the data, not just restate it.\n\n"
        "Your explanations should:\n"
        "- Be 2-3 sentences maximum\n"
        '- Use rugby-league language: call each game a "match", refer to '
        'the fixture week as "Round N", and remember scores are in points\n'
        "- Ground the pick ONLY in the concrete signals provided (Elo "
        "ratings, recent form, home advantage, head-to-head record)\n"
        "- Weigh both sides honestly: acknowledge the main risk to the "
        "pick when one exists\n"
        "- Be honest about uncertainty: rugby league has drawn results, "
        "so a tight matchup can finish level\n"
        "- Name the specific model being explained\n\n"
        "Only reference a data point if it is present in the context. "
        "Do not invent stats. Keep it brief."
    )


def build_league_match_context(
    prior_results: "list[CompletedEvent]",
    *,
    home_participant_id: int,
    away_participant_id: int,
) -> dict[str, Any]:
    """Reduced-set evidence for one match, from its PRIOR results only.

    Built by the SAME pure functions that made the pick
    (:func:`compute_elo_ratings`, :func:`wins_in_last_n`,
    :func:`head_to_head_wins` from :mod:`.league_heuristics`), so the
    explanation can only ever cite signals the pick itself could see.
    Sections appear only when prior data exists; the excluded
    AFL-scrape signals (weather, injuries, player form, odds value)
    have no code path into this dict at all.

    ``prior_results`` must contain only events BEFORE this match (the
    no-look-ahead guarantee lives in the caller's slice).
    """
    evidence: dict[str, Any] = {"home_advantage": True}

    ratings = compute_elo_ratings(prior_results)
    if home_participant_id in ratings and away_participant_id in ratings:
        home_rating = round(ratings[home_participant_id])
        away_rating = round(ratings[away_participant_id])
        evidence["elo"] = {
            "home": home_rating,
            "away": away_rating,
            "diff": home_rating - away_rating,
        }

    form: dict[str, Any] = {"window": FORM_WINDOW}
    for label, pid in (
        ("home", home_participant_id),
        ("away", away_participant_id),
    ):
        involved = [
            event
            for event in prior_results
            if pid in (event.home_participant_id, event.away_participant_id)
        ]
        if not involved:
            continue
        recent = involved[-FORM_WINDOW:]
        # Same pure functions the pick used: a draw is NOT a win
        # (:func:`wins_in_last_n`, :func:`is_drawn`).
        wins = wins_in_last_n(pid, prior_results)
        draws = sum(1 for event in recent if is_drawn(event))
        form[label] = {
            "games": len(recent),
            "wins": wins,
            "draws": draws,
            "losses": len(recent) - wins - draws,
        }
    if "home" in form or "away" in form:
        evidence["form"] = form

    home, away = home_participant_id, away_participant_id
    pair_events = [
        event
        for event in prior_results
        if event.home_participant_id is not None
        and event.away_participant_id is not None
        and {event.home_participant_id, event.away_participant_id} == {home, away}
    ]
    if pair_events:
        wins_home, wins_away = head_to_head_wins(home, away, prior_results)
        evidence["head_to_head"] = {
            "games": len(pair_events),
            "home_wins": wins_home,
            "away_wins": wins_away,
            "draws": sum(1 for event in pair_events if is_drawn(event)),
        }

    return evidence


def _league_evidence_lines(
    home_team: str, away_team: str, evidence: dict[str, Any]
) -> list[str]:
    """Render the evidence dict into compact prompt/fallback lines."""
    lines: list[str] = []
    elo = evidence.get("elo")
    if elo:
        fav = home_team if elo["diff"] >= 0 else away_team
        lines.append(
            f"Elo ratings: {home_team} {elo['home']} vs {away_team} {elo['away']} "
            f"({fav} holds a {abs(elo['diff'])}-point edge)"
        )
    form = evidence.get("form")
    if form:
        h, a = form["home"], form["away"]
        window = form.get("window", FORM_WINDOW)
        lines.append(
            f"Recent form (last {window} matches): "
            f"{home_team} {h['wins']}-{h['draws']}-{h['losses']} (W-D-L); "
            f"{away_team} {a['wins']}-{a['draws']}-{a['losses']} (W-D-L)"
        )
    h2h = evidence.get("head_to_head")
    if h2h:
        lines.append(
            f"Head-to-head (last {h2h['games']} meetings): "
            f"{home_team} {h2h['home_wins']} - {away_team} {h2h['away_wins']} "
            f"({h2h['draws']} drawn)"
        )
    if evidence.get("home_advantage"):
        lines.append(f"Home advantage: {home_team} at home")
    return lines


def format_league_prompt_context(
    *,
    competition_name: str,
    round_id: Optional[int],
    home_team: str,
    away_team: str,
    venue: Optional[str],
    kickoff: Optional[str],
    heuristic: str,
    picked: Optional[str],
    evidence: dict[str, Any],
) -> str:
    """The sport-aware user prompt for one rugby-league tip.

    SportConfig language: the competition display name, "Round N" for
    the stage, "Match" for the contest, points as the scoring unit, and
    draws acknowledged as a live outcome.
    """
    round_part = f" — Round {round_id}" if round_id is not None else ""
    lines = [
        f"Competition: {competition_name}{round_part}",
        f"Match: {home_team} vs {away_team} at {venue or 'a venue to be confirmed'}",
    ]
    if kickoff:
        lines.append(f"Kick-off: {kickoff}")
    lines.append(f"Model: {heuristic}")
    if picked is None:
        lines.append(f"Pick: {_LEAGUE_DRAW_PICK_PHRASE}")
    else:
        lines.append(
            f"Pick: {picked} to win (scores in points; a drawn result is possible)"
        )
    signal_lines = _league_evidence_lines(home_team, away_team, evidence)
    if signal_lines:
        lines.append("")
        lines.append("Signals:")
        lines.extend(signal_lines)
    lines.append("")
    lines.append(
        "Interpret this tip: explain WHY the pick makes sense using ONLY the "
        "signals above, and note the main risk if there is one."
    )
    return "\n".join(lines)


def league_fallback_explanation(
    *,
    competition_name: str,
    home_team: str,
    away_team: str,
    heuristic: str,
    picked: Optional[str],
    evidence: dict[str, Any],
) -> str:
    """Deterministic degradation when no LLM text is available.

    One sentence naming the pick + the top evidence clause, then a
    standard draw caveat.  References only the reduced set — same
    guarantee as the LLM prompt.
    """
    if picked is None:
        return (
            f"The models expect a draw: {home_team} and {away_team} are evenly "
            f"matched on the available signals, and a drawn result is a live "
            f"outcome in {competition_name} rugby league."
        )

    if heuristic == HEURISTIC_ELO:
        lead = f"{picked} is the Elo rating pick for this {competition_name} match"
        elo = evidence.get("elo")
        if elo:
            fav = home_team if elo["diff"] >= 0 else away_team
            lead += (
                f" — the ratings give {fav} a {abs(elo['diff'])}-point edge"
            )
    elif heuristic == HEURISTIC_FORM:
        lead = f"{picked} gets the nod on recent form"
        form = evidence.get("form")
        if form:
            side = form["home"] if picked == home_team else form["away"]
            lead += (
                f" ({side['wins']}-{side['draws']}-{side['losses']} W-D-L "
                f"across their last {side['games']} matches)"
            )
    elif heuristic == HEURISTIC_HOME_ADVANTAGE:
        lead = (
            f"Home advantage points to {home_team} — playing at home remains "
            f"a genuine edge in rugby league"
        )
    elif heuristic == HEURISTIC_MATCHUP:
        lead = f"The head-to-head record leans to {picked}"
        h2h = evidence.get("head_to_head")
        if h2h:
            lead += (
                f" ({h2h['home_wins']}-{h2h['away_wins']} with "
                f"{h2h['draws']} drawn from {h2h['games']} meetings)"
            )
    else:  # defensive: an unregistered model string still gets clean text
        lead = f"{picked} is the pick for this match"

    return (
        lead
        + ". Scores are in points, and if the sides finish level the match "
        "is a draw."
    )


class RugbyLeagueOpenRouterClient(OpenRouterClient):
    """OpenRouter client that speaks rugby league for ONE competition.

    Overrides ONLY the prompt/fallback hooks; ``generate_explanation``'s
    request path, model/token budget, provider-error handling and
    content extraction are inherited unchanged from the shared pipeline
    (the AFL client class above is byte-identical in behaviour).
    """

    def __init__(self, competition_name: str):
        super().__init__()
        self.competition_name = competition_name

    def _get_system_prompt(self) -> str:
        return rugby_league_system_prompt(self.competition_name)

    def _build_prompt_context(
        self,
        game: dict,
        prediction: dict,
        heuristic: str,
        model_predictions: Optional[dict],
        match_context: Optional[dict],
    ) -> str:
        """Sport-aware user context (evidence: reduced set only)."""
        return format_league_prompt_context(
            competition_name=self.competition_name,
            round_id=game.get("round_id"),
            home_team=game.get("home_team", "the home side"),
            away_team=game.get("away_team", "the away side"),
            venue=game.get("venue"),
            kickoff=game.get("date"),
            heuristic=heuristic,
            picked=prediction.get("winner") or None,
            evidence=match_context or {},
        )

    def _generate_fallback_explanation(
        self,
        game: dict,
        prediction: dict,
        heuristic: str,
        match_context: Optional[dict] = None,
    ) -> str:
        """Sport-aware deterministic fallback (no AFL heuristic branding)."""
        return league_fallback_explanation(
            competition_name=self.competition_name,
            home_team=game.get("home_team", "the home side"),
            away_team=game.get("away_team", "the away side"),
            heuristic=heuristic,
            picked=prediction.get("winner") or None,
            evidence=match_context or {},
        )


class LeagueTipExplanationService:
    """Generate cached AI explanations for rugby-league ``league_tips``.

    One public operation per shape:

    * :meth:`generate_explanation_for_tip` — one tip; expected failures
      raise ``BackendServiceError`` with the repo-standard shape (404
      unknown competition/event/tip, 422 unknown model, 400 excluded
      model, 502 provider catastrophe).
    * :meth:`explanations_for_event` — every tip on one event; a
      per-tip failure degrades to ``explanation=None`` and NEVER
      blocks the tips themselves.

    Explanations are cached under the rugby-league namespace, so a tip
    costs at most one OpenRouter call per TTL window (cost discipline:
    the shared pipeline's budget is inherited — 300 output tokens).
    """

    def __init__(
        self,
        client: Optional[OpenRouterClient] = None,
        cache: Optional[RedisCache] = None,
    ):
        self._injected_client = client
        self._cache = cache

    # -- public operations -------------------------------------------------

    async def generate_explanation_for_tip(
        self,
        db: AsyncSession,
        *,
        competition_id: int,
        event_id: int,
        heuristic: str,
    ) -> str:
        """Generate (or fetch cached) the AI explanation for one tip."""
        # Local import: packages.shared stays free of app.* at module
        # load time (the repo's documented pattern).
        from app.core.exceptions import http_error

        competition = await self._fetch_competition(db, competition_id)
        if competition is None:
            raise http_error(
                404, "not_found", f"Competition {competition_id} not found"
            )
        sport_id, competition_name = competition

        # Registry gate: excluded AFL-scrape models → 400
        # model_unavailable_for_sport (league_heuristics' shape); names
        # unknown to the result-derived registry → 422 invalid_heuristic.
        try:
            require_league_model(heuristic, sport_id=sport_id)
        except ValueError as exc:
            raise http_error(422, "invalid_heuristic", str(exc)) from None

        event = await self._fetch_event(db, event_id, competition_id)
        if event is None:
            raise http_error(
                404,
                "not_found",
                f"Event {event_id} not found for competition {competition_id}",
            )
        tip = await self._fetch_tip(db, event_id, heuristic)
        if tip is None:
            raise http_error(
                404,
                "not_found",
                f"No {heuristic} tip found for event {event_id}",
            )

        cache = self._cache if self._cache is not None else LEAGUE_EXPLANATION_CACHE
        cache_key = self._cache_key(competition_id, event_id, heuristic)
        cached = await cache.get(cache_key)
        if cached is not None:
            return str(cached)

        sides = await self._fetch_sides(db, event_id)
        prior_results = await self._fetch_prior_results(db, competition_id, event)
        evidence = self._build_evidence(sides, prior_results)
        game_dict = {
            "home_team": sides.get("home", {}).get("name", "the home side"),
            "away_team": sides.get("away", {}).get("name", "the away side"),
            "venue": event.venue,
            "date": event.starts_at.isoformat() if event.starts_at else None,
            "round_id": event.round_id,
        }
        prediction = {
            "winner": self._picked_name(tip, sides),
            "margin": None,
            "confidence": None,
        }

        client = self._injected_client or RugbyLeagueOpenRouterClient(
            competition_name
        )
        try:
            explanation = await client.generate_explanation(
                game=game_dict,
                prediction=prediction,
                heuristic=heuristic,
                match_context=evidence,
            )
        except Exception as exc:  # noqa: BLE001 - mapped to repo-standard shape
            logger.error(
                "League explanation generation failed for "
                f"(competition {competition_id}, event {event_id}, "
                f"{heuristic}): {exc}",
                exc_info=True,
            )
            raise http_error(
                502,
                "explanation_generation_failed",
                "AI explanation generation failed for this tip",
            ) from None

        await cache.set(cache_key, explanation)
        return explanation

    async def explanations_for_event(
        self, db: AsyncSession, *, event_id: int
    ) -> list[dict[str, Optional[str]]]:
        """Every sport-valid tip on one event, explanations attached.

        Graceful by contract: a tip whose explanation fails comes back
        as ``explanation=None`` (tip-without-explanation) and a missing
        event yields ``[]`` — this path NEVER raises for generation
        problems, so tips are never blocked by the AI layer.
        """
        row = (
            await db.execute(
                select(
                    Event,
                    Season.competition_id,
                    Competition.sport_id,
                    Competition.name,
                )
                .join(Season, Event.season_id == Season.id)
                .join(Competition, Season.competition_id == Competition.id)
                .where(Event.id == event_id)
            )
        ).first()
        if row is None:
            return []
        event, competition_id, sport_id, _competition_name = row

        tips = list(
            (
                await db.execute(
                    select(LeagueTip).where(LeagueTip.event_id == event_id)
                )
            ).scalars()
        )
        if not tips:
            return []

        models_order = league_model_set_for_sport(
            str(sport_id) if sport_id is not None else None
        )
        allowed = set(models_order)
        sides = await self._fetch_sides(db, event_id)

        results: list[dict[str, Optional[str]]] = []
        for tip in tips:
            heuristic = str(tip.heuristic)
            if heuristic not in allowed:
                # Defence in depth: a tip row for an excluded model is a
                # data bug — never explain it, never fail the batch.
                logger.warning(
                    "League explanations: skipping model %r not in the "
                    "sport's registry for event %s",
                    heuristic,
                    event_id,
                )
                continue
            picked = self._picked_name(tip, sides)
            try:
                explanation: Optional[str] = (
                    await self.generate_explanation_for_tip(
                        db,
                        competition_id=int(competition_id),
                        event_id=event_id,
                        heuristic=heuristic,
                    )
                )
            except Exception as exc:  # noqa: BLE001 - degrade, never block
                logger.warning(
                    "League explanations: tip (%s, %s) degraded to "
                    "no-explanation: %s",
                    event_id,
                    heuristic,
                    exc,
                )
                explanation = None
            results.append(
                {
                    "heuristic": heuristic,
                    "picked": picked,
                    "explanation": explanation,
                }
            )
        results.sort(key=lambda item: models_order.index(item["heuristic"]))
        return results

    # -- helpers ------------------------------------------------------------

    @staticmethod
    def _cache_key(competition_id: int, event_id: int, heuristic: str) -> str:
        """Sport-namespaced cache key (the ``wimt:rugby-league:`` prefix
        comes from the SportContext, not from the cache instance)."""
        return RUGBY_LEAGUE.cache_key(
            "tip_explanation",
            str(competition_id),
            str(event_id),
            heuristic,
        )

    def _build_evidence(
        self,
        sides: dict[str, dict[str, Any]],
        prior_results: list[CompletedEvent],
    ) -> dict[str, Any]:
        """Reduced-set evidence; a side-less event still gets a prompt."""
        home = sides.get("home")
        away = sides.get("away")
        if home is None or away is None:
            return {"home_advantage": True}
        return build_league_match_context(
            prior_results,
            home_participant_id=int(home["participant_id"]),
            away_participant_id=int(away["participant_id"]),
        )

    @staticmethod
    async def _fetch_competition(
        db: AsyncSession, competition_id: int
    ) -> Optional[tuple[Optional[str], str]]:
        """(sport_id, name) for the competition, or ``None``."""
        row = (
            await db.execute(
                select(Competition.sport_id, Competition.name).where(
                    Competition.id == competition_id
                )
            )
        ).first()
        if row is None:
            return None
        sport_id = None if row[0] is None else str(row[0])
        return sport_id, str(row[1])

    @staticmethod
    async def _fetch_event(
        db: AsyncSession, event_id: int, competition_id: int
    ) -> Optional[Event]:
        """The event row, but ONLY when it belongs to ``competition_id``
        (a slug/id from another competition must not be explainable
        under this one)."""
        row = (
            await db.execute(
                select(Event, Season.competition_id)
                .join(Season, Event.season_id == Season.id)
                .where(Event.id == event_id)
            )
        ).first()
        if row is None:
            return None
        event, event_competition_id = row
        if int(event_competition_id) != int(competition_id):
            return None
        return event

    @staticmethod
    async def _fetch_tip(
        db: AsyncSession, event_id: int, heuristic: str
    ) -> Optional[LeagueTip]:
        return (
            await db.execute(
                select(LeagueTip).where(
                    LeagueTip.event_id == event_id,
                    LeagueTip.heuristic == heuristic,
                )
            )
        ).scalar_one_or_none()

    @staticmethod
    async def _fetch_sides(
        db: AsyncSession, event_id: int
    ) -> dict[str, dict[str, Any]]:
        """home/away side rows with participant identity + display name."""
        rows = (
            await db.execute(
                select(EventParticipant, Participant.name)
                .join(
                    Participant,
                    EventParticipant.participant_id == Participant.id,
                )
                .where(
                    EventParticipant.event_id == event_id,
                    EventParticipant.side.in_(("home", "away")),
                )
            )
        ).all()
        sides: dict[str, dict[str, Any]] = {}
        for side_row, name in rows:
            sides[str(side_row.side)] = {
                "side_id": int(side_row.id),
                "participant_id": int(side_row.participant_id),
                "name": str(name),
            }
        return sides

    @staticmethod
    async def _fetch_prior_results(
        db: AsyncSession, competition_id: int, event: Event
    ) -> list[CompletedEvent]:
        """Completed events of the competition STRICTLY BEFORE ``event``
        (chronological, the no-look-ahead slice the pick functions need)."""
        season_ids = [
            int(row[0])
            for row in (
                await db.execute(
                    select(Season.id).where(
                        Season.competition_id == competition_id
                    )
                )
            ).all()
        ]
        if not season_ids:
            return []
        history = await LeagueHeuristicsService._fetch_completed_events(
            db, season_ids
        )
        cutoff = (event.starts_at or _MIN_TIME, int(event.id))
        return [
            completed
            for completed in sort_chronologically(history)
            if (completed.starts_at or _MIN_TIME, completed.event_id) < cutoff
        ]

    @staticmethod
    def _picked_name(
        tip: LeagueTip, sides: dict[str, dict[str, Any]]
    ) -> Optional[str]:
        """The picked side's display name; ``None`` = predicted draw."""
        if tip.selected_participant_id is None:
            return None
        selected = int(tip.selected_participant_id)
        for side in sides.values():
            if side["side_id"] == selected:
                return side["name"]
        return None
