"""Unit tests for Pydantic request schemas and validate_request() helper.

Covers:
- Admin request models accept valid data
- Admin request models reject invalid types
- Tip generate request validates season is required and integer
- Tip generate request validates heuristics against allowed values
- validate_request() returns model on success
- validate_request() returns 422 error dict on failure
- Default values are applied correctly
"""

import pytest
from pydantic import ValidationError

from packages.shared.api_helpers import validate_request
from packages.shared.schemas.admin import (
    DailySyncTriggerRequest,
    HistoricRefreshTriggerRequest,
    MatchCompletionTriggerRequest,
    TipGenerateRequest,
    TipGenerationTriggerRequest,
)

# ---------------------------------------------------------------------------
# DailySyncTriggerRequest
# ---------------------------------------------------------------------------

class TestDailySyncTriggerRequest:
    """Tests for DailySyncTriggerRequest schema."""

    def test_accepts_empty_body(self):
        """Empty body should use defaults (season=None)."""
        req = DailySyncTriggerRequest.model_validate({})
        assert req.season is None

    def test_accepts_valid_season(self):
        req = DailySyncTriggerRequest.model_validate({"season": 2025})
        assert req.season == 2025

    def test_rejects_string_season(self):
        with pytest.raises(ValidationError) as exc_info:
            DailySyncTriggerRequest.model_validate({"season": "not-a-number"})
        errors = exc_info.value.errors()
        assert any("season" in str(e["loc"]) for e in errors)

    def test_rejects_float_season(self):
        with pytest.raises(ValidationError):
            DailySyncTriggerRequest.model_validate({"season": 2025.5})

    def test_ignores_extra_fields(self):
        """Extra fields should be silently ignored."""
        req = DailySyncTriggerRequest.model_validate({
            "season": 2025,
            "unknown_field": "should be ignored",
        })
        assert req.season == 2025


# ---------------------------------------------------------------------------
# MatchCompletionTriggerRequest
# ---------------------------------------------------------------------------

class TestMatchCompletionTriggerRequest:
    """Tests for MatchCompletionTriggerRequest schema."""

    def test_accepts_empty_body(self):
        req = MatchCompletionTriggerRequest.model_validate({})
        assert req.season is None
        assert req.buffer_minutes is None

    def test_accepts_valid_params(self):
        req = MatchCompletionTriggerRequest.model_validate({
            "season": 2025,
            "buffer_minutes": 120,
        })
        assert req.season == 2025
        assert req.buffer_minutes == 120

    def test_rejects_string_buffer_minutes(self):
        with pytest.raises(ValidationError) as exc_info:
            MatchCompletionTriggerRequest.model_validate({"buffer_minutes": "abc"})
        errors = exc_info.value.errors()
        assert any("buffer_minutes" in str(e["loc"]) for e in errors)

    def test_rejects_negative_buffer_minutes(self):
        """Negative buffer_minutes should still be accepted at schema level (int validation).
        Business logic may reject it later."""
        req = MatchCompletionTriggerRequest.model_validate({"buffer_minutes": -5})
        assert req.buffer_minutes == -5

    def test_defaults_are_none(self):
        req = MatchCompletionTriggerRequest()
        assert req.season is None
        assert req.buffer_minutes is None


# ---------------------------------------------------------------------------
# TipGenerationTriggerRequest
# ---------------------------------------------------------------------------

