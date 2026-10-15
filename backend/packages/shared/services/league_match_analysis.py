"""League match analysis — balanced talking points per event (PARITY-1).

The league counterpart of :mod:`.match_analysis`: one "Talking Points"
text per event, generated through the SAME OpenRouter pipeline (the
sport-aware rugby-league client subclass overrides only the wording
hooks), stored on ``league_match_analyses`` — the league twin of
``match_analyses``.  Evidence is the reduced league set
(:func:`build_league_match_context`) plus the numeric model
breakdown, so the talking points can never cite a signal the models
did not see.
"""

from __future__ import annotations

from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..logger import get_logger
from ..models import LeagueMatchAnalysis
from ..openrouter import OpenRouterClient
from .explanation import (
    LeagueTipExplanationService,
    RugbyLeagueOpenRouterClient,
    build_league_match_context,
)

logger = get_logger(__name__)


class LeagueMatchAnalysisOpenRouterClient(RugbyLeagueOpenRouterClient):
    """OpenRouter client that speaks rugby league for talking points.

    Overrides ONLY the match-analysis wording hooks; the request path,
    token budget and content extraction are inherited unchanged.
    """

    def _get_match_analysis_system_prompt(self) -> str:
        return (
            "You are a knowledgeable rugby league analyst writing balanced "
            '"Talking Points" for an upcoming match. Your job is to give a '
            "fan a genuine, well-rounded read on the game using ALL the "
            "data provided.\n\n"
            "Your talking points should:\n"
            "- Be grounded in the supplied data (Elo ratings, recent form, "
            "head-to-head record, home advantage, weather, model "
            "consensus) — cite the specific signal each point leans on\n"
            "- Present BOTH teams' cases — strengths for each side and the "
            "main risk or counter-argument\n"
            "- Be honest about uncertainty; rugby league has drawn results, "
            "so a tight matchup can finish level — say so when the data is "
            "split rather than manufacturing false confidence\n"
            "- Be written in a natural, conversational tone a fan would "
            "actually say\n"
            "- Stay factual — never invent stats, scores or injuries that "
            "aren't in the context\n\n"
            "Format your response as 4-5 short talking points separated by "
            "newlines. Each point should be 1-2 sentences. No bullet points "
            "or numbers — just the raw talking points."
        )

    def _generate_fallback_match_analysis(
        self, game: dict, match_context: Optional[dict] = None
    ) -> str:
        """Sport-aware deterministic fallback for the talking points."""
        home = game.get("home_team", "the home side")
        away = game.get("away_team", "the visitors")
        context = match_context or {}
        points = []

        elo = context.get("elo")
        if elo:
            fav = home if elo["diff"] >= 0 else away
            points.append(
                f"On the ratings, {fav} holds a {abs(elo['diff'])}-point "
                "Elo edge, which usually translates to a favoured result."
            )
        else:
            points.append(
                f"{home} have the home-ground advantage, but the ratings "
                f"are close enough that {away} are a genuine chance."
            )

        form = context.get("form")
        if form:
            h, a = form.get("home", {}), form.get("away", {})
            if h.get("games") and a.get("games"):
                points.append(
                    f"Recent form reads {home} {h['wins']}-{h['draws']}-"
                    f"{h['losses']} against {away} {a['wins']}-{a['draws']}-"
                    f"{a['losses']} (W-D-L), so momentum leans to whichever "
                    "side is hotter right now."
                )

        h2h = context.get("head_to_head")
        if h2h and h2h.get("games"):
            points.append(
                f"The head-to-head over the last {h2h['games']} meetings "
                f"favours {home if h2h['home_wins'] >= h2h['away_wins'] else away} "
                f"({max(h2h['home_wins'], h2h['away_wins'])}-"
                f"{min(h2h['home_wins'], h2h['away_wins'])})."
            )

        if context.get("home_advantage"):
            points.append(
                f"Playing at home remains a genuine edge in rugby league, "
                f"and {home} will take it."
            )

        if len(points) < 3:
            points.append(
                "All up, the models see this as competitive — and if the "
                "sides finish level, the match is a draw."
            )

        return "\n".join(points[:5])


