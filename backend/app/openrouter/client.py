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
    
    async def close(self):
        """Close the client connection."""
        await self.client.close()
