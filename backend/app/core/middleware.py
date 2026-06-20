"""Pure ASGI middleware for the FastAPI app.

Three middlewares are exported:

* :class:`SecurityHeadersMiddleware` — OWASP-recommended response headers.
* :class:`RequestSizeLimitMiddleware` — caps request body size.
* :class:`RequestIDMiddleware` — UUID4 per request, attached to
  ``request.state.request_id`` and echoed via the ``X-Request-ID`` header.
"""

from __future__ import annotations

import json
import re
import secrets
import uuid
from typing import Awaitable, Callable

from fastapi import Request
from starlette.datastructures import State
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from packages.shared.config import settings


# ---------------------------------------------------------------------------
# X-Request-ID validation
# ---------------------------------------------------------------------------
#
# Inbound ``X-Request-ID`` values are untrusted: an attacker can put
# anything in the header, including CRLF sequences that would let
# them forge log lines or smuggle additional headers, or very long
# values that blow up log indexers.  We therefore reject any value
# outside the allow-list ``[A-Za-z0-9_-]{1,128}`` and replace it with
# a fresh UUID4.  UUID4 itself is 36 chars and matches the
# allow-list, so the canonical path is always safe.
_REQUEST_ID_ALLOWED = re.compile(r"^[A-Za-z0-9_\-]{1,128}$")


def _is_valid_request_id(value: str) -> bool:
    """Return True iff ``value`` is a safe inbound ``X-Request-ID``."""
    return bool(_REQUEST_ID_ALLOWED.match(value))


# ---------------------------------------------------------------------------
# Security headers
# ---------------------------------------------------------------------------


def generate_csp_nonce() -> str:
    """Return a fresh, URL-safe CSP nonce for per-request inline-style hardening.

    Returns:
        A 32-character URL-safe base64 string (``[A-Za-z0-9_-]+``) that
        is safe to embed in a ``Content-Security-Policy`` header source
        list.  Two calls always return distinct values.

    Note:
        SEC-ME-006 adds this helper as **structural** support for a
        future Nuxt-side change.  The current ``_DEFAULT_CSP_TEMPLATE``
        still ships ``'unsafe-inline'`` for ``style-src`` so the existing
        Nuxt build (which emits inline ``<style>`` blocks at build time)
        continues to work without coordination.  When the Nuxt side is
        updated to read a nonce from the response header / request
        state, the inline policy can be replaced with
        ``style-src 'self' 'nonce-{nonce}'`` in one line.  The template
        below includes a ``{nonce}`` placeholder for that change.
    """
    return secrets.token_urlsafe(24)


# CSP allows the Nuxt frontend's inline <style> tags.  The ``{nonce}``
# placeholder is currently replaced with a sentinel by ``_DEFAULT_CSP``
# below; future SEC-ME-006 work can switch to a per-request nonce in
# the SecurityHeadersMiddleware ``send_with_headers`` closure without
# touching this template.
_DEFAULT_CSP_TEMPLATE = (
    "default-src 'self'; "
    "img-src 'self' data:; "  # SEC-ME-006: dropped the ``https:`` wildcard
    "style-src 'self' 'unsafe-inline' 'nonce-{nonce}'; "
    "script-src 'self'; "
    "connect-src 'self'; "
    "object-src 'none'; "
    "base-uri 'self'; "
    "form-action 'self'; "
    "frame-ancestors 'none'"
)

# The actual CSP sent today: we use a stable sentinel for ``{nonce}`` so
# the current static policy is still parsable, but the template above
# preserves the placeholder for the future per-request swap.
_DEFAULT_CSP = _DEFAULT_CSP_TEMPLATE.format(nonce="__future__")

_SECURITY_HEADERS: dict[str, str] = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
    "Cross-Origin-Opener-Policy": "same-origin",
    "Content-Security-Policy": _DEFAULT_CSP,
}


