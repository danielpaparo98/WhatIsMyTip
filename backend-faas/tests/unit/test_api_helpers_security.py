"""Unit tests for security hardening in api_helpers.py.

Covers:
- Security headers present in all responses
- Rate limiting returns 429 when limit exceeded
- Rate limiting allows requests within limit
- Rate limiting falls back gracefully when Redis unavailable
- Request size check returns 413 when body too large
- Request size check allows normal requests
"""

import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

# The Redis client factory is imported dynamically inside check_rate_limit(),
# so we patch it at the source module.
_REDIS_CLIENT_PATCH = "packages.shared.cache._get_client"


# ---------------------------------------------------------------------------
# Security Headers Tests
# ---------------------------------------------------------------------------

class TestSecurityHeaders:
    """Verify OWASP-recommended security headers on all responses."""

    EXPECTED_SECURITY_HEADERS = {
        "X-Content-Type-Options": "nosniff",
        "X-Frame-Options": "DENY",
        "Strict-Transport-Security": "max-age=63072000; includeSubDomains; preload",
        "Content-Security-Policy": "default-src 'none'; frame-ancestors 'none'",
        "Referrer-Policy": "strict-origin-when-cross-origin",
        "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
        "Cross-Origin-Opener-Policy": "same-origin",
    }

    def test_security_headers_on_200_response(self):
        """Security headers are present on a 200 OK response."""
        from packages.shared.api_helpers import response

        result = response(200, data={"foo": "bar"})

        for header, expected_value in self.EXPECTED_SECURITY_HEADERS.items():
            assert result["headers"][header] == expected_value, (
                f"Expected {header}={expected_value!r}, got {result['headers'].get(header)!r}"
            )

    def test_security_headers_on_400_response(self):
        """Security headers are present on a 400 Bad Request response."""
        from packages.shared.api_helpers import response

        result = response(400, error="Bad request")

        for header, expected_value in self.EXPECTED_SECURITY_HEADERS.items():
            assert result["headers"][header] == expected_value

    def test_security_headers_on_404_response(self):
        """Security headers are present on a 404 Not Found response."""
        from packages.shared.api_helpers import response

        result = response(404, error="Not found")

        for header, expected_value in self.EXPECTED_SECURITY_HEADERS.items():
            assert result["headers"][header] == expected_value

    def test_security_headers_on_500_response(self):
        """Security headers are present on a 500 Internal Server Error response."""
        from packages.shared.api_helpers import response

        result = response(500, error="Internal error")

        for header, expected_value in self.EXPECTED_SECURITY_HEADERS.items():
            assert result["headers"][header] == expected_value

    def test_security_headers_on_204_no_content(self):
        """Security headers are present on a 204 No Content (CORS preflight) response."""
        from packages.shared.api_helpers import response

        result = response(204)

        for header, expected_value in self.EXPECTED_SECURITY_HEADERS.items():
            assert result["headers"][header] == expected_value

    def test_cors_headers_still_present(self):
        """CORS headers are still included alongside security headers."""
        from packages.shared.api_helpers import response

        result = response(200, data={"ok": True})

        assert "Access-Control-Allow-Origin" in result["headers"]
        assert "Access-Control-Allow-Methods" in result["headers"]
        assert "Access-Control-Allow-Headers" in result["headers"]
        assert result["headers"]["Content-Type"] == "application/json"

    def test_security_headers_on_429_response(self):
        """Security headers are present on a 429 Too Many Requests response."""
        from packages.shared.api_helpers import response

        result = response(429, error="Rate limit exceeded")

        for header, expected_value in self.EXPECTED_SECURITY_HEADERS.items():
            assert result["headers"][header] == expected_value

    def test_security_headers_on_413_response(self):
        """Security headers are present on a 413 Payload Too Large response."""
        from packages.shared.api_helpers import response

        result = response(413, error="Request body too large")

        for header, expected_value in self.EXPECTED_SECURITY_HEADERS.items():
            assert result["headers"][header] == expected_value


# ---------------------------------------------------------------------------
# Rate Limiting Tests
# ---------------------------------------------------------------------------

