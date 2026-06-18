"""Shared API helper functions for Digital Ocean Functions."""

import json
import logging
import os
import secrets
import sys
from datetime import datetime, timezone
from typing import Optional, Tuple
from urllib.parse import parse_qs

from pydantic import BaseModel, ValidationError

from packages.shared.config import settings

logger = logging.getLogger(__name__)

# Maximum path segment length to cap response sizes (prevents abuse of 404 paths)
_MAX_PATH_SEGMENT_LENGTH = 128


def _ensure_shared_on_path() -> None:
    """Ensure the packages root is on ``sys.path`` (idempotent).

    DO Functions need the ``packages/`` root on ``sys.path`` to resolve
    ``packages.shared.*`` imports.  Calling this multiple times is safe —
    it skips insertion when the path is already present.
    """
    packages_root = os.path.join(os.path.dirname(__file__), "..", "..")
    packages_root = os.path.normpath(packages_root)
    if packages_root not in sys.path:
        sys.path.insert(0, packages_root)


_ensure_shared_on_path()


def parse_request(args: dict) -> tuple:
    """Parse DO Function args into (method, path, query, body, headers).

    The returned tuple also indicates whether the body was malformed JSON
    via ``_body_parse_error: str`` in the query dict (a sentinel key).
    Callers should check for ``query.get("_body_parse_error")`` and
    return 400 if present.
    """
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
                # Signal to callers that the body was malformed JSON
                body = {}
                query["_body_parse_error"] = "Invalid JSON body"
        elif isinstance(body_raw, dict):
            body = body_raw

    return method, path, query, body, headers


# SEC-LO-001: the legacy `verify_api_key` helper was removed.  It
# accepted the API key from the query string and the request body,
# which is dangerous because the value ends up in nginx access logs,
# server-side request logs, and browser history.  The header-only
# `app.core.security.verify_api_key` should be used instead.


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
    allowed_methods: list[str] | None = None,
) -> dict:
    """Build a DO Function response dict with proper CORS headers.

    Args:
        status_code: HTTP status code.
        data: Response body data (used when no error).
        error: Error message string.
        request_args: Original DO Function args (used for CORS origin matching).
        allowed_methods: CORS Allow-Methods value.  When ``None`` (the
            default), the response advertises ``["OPTIONS"]`` only —
            preflight only, no actual data operations.  SEC-LO-001
            removes the legacy ``["GET", "POST", "OPTIONS"]`` wildcard
            default so a misconfigured route that forgets to pass an
            explicit list is safe by default: the browser will reject
            non-OPTIONS methods via the
            ``Access-Control-Allow-Methods`` header.
    """
    body = {}
    if error:
        body = {"error": error}
    elif data is not None:
        body = data

    origin = _resolve_cors_origin(request_args)
    # SEC-LO-001: default to preflight-only.  Callers that serve data
    # must opt in explicitly with ``allowed_methods=["GET", "POST", ...]``.
    methods = ", ".join(allowed_methods or ["OPTIONS"])

    headers = {
        "Content-Type": "application/json",
        "Access-Control-Allow-Origin": origin,
        "Access-Control-Allow-Methods": methods,
        "Access-Control-Allow-Headers": "Content-Type, X-API-Key",
        "Access-Control-Max-Age": "600",
        # Security headers (OWASP recommended)
        "X-Content-Type-Options": "nosniff",
        "X-Frame-Options": "DENY",
        "Strict-Transport-Security": "max-age=63072000; includeSubDomains; preload",
        "Content-Security-Policy": "default-src 'none'; frame-ancestors 'none'",
        "Referrer-Policy": "strict-origin-when-cross-origin",
        "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
        "Cross-Origin-Opener-Policy": "same-origin",
    }

    # Add Retry-After header for rate-limited responses
    if status_code == 429:
        headers["Retry-After"] = str(settings.rate_limit_window_seconds)

    return {
        "statusCode": status_code,
        "headers": headers,
        "body": body,
    }


def segments(path: str) -> list[str]:
    """Split path into non-empty segments, capping per-segment length.

    Each segment is truncated to ``_MAX_PATH_SEGMENT_LENGTH`` characters
    to prevent abuse via excessively long paths in error responses.
    """
    return [s[:_MAX_PATH_SEGMENT_LENGTH] for s in path.split("/") if s]


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
    """Extract int from query params. Strips whitespace before parsing."""
    val = query.get(key)
    if val is None:
        return None
    try:
        return int(str(val).strip())
    except (ValueError, TypeError):
        return None


def bool_query(query: dict, key: str) -> bool:
    """Extract bool from query params. Strips whitespace before parsing."""
    val = str(query.get(key, "")).strip().lower()
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

    try:
        from packages.shared.cache import _get_client

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
        logger.warning("Rate limit check failed (allowing request): %s", exc)

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
    body_size = (
        len(body_raw.encode("utf-8"))
        if isinstance(body_raw, str)
        else len(str(body_raw).encode("utf-8"))
    )
    if body_size > max_bytes:
        return response(413, error="Request body too large", request_args=args)

    return None


def validate_request(
    body: dict,
    model_class: type[BaseModel],
) -> Tuple[BaseModel | None, dict | None]:
    """Validate request body against a Pydantic model.

    Args:
        body: Raw request body dictionary.
        model_class: Pydantic BaseModel subclass to validate against.

    Returns:
        (validated_model, None) on success.
        (None, error_response_dict) on validation failure (422 status).
    """
    try:
        validated = model_class.model_validate(body)
        return validated, None
    except ValidationError as exc:
        errors = []
        for err in exc.errors():
            loc = ".".join(str(part) for part in err.get("loc", []))
            errors.append(
                {
                    "field": loc,
                    "message": err.get("msg", "Validation error"),
                    "type": err.get("type", "value_error"),
                }
            )
        return None, response(422, data={"error": "Validation failed", "errors": errors})


# ---------------------------------------------------------------------------
# Health check (shared across all API functions)
# ---------------------------------------------------------------------------


async def handle_health(request_args: dict | None = None) -> dict:
    """Check PostgreSQL and Redis connectivity.

    Returns 200 with ``status: healthy`` when both services are reachable,
    or 503 with ``status: degraded`` when any check fails.

    This helper can be used in every API function's ``main()`` entry point
    via a ``GET /health`` route.
    """
    from sqlalchemy import text

    from packages.shared.cache import _get_client
    from packages.shared.db import get_engine

    checks: dict[str, str] = {}
    healthy = True

    # --- DB check ---
    try:
        engine = get_engine()
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        checks["db"] = "ok"
    except Exception as exc:
        checks["db"] = f"error: {exc}"
        healthy = False

    # --- Redis check ---
    try:
        client = _get_client()
        await client.ping()  # type: ignore[reportReturnType]
        checks["redis"] = "ok"
    except Exception as exc:
        checks["redis"] = f"error: {exc}"
        healthy = False

    status_code = 200 if healthy else 503
    status = "healthy" if healthy else "degraded"

    return response(
        status_code,
        data={
            "status": status,
            "checks": checks,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        },
        request_args=request_args,
        allowed_methods=["GET", "OPTIONS"],
    )
