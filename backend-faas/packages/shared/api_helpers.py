"""Shared API helper functions for Digital Ocean Functions."""

import json
import logging
import secrets
import sys
import os
from urllib.parse import parse_qs
from typing import Optional

from packages.shared.config import settings

logger = logging.getLogger(__name__)


def parse_request(args: dict) -> tuple:
    """Parse DO Function args into (method, path, query, body, headers)."""
    method = args.get("__ow_method", "GET").upper()
    path = args.get("__ow_path", "/").strip("/")
    raw_query = args.get("__ow_query", "")
    if isinstance(raw_query, str) and raw_query:
        parsed = parse_qs(raw_query)
        query = {k: v[0] if len(v) == 1 else v for k, v in parsed.items()}
    elif isinstance(raw_query, dict):
        query = raw_query
    else:
        query = {}
    body_raw = args.get("__ow_body", "")
    headers = args.get("__ow_headers", {}) or {}

    body: dict = {}
    if body_raw:
        if isinstance(body_raw, str):
            try:
                body = json.loads(body_raw)
            except json.JSONDecodeError:
                body = {}
        elif isinstance(body_raw, dict):
            body = body_raw

    return method, path, query, body, headers


def verify_api_key(headers: dict, query: dict | None = None, body: dict | None = None) -> bool:
    """Verify the API key from request headers, query params, or body.

    Uses secrets.compare_digest() for timing-attack resistance.

    Args:
        headers: Request headers dict.
        query: Optional query parameters dict.
        body: Optional request body dict.

    Returns:
        True if the API key matches the configured ADMIN_API_KEY.
    """
    api_key: Optional[str] = None

    # Check headers (case-insensitive via lower() comparison)
    if headers:
        api_key = headers.get("x-api-key") or headers.get("X-Api-Key")

    # Check query params
    if not api_key and query:
        api_key = query.get("api_key")

    # Check body
    if not api_key and body:
        api_key = body.get("api_key")

    if not api_key or not settings.admin_api_key:
        return False

    return secrets.compare_digest(api_key, settings.admin_api_key)


def _resolve_cors_origin(request_args: dict | None = None) -> str:
    """Determine the Access-Control-Allow-Origin value for a response.

    Checks the request's Origin header against the configured allowed
    origins list. Returns the matching origin or '*' if no match / no
    configured origins.
    """
    if not request_args:
        return settings.cors_origins[0] if settings.cors_origins else "*"

    headers = request_args.get("__ow_headers", {}) or {}
    request_origin = headers.get("origin") or headers.get("Origin", "")

    if request_origin:
        allowed = settings.cors_origins_list
        if allowed and request_origin in allowed:
            return request_origin

    return settings.cors_origins[0] if settings.cors_origins else "*"


def response(
    status_code: int,
    data=None,
    error: str | None = None,
    request_args: dict | None = None,
) -> dict:
    """Build a DO Function response dict with proper CORS headers.

    Args:
        status_code: HTTP status code.
        data: Response body data (used when no error).
        error: Error message string.
        request_args: Original DO Function args (used for CORS origin matching).
    """
    body = {}
    if error:
        body = {"error": error}
    elif data is not None:
        body = data

    origin = _resolve_cors_origin(request_args)

    return {
        "statusCode": status_code,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": origin,
            "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
            "Access-Control-Allow-Headers": "Content-Type, X-API-Key",
            # Security headers (OWASP recommended)
            "X-Content-Type-Options": "nosniff",
            "X-Frame-Options": "DENY",
            "Strict-Transport-Security": "max-age=63072000; includeSubDomains; preload",
            "Content-Security-Policy": "default-src 'none'; frame-ancestors 'none'",
            "Referrer-Policy": "strict-origin-when-cross-origin",
            "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
            "Cross-Origin-Opener-Policy": "same-origin",
        },
        "body": body,
    }


def segments(path: str) -> list[str]:
    """Split path into non-empty segments."""
    return [s for s in path.split("/") if s]


