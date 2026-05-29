"""Shared API helper functions for Digital Ocean Functions."""

import json
from urllib.parse import parse_qs

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


def response(status_code: int, data=None, error: str | None = None) -> dict:
    """Build a DO Function response dict."""
    body = {}
    if error:
        body = {"error": error}
    elif data is not None:
        body = data

    return {
        "statusCode": status_code,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": settings.cors_origins[0] if settings.cors_origins else "*",
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
