import logging
from openai import AsyncOpenAI
from typing import Optional
from app.config import settings

logger = logging.getLogger(__name__)


class OpenRouterClient:
    """Client for interacting with OpenRouter API."""
    
    def __init__(self):
        self.client = AsyncOpenAI(
            api_key=settings.openrouter_api_key,
            base_url=settings.openrouter_base_url,
            timeout=30.0,
        )
        self.model = settings.openrouter_model
    
    async def generate_explanation(
        self,
        game: dict,
        prediction: dict,
        heuristic: str,
        model_predictions: Optional[dict] = None,
    ) -> str:
        """Generate a human-readable explanation for a tip.
        
        Args:
            game: Game dictionary with keys: home_team, away_team, venue, date
            prediction: Prediction dictionary with keys: winner, confidence, margin
            heuristic: Heuristic type (best_bet, yolo, high_risk_high_reward)
            model_predictions: Optional dict of model_name -> (winner, confidence, margin)
            
        Returns:
            Human-readable explanation string
        """
        
        # If no API key is configured, return fallback immediately
        if not settings.openrouter_api_key:
            logger.warning(
                "OpenRouter API key not configured, using fallback explanation"
            )
            return self._generate_fallback_explanation(
                game, prediction, heuristic
            )
        
        # Build context for the AI
        context = self._build_prompt_context(
            game, prediction, heuristic, model_predictions
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
                game, prediction, heuristic
            )
    
    def _get_system_prompt(self) -> str:
        """Get the system prompt for the AI."""
        return """You are an AFL footy tipping expert. Your task is to generate concise, 
human-readable explanations for footy tips. 

Your explanations should:
- Be 2-3 sentences maximum
- Use clear, simple language
- Focus on the key reasons for the pick
- Mention the specific heuristic being used
- Be honest about uncertainty when confidence is low

Heuristics:
- best_bet: Conservative, consensus-based picks with high confidence
- yolo: Aggressive picks based on the highest confidence model prediction
- high_risk_high_reward: Picks targeting upset opportunities when models disagree

Keep explanations brief and to the point. No fluff."""
    
    def _build_prompt_context(
        self,
        game: dict,
        prediction: dict,
        heuristic: str,
        model_predictions: Optional[dict],
    ) -> str:
        """Build the prompt context for the AI."""
        context = f"""Game: {game['home_team']} vs {game['away_team']} at {game['venue']}
Heuristic: {heuristic}
Prediction: {prediction['winner']} to win by {prediction['margin']} points
Confidence: {prediction['confidence']:.0%}

"""
        
        if model_predictions:
            context += "Model predictions:\n"
            for model_name, (winner, confidence, margin) in model_predictions.items():
                context += f"- {model_name}: {winner} ({confidence:.0%}, {margin} pts)\n"
            context += "\n"
        
        context += "Generate a brief explanation for this tip:"
        
        return context
    
    def _generate_fallback_explanation(
        self, game: dict, prediction: dict, heuristic: str
    ) -> str:
        """Generate a simple fallback explanation if AI fails."""
        winner = prediction["winner"]
        margin = prediction["margin"]
        confidence = prediction["confidence"]
        
        if heuristic == "best_bet":
            return f"{winner} is the consensus pick across multiple models. Confidence is {confidence:.0%} with a predicted margin of {margin} points."
        elif heuristic == "yolo":
            return f"Going all in on {winner} with {confidence:.0%} confidence. The highest confidence model predicts a {margin}-point win."
        else:  # high_risk_high_reward
            return f"Targeting an upset with {winner}. Models are split, creating a high-risk high-reward opportunity. Predicted margin: {margin} points."
    
    async def generate_match_analysis(
        self,
        game: dict,
        model_predictions: Optional[dict] = None,
    ) -> str:
        """Generate casual, tongue-in-cheek talking points for a match.

        Args:
            game: Game dictionary with keys: home_team, away_team, venue, date
            model_predictions: Optional dict of model_name -> (winner, confidence, margin)

        Returns:
            Casual talking points string
        """

        # If no API key is configured, return fallback immediately
        if not settings.openrouter_api_key:
            logger.warning(
                "OpenRouter API key not configured, using fallback match analysis"
            )
            return self._generate_fallback_match_analysis(game)

        # Build context for the AI
        context = self._build_match_analysis_context(game, model_predictions)

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
                max_tokens=400,
                temperature=0.8,
            )

            analysis = response.choices[0].message.content.strip()
            return analysis

        except Exception as e:
            logger.error(f"OpenRouter AI match analysis failed: {e}", exc_info=True)
            return self._generate_fallback_match_analysis(game)

    def _get_match_analysis_system_prompt(self) -> str:
        """Get the system prompt for match analysis generation."""
        return """You are a witty, slightly sarcastic AFL footy fan who helps people sound knowledgeable \
about upcoming matches at BBQs, pubs, and watercooler conversations. Your job is to give \
casual talking points that someone can drop into conversation to sound like they know \
their footy — even if they haven't watched a game all season.

Keep it fun, tongue-in-cheek, and relatable. Use Aussie footy slang where natural. \
Each talking point should be something someone could casually say in conversation.

Format your response as 4-5 short, punchy talking points separated by newlines. \
Each point should be 1-2 sentences max. No bullet points or numbers — just the raw \
talking points as conversational snippets."""

    def _build_match_analysis_context(
        self,
        game: dict,
        model_predictions: Optional[dict],
    ) -> str:
        """Build the prompt context for match analysis generation."""
        context = f"""Game: {game['home_team']} vs {game['away_team']} at {game['venue']}
Date: {game.get('date', 'TBD')}

"""

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
                context += f"\nConsensus: {most_picked[0]} picked by {most_picked[1]}/{len(model_predictions)} models"
                context += f"\nAverage predicted margin: {avg_margin:.0f} pts"
                context += f"\nAverage confidence: {avg_confidence:.0%}\n"

        context += "\nGenerate casual talking points for this match:"

        return context

    def _generate_fallback_match_analysis(self, game: dict) -> str:
        """Generate a simple fallback match analysis if AI fails."""
        home = game.get("home_team", "the home side")
        away = game.get("away_team", "the visitors")
        return (
            f"Look, {home} at home, you'd back them wouldn't you? "
            f"But {away} have been known to upset the apple cart.\n"
            f"Apparently the bookies have this one as a coin flip, so basically nobody knows. "
            f"Perfect — your opinion is as good as anyone's.\n"
            f"Just nod confidently if someone mentions the margin. It's all about the vibe."
        )

    async def close(self):
        """Close the client connection."""
        await self.client.close()