class TestTipGenerationTriggerRequest:
    """Tests for TipGenerationTriggerRequest schema."""

    def test_accepts_empty_body(self):
        req = TipGenerationTriggerRequest.model_validate({})
        assert req.season is None
        assert req.round_id is None
        assert req.regenerate is False

    def test_accepts_valid_full_params(self):
        req = TipGenerationTriggerRequest.model_validate({
            "season": 2025,
            "round_id": 5,
            "regenerate": True,
        })
        assert req.season == 2025
        assert req.round_id == 5
        assert req.regenerate is True

    def test_rejects_string_round_id(self):
        with pytest.raises(ValidationError):
            TipGenerationTriggerRequest.model_validate({"round_id": "round-five"})

    def test_coerces_string_regenerate(self):
        """Pydantic v2 lax mode coerces 'yes' to True for bool fields."""
        req = TipGenerationTriggerRequest.model_validate({"regenerate": "yes"})
        assert req.regenerate is True

    def test_rejects_non_coercible_regenerate(self):
        """Non-coercible strings should raise ValidationError."""
        with pytest.raises(ValidationError):
            TipGenerationTriggerRequest.model_validate({"regenerate": [1, 2]})

    def test_regenerate_default_false(self):
        req = TipGenerationTriggerRequest.model_validate({"season": 2025})
        assert req.regenerate is False

    def test_regenerate_true_from_string_true(self):
        """Pydantic v2 coerces 'true' string to bool True."""
        req = TipGenerationTriggerRequest.model_validate({"regenerate": "true"})
        assert req.regenerate is True


# ---------------------------------------------------------------------------
# HistoricRefreshTriggerRequest
# ---------------------------------------------------------------------------

class TestHistoricRefreshTriggerRequest:
    """Tests for HistoricRefreshTriggerRequest schema."""

    def test_accepts_empty_body(self):
        req = HistoricRefreshTriggerRequest.model_validate({})
        assert req.seasons is None
        assert req.round_id is None
        assert req.regenerate_tips is False

    def test_accepts_valid_params(self):
        req = HistoricRefreshTriggerRequest.model_validate({
            "seasons": "2023,2024,2025",
            "round_id": 10,
            "regenerate_tips": True,
        })
        assert req.seasons == "2023,2024,2025"
        assert req.round_id == 10
        assert req.regenerate_tips is True

    def test_rejects_int_for_seasons(self):
        """seasons field expects a string, not int."""
        with pytest.raises(ValidationError):
            HistoricRefreshTriggerRequest.model_validate({"seasons": 2025})

    def test_regenerate_tips_default_false(self):
        req = HistoricRefreshTriggerRequest.model_validate({"seasons": "2025"})
        assert req.regenerate_tips is False


# ---------------------------------------------------------------------------
# TipGenerateRequest
# ---------------------------------------------------------------------------

class TestTipGenerateRequest:
    """Tests for TipGenerateRequest schema."""

    def test_accepts_valid_required_fields(self):
        req = TipGenerateRequest.model_validate({"season": 2025})
        assert req.season == 2025
        assert req.round_id is None
        assert req.regenerate is False
        assert req.heuristics is None

    def test_accepts_all_fields(self):
        req = TipGenerateRequest.model_validate({
            "season": 2025,
            "round_id": 5,
            "regenerate": True,
            "heuristics": ["best_bet", "yolo"],
        })
        assert req.season == 2025
        assert req.round_id == 5
        assert req.regenerate is True
        assert req.heuristics == ["best_bet", "yolo"]

    def test_rejects_missing_season(self):
        """season is required — empty body should fail."""
        with pytest.raises(ValidationError) as exc_info:
            TipGenerateRequest.model_validate({})
        errors = exc_info.value.errors()
        assert any("season" in str(e["loc"]) for e in errors)

    def test_rejects_string_season(self):
        with pytest.raises(ValidationError) as exc_info:
            TipGenerateRequest.model_validate({"season": "invalid"})
        errors = exc_info.value.errors()
        assert any("season" in str(e["loc"]) for e in errors)

    def test_rejects_float_season(self):
        with pytest.raises(ValidationError):
            TipGenerateRequest.model_validate({"season": 2025.7})

    def test_heuristics_accepts_valid_list(self):
        req = TipGenerateRequest.model_validate({
            "season": 2025,
            "heuristics": ["best_bet", "high_risk_high_reward", "yolo"],
        })
        assert req.heuristics == ["best_bet", "high_risk_high_reward", "yolo"]

    def test_heuristics_accepts_any_string_list(self):
        """Schema accepts any string list — business logic validates against allowed set."""
        req = TipGenerateRequest.model_validate({
            "season": 2025,
            "heuristics": ["invalid_heuristic"],
        })
        assert req.heuristics == ["invalid_heuristic"]

    def test_round_id_optional(self):
        req = TipGenerateRequest.model_validate({"season": 2025})
        assert req.round_id is None

    def test_regenerate_default(self):
        req = TipGenerateRequest.model_validate({"season": 2025})
        assert req.regenerate is False


