"""Tests for OpenRouter malformed-response handling.

Why this matters
----------------
OpenRouter can answer a request with HTTP 200 but ``choices == null``
and a top-level ``error`` object (free-model rate limits, provider
outages, moderation filters).  The OpenAI SDK turns that into a
``ChatCompletion`` whose ``choices`` attribute is ``None``.  The old code
did ``response.choices[0].message.content.strip()`` which raised
``TypeError: 'NoneType' object is not subscriptable`` on *every* request,
so AI explanations silently fell back and the real provider error was
buried under a noisy traceback (see production logs).

The client must now:
* never raise on a null/empty ``choices`` or null/empty ``content``;
* surface the provider ``error`` (when present) as a WARNING so operators
  know *why* the generation failed;
* return the deterministic fallback so tip generation keeps working.
"""

from __future__ import annotations

import logging
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from packages.shared.config import settings
from packages.shared.openrouter.client import OpenRouterClient

GAME = {
    "home_team": "Lions",
    "away_team": "Swans",
    "venue": "Gabba",
    "date": "2026-06-21T10:00:00Z",
}
PREDICTION = {"winner": "Lions", "confidence": 0.72, "margin": 18}

LOGGER_NAME = "packages.shared.openrouter.client"


def _choice(content):
    """Build a minimal ``choices[0]``-shaped object."""
    return SimpleNamespace(message=SimpleNamespace(content=content))


def _resp(choices, error=None):
    """Build a minimal chat-completion response with optional provider error."""
    return SimpleNamespace(choices=choices, error=error)


class TestExtractContent:
    """``_extract_content`` is the single safe point that reads the response."""

    def test_null_choices_returns_none(self):
        client = OpenRouterClient()
        assert client._extract_content(_resp(choices=None)) is None

    def test_missing_choices_attr_returns_none(self):
        client = OpenRouterClient()
        assert client._extract_content(SimpleNamespace()) is None

    def test_empty_choices_returns_none(self):
        client = OpenRouterClient()
        assert client._extract_content(_resp(choices=[])) is None

    def test_null_content_returns_none(self):
        client = OpenRouterClient()
        assert client._extract_content(_resp(choices=[_choice(None)])) is None

    def test_blank_content_returns_none(self):
        client = OpenRouterClient()
        assert client._extract_content(_resp(choices=[_choice("   ")])) is None

    def test_valid_content_returned_and_stripped(self):
        client = OpenRouterClient()
        assert client._extract_content(_resp(choices=[_choice("  hello  ")])) == "hello"

    def test_provider_error_logged_as_warning(self, caplog):
        client = OpenRouterClient()
        with caplog.at_level(logging.DEBUG, logger=LOGGER_NAME):
            result = client._extract_content(
                _resp(choices=None, error={"message": "rate limit exceeded"})
            )
        assert result is None
        assert any(
            "rate limit exceeded" in r.message and r.levelno == logging.WARNING
            for r in caplog.records
        )


def _build_client_with_response(monkeypatch, response):
    """Construct an OpenRouterClient whose SDK call returns ``response``."""
    monkeypatch.setattr(settings, "openrouter_api_key", "sk-or-v1-test")
    with patch("packages.shared.openrouter.client.AsyncOpenAI"):
        client = OpenRouterClient()

    async def fake_create(*args, **kwargs):
        return response

    client.client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=fake_create))
    )
    return client


class TestGenerateExplanationMalformedResponse:
    @pytest.mark.asyncio
    async def test_null_choices_falls_back_without_error_log(self, monkeypatch, caplog):
        client = _build_client_with_response(
            monkeypatch,
            _resp(choices=None, error={"message": "rate limit exceeded"}),
        )

        with caplog.at_level(logging.DEBUG, logger=LOGGER_NAME):
            result = await client.generate_explanation(GAME, PREDICTION, "best_bet")

        # Deterministic, data-aware fallback is returned.
        assert isinstance(result, str)
        assert "Lions" in result
        # The provider reason is surfaced...
        assert any("rate limit exceeded" in r.message for r in caplog.records)
        # ...without a noisy ERROR-level traceback.
        assert not any(r.levelno >= logging.ERROR for r in caplog.records)


class TestGenerateMatchAnalysisMalformedResponse:
    @pytest.mark.asyncio
    async def test_null_choices_falls_back_without_error_log(self, monkeypatch, caplog):
        client = _build_client_with_response(
            monkeypatch,
            _resp(choices=None, error={"message": "provider outage"}),
        )

        with caplog.at_level(logging.DEBUG, logger=LOGGER_NAME):
            result = await client.generate_match_analysis(GAME)

        assert isinstance(result, str)
        assert "Lions" in result
        assert any("provider outage" in r.message for r in caplog.records)
        assert not any(r.levelno >= logging.ERROR for r in caplog.records)