class TestRateLimiting:
    """Verify Redis-based sliding window rate limiting."""

    @pytest.mark.asyncio
    async def test_allows_request_within_limit(self):
        """Requests within the limit return None (no error)."""
        from packages.shared.api_helpers import check_rate_limit

        mock_client = AsyncMock()
        mock_client.incr = AsyncMock(return_value=1)
        mock_client.expire = AsyncMock(return_value=True)

        with patch(_REDIS_CLIENT_PATCH, return_value=mock_client):
            result = await check_rate_limit(
                {"__ow_headers": {"x-forwarded-for": "1.2.3.4"}},
                max_requests=60,
                window_seconds=60,
            )

        assert result is None

    @pytest.mark.asyncio
    async def test_returns_429_when_limit_exceeded(self):
        """Requests exceeding the limit return a 429 response dict."""
        from packages.shared.api_helpers import check_rate_limit

        mock_client = AsyncMock()
        # Simulate the 61st request in the window
        mock_client.incr = AsyncMock(return_value=61)
        mock_client.expire = AsyncMock(return_value=True)

        with patch(_REDIS_CLIENT_PATCH, return_value=mock_client):
            result = await check_rate_limit(
                {"__ow_headers": {"x-forwarded-for": "1.2.3.4"}},
                max_requests=60,
                window_seconds=60,
            )

        assert result is not None
        assert result["statusCode"] == 429
        assert "Rate limit" in result["body"]["error"]

    @pytest.mark.asyncio
    async def test_returns_429_exactly_at_boundary(self):
        """Request at exactly max_requests + 1 is rejected."""
        from packages.shared.api_helpers import check_rate_limit

        mock_client = AsyncMock()
        mock_client.incr = AsyncMock(return_value=10)
        mock_client.expire = AsyncMock(return_value=True)

        with patch(_REDIS_CLIENT_PATCH, return_value=mock_client):
            result = await check_rate_limit(
                {"__ow_headers": {"x-real-ip": "5.6.7.8"}},
                max_requests=9,
                window_seconds=30,
            )

        assert result is not None
        assert result["statusCode"] == 429

    @pytest.mark.asyncio
    async def test_allows_at_exactly_max(self):
        """Request at exactly max_requests is allowed."""
        from packages.shared.api_helpers import check_rate_limit

        mock_client = AsyncMock()
        mock_client.incr = AsyncMock(return_value=60)
        mock_client.expire = AsyncMock(return_value=True)

        with patch(_REDIS_CLIENT_PATCH, return_value=mock_client):
            result = await check_rate_limit(
                {"__ow_headers": {"x-real-ip": "5.6.7.8"}},
                max_requests=60,
                window_seconds=30,
            )

        assert result is None

    @pytest.mark.asyncio
    async def test_falls_back_gracefully_when_redis_unavailable(self):
        """When Redis throws an exception, the request is allowed (returns None)."""
        from packages.shared.api_helpers import check_rate_limit

        mock_client = AsyncMock()
        mock_client.incr = AsyncMock(side_effect=Exception("Connection refused"))

        with patch(_REDIS_CLIENT_PATCH, return_value=mock_client):
            result = await check_rate_limit(
                {"__ow_headers": {"x-forwarded-for": "1.2.3.4"}},
                max_requests=1,
                window_seconds=60,
            )

        assert result is None

    @pytest.mark.asyncio
    async def test_uses_default_config_values(self):
        """Uses settings defaults when max_requests/window_seconds not provided."""
        from packages.shared.api_helpers import check_rate_limit

        mock_client = AsyncMock()
        mock_client.incr = AsyncMock(return_value=1)
        mock_client.expire = AsyncMock(return_value=True)

        with patch(_REDIS_CLIENT_PATCH, return_value=mock_client):
            result = await check_rate_limit(
                {"__ow_headers": {"x-forwarded-for": "1.2.3.4"}},
            )

        assert result is None

    @pytest.mark.asyncio
    async def test_sets_expiry_on_first_request(self):
        """EXPIRE is called when the counter is at 1 (first request in window)."""
        from packages.shared.api_helpers import check_rate_limit

        mock_client = AsyncMock()
        mock_client.incr = AsyncMock(return_value=1)
        mock_client.expire = AsyncMock(return_value=True)

        with patch(_REDIS_CLIENT_PATCH, return_value=mock_client):
            await check_rate_limit(
                {"__ow_headers": {"x-forwarded-for": "1.2.3.4"}},
                max_requests=60,
                window_seconds=120,
            )

        mock_client.expire.assert_called_once()
        # The second arg should be the window_seconds
        call_args = mock_client.expire.call_args
        assert call_args[0][1] == 120

    @pytest.mark.asyncio
    async def test_no_expiry_on_subsequent_requests(self):
        """EXPIRE is NOT called when the counter is > 1."""
        from packages.shared.api_helpers import check_rate_limit

        mock_client = AsyncMock()
        mock_client.incr = AsyncMock(return_value=5)
        mock_client.expire = AsyncMock(return_value=True)

        with patch(_REDIS_CLIENT_PATCH, return_value=mock_client):
            await check_rate_limit(
                {"__ow_headers": {"x-forwarded-for": "1.2.3.4"}},
                max_requests=60,
                window_seconds=60,
            )

        mock_client.expire.assert_not_called()

    @pytest.mark.asyncio
    async def test_429_response_includes_security_headers(self):
        """Rate limit 429 responses include all security headers."""
        from packages.shared.api_helpers import check_rate_limit

        mock_client = AsyncMock()
        mock_client.incr = AsyncMock(return_value=61)
        mock_client.expire = AsyncMock(return_value=True)

        with patch(_REDIS_CLIENT_PATCH, return_value=mock_client):
            result = await check_rate_limit(
                {"__ow_headers": {"x-forwarded-for": "1.2.3.4"}},
                max_requests=60,
                window_seconds=60,
            )

        assert result["headers"]["X-Content-Type-Options"] == "nosniff"
        assert result["headers"]["X-Frame-Options"] == "DENY"