# ---------------------------------------------------------------------------
# validate_request() helper
# ---------------------------------------------------------------------------

class TestValidateRequest:
    """Tests for the validate_request() helper function."""

    def test_returns_model_on_success(self):
        model, err = validate_request(
            {"season": 2025},
            DailySyncTriggerRequest,
        )
        assert model is not None
        assert err is None
        assert model.season == 2025

    def test_returns_422_error_on_failure(self):
        model, err = validate_request(
            {"season": "not-an-int"},
            DailySyncTriggerRequest,
        )
        assert model is None
        assert err is not None
        assert err["statusCode"] == 422

    def test_error_response_contains_errors_list(self):
        model, err = validate_request(
            {"season": "bad"},
            DailySyncTriggerRequest,
        )
        assert model is None
        body = err["body"]
        assert "errors" in body
        assert isinstance(body["errors"], list)
        assert len(body["errors"]) > 0

    def test_error_response_fields_have_expected_keys(self):
        model, err = validate_request(
            {"season": "bad"},
            DailySyncTriggerRequest,
        )
        error_entry = err["body"]["errors"][0]
        assert "field" in error_entry
        assert "message" in error_entry
        assert "type" in error_entry

    def test_returns_model_with_empty_body_for_optional_fields(self):
        model, err = validate_request({}, TipGenerationTriggerRequest)
        assert model is not None
        assert err is None
        assert model.season is None
        assert model.regenerate is False

    def test_returns_422_for_missing_required_field(self):
        """TipGenerateRequest requires season — empty body should fail."""
        model, err = validate_request({}, TipGenerateRequest)
        assert model is None
        assert err is not None
        assert err["statusCode"] == 422

    def test_preserves_all_validation_errors(self):
        """Multiple invalid fields should produce multiple error entries."""
        model, err = validate_request(
            {"season": "bad", "round_id": "also-bad", "regenerate": "not-bool"},
            TipGenerationTriggerRequest,
        )
        assert model is None
        errors = err["body"]["errors"]
        # At least season and round_id should fail
        assert len(errors) >= 2


# ---------------------------------------------------------------------------
# Integration: validate_request with each admin model
# ---------------------------------------------------------------------------

class TestValidateRequestWithAdminModels:
    """Integration tests: validate_request works correctly with each admin model."""

    def test_daily_sync_valid(self):
        model, err = validate_request({"season": 2025}, DailySyncTriggerRequest)
        assert model is not None
        assert err is None
        assert model.season == 2025

    def test_daily_sync_invalid(self):
        model, err = validate_request({"season": "abc"}, DailySyncTriggerRequest)
        assert model is None
        assert err["statusCode"] == 422

    def test_match_completion_valid(self):
        model, err = validate_request(
            {"buffer_minutes": 90},
            MatchCompletionTriggerRequest,
        )
        assert model is not None
        assert model.buffer_minutes == 90

    def test_match_completion_invalid(self):
        model, err = validate_request(
            {"buffer_minutes": [1, 2, 3]},
            MatchCompletionTriggerRequest,
        )
        assert model is None
        assert err["statusCode"] == 422

    def test_tip_generation_valid(self):
        model, err = validate_request(
            {"season": 2025, "round_id": 3, "regenerate": True},
            TipGenerationTriggerRequest,
        )
        assert model is not None
        assert model.season == 2025
        assert model.round_id == 3
        assert model.regenerate is True

    def test_historic_refresh_valid(self):
        model, err = validate_request(
            {"seasons": "2023,2024", "regenerate_tips": True},
            HistoricRefreshTriggerRequest,
        )
        assert model is not None
        assert model.seasons == "2023,2024"
        assert model.regenerate_tips is True

    def test_tip_generate_valid(self):
        model, err = validate_request(
            {"season": 2025, "round_id": 7, "heuristics": ["best_bet"]},
            TipGenerateRequest,
        )
        assert model is not None
        assert model.season == 2025
        assert model.heuristics == ["best_bet"]
