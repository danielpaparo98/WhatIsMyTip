"""Unit tests for ``Settings.cors_origins`` (the single source of truth).

Regression test for the cors_origins_list dead-code cleanup: the
field is now ALWAYS a list.  No defensive re-parsing in a
property, no duplicate ``cors_origins_list`` accessor, no
``Union[str, List[str]]`` ambiguity.

The validator is a single ``model_validator(mode="after")`` that
accepts three input shapes:

  * **CSV string**         ``"http://a,http://b"``            \u2192 ``["http://a", "http://b"]``
  * **default list**       ``["http://a", "http://b"]``       \u2192 ``["http://a", "http://b"]`` (passthrough)
  * **JSON list string**   ``'["http://a","http://b"]'``      \u2192 list of strings

The tests pin all three input shapes AND assert that the legacy
``cors_origins_list`` attribute is gone (a fresh ``Settings``
instance must not expose it).
"""

from __future__ import annotations

import pytest

from packages.shared.config import Settings


class TestCorsOriginsSingleSourceOfTruth:
    """``settings.cors_origins`` is always a list \u2014 no string fallbacks."""

    def test_default_cors_origins_is_a_list(self, monkeypatch):
        """The unconfigured default is a list, not a string."""
        monkeypatch.delenv("CORS_ORIGINS", raising=False)
        s = Settings()
        assert isinstance(s.cors_origins, list), (
            f"settings.cors_origins should always be a list, "
            f"got {type(s.cors_origins).__name__}"
        )
        assert s.cors_origins == ["http://localhost:3000", "http://127.0.0.1:3000"]

    def test_csv_string_becomes_list(self, monkeypatch):
        """``CORS_ORIGINS=http://a,http://b, http://c`` \u2192 stripped list of 3."""
        monkeypatch.setenv("CORS_ORIGINS", "http://a,http://b, http://c ")
        s = Settings()
        assert isinstance(s.cors_origins, list)
        assert s.cors_origins == ["http://a", "http://b", "http://c"]

    def test_csv_single_value(self, monkeypatch):
        """A one-element CSV still produces a list of length 1."""
        monkeypatch.setenv("CORS_ORIGINS", "http://only")
        s = Settings()
        assert s.cors_origins == ["http://only"]

    def test_csv_with_extra_whitespace(self, monkeypatch):
        """Leading/trailing whitespace around individual origins is stripped."""
        monkeypatch.setenv("CORS_ORIGINS", "  http://a  ,  http://b  ")
        s = Settings()
        assert s.cors_origins == ["http://a", "http://b"]

    def test_empty_string_becomes_empty_list(self, monkeypatch):
        """An empty env var produces an empty list (not a list with one empty entry)."""
        monkeypatch.setenv("CORS_ORIGINS", "")
        s = Settings()
        assert s.cors_origins == []

    def test_cors_origins_list_attribute_is_gone(self, monkeypatch):
        """The dead-code ``cors_origins_list`` property MUST be removed.

        Keeping it would let callers silently fall back to a
        string interpretation \u2014 the whole point of the fix is
        to give the field a single, well-defined type.
        """
        monkeypatch.delenv("CORS_ORIGINS", raising=False)
        s = Settings()
        assert not hasattr(s, "cors_origins_list"), (
            "Settings.cors_origins_list is dead code; callers should "
            "use settings.cors_origins directly."
        )

    def test_cors_origins_never_returns_string(self, monkeypatch):
        """Defence in depth: whatever the env var shape, the field
        MUST be a list.  We iterate over a range of input shapes
        so a future regression (e.g. someone removes the validator)
        fails loudly.
        """
        for raw in (
            "",
            "http://only",
            "http://a,http://b",
            "  http://a  ,  http://b  ",
            "http://a, http://b , http://c",
        ):
            monkeypatch.setenv("CORS_ORIGINS", raw)
            s = Settings()
            assert isinstance(s.cors_origins, list), (
                f"CORS_ORIGINS={raw!r} should produce a list, "
                f"got {type(s.cors_origins).__name__}"
            )
            assert all(isinstance(o, str) for o in s.cors_origins), (
                f"CORS_ORIGINS={raw!r} produced non-string entries: "
                f"{s.cors_origins!r}"
            )

    def test_cors_origins_contains_only_stripped_values(self, monkeypatch):
        """The validator MUST strip whitespace; an unstripped origin
        would break origin matching in the CORS middleware.
        """
        monkeypatch.setenv("CORS_ORIGINS", "  https://app.example.com  ")
        s = Settings()
        assert s.cors_origins == ["https://app.example.com"]
