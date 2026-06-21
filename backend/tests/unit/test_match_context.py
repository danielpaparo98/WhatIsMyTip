"""Tests for the rich match-context builder and the data-aware AI fallbacks.

These cover the two behaviours introduced for the "Talking Points" +
heuristic-explanation improvement:

* ``_format_match_context`` renders each available data section and omits
  missing ones.
* The system prompts no longer contain the old "BBQ" framing and now ask
  the model to interpret/balance the data.
* The deterministic fallbacks (used when the OpenRouter key is missing or
  the API call fails) incorporate the rich context so output is still
  meaningful instead of a generic template.
"""

from __future__ import annotations

from packages.shared.openrouter.client import OpenRouterClient, _format_match_context


GAME = {
    "home_team": "Lions",
    "away_team": "Swans",
    "venue": "Gabba",
    "date": "2026-06-21T10:00:00Z",
}
PREDICTION = {"winner": "Lions", "confidence": 0.72, "margin": 18}


class TestFormatMatchContext:
    def test_none_returns_empty(self):
        assert _format_match_context(None) == ""

    def test_empty_returns_empty(self):
        assert _format_match_context({}) == ""

    def test_core_only_returns_empty(self):
        # No optional sections -> nothing to render.
        assert _format_match_context({"home_team": "Lions", "away_team": "Swans"}) == ""

    def test_renders_elo(self):
        out = _format_match_context(
            {
                "home_team": "Lions",
                "away_team": "Swans",
                "elo": {"home": 1600, "away": 1500, "diff": 100},
            }
        )
        assert "ELO" in out
        assert "Lions" in out
        assert "100" in out

    def test_renders_form(self):
        out = _format_match_context(
            {
                "home_team": "Lions",
                "away_team": "Swans",
                "form": {
                    "home": {"games": 5, "wins": 4, "losses": 1, "streak": "WWWLW", "avg_margin": 12.0},
                    "away": {"games": 5, "wins": 2, "losses": 3, "streak": "LWLWL", "avg_margin": -5.0},
                },
            }
        )
        assert "Recent form" in out
        assert "4-1" in out

    def test_renders_head_to_head(self):
        out = _format_match_context(
            {
                "home_team": "Lions",
                "away_team": "Swans",
                "head_to_head": {"games": 6, "home_wins": 4, "away_wins": 2},
            }
        )
        assert "Head-to-head" in out

    def test_renders_weather(self):
        out = _format_match_context(
            {
                "home_team": "Lions",
                "away_team": "Swans",
                "weather": {
                    "temperature": 14.0,
                    "precipitation": 2.5,
                    "wind_speed": 30.0,
                    "humidity": 80,
                    "conditions": "rain",
                },
            }
        )
        assert "Weather" in out
        assert "rain" in out

    def test_renders_injuries(self):
        out = _format_match_context(
            {
                "home_team": "Lions",
                "away_team": "Swans",
                "injuries": {
                    "home": ["Joe Bloggs (3 weeks)"],
                    "away": ["Jack Smith (1-2 weeks)", "Pat Hame (test)"],
                },
            }
        )
        assert "Key outs" in out
        assert "Joe Bloggs" in out


class TestPromptsReframed:
    def test_explanation_prompt_asks_to_interpret(self):
        client = OpenRouterClient()
        prompt = client._get_system_prompt()
        assert "INTERPRET" in prompt
        assert "BBQ" not in prompt

    def test_analysis_prompt_drops_bbq_and_requires_balance(self):
        client = OpenRouterClient()
        prompt = client._get_match_analysis_system_prompt()
        assert "bbq" not in prompt.lower()
        assert "balanced" in prompt.lower()


class TestDataAwareFallbacks:
    def test_fallback_explanation_uses_elo_edge(self):
        client = OpenRouterClient()
        ctx = {
            "home_team": "Lions",
            "away_team": "Swans",
            "elo": {"home": 1600, "away": 1480, "diff": 120},
        }
        out = client._generate_fallback_explanation(GAME, PREDICTION, "best_bet", ctx)
        assert "Lions" in out
        assert "120" in out  # the ELO edge is referenced

    def test_fallback_explanation_without_context_still_works(self):
        client = OpenRouterClient()
        out = client._generate_fallback_explanation(GAME, PREDICTION, "yolo", None)
        assert "Lions" in out
        assert "18" in out  # margin still present

    def test_fallback_analysis_uses_context(self):
        client = OpenRouterClient()
        ctx = {
            "home_team": "Lions",
            "away_team": "Swans",
            "elo": {"home": 1600, "away": 1500, "diff": 100},
            "form": {
                "home": {"games": 5, "wins": 4, "losses": 1, "avg_margin": 10.0},
                "away": {"games": 5, "wins": 1, "losses": 4, "avg_margin": -8.0},
            },
            "head_to_head": {"games": 6, "home_wins": 4, "away_wins": 2},
        }
        out = client._generate_fallback_match_analysis(GAME, ctx)
        # Multiple talking points separated by newlines.
        assert out.count("\n") >= 1
        assert "ELO" in out or "edge" in out.lower()

    def test_fallback_analysis_without_context(self):
        client = OpenRouterClient()
        out = client._generate_fallback_match_analysis(GAME, None)
        assert "Lions" in out
        # Still produces multiple points.
        assert out.count("\n") >= 1