class SecurityHeadersMiddleware:
    """Add OWASP-recommended security headers to every response.

    HSTS is only added for HTTPS requests (per the HSTS specification —
    browsers must ignore it on plain HTTP).
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        is_https = scope.get("scheme") == "https"

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                existing = {k.lower() for k, _ in headers}
                for name, value in _SECURITY_HEADERS.items():
                    if name.lower() not in existing:
                        headers.append((name.encode("latin-1"), value.encode("latin-1")))
                if is_https and b"strict-transport-security" not in {
                    k.lower() for k, _ in headers
                }:
                    headers.append(
                        (
                            b"strict-transport-security",
                            b"max-age=31536000; includeSubDomains",
                        )
                    )
                message["headers"] = headers
            await send(message)

        await self.app(scope, receive, send_with_headers)


# ---------------------------------------------------------------------------
# Request size limit
# ---------------------------------------------------------------------------


def _make_error_response(status_code: int, code: str, message: str) -> dict:
    """Build a minimal ASGI ``http.response.start`` + body payload."""
    body = json.dumps({"code": code, "message": message}).encode("utf-8")
    return {
        "status_code": status_code,
        "headers": [
            (b"content-type", b"application/json"),
            (b"content-length", str(len(body)).encode("latin-1")),
        ],
        "body": body,
    }


class RequestSizeLimitMiddleware:
    """Cap request body size.

    * Requests with a ``Content-Length`` header above the limit are
      rejected with **413 Payload Too Large** before the body is read.
    * Requests using chunked transfer (no ``Content-Length``) are
      streamed; if the accumulated body exceeds the limit, the connection
      is aborted with **422 Unprocessable Entity**.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        max_bytes = settings.max_request_body_bytes

        # Inspect Content-Length
        headers = dict(scope.get("headers") or [])
        content_length_raw = headers.get(b"content-length")
        if content_length_raw is not None:
            try:
                content_length = int(content_length_raw.decode("latin-1"))
            except ValueError:
                content_length = 0
            if content_length > max_bytes:
                payload = _make_error_response(
                    413,
                    "payload_too_large",
                    f"Request body exceeds {max_bytes} bytes",
                )
                await send(
                    {
                        "type": "http.response.start",
                        "status": payload["status_code"],
                        "headers": payload["headers"],
                    }
                )
                await send(
                    {
                        "type": "http.response.body",
                        "body": payload["body"],
                        "more_body": False,
                    }
                )
                return

        # For chunked transfer, count bytes as they arrive.
        body_bytes = 0
        aborted = False

        async def guarded_receive() -> Message:
            nonlocal body_bytes, aborted
            if aborted:
                # Drain the message but tell the downstream to stop
                return {"type": "http.disconnect"}
            message = await receive()
            if message["type"] == "http.request":
                chunk = message.get("body", b"") or b""
                body_bytes += len(chunk)
                if body_bytes > max_bytes:
                    aborted = True
                    # Reject with 422
                    payload = _make_error_response(
                        422,
                        "payload_too_large",
                        f"Chunked request body exceeds {max_bytes} bytes",
                    )
                    await send(
                        {
                            "type": "http.response.start",
                            "status": payload["status_code"],
                            "headers": payload["headers"],
                        }
                    )
                    await send(
                        {
                            "type": "http.response.body",
                            "body": payload["body"],
                            "more_body": False,
                        }
                    )
                    return {"type": "http.disconnect"}
            return message

        await self.app(scope, guarded_receive, send)


# ---------------------------------------------------------------------------
# Request ID
# ---------------------------------------------------------------------------


class RequestIDMiddleware:
    """Attach a UUID4 to every request.

    The ID is stored on ``request.state.request_id`` and echoed to the
    client via the ``X-Request-ID`` response header.  If the client
    supplies ``X-Request-ID`` (e.g. an upstream proxy), it is reused —
    otherwise a fresh UUID4 is generated.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        # Reuse upstream X-Request-ID if present, but ONLY if it
        # matches the allow-list ``^[A-Za-z0-9_-]{1,128}$``.  Any
        # other value (CRLF, control chars, overlong, non-ASCII,
        # anything outside the allow-list) MUST be replaced with a
        # fresh UUID4 — otherwise the value would flow into log
        # lines and could be used to forge log entries or smuggle
        # headers.
        headers = dict(scope.get("headers") or [])
        request_id_raw = headers.get(b"x-request-id")
        if request_id_raw:
            try:
                candidate = request_id_raw.decode("latin-1")
            except UnicodeDecodeError:
                candidate = ""
            if _is_valid_request_id(candidate):
                request_id = candidate
            else:
                request_id = str(uuid.uuid4())
        else:
            request_id = str(uuid.uuid4())

        # ``request.state`` is backed by ``scope["state"]``, which must be
        # a :class:`starlette.datastructures.State` instance (or anything
        # supporting attribute access) for ``request.state.request_id`` to
        # work inside route handlers.
        state = scope.setdefault("state", State())
        if not hasattr(state, "_state") or not isinstance(
            getattr(state, "_state", None), dict
        ):
            # Replace any non-State placeholder with a real State.
            state = State()
            scope["state"] = state
        setattr(state, "request_id", request_id)

        async def send_with_request_id(message: Message) -> None:
            if message["type"] == "http.response.start":
                hdrs = list(message.get("headers", []))
                hdrs.append((b"x-request-id", request_id.encode("latin-1")))
                message["headers"] = hdrs
            await send(message)

        await self.app(scope, receive, send_with_request_id)


# ---------------------------------------------------------------------------
# Helper dependency
# ---------------------------------------------------------------------------


def get_request_id(request: Request) -> str:
    """FastAPI dependency that returns the current request's ID.

    Returns the value set by :class:`RequestIDMiddleware` on
    ``request.state.request_id``.  When the middleware isn't installed
    (e.g. in unit tests that exercise a route handler directly), a
    fresh UUID4 is generated so downstream logging / response
    headers always carry a unique identifier — the previous
    ``"test-request-id"`` placeholder collided across every test in
    a single run, making it impossible to correlate logs back to a
    specific request.
    """
    rid = getattr(request.state, "request_id", None)
    if rid is None:
        rid = str(uuid.uuid4())
        # Stash it on the state so any later access (within the same
        # request) returns the same value rather than minting a new one.
        try:
            request.state.request_id = rid
        except (AttributeError, TypeError):
            pass
    return rid