def to_dict(obj):
    """Recursively convert Pydantic models / lists to JSON-safe dicts."""
    if obj is None:
        return None
    if hasattr(obj, "model_dump"):
        return obj.model_dump(mode="json")
    if isinstance(obj, list):
        return [to_dict(item) for item in obj]
    if isinstance(obj, dict):
        return {k: to_dict(v) for k, v in obj.items()}
    return obj


def int_query(query: dict, key: str) -> int | None:
    """Extract int from query params."""
    val = query.get(key)
    if val is None:
        return None
    try:
        return int(val)
    except (ValueError, TypeError):
        return None


def bool_query(query: dict, key: str) -> bool:
    """Extract bool from query params."""
    val = query.get(key, "").lower()
    return val in ("true", "1", "yes")


def _extract_client_identity(body: dict) -> str:
    """Extract a client identity from the request for rate limiting.

    Prefers the forwarded IP from __ow_headers, then API key, then
    falls back to 'anonymous'.
    """
    headers = body.get("__ow_headers") if isinstance(body, dict) else None
    if headers and isinstance(headers, dict):
        # Check common forwarding headers
        for header in ("x-forwarded-for", "x-real-ip", "cf-connecting-ip"):
            ip = headers.get(header) or headers.get(header.title())
            if ip:
                # x-forwarded-for may contain multiple IPs; use the first
                return str(ip).split(",")[0].strip()
    return "anonymous"


async def check_rate_limit(
    args: dict,
    max_requests: int | None = None,
    window_seconds: int | None = None,
) -> dict | None:
    """Check rate limit using Redis sliding window counter.

    Uses Redis INCR + EXPIRE for a fixed-window counter per identity.

    Args:
        args: The raw DO Function args dict (used to extract client identity).
        max_requests: Maximum requests allowed in the window. Defaults to
            ``settings.rate_limit_max_requests``.
        window_seconds: Window duration in seconds. Defaults to
            ``settings.rate_limit_window_seconds``.

    Returns:
        ``None`` if the request is within limits, or a 429 response dict
        if the limit has been exceeded.
    """
    if max_requests is None:
        max_requests = settings.rate_limit_max_requests
    if window_seconds is None:
        window_seconds = settings.rate_limit_window_seconds

    identity = _extract_client_identity(args)

    # Make shared package importable for cache module
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

    try:
        from packages.shared.cache import _get_client
        import redis.asyncio as redis

        client = _get_client()
        key = f"wimt:ratelimit:{identity}:{window_seconds}"
        count = await client.incr(key)
        if count == 1:
            await client.expire(key, window_seconds)
        if count > max_requests:
            return response(
                429,
                error="Rate limit exceeded. Please try again later.",
                request_args=args,
            )
    except Exception as exc:
        # Fall back gracefully — allow the request if Redis is unavailable
        logger.warning(f"Rate limit check failed (allowing request): {exc}")

    return None


def check_request_size(args: dict, max_bytes: int | None = None) -> dict | None:
    """Check request body size.

    Args:
        args: The raw DO Function args dict.
        max_bytes: Maximum allowed body size in bytes. Defaults to
            ``settings.max_request_body_bytes``.

    Returns:
        ``None`` if the request is within limits, or a 413 response dict
        if the body exceeds the maximum size.
    """
    if max_bytes is None:
        max_bytes = settings.max_request_body_bytes

    body_raw = args.get("__ow_body", "")
    if not body_raw:
        return None

    # Check Content-Length header if available
    headers = args.get("__ow_headers", {}) or {}
    content_length = headers.get("content-length") or headers.get("Content-Length")
    if content_length:
        try:
            if int(content_length) > max_bytes:
                return response(413, error="Request body too large", request_args=args)
        except (ValueError, TypeError):
            pass

    # Estimate size from the raw body string
    body_size = len(body_raw.encode("utf-8")) if isinstance(body_raw, str) else len(str(body_raw).encode("utf-8"))
    if body_size > max_bytes:
        return response(413, error="Request body too large", request_args=args)

    return None
