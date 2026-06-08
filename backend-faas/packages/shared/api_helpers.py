"""Shared API helper functions for Digital Ocean Functions."""

import json
import secrets
from urllib.parse import parse_qs
from typing import Optional

from packages.shared.config import settings


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