class LeagueMatchAnalysisService:
    """Generate/store talking points for one league event."""

    def __init__(self, client: Optional[OpenRouterClient] = None):
        # F-3 (code review): the default client is constructed ONCE and
        # closed by :meth:`close` (the sweep's finally) — a fresh httpx
        # pool per analysis leaked until GC.
        self._client: Optional[OpenRouterClient] = client

    def _client_for(self, competition_name: str) -> OpenRouterClient:
        """The injected client, or a sport-aware default."""
        if self._client is not None:
            return self._client
        self._client = LeagueMatchAnalysisOpenRouterClient(competition_name)
        return self._client

    async def generate_and_store_analysis(
        self, db: AsyncSession, *, event_id: int
    ) -> Optional[str]:
        """Generate talking points for the event; skip-if-exists.

        Returns the analysis text, or ``None`` when the event does not
        exist or generation failed (best-effort by contract).
        """
        existing = await self.get_analysis(db, event_id=event_id)
        if existing is not None:
            return existing

        sides = await LeagueTipExplanationService._fetch_sides(db, event_id)
        if not sides.get("home") or not sides.get("away"):
            return None

        from ..models import Competition, Event, Season

        event_row = (
            await db.execute(
                select(
                    Event,
                    Competition.name,
                    Competition.id,
                )
                .join(Season, Event.season_id == Season.id)
                .join(Competition, Season.competition_id == Competition.id)
                .where(Event.id == event_id)
            )
        ).first()
        if event_row is None:
            return None
        event, competition_name, competition_id = event_row

        prior_results = await LeagueTipExplanationService._fetch_prior_results(
            db, int(competition_id), event
        )
        evidence = build_league_match_context(
            prior_results,
            home_participant_id=int(sides["home"]["participant_id"]),
            away_participant_id=int(sides["away"]["participant_id"]),
        )

        # The numeric model breakdown rides into the prompt as the
        # AFL-shaped ``model_predictions`` context.
        model_predictions: dict[str, tuple[str, float, int]] = {}
        try:
            from ..models import LeagueModelPrediction

            rows = (
                await db.execute(
                    select(LeagueModelPrediction).where(
                        LeagueModelPrediction.event_id == event_id
                    )
                )
            ).scalars()
            for row in rows:
                if row.winner is not None:
                    model_predictions[str(row.model_name)] = (
                        str(row.winner),
                        float(row.confidence or 0.5),
                        int(row.margin or 1),
                    )
        except Exception:  # noqa: BLE001 — context is optional
            logger.warning(
                "league match analysis: model-prediction context "
                "unavailable for event %s",
                event_id,
                exc_info=True,
            )

        game_dict = {
            "home_team": sides["home"]["name"],
            "away_team": sides["away"]["name"],
            "venue": event.venue,
            "date": event.starts_at.isoformat() if event.starts_at else "TBD",
        }

        client = self._client_for(competition_name)
        try:
            analysis_text = await client.generate_match_analysis(
                game_dict,
                model_predictions or None,
                match_context=evidence,
            )
        except Exception:  # noqa: BLE001 — best-effort by contract
            logger.warning(
                "league match analysis: generation failed for event %s",
                event_id,
                exc_info=True,
            )
            return None

        if not analysis_text:
            return None
        await self.store_analysis(db, event_id=event_id, analysis_text=analysis_text)
        return analysis_text

    @staticmethod
    async def get_analysis(
        db: AsyncSession, *, event_id: int
    ) -> Optional[str]:
        """The stored talking-points text, or ``None``."""
        row = (
            await db.execute(
                select(LeagueMatchAnalysis.analysis_text).where(
                    LeagueMatchAnalysis.event_id == event_id
                )
            )
        ).scalar_one_or_none()
        return None if row is None else str(row)

    @staticmethod
    async def store_analysis(
        db: AsyncSession, *, event_id: int, analysis_text: str
    ) -> None:
        """Insert or overwrite the event's talking points.

        F-1 (code review): a failed commit leaves the session ABORTED —
        roll back so the caller's remaining work survives (the repo's
        write-back discipline, explanation.py:679-688).
        """
        try:
            row = (
                await db.execute(
                    select(LeagueMatchAnalysis).where(
                        LeagueMatchAnalysis.event_id == event_id
                    )
                )
            ).scalar_one_or_none()
            if row is None:
                db.add(
                    LeagueMatchAnalysis(
                        event_id=event_id, analysis_text=analysis_text
                    )
                )
            else:
                row.analysis_text = analysis_text
            await db.commit()
        except Exception:  # noqa: BLE001 — degrade, never break the sweep
            logger.warning(
                "league match analysis: persisting analysis for event %s "
                "failed",
                event_id,
                exc_info=True,
            )
            try:
                await db.rollback()
            except Exception:  # noqa: BLE001
                pass

    async def close(self) -> None:
        """Release the client's resources (default or injected)."""
        if self._client is not None:
            await self._client.close()
