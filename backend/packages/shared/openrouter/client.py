import logging
from typing import Any, Dict, Optional

from openai import AsyncOpenAI

from ..config import settings

logger = logging.getLogger(__name__)


def _format_match_context(ctx: Optional[Dict[str, Any]]) -> str:
    """Render the rich match-context dict into a compact text block.

    Only sections that actually have data are included, so the prompt
    stays short for cold-start games and grows as data becomes
    available.  Returns an empty string when ``ctx`` is ``None``.
    """
    if not ctx:
        return ""

    lines = []
    home = ctx.get("home_team", "the home side")
    away = ctx.get("away_team", "the away side")

    elo = ctx.get("elo")
    if elo:
        fav = home if elo["diff"] >= 0 else away
        lines.append(
            f"ELO ratings: {home} {elo['home']} vs {away} {elo['away']} "
            f"({fav} holds a {abs(elo['diff'])}-point edge)"
        )

    form = ctx.get("form")
    if form:
        h, a = form.get("home", {}), form.get("away", {})
        if h.get("games") or a.get("games"):
            lines.append(
                f"Recent form (last {max(h.get('games', 0), a.get('games', 0))} games): "
                f"{home} {h.get('wins', 0)}-{h.get('losses', 0)} "
                f"(streak {h.get('streak', '-')}, avg margin {h.get('avg_margin', 0):+.0f}); "
                f"{away} {a.get('wins', 0)}-{a.get('losses', 0)} "
                f"(streak {a.get('streak', '-')}, avg margin {a.get('avg_margin', 0):+.0f})"
            )

    h2h = ctx.get("head_to_head")
    if h2h and h2h.get("games"):
        lines.append(
            f"Head-to-head (last {h2h['games']} meetings): "
            f"{home} {h2h['home_wins']} - {away} {h2h['away_wins']}"
        )

    weather = ctx.get("weather")
    if weather:
        bits = []
        if weather.get("temperature") is not None:
            bits.append(f"{weather['temperature']:.0f}C")
        if weather.get("precipitation") is not None:
            bits.append(f"{weather['precipitation']:.1f}mm rain")
        if weather.get("wind_speed") is not None:
            bits.append(f"{weather['wind_speed']:.0f}km/h wind")
        if bits:
            cond = weather.get("conditions")
            lines.append(
                "Weather forecast: " + ", ".join(bits)
                + (f" ({cond})" if cond else "")
            )

    injuries = ctx.get("injuries")
    if injuries and (injuries.get("home") or injuries.get("away")):
        parts = []
        if injuries.get("home"):
            parts.append(f"{home} out: {', '.join(injuries['home'][:5])}")
        if injuries.get("away"):
            parts.append(f"{away} out: {', '.join(injuries['away'][:5])}")
        lines.append("Key outs: " + "; ".join(parts))

    return "\n".join(lines)


