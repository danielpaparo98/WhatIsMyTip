"""Tests for SEC-ME-003: OpenAI client must not be constructed with an empty API key.

Why this matters
----------------
The OpenAI SDK raises a non-obvious error from inside its request loop
when constructed with an empty ``api_key``.  That makes the failure
mode very hard to debug because:

* The error surfaces only when a request is attempted, not at startup.
* The error mentions the request URL, not the configuration issue.
* Constructing the client is non-trivial work (creates an httpx
  client, sets up auth headers, etc.) for nothing if the key is empty.

Fix
---
Gate the ``AsyncOpenAI(...)`` construction behind a non-empty
``settings.openrouter_api_key``.  When the key is missing, log a warning
and expose ``self.client = None`` so the existing fallback paths
(``if not settings.openrouter_api_key: ... return _generate_fallback_...``)
are the only path the request can take.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from packages.shared.config import settings
from packages.shared.openrouter.client import OpenRouterClient


class TestEmptyKeyGuard:
    """When ``openrouter_api_key`` is empty, no SDK client is built."""

    def test_empty_key_does_not_construct_async_openai(self, monkeypatch) -> None:
        monkeypatch.setattr(settings, "openrouter_api_key", "")

        with patch("packages.shared.openrouter.client.AsyncOpenAI") as mock_ctor:
            client = OpenRouterClient()

        # The SDK must NOT have been instantiated with an empty key.
        mock_ctor.assert_not_called()
        # And the attribute should be None (or a sentinel) so any later
        # attribute access fails fast / falls into the fallback path.
        assert client.client is None

    def test_whitespace_only_key_treated_as_empty(self, monkeypatch) -> None:
        """A key of only whitespace is functionally empty — same guard."""
        monkeypatch.setattr(settings, "openrouter_api_key", "   \t  ")

        with patch("packages.shared.openrouter.client.AsyncOpenAI") as mock_ctor:
            client = OpenRouterClient()

        mock_ctor.assert_not_called()
        assert client.client is None

    def test_valid_key_constructs_async_openai(self, monkeypatch) -> None:
        monkeypatch.setattr(settings, "openrouter_api_key", "sk-or-v1-test")

        with patch("packages.shared.openrouter.client.AsyncOpenAI") as mock_ctor:
            mock_instance = object()
            mock_ctor.return_value = mock_instance
            client = OpenRouterClient()

        mock_ctor.assert_called_once()
        # The first positional / keyword ``api_key`` is the real key.
        kwargs = mock_ctor.call_args.kwargs
        assert kwargs.get("api_key") == "sk-or-v1-test"
        assert client.client is mock_instance

    @pytest.mark.asyncio
    async def test_empty_key_uses_fallback_path(self, monkeypatch) -> None:
        """``generate_explanation`` must return a fallback string when the
        key is empty, not attempt to call the SDK."""
        monkeypatch.setattr(settings, "openrouter_api_key", "")

        client = OpenRouterClient()
        result = await client.generate_explanation(
            game={"home_team": "A", "away_team": "B", "venue": "V"},
            prediction={"winner": "A", "margin": 10, "confidence": 0.7},
            heuristic="best_bet",
        )

        # Should match the fallback explanation for ``best_bet``.
        assert isinstance(result, str)
        assert "A" in result
        # And critically: the SDK was never touched, so the fallback is
        # the only path.
        assert client.client is None
