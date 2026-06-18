# WhatIsMyTip Security Model

> **Status:** Living document — populated incrementally.  Currently cross-links to the auth + rate-limit references scattered across the codebase; future Phase 7+ work will consolidate the full model here.

## Current cross-links

| Topic | Reference |
|-------|-----------|
| Public vs admin endpoints | [`docs/api.md`](api.md#authentication) |
| `X-API-Key` flow (header, env var, server-side check) | [`docs/api.md`](api.md#admin) and [`backend/app/core/security.py`](../backend/app/core/security.py:1) |
| Per-IP rate limits (general + slowapi per-route) | [`docs/api.md`](api.md#rate-limiting) and [`backend/app/core/rate_limit.py`](../backend/app/core/rate_limit.py:1) |
| Security response headers (`X-Content-Type-Options`, CSP, etc.) | [`backend/app/core/middleware.py`](../backend/app/core/middleware.py:1) |
| Request-size cap | [`backend/app/core/middleware.py`](../backend/app/core/middleware.py:1) |
| Error sanitization (no stack-trace leakage) | [`backend/main.py`](../backend/main.py:1) and [`docs/backend.md`](backend.md#error-handling) |
| Secret management (env-var only, never committed) | [`backend/.env.example`](../backend/.env.example) and the deploy section of [`docs/deployment.md`](deployment.md#step-4-configure-environment-variables) |