class OpenRouterClient:
    """Client for interacting with OpenRouter API."""

    def __init__(self):
        # SEC-ME-003: avoid constructing the OpenAI SDK with an empty
        # key.  The SDK's failure mode is non-obvious (it raises from
        # inside the request loop rather than at construction time)
        # which makes debugging hard.  When the key is missing we
        # leave ``self.client = None`` and rely on the existing
        # fallback paths in ``generate_explanation`` /
        # ``generate_match_analysis``.
        api_key = (settings.openrouter_api_key or "").strip()
        if api_key:
            self.client = AsyncOpenAI(
                api_key=api_key,
                base_url=settings.openrouter_base_url,
                timeout=30.0,
            )
        else:
            logger.warning(
                "OpenRouter API key not configured; AI explanations will "
                "fall back to deterministic templates."
            )
            self.client = None
        self.model = settings.openrouter_model

    async def generate_explanation(
        self,
        game: dict,
        prediction: dict,
        heuristic: str,
        model_predictions: Optional[dict] = None,
        match_context: Optional[dict] = None,
    ) -> str:
        """Generate a human-readable explanation for a tip.

        Args:
            game: Game dictionary with keys: home_team, away_team, venue, date
            prediction: Prediction dictionary with keys: winner, confidence, margin
            heuristic: Heuristic type (best_bet, yolo, high_risk_high_reward)
            model_predictions: Optional dict of model_name -> (winner, confidence, margin)
            match_context: Optional rich context dict from build_match_context
                (elo, form, weather, injuries, head_to_head).

        Returns:
            Human-readable explanation string
        """

        # If no API key is configured, return fallback immediately
        if not settings.openrouter_api_key:
            logger.warning("OpenRouter API key not configured, using fallback explanation")
            return self._generate_fallback_explanation(
                game, prediction, heuristic, match_context
            )

        # Build context for the AI
        context = self._build_prompt_context(
            game, prediction, heuristic, model_predictions, match_context
        )

        try:
            response = await self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {
                        "role": "system",
                        "content": self._get_system_prompt(),
                    },
                    {
                        "role": "user",
                        "content": context,
                    },
                ],
                max_tokens=300,
                temperature=0.7,
            )

            explanation = response.choices[0].message.content.strip()
            return explanation

        except Exception as e:
            # Log the error but fallback to simple explanation
            logger.error(f"OpenRouter AI explanation failed: {e}", exc_info=True)
            return self._generate_fallback_explanation(
                game, prediction, heuristic, match_context
            )

    def _get_system_prompt(self) -> str:
        """Get the system prompt for the AI."""
        return """You are an AFL footy tipping expert. Your task is to generate concise, \
human-readable explanations for footy tips that INTERPRET the data, not just restate it.

Your explanations should:
- Be 2-3 sentences maximum
- Use clear, simple language
- Ground the pick in the concrete signals provided (ELO gap, recent form, head-to-head, weather, injuries) — e.g. "their 120-point ELO edge and 3-game winning streak point to..."
- Weigh both sides honestly: acknowledge the main risk to the pick (injuries, poor form, an opposing ELO edge) when it exists
- Mention the specific heuristic being used
- Be honest about uncertainty when confidence is low

Heuristics:
- best_bet: Conservative, consensus-based picks with high confidence
- yolo: Aggressive picks based on the highest confidence model prediction
- high_risk_high_reward: Picks targeting upset opportunities when models disagree

Only reference a data point if it is present in the context. Do not invent stats. Keep it brief."""

    def _build_prompt_context(
        self,
        game: dict,
        prediction: dict,
        heuristic: str,
        model_predictions: Optional[dict],
        match_context: Optional[dict],
    ) -> str:
        """Build the prompt context for the AI."""
        context = f"""Game: {game["home_team"]} vs {game["away_team"]} at {game["venue"]}
Heuristic: {heuristic}
Prediction: {prediction["winner"]} to win by {prediction["margin"]} points
Confidence: {prediction["confidence"]:.0%}

"""
        rich = _format_match_context(match_context)
        if rich:
            context += rich + "\n\n"

        if model_predictions:
            context += "Model predictions:\n"
            for model_name, (winner, confidence, margin) in model_predictions.items():
                context += f"- {model_name}: {winner} ({confidence:.0%}, {margin} pts)\n"
            context += "\n"

        context += (
            "Interpret this tip: explain WHY the pick makes sense using the "
            "signals above, and note the main risk if there is one."
        )

        return context

    def _generate_fallback_explanation(
        self,
        game: dict,
        prediction: dict,
        heuristic: str,
        match_context: Optional[dict] = None,
    ) -> str:
        """Generate a data-aware fallback explanation if AI fails."""
        winner = prediction["winner"]
        margin = prediction["margin"]
        confidence = prediction["confidence"]

        drivers = self._fallback_drivers(game, match_context)
        driver_text = ""
        if drivers:
            driver_text = " " + " ".join(drivers[:-1])
            tail = drivers[-1]
            driver_text += (" and " if drivers[:-1] else "") + tail + "."

        if heuristic == "best_bet":
            base = (
                f"{winner} is the consensus pick across the models, projected to "
                f"win by {margin} points at {confidence:.0%} confidence."
            )
        elif heuristic == "yolo":
            base = (
                f"Going all-in on {winner} — the highest-confidence model tips a "
                f"{margin}-point win at {confidence:.0%} confidence."
            )
        else:  # high_risk_high_reward
            base = (
                f"Targeting an upset with {winner}. The models are split, creating a "
                f"high-risk, high-reward play around a projected {margin}-point margin."
            )

        return (base + driver_text).strip()

    def _fallback_drivers(
        self, game: dict, match_context: Optional[dict]
    ) -> list[str]:
        """Pull concrete one-clause drivers out of the match context."""
        drivers: list[str] = []
        if not match_context:
            return drivers
        home = game.get("home_team", "")
        away = game.get("away_team", "")

        elo = match_context.get("elo")
        if elo:
            fav = home if elo["diff"] >= 0 else away
            drivers.append(
                f"a {abs(elo['diff'])}-point ELO edge to {fav}"
            )

        form = match_context.get("form")
        if form:
            h, a = form.get("home", {}), form.get("away", {})
            if h.get("games") and a.get("games") and h["wins"] != a["wins"]:
                if h["wins"] > a["wins"]:
                    drivers.append(f"{home}'s {h['wins']}-{h['losses']} recent form")
                else:
                    drivers.append(f"{away}'s {a['wins']}-{a['losses']} recent form")

        injuries = match_context.get("injuries")
        if injuries:
            if injuries.get("home") and len(injuries["home"]) >= 2:
                drivers.append(f"{home} missing {len(injuries['home'])} key players")
            if injuries.get("away") and len(injuries["away"]) >= 2:
                drivers.append(f"{away} missing {len(injuries['away'])} key players")

        return drivers

    async def generate_match_analysis(
        self,
        game: dict,
        model_predictions: Optional[dict] = None,
        match_context: Optional[dict] = None,
    ) -> str:
        """Generate balanced talking points for a match.

        Args:
            game: Game dictionary with keys: home_team, away_team, venue, date
            model_predictions: Optional dict of model_name -> (winner, confidence, margin)
            match_context: Optional rich context dict from build_match_context
                (elo, form, weather, injuries, head_to_head).

        Returns:
            Talking points string
        """

        # If no API key is configured, return fallback immediately
        if not settings.openrouter_api_key:
            logger.warning("OpenRouter API key not configured, using fallback match analysis")
            return self._generate_fallback_match_analysis(game, match_context)

        # Build context for the AI
        context = self._build_match_analysis_context(
            game, model_predictions, match_context
        )

        try:
            response = await self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {
                        "role": "system",
                        "content": self._get_match_analysis_system_prompt(),
                    },
                    {
                        "role": "user",
                        "content": context,
                    },
                ],
                max_tokens=450,
                temperature=0.75,
            )

            analysis = response.choices[0].message.content.strip()
            return analysis

        except Exception as e:
            logger.error(f"OpenRouter AI match analysis failed: {e}", exc_info=True)
            return self._generate_fallback_match_analysis(game, match_context)

    def _get_match_analysis_system_prompt(self) -> str:
        """Get the system prompt for talking-points generation."""
        return """You are a knowledgeable AFL analyst writing balanced "Talking Points" for an \
upcoming match. Your job is to give a fan a genuine, well-rounded read on the game using ALL \
the data provided.

Your talking points should:
- Be grounded in the supplied data (ELO gap, recent form, head-to-head, weather, injuries, model consensus) — cite the specific signal each point leans on
- Present BOTH teams' cases — strengths for each side and the main risk or counter-argument
- Be honest about uncertainty; if the data is split or thin, say so rather than manufacturing false confidence
- Be written in a natural, conversational tone a fan would actually say
- Stay factual — never invent stats, scores or injuries that aren't in the context

Format your response as 4-5 short talking points separated by newlines. \
Each point should be 1-2 sentences. No bullet points or numbers — just the raw talking points."""

    def _build_match_analysis_context(
        self,
        game: dict,
        model_predictions: Optional[dict],
        match_context: Optional[dict],
    ) -> str:
        """Build the prompt context for talking-points generation."""
        context = f"""Game: {game["home_team"]} vs {game["away_team"]} at {game["venue"]}
Date: {game.get("date", "TBD")}

"""
        rich = _format_match_context(match_context)
        if rich:
            context += rich + "\n\n"

        if model_predictions:
            context += "Model predictions:\n"
            for model_name, (winner, confidence, margin) in model_predictions.items():
                context += f"- {model_name}: {winner} ({confidence:.0%}, {margin} pts)\n"

            # Calculate consensus
            winners = [w for w, c, m in model_predictions.values()]
            margins = [m for w, c, m in model_predictions.values()]
            confidences = [c for w, c, m in model_predictions.values()]

            if winners:
                from collections import Counter

                most_picked = Counter(winners).most_common(1)[0]
                avg_margin = sum(margins) / len(margins)
                avg_confidence = sum(confidences) / len(confidences)
                context += (
                    f"\nConsensus: {most_picked[0]} picked by "
                    f"{most_picked[1]}/{len(model_predictions)} models"
                )
                context += f"\nAverage predicted margin: {avg_margin:.0f} pts"
                context += f"\nAverage confidence: {avg_confidence:.0%}\n"

        context += (
            "\nGenerate balanced talking points for this match that weigh up both "
            "sides using the data above:"
        )

        return context

    def _generate_fallback_match_analysis(
        self, game: dict, match_context: Optional[dict] = None
    ) -> str:
        """Generate a data-aware fallback if AI fails."""
        home = game.get("home_team", "the home side")
        away = game.get("away_team", "the visitors")
        points = []

        elo = (match_context or {}).get("elo")
        if elo:
            fav = home if elo["diff"] >= 0 else away
            points.append(
                f"On the ratings, {fav} holds a {abs(elo['diff'])}-point ELO edge, "
                f"which usually translates to a favoured result."
            )
        else:
            points.append(
                f"{home} have the home-ground advantage, but the ratings are close "
                f"enough that {away} are a genuine chance."
            )

        form = (match_context or {}).get("form")
        if form:
            h, a = form.get("home", {}), form.get("away", {})
            if h.get("games") and a.get("games"):
                points.append(
                    f"Recent form reads {home} {h['wins']}-{h['losses']} against "
                    f"{away} {a['wins']}-{a['losses']}, so momentum leans to whichever "
                    f"side is hotter right now."
                )

        h2h = (match_context or {}).get("head_to_head")
        if h2h and h2h.get("games"):
            points.append(
                f"The head-to-head over the last {h2h['games']} meetings favours "
                f"{home if h2h['home_wins'] >= h2h['away_wins'] else away} "
                f"({max(h2h['home_wins'], h2h['away_wins'])}-{min(h2h['home_wins'], h2h['away_wins'])})."
            )

        injuries = (match_context or {}).get("injuries")
        if injuries and (injuries.get("home") or injuries.get("away")):
            parts = []
            if injuries.get("home"):
                parts.append(f"{home} are missing {len(injuries['home'])}")
            if injuries.get("away"):
                parts.append(f"{away} are missing {len(injuries['away'])}")
            points.append(
                "Injuries are a factor — " + " while ".join(parts) + " key players."
            )

        weather = (match_context or {}).get("weather")
        if weather and weather.get("wind_speed") is not None and weather["wind_speed"] >= 25:
            points.append(
                f"It's forecast to blow {weather['wind_speed']:.0f}km/h, which tends "
                f"to drag scores down and favour the defence."
            )

        if len(points) < 3:
            points.append(
                "All up, the models see this as competitive — your read is as good as the data."
            )

        return "\n".join(points[:5])

    async def close(self):
        """Close the client connection."""
        if self.client is not None:
            await self.client.close()