# ---------------------------------------------------------------------------
# Client Identity Extraction Tests
# ---------------------------------------------------------------------------

class TestClientIdentityExtraction:
    """Verify client identity extraction for rate limiting."""

    def test_extracts_forwarded_ip(self):
        """Extracts IP from x-forwarded-for header."""
        from packages.shared.api_helpers import _extract_client_identity

        identity = _extract_client_identity({
            "__ow_headers": {"x-forwarded-for": "10.0.0.1"},
        })
        assert identity == "10.0.0.1"

    def test_extracts_first_ip_from_forwarded_list(self):
        """Extracts only the first IP from a comma-separated list."""
        from packages.shared.api_helpers import _extract_client_identity

        identity = _extract_client_identity({
            "__ow_headers": {"x-forwarded-for": "10.0.0.1, 172.16.0.1"},
        })
        assert identity == "10.0.0.1"

    def test_extracts_real_ip(self):
        """Extracts IP from x-real-ip header."""
        from packages.shared.api_helpers import _extract_client_identity

        identity = _extract_client_identity({
            "__ow_headers": {"x-real-ip": "192.168.1.1"},
        })
        assert identity == "192.168.1.1"

    def test_extracts_cf_connecting_ip(self):
        """Extracts IP from cf-connecting-ip header."""
        from packages.shared.api_helpers import _extract_client_identity

        identity = _extract_client_identity({
            "__ow_headers": {"cf-connecting-ip": "203.0.113.50"},
        })
        assert identity == "203.0.113.50"

    def test_falls_back_to_anonymous(self):
        """Falls back to 'anonymous' when no IP headers present."""
        from packages.shared.api_helpers import _extract_client_identity

        identity = _extract_client_identity({})
        assert identity == "anonymous"

    def test_falls_back_to_anonymous_with_empty_headers(self):
        """Falls back to 'anonymous' when headers are empty."""
        from packages.shared.api_helpers import _extract_client_identity

        identity = _extract_client_identity({"__ow_headers": {}})
        assert identity == "anonymous"


# ---------------------------------------------------------------------------
# Request Size Check Tests
# ---------------------------------------------------------------------------

