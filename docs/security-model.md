# Security Model

How authentication, authorization, rate limiting, and secrets are
handled in the WhatIsMyTip backend. For the API surface see
[`docs/api.md`](api.md); for the production runbook see
[`docs/operations.md`](operations.md).

## Authentication

The only authentication mechanism is a single static **admin API key**.

| Property | Value |
|----------|-------|
| Header name | `X-API-Key` |
| Value | `ADMIN_API_KEY` env var (any non-empty string) |
| Algorithm | constant-time compare (`secrets.compare_digest`) |
| Failure code | `401 invalid_api_key` |
| Implementation | [`backend/app/core/security.py`](../backend/app/core/security.py:1) |
| Mounted at | Router level on `admin` router — cannot be bypassed by adding a new route |

### Key generation

```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

### Scope

The `X-API-Key` is the **only** authentication boundary. There is **no
user authentication, no JWT, no OAuth, no per-endpoint roles** —
endpoints are either public, admin-only, or non-existent.

## Authorization

Authorization is binary: **public or admin-only**. Admin-only endpoints
are mounted on the `admin` router with `require_admin_key` applied at
the router level.

| Category | Auth |
|----------|------|
| `GET /health` | Public |
| `GET /api/games`, `/api/games/{slug}`, `/api/games/{slug}/detail` | Public |
| `GET /api/tips`, `/api/tips/games-with-tips`, `/api/tips/{heuristic}` | Public |
| `POST /api/tips/generate` | **Public, rate-limited** (see below) |
| `GET /api/backtest*` | Public |
| `POST /api/backtest/run` | **Admin only** |
| `POST /api/admin/{job}/trigger` | **Admin only** |
| `GET /api/admin/{job}/progress` | **Admin only** |
| `GET /api/admin/metrics` | **Admin only** |
| `GET /docs`, `/redoc`, `/openapi.json` | Public |

`/api/tips/generate` is intentionally public and rate-limited: any
caller may trigger tip generation for a season/round that has no tips
yet.  This avoids forcing the cron to be the only path that ever
produces tips.

## Rate Limiting

Enforced by [`backend/app/core/rate_limit.py`](../backend/app/core/rate_limit.py:1)
(slowapi with a Redis storage backend in production so limits are
shared across instances).

| Scope | Limit | Window | Per |
|-------|-------|--------|-----|
| All endpoints (default) | `RATE_LIMIT_MAX_REQUESTS` (60) | `RATE_LIMIT_WINDOW_SECONDS` (60 s) | Client IP |
| `POST /api/tips/generate` | 10 requests | 60 s | Client IP (overrides default) |
| `POST /api/admin/{job}/trigger` | Uses default | 60 s | Client IP |
| `POST /api/backtest/run` | Uses default | 60 s | Client IP |

The limit applies **before** auth — an unauthenticated flood of
admin-endpoint requests still hits the rate limit and returns `429`.

## Public Endpoints (no auth)

These endpoints are intentionally public:

- `GET /health`
- `GET /api/games*` (game data, tips, predictions, weather, analysis)
- `GET /api/tips*` (read-only tip listings)
- `GET /api/backtest/*` (read-only)
- `POST /api/tips/generate` (rate-limited, see above)
- `GET /docs`, `/redoc`, `/openapi.json`

## Secrets

| Secret | Where it lives | Rotation cadence |
|--------|---------------|------------------|
| `ADMIN_API_KEY` | App Platform env vars (per component) | On operator departure, on suspected exposure, or quarterly. Update via `doctl apps update` or dashboard → redeploy. |
| `DATABASE_URL` (incl. password) | App Platform env vars | On DB password reset (manual via DO dashboard). |
| `REDIS_URL` | App Platform env vars | On Redis credential rotation. |
| `OPENROUTER_API_KEY` | App Platform env vars | On OpenRouter dashboard → regenerate. |
| `SQUIGGLE_CONTACT_EMAIL` | App Platform env vars (not secret, identifies us to Squiggle) | n/a |
| `ALERT_WEBHOOK_URL` | App Platform env vars (may include signing secret in the URL) | On webhook consumer rotation. |

### Rotation checklist

1. Generate the new credential out-of-band (e.g. in a fresh
   `doctl apps env set` for App Platform, or the upstream dashboard).
2. Update the env var on the **staging** app first; redeploy; verify
   `GET /health` and a single admin trigger work.
3. Update the **production** env var; redeploy via
   [`backend/scripts/deploy.sh`](../backend/scripts/deploy.sh:1).
4. Revoke the old credential upstream.
5. Watch `/api/admin/metrics` and the app logs for the next hour.

> App Platform env-var updates are **not** zero-downtime for the api
> component (the container restarts).  Schedule rotations during a
> quiet window, or expect ~10-30 s of `/api/*` 502s.

## TLS Posture

| Connection | TLS | Notes |
|------------|-----|-------|
| Browser → nginx (`whatismytip.com`) | Yes (App Platform managed cert) | Free Let's Encrypt cert, auto-renewed |
| nginx → FastAPI (`api:8000`) | **No** (private App Platform network) | Both components share the App Platform private network; TLS would add latency without security benefit. |
| FastAPI → managed PostgreSQL | Yes | `?ssl=require` is appended to `DATABASE_URL` for managed DBs.  The connection layer rejects non-TLS. |
| FastAPI → managed Redis | Yes (TLS) | Connection string uses `rediss://` for managed Redis. |
| FastAPI → Squiggle API (`https://api.squiggle.com.au`) | Yes | Outbound HTTPS only. |
| FastAPI → OpenRouter (`https://openrouter.ai/api/v1`) | Yes | Outbound HTTPS only. |
| FastAPI → Open-Meteo (weather) | Yes | Outbound HTTPS only. |

## Audit Logging

WhatIsMyTip does **not** maintain a separate audit log.  All security-
relevant events are recorded in the standard application logs and
in the database:

| Event | Where |
|-------|-------|
| Failed admin auth (401) | App logs (per-request line + `X-API-Key` *not* logged) |
| Admin job trigger | `job_executions` table (start/end timestamps, status, error) + app logs |
| Backtest run | `job_executions` table + app logs |
| Rate-limit exceeded (429) | App logs (path + IP, no headers / bodies) |
| Tip generation | `job_executions` table + app logs |
| Health degraded | App logs at WARNING |

The `job_executions` table is retained for `METRICS_RETENTION_DAYS`
(30 days by default; see [`backend/packages/shared/config.py`](../backend/packages/shared/config.py:93)).

## Headers

Emitted by [`backend/app/core/middleware.py`](../backend/app/core/middleware.py:1):

| Header | Value |
|--------|-------|
| `Content-Security-Policy` | Allows self, analytics host, inline styles (no inline scripts) |
| `Referrer-Policy` | `strict-origin-when-cross-origin` |
| `X-Content-Type-Options` | `nosniff` |
| `X-Frame-Options` | `DENY` |
| `Permissions-Policy` | Locked down (geolocation, camera, microphone all denied) |
| `X-XSS-Protection` | **Not sent** (deprecated) |
| `Strict-Transport-Security` | Delegated to App Platform / nginx |

## CORS

`CORS_ORIGINS` is a comma-separated list of allowed origins.  Default
in dev: `http://localhost:3000, http://127.0.0.1:3000`.  Production:
`https://whatismytip.com,https://www.whatismytip.com`.  Empty list is
rejected at startup.

## Request Size

`MAX_REQUEST_BODY_BYTES` defaults to 5 MiB.  Requests larger than this
are rejected with `413` before the body is read.

## Reporting a Vulnerability

Open a **private** GitHub issue or email the maintainer (see README
author section).  Do not post details publicly until a fix lands.