class TestRequestSizeCheck:
    """Verify request body size enforcement."""

    def test_allows_normal_request(self):
        """Normal-sized requests return None (no error)."""
        from packages.shared.api_helpers import check_request_size

        result = check_request_size({
            "__ow_body": json.dumps({"season": 2025}),
            "__ow_headers": {},
        })
        assert result is None

    def test_allows_empty_body(self):
        """Requests with no body return None."""
        from packages.shared.api_helpers import check_request_size

        result = check_request_size({})
        assert result is None

    def test_allows_empty_string_body(self):
        """Requests with empty string body return None."""
        from packages.shared.api_helpers import check_request_size

        result = check_request_size({"__ow_body": ""})
        assert result is None

    def test_rejects_oversized_body_via_content_length(self):
        """Rejects request when Content-Length header exceeds limit."""
        from packages.shared.api_helpers import check_request_size

        result = check_request_size(
            {
                "__ow_body": "{}",
                "__ow_headers": {"content-length": "99999999"},
            },
            max_bytes=1000,
        )

        assert result is not None
        assert result["statusCode"] == 413
        assert "too large" in result["body"]["error"].lower()

    def test_rejects_oversized_body_via_raw_body_size(self):
        """Rejects request when raw body string exceeds limit."""
        from packages.shared.api_helpers import check_request_size

        # Create a body that exceeds 100 bytes
        big_body = "x" * 200
        result = check_request_size(
            {"__ow_body": big_body, "__ow_headers": {}},
            max_bytes=100,
        )

        assert result is not None
        assert result["statusCode"] == 413

    def test_allows_body_exactly_at_limit(self):
        """Allows a request body exactly at the max size."""
        from packages.shared.api_helpers import check_request_size

        # Create a body exactly at the limit
        exact_body = "x" * 100
        result = check_request_size(
            {"__ow_body": exact_body, "__ow_headers": {}},
            max_bytes=100,
        )

        assert result is None

    def test_rejects_body_one_byte_over_limit(self):
        """Rejects a request body one byte over the max size."""
        from packages.shared.api_helpers import check_request_size

        over_body = "x" * 101
        result = check_request_size(
            {"__ow_body": over_body, "__ow_headers": {}},
            max_bytes=100,
        )

        assert result is not None
        assert result["statusCode"] == 413

    def test_413_response_includes_security_headers(self):
        """413 responses include all security headers."""
        from packages.shared.api_helpers import check_request_size

        result = check_request_size(
            {"__ow_body": "x" * 200, "__ow_headers": {}},
            max_bytes=100,
        )

        assert result["headers"]["X-Content-Type-Options"] == "nosniff"
        assert result["headers"]["X-Frame-Options"] == "DENY"
        assert result["headers"]["Strict-Transport-Security"] == (
            "max-age=63072000; includeSubDomains; preload"
        )

    def test_uses_default_max_bytes_from_settings(self):
        """Uses settings.max_request_body_bytes when max_bytes not provided."""
        from packages.shared.api_helpers import check_request_size

        # Small body should be fine with the 5MB default
        result = check_request_size({"__ow_body": "small", "__ow_headers": {}})

        assert result is None

    def test_content_length_with_invalid_value_falls_back_to_body_check(self):
        """Invalid Content-Length value falls back to body size check."""
        from packages.shared.api_helpers import check_request_size

        result = check_request_size(
            {
                "__ow_body": "small",
                "__ow_headers": {"content-length": "not-a-number"},
            },
            max_bytes=1000,
        )

        # Should still be fine because body is small
        assert result is None

    def test_content_length_header_case_insensitive(self):
        """Both content-length and Content-Length headers are checked."""
        from packages.shared.api_helpers import check_request_size

        # With lowercase
        result = check_request_size(
            {
                "__ow_body": "{}",
                "__ow_headers": {"content-length": "9999"},
            },
            max_bytes=100,
        )
        assert result is not None
        assert result["statusCode"] == 413

        # With title case
        result2 = check_request_size(
            {
                "__ow_body": "{}",
                "__ow_headers": {"Content-Length": "9999"},
            },
            max_bytes=100,
        )
        assert result2 is not None
        assert result2["statusCode"] == 413


# ---------------------------------------------------------------------------
# Config Settings Tests
# ---------------------------------------------------------------------------

class TestSecurityConfig:
    """Verify security-related config settings exist with correct defaults."""

    def test_rate_limit_max_requests_default(self):
        """rate_limit_max_requests defaults to 60."""
        from packages.shared.config import settings

        assert settings.rate_limit_max_requests == 60

    def test_rate_limit_window_seconds_default(self):
        """rate_limit_window_seconds defaults to 60."""
        from packages.shared.config import settings

        assert settings.rate_limit_window_seconds == 60

    def test_max_request_body_bytes_default(self):
        """max_request_body_bytes defaults to 5MB (5242880)."""
        from packages.shared.config import settings

        assert settings.max_request_body_bytes == 5242880
