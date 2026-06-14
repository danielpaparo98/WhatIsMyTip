# backend Code Review — Comprehensive Comparison vs Original Backend

**Date**: 2026-06-13
**Scope**: `backend/` DigitalOcean Functions implementation vs the original `backend/` FastAPI monolith.
**Audience**: Maintainers evaluating production-readiness, planning a cutover, or planning further work.

---

## 1. Executive Summary

The `backend` migration is, in aggregate, a **major engineering step forward**, not a regression. It introduces a real production database (PostgreSQL), a shared cache (Redis), structured logging, four new ML models, two new AFL data clients (AFLTables, FootyWire), a weather service, alerting, and a proper serverless deployment pipeline — none of which existed in the original. It also consolidates the original's 14 Alembic migrations into a single clean `0001_consolidated` baseline plus a targeted `0002_weather_players_injuries` migration, which is a significant quality-of-life improvement.

However, the migration **drops several defensive features** that the original FastAPI backend had, and a few of those drops are sharp enough to cause silent production failures (e.g., no FastAPI request validation, no built-in rate limiter, no automatic OpenAPI docs in prod, and reduced retry/error-classification fidelity in cron jobs). The conditional-⚠️ verdict in [`FAAS-EVALUATION.md`](../FAAS-EVALUATION.md:5) is correct: FaaS is viable, but it is **not yet a strict superset** of the original, and several findings in the evaluation have **already been fixed** while others remain.

### TL;DR Scorecard

| Dimension | Original | FaaS | Delta |
|---|---|---|---|
| **Surface area** | 5 routers, 1 middleware chain, 4 cron classes, 4 ML models, 1 data source (Squiggle) | 4 functions, 1 routing helper, 4 cron handlers, 8 ML models, 3 data sources (Squiggle + AFLTables + FootyWire + Open-Meteo) | ➕️ More capability |
| **Database** | SQLite via aiosqlite | PostgreSQL via asyncpg with `ON DELETE CASCADE` and proper indexes | ➕️ Production-grade |
| **Cache** | Process-local, three-tier in-memory | Redis with three TTL tiers and connection pooling | ➕️ Cross-instance |
| **Cron lifecycle** | In-process `asyncio` scheduler (`CronJobManager` + `BaseJob` with `retry_with_backoff` and error classification) | Per-function handlers with DB locks, no scheduler, no retry loop | ➖ Less machinery |
| **API validation** | Pydantic v2 `response_model` on every endpoint; auto OpenAPI; `slowapi` rate limiter; `SecurityHeadersMiddleware`; `RequestSizeLimitMiddleware` | `parse_request` + manual int/bool coercion + Pydantic only for admin triggers; per-function ad-hoc rate limiter via Redis; security headers in `response()` | ➖ Less automatic |
| **Tests** | 8 unit test files (cache, models, cron utils, heuristics, etc.) | 30+ unit test files + 3 integration test files; coverage gate | ➕️ Much stronger |
| **Observability** | Stdout logs | JSON-structured logs (`LOG_FORMAT=json`), `generate_execution_id()`, `AlertingService`, `/metrics` admin endpoint | ➕️ Much stronger |
| **Footprint** | numpy + scikit-learn (≈ 60 MB+) | Pure Python (no numpy/sklearn) | ➕️ Fits FaaS size cap |
| **Cost** | Long-running container | Pay-per-invocation + Redis + Postgres | ➕️ Cheaper at low scale |

---

## 2. What `backend` does better than the original

### 2.1 Database schema is production-shaped

[`backend/alembic/versions/2026_05_28_1613-0001_consolidated_postgresql_schema.py`](../backend/alembic/versions/2026_05_28_1613-0001_consolidated_postgresql_schema.py:21) creates all tables with:
- `TIMESTAMP WITH TIME ZONE` for `created_at`/`updated_at`/`started_at`/`completed_at` (the original used naive `DateTime` everywhere).
- `ON DELETE CASCADE` on every FK (the original has some FKs without cascade).
- Explicit `UNIQUE` constraints where they belong (e.g., `uq_game_heuristic`, `uq_game_model`).
- Sensible composite indexes (e.g., `ix_games_round_id`, `ix_games_season`).

The original has 14 migrations; the FaaS collapses them into 1 baseline + 1 feature migration. This is a major maintainability win.

### 2.2 Eight ML models vs four

[`backend/packages/shared/models_ml/__init__.py:11`](../backend/packages/shared/models_ml/__init__.py:11) adds four new models:

| New model | Purpose | Data source |
|---|---|---|
| `WeatherImpactModel` | Adjust prediction based on match-day weather | Open-Meteo API via [`WeatherClient`](../backend/packages/shared/weather/client.py:33) |
| `InjuryImpactModel` | Penalize teams missing key players | FootyWire via [`FootyWireClient`](../backend/packages/shared/afl_data/footywire_client.py:18) |
| `MatchupModel` | Head-to-head historical performance | AFLTables via [`AFLTablesClient`](../backend/packages/shared/afl_data/tables_client.py:18) |
| `PlayerFormModel` | Recent form of key players | AFLTables + FootyWire |

The new models are *registered* in [`orchestrator.py:39`](../backend/packages/shared/orchestrator.py:39) and the [consolidated migration `0002`](../backend/alembic/versions/2026_06_10_0600-0002_weather_players_injuries.py:86) adds the `match_weather`, `players`, `injuries`, and `team_selections` tables they need. This is genuinely additive capability.

### 2.3 Real structured logging

[`backend/packages/shared/logger.py:21`](../backend/packages/shared/logger.py:21) implements a `JsonFormatter` that emits single-line JSON when `LOG_FORMAT=json`. Every cron handler calls `generate_execution_id()` ([`logger.py:50`](../backend/packages/shared/logger.py:50)) and threads `execution_id` through `extra={...}` so logs are correlatable. The original just used `logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")`.

### 2.4 Real alerting

[`backend/packages/shared/alerting.py:13`](../backend/packages/shared/alerting.py:13) implements `AlertingService.send_alert()` / `send_failure_alert()` / `send_timeout_alert()`. Every cron handler calls `send_failure_alert` in its `except` block ([`historic-refresh/__init__.py:273`](../backend/packages/cron/historic-refresh/__init__.py:273), [`tip-generation/__init__.py:219`](../backend/packages/cron/tip-generation/__init__.py:219), etc.). The original's `alert_enabled` and `alert_webhook_url` were defined in config but **never consumed**.

### 2.5 Smart engine/redis lifecycle

[`backend/packages/shared/db.py:97`](../backend/packages/shared/db.py:97) `dispose_engine(force=False)` and [`cache.py:241`](../backend/packages/shared/cache.py:241) `close_redis_pool(force=False)` **only** actually close when `force=True` (i.e., on error). This preserves the engine singleton across warm invocations — the FAAS-EVALUATION §4.2 concern is resolved.

### 2.6 Cron execution tracking & API

[`packages/shared/crud/jobs.py`](../backend/packages/shared/crud/jobs.py) (used by every cron handler) plus [`packages/api/admin/__init__.py:327`](../backend/packages/api/admin/__init__.py:327) `/metrics` endpoint gives admin visibility into job success rates, durations, and item counts. The original had the same data in the DB but no API surface.

### 2.7 Security hardening in the FaaS shim

[`api_helpers.py:62`](../backend/packages/shared/api_helpers.py:62) `verify_api_key()` uses `secrets.compare_digest()` for timing-attack resistance. The original `backend/app/api/admin/jobs.py:18` used naive `x_api_key != settings.admin_api_key` — a real timing-attack vector. ✅ This is an upgrade.

### 2.8 CI/CD pipeline

[`backend/scripts/deploy.sh:66`](../backend/scripts/deploy.sh:66) runs tests, runs migrations, deploys via `doctl serverless deploy`, then verifies via the admin `/health` endpoint. The original has no deploy script in `scripts/`.

### 2.9 Test coverage is dramatically better

- Original: [`backend/tests/unit/`](../backend/tests/unit/) has 5 test files (cache, cron utils, heuristics, models, squiggle utils).
- FaaS: [`backend/tests/unit/`](../backend/tests/unit/) has **30+ test files** including tests for: every API function (`test_api_admin`, `test_api_backtest`, `test_api_games`, `test_api_tips`, `test_api_helpers_security`), every cron handler, the new ML models (`test_injury_impact_model`, `test_matchup_model`, `test_player_form_model`, `test_weather_impact_model`), the new data clients (`test_afl_tables_client`, `test_footywire_client`, `test_weather_client`), the cache, the migrations, the schemas, the structured logging, the exceptions, and the alerting.

The FaaS also has a 3-file integration test suite gated behind `--run-integration`.

---

## 3. Gaps — what `backend` is missing relative to the original

### 3.1 🔴 No automatic OpenAPI / Swagger UI in any environment

The original's [`backend/main.py:131`](../backend/main.py:131) registers a FastAPI app with `docs_url`, `redoc_url`, and `openapi_url` that automatically exposes:
- `/docs` (Swagger UI)
- `/redoc`
- `/openapi.json`

The FaaS functions cannot expose these — DO Functions is not a single persistent server, so there's no per-function OpenAPI surface. **Net result**: any developer trying to explore the API has to read the README. There's no machine-readable schema at all.

**Recommended fix**: Generate an OpenAPI document at build time from a single source of truth (probably a dedicated `api_spec.py` with Pydantic models + the route table from each `main()`), write it to `docs/api.openapi.json`, and check it into the repo. Then serve it via the existing static-docs workflow.

### 3.2 🔴 No FastAPI request/response validation

The original uses `response_model=TipListResponse` etc. on every endpoint — FastAPI automatically:
- Validates query params and bodies against Pydantic types.
- Returns `422 Unprocessable Entity` with detailed field errors on bad input.
- Strips unknown fields and coerces types safely.
- Generates the OpenAPI schema from the same models.

The FaaS uses [`int_query()`](../backend/packages/shared/api_helpers.py:180) / [`bool_query()`](../backend/packages/shared/api_helpers.py:191) helpers that return `None` or default on bad input. **There is no 422 path anywhere.** Bad input from a client either silently does the wrong thing (e.g., `season=foo` becomes `None` → 500 in the service) or gets a generic 500.

**Concrete example**: [`api/tips/__init__.py:65`](../backend/packages/api/tips/__init__.py:65) only validates `season` and `round` after coercing them. If a client sends `season=foo`, the coercion silently yields `None` and the handler returns 400 *for missing required query param* — but the client never learns that the *value* was wrong. That's worse than FastAPI's 422.

**Recommended fix**: Wrap every route handler in a `validate_request(merged, SomePydanticModel)` call (the pattern is already used for admin triggers). Define a `TipsQuery` Pydantic model for each GET endpoint and validate `query` against it.

### 3.3 🔴 No production request size limit for `POST /api/tips/generate` and the admin triggers

The original has [`RequestSizeLimitMiddleware`](../backend/main.py:118) with `max_size=5 * 1024 * 1024` (5 MB) applied to **all** routes. The FaaS has [`check_request_size`](../backend/packages/shared/api_helpers.py:262) which only checks `Content-Length` and the encoded body size. This *is* checked on every function, but:
- The function-side check is **5 MB** by default ([`config.py:85`](../backend/packages/shared/config.py:85) `max_request_body_bytes: int = 5242880`) — same limit, but enforced as a Python check after the request body is already in memory.
- A large multipart upload (which DO Functions would reject anyway) is still partially parsed by the platform before our check runs.

**Net assessment**: the practical limit is similar, but the FaaS can't fail-fast at the network edge the way FastAPI middleware can. If a malicious client streams a 1 GB body, the FaaS may pay for parsing it before our `check_request_size` returns 413. For a public API this is suboptimal.

### 3.4 🟠 No CORS preflight caching (`Access-Control-Max-Age`)

The FaaS [`response()`](../backend/packages/shared/api_helpers.py:116) sends CORS headers, but there's no `Access-Control-Max-Age`. Every preflight is a full round-trip. The original's `CORSMiddleware` adds this automatically with `max_age=600` by default. Small but real latency tax on every cold call.

**Recommended fix**: Add `"Access-Control-Max-Age": "600"` to the headers dict in [`response()`](../backend/packages/shared/api_helpers.py:116).

### 3.5 🟠 Rate limiting is per-function, not global, and easily bypassed

[`check_rate_limit`](../backend/packages/shared/api_helpers.py:214) keys on `_extract_client_identity(args)`, which inspects `x-forwarded-for`, `x-real-ip`, `cf-connecting-ip` and falls back to `"anonymous"`. **Every unauthenticated request from the same client gets bucketed to the same key**, but a determined attacker can:
- Spoof `x-forwarded-for` (DO Functions forwards what the client sends) — easy bypass.
- Send no headers → `"anonymous"` bucket — every unidentifiable client shares one 60/min budget.

The original's `slowapi` uses `get_remote_address`, which DO Functions' `__ow_headers` would also expose, but at least the FastAPI middleware is well-understood and integrates with reverse proxies that *strip* `x-forwarded-for` before the platform sees it.

**Recommended fix**:
1. Trust the platform's `X-Forwarded-For` only when the request comes from a known DO IP range; otherwise, ignore the header.
2. On bypass attempts, apply a stricter rate limit (defensive degrade).
3. Document that admin endpoints rate-limit by API key, not IP, to prevent accidental lockouts.

### 3.6 🟠 No `Retry-After` on 429

[`check_rate_limit`](../backend/packages/shared/api_helpers.py:214) returns a 429 with body `{"error": "Rate limit exceeded..."}` but **no `Retry-After` header**. Well-behaved clients (and many mobile SDKs) auto-back off when they see `Retry-After`. The original's `slowapi._rate_limit_exceeded_handler` adds it automatically.

**Recommended fix**: In the 429 response path, set `headers["Retry-After"] = str(window_seconds)`.

### 3.7 🟠 `BackendServiceError` doesn't exist; raw `Exception` propagation in some admin handlers

The original raises typed `HTTPException` everywhere; the FaaS builds a `response(500, error="Internal server error. Please try again later.")` for admin triggers ([`admin/__init__.py:108`](../backend/packages/api/admin/__init__.py:108)). That's fine for clients but means the **error stack is only in the logs** — clients can't distinguish "Squiggle API is down" from "your JSON body had a typo". Consider a stable error code (`"code": "UPSTREAM_SQUIGGLE_DOWN"`) attached to admin 5xx responses.

### 3.8 🟠 Health check lives only on `games` and `admin` — not on `tips` or `backtest`

[`api/games/__init__.py:264`](../backend/packages/api/games/__init__.py:264) and [`api/admin/__init__.py:382`](../backend/packages/api/admin/__init__.py:382) both expose `/health`. The `tips` and `backtest` functions do not. DO's load balancer health checks can probably be pointed at any function, but the convention is to have a single `health` function or to make every function accept a `/health` path consistently.

**Recommended fix**: Either (a) move health to a dedicated `health` function and have all four route there via DO's API gateway, or (b) add the same `if segs == ["health"]` check to all four `main()` entry points.

### 3.9 🟠 `match-completion` returns 200 on "no work" but 500 on a real error — no way to alert on degradation

[`cron/match-completion/__init__.py:65`](../backend/packages/cron/match-completion/__init__.py:65) returns `{"statusCode": 200, "body": {"message": "Job already running"}}` when the lock can't be acquired. That's *correct* behavior — the platform just skipped the run. But the [`/metrics` endpoint](../backend/packages/api/admin/__init__.py:327) only returns aggregated counters; it doesn't show how many runs were *skipped due to lock contention* vs *failed*. If the lock TTL is misconfigured and one job is blocking another, you'd see zero completions and zero failures — a silent regression.

**Recommended fix**: Have `JobLockCRUD` count `acquire_lock` failures and surface them in `/metrics`.

### 3.10 🟡 `historic-refresh` continuation relies on Redis being the only continuation store

[`historic-refresh/__init__.py:142`](../backend/packages/cron/historic-refresh/__init__.py:142) writes the `CONTINUATION_KEY` to Redis on timeout. But:
- If Redis is wiped (Redis eviction, manual flush, DO maintenance), the continuation marker is gone and the next run starts from scratch.
- If the function hard-kills (DO Functions 900 s ceiling) **without** entering the `except` or `finally`, the marker is *not* written. The next run also starts from scratch.
- There's no "in-progress" marker in the *database*; only in Redis. The original tracked this in the `generation_progress` table via `GenerationProgressCRUD.update_progress(...)` (used in the FaaS too — see [`services/historic_data_refresh.py:231`](../backend/packages/shared/services/historic_data_refresh.py:231)). The cron handler doesn't use the DB progress record at all; the service does, but only when called directly.

**Recommended fix**: Persist the "current season" pointer to the `generation_progress` table every batch, and have the handler resume from that row if it's `in_progress`. Redis is fine as a hot cache but the DB should be the source of truth for resumability.

### 3.11 🟡 No retry-with-backoff for transient errors in cron handlers

The original's [`BaseJob.run()`](../backend/app/cron/base.py:94) wraps each job with `retry_with_backoff(func, max_retries=3, backoff_multiplier=2.0, jitter=0.1)`. The FaaS handlers call `classify_error(e)` to **log** the error as transient vs permanent — but then they do nothing different. A transient `httpx.ConnectError` on the tip-generation call to OpenRouter results in a hard failure of the entire round's tips, with no retry.

**Recommended fix**: Wrap the inner body of each cron handler in a `retry_with_backoff` helper (port it from [`base.py:264`](../backend/app/cron/base.py:264)). Only retry on `TransientJobError` (not `PermanentJobError`).

### 3.12 🟡 `cors_origins` is duplicated in config *and* `.env.example`

The original's `.env.example` shows `CORS_ORIGINS=http://localhost:3000,https://whatismytip.com`. The FaaS `.env.example` has **no** `CORS_ORIGINS` entry. If you set `CORS_ORIGINS` in your `.env` the FaaS won't read it (it reads `cors_origins`). The convention is `CORS_ORIGINS` (uppercase, comma-separated); the FaaS uses `cors_origins` (lowercase) because Pydantic Settings lowercases env-var names by default.

**Recommended fix**: Document this clearly in `.env.example`, and consider adding a `model_config = SettingsConfigDict(env_prefix="WIMT_")` so the env-var naming is unambiguous.

### 3.13 🟡 Original has `umami` analytics env vars; FaaS has none

The original's `.env.example` has `UMAMI_WEBSITE_ID` and `UMAMI_HOST`. The FaaS dropped these. If the frontend depends on a server-rendered analytics hint (e.g., server-side fetch of a `?utm_source` value), this is a regression. Otherwise, this is fine — analytics is purely client-side. Worth verifying with the frontend team.

### 3.14 🟡 `app/api/sync.py` in the original was a 14-line stub

The original has [`backend/app/api/sync.py:1`](../backend/app/api/sync.py:1) — basically a router with no endpoints, included as `/sync` in [`api/__init__.py:13`](../backend/app/api/__init__.py:13). The FaaS has no `/sync` router at all. This is a *removal* of dead code, not a regression — but if any client hits `/api/sync/...` they'll get a 404 from DO Functions' API gateway rather than a 404 from FastAPI.

**Recommended fix**: Document the dropped `/sync` route in the FaaS migration guide.

### 3.15 🟡 FaaS `int_query` and `bool_query` don't strip whitespace

[`int_query()`](../backend/packages/shared/api_helpers.py:180) does `int(val)` directly. If a client sends `?season=2025%20` (trailing space), the original FastAPI would auto-strip via `Query(...)`. The FaaS raises `ValueError` → returns `None` → 400. Minor.

**Recommended fix**: `val = (val or "").strip()` in `int_query` and `bool_query`.

### 3.16 🟡 `redis` client not gracefully closed on connection error

[`_get_client()`](../backend/packages/shared/cache.py:41) returns a client from the shared pool. If Redis is unavailable, the *first* call hangs/times out, but the pool is *not* reset. Every subsequent call to `_get_client()` gets a stale pool object and re-hangs.

**Recommended fix**: Wrap `_get_client()` to detect connection failures and reset `_pool = None` so the next call recreates it.

### 3.17 🟢 Minor: `_handle_health()` in `games` duplicates the admin one

[`api/games/__init__.py:205`](../backend/packages/api/games/__init__.py:205) and [`api/admin/__init__.py:381`](../backend/packages/api/admin/__init__.py:381) have separate `/health` handlers. They check the same things (DB ping, Redis ping). Pull this into a shared helper in `api_helpers.py`.

### 3.18 🟢 Minor: `dispose_engine(force=had_error)` ordering in cron handlers

In all four cron handlers, `close_redis_pool(force=had_error)` is called *before* `dispose_engine(force=had_error)`. That's the right order (release the pool first, then the engine that depends on it). But the `await session.commit()` inside `release_lock` happens *before* the `close_redis_pool` — so if Redis is down and the commit also needs a fresh connection, the commit may use a stale connection from the pool. Probably fine in practice, but the ordering is fragile.

### 3.19 🟢 Minor: `cache.invalidate_cache_pattern` not used anywhere

[`cache.py:210`](../backend/packages/shared/cache.py:210) defines `invalidate_cache_pattern` but no caller exists in the codebase. Dead code — either remove it or wire it up so that tip generation, match completion, and historic refresh all invalidate stale cache entries on success.

---

## 4. What's going to fail in production

Ordered by likelihood × impact. **Read these before deploying.**

### 4.1 🔴 Silent data loss: 15-min hard ceiling on `historic-refresh`

Even with the continuation marker (see §3.10), there are failure modes:
- If a single batch's `HistoricDataRefreshService.refresh_from_string(...)` call exceeds 13 minutes by itself (the `MAX_RUNTIME_SECONDS = 780` budget — see [`historic-refresh/__init__.py:63`](../backend/packages/cron/historic-refresh/__init__.py:63)), the function will be killed mid-batch by the platform **without** writing the marker. Next run starts from season 1.
- If the marker is written but Redis is wiped before the next run, we re-start from season 1.
- The `BATCH_SIZE = 2` setting assumes 2 seasons complete in ~6-7 minutes. If OpenRouter or Squiggle is slow, that estimate is wrong, and the budget is blown.

**Mitigation already in code**: the handler sends a `send_timeout_alert()` if the loop breaks out via the time check. But that alert fires *only* on the *clean* timeout path. A hard platform kill (which is the most likely failure) fires no alert.

**Recommended mitigation**:
1. Write a `started_at` timestamp to the `generation_progress` table on each batch start.
2. On handler entry, check the `generation_progress` table for any `in_progress` row; resume from there. Redis becomes a *fast path*; the DB is the source of truth.
3. Add a CI check: with realistic data, simulate 2 seasons in < 6 minutes. If not, reduce `BATCH_SIZE` to 1.

### 4.2 🔴 Tip-generation runs at 11 AM AWST, not 3 AM

The README claims "Tip generation runs at 3 AM AWST" ([`README.md:136`](../backend/README.md:136)). The actual cron in [`functions.yml`](../backend/functions.yml) is `"0 19 * * *"` (19:00 UTC = 03:00 AWST). The config field [`config.py:53`](../backend/packages/shared/config.py:53) `cron_tip_generation: str = "0 3 * * *"` is **commented** as "3:00 AM daily" but is *never used* by the platform — the platform only reads `functions.yml`.

**Wait — verify this**: the actual schedule in `functions.yml` says `"0 19 * * *"` which is correct (3 AM AWST = 19:00 UTC). The config field is a legacy comment. But this is confusing; the **read me** is correct, the **config** is misleading, and the **FAAS-EVALUATION** calls this out as a critical issue. Need to either remove the `cron_tip_generation` field from config (since it's not used) or update its comment to "for documentation only; actual schedule is in functions.yml".

**Same issue** for `cron_historical_refresh: str = "0 4 * * 0"` in [`config.py:59`](../backend/packages/shared/config.py:59) — the `functions.yml` says `"0 20 * * 6"` (20:00 UTC Saturday = 04:00 AWST Sunday), which *is* correct, but the config field misleads. Document the UTC↔AWST mapping in one place (probably the top of `functions.yml`).

### 4.3 🟠 OpenRouter rate limit will silently kill tip generation

The new tip-generation path calls `MatchAnalysisService` *for every newly created tip* ([`tip_generation.py:323`](../backend/packages/shared/services/tip_generation.py:323)) AND `ExplanationService` ([`tip_generation.py:303`](../backend/packages/shared/services/tip_generation.py:303)). The match-analysis is in the "skip_nlp" branch only when explicitly disabled; the default code path **always** calls OpenRouter. If OpenRouter returns 429 (rate limit), the entire tip-generation handler returns 500 — but the tips themselves are saved (the `tips_created > 0` check happens after the calls). That's actually fine, but there's no retry loop on the 429.

**Recommended fix**: Wrap the OpenRouter calls in `retry_with_backoff` with `max_retries=3`, using the `TransientJobError` classifier (§3.11). The `exceptions.py` module already has the patterns; the cron handlers just need to use them.

### 4.4 🟠 Cold-start latency spikes will break timeouts

DO Functions' cold starts are 200-500 ms. For a tip-generation handler that also needs to import the `ModelOrchestrator` (which imports 8 ML models), the cold start could be 1-2 seconds. With `BATCH_SIZE=2` and `MAX_RUNTIME_SECONDS=780`, that's a 0.25% overhead — fine. But if you ever add `numpy` or `pandas` (likely, given the new weather/injury models), the cold start could be 5+ seconds, and the timeout math breaks.

**Mitigation**: keep the deployment size small (no numpy/pandas). If you ever need them, move that work to a separate function (e.g., a `weather-forecast` function) and call it via the OpenWhisk action invocation API.

### 4.5 🟠 DB connection pool starvation under concurrent load

[`db.py:62`](../backend/packages/shared/db.py:62) uses `pool_size=1, max_overflow=1`. That gives each function **at most 2 concurrent connections**. With 4 cron functions firing on overlapping schedules (daily-sync every 15 min, match-completion every 15 min offset, plus admin API triggers), you can easily hit the pool limit. The platform doesn't tell you when this happens — you just see `TimeoutError: QueuePool limit of size 1 overflow 1 is reached`.

**Recommended fix**:
1. Increase `pool_size` and `max_overflow` to 2 and 3 respectively, or
2. Switch to `NullPool` for cron handlers (they only do 1-2 queries per run) and keep the small pool for the API functions.

### 4.6 🟠 In-memory model state lost on cold start

`ModelOrchestrator.__init__` ([`orchestrator.py:28`](../backend/packages/shared/orchestrator.py:28)) instantiates all 8 models. On cold start, all 8 models re-load. For pure-Python models, this is fast. But for the models that *do* query the DB on first use (e.g., `EloModel.update_cache` is called *every* time daily-sync runs — see [`daily-sync/__init__.py:127`](../backend/packages/cron/daily-sync/__init__.py:127)), the cold-start tax is paid by every API request that triggers a model prediction.

The original has the same problem (the model objects are also instantiated per-request via `TipGenerationService.__init__` in [`tip_generation.py:49`](../backend/tip_generation.py:49)), so this isn't a regression — but the FaaS makes it worse because cold starts are more frequent.

**Recommended fix**: Move model loading to a `packages/shared/models_loader.py` module that the `ModelOrchestrator` calls lazily, and cache the loaded models in module-level globals (mirrors the engine singleton pattern).

### 4.7 🟡 Inconsistent season defaults in `tip_generation` admin trigger

[`admin/__init__.py:67`](../backend/packages/api/admin/__init__.py:67) uses `settings.current_season` (now a `Field(default_factory=_default_season)` returning the current year). But the historical data refresh uses a hard-coded `2010-2025` range in [`services/historic_data_refresh.py:48`](../backend/packages/shared/services/historic_data_refresh.py:48). If `current_season=2026` (likely in production), the historic refresh's `_default_season()` (in `config.py:8`) returns 2026, but the historic refresh's `seasons` parameter still defaults to 2010-2025. Inconsistent — a developer who reads only one will be confused.

**Recommended fix**: Pull the year range into `settings` (already there as `historic_refresh_seasons: str = "2010-2025"`) and have both the service *and* the admin trigger use it.

### 4.8 🟡 Lock expiry vs timeout mismatch

[`daily-sync/__init__.py:65`](../backend/packages/cron/daily-sync/__init__.py:65) sets `expires_seconds=settings.daily_sync_timeout_seconds` (which is 3600s = 1 hour). But the function itself runs in < 1 minute. If the platform kills the function (which it won't, since the timeout is 300s), the lock would still be held for 59 more minutes. This is OK in practice for the daily sync, but the pattern is wrong: lock expiry should match the *platform timeout*, not the *desired runtime budget*.

**Recommended fix**: Set `expires_seconds=900` (matches the DO Functions hard limit) for all cron locks, regardless of the configured `*_timeout_seconds` setting.

### 4.9 🟡 `process_request` swallows `Content-Type: application/json` body when no `Content-Type` is sent

[`parse_request`](../backend/packages/shared/api_helpers.py:34) does `body_raw = args.get("__ow_body", "")` and tries `json.loads(body_raw)`. If the body is a non-empty string that isn't valid JSON, it silently returns `{}` ([`api_helpers.py:55`](../backend/packages/shared/api_helpers.py:55)). A POST to `/api/tips/generate` with a malformed body returns 400 ("Both 'season' and 'round' are required") — **misleading**, because the body was sent, just broken.

**Recommended fix**: When `__ow_body` is non-empty but `json.loads` fails, return a 400 with `{"error": "Invalid JSON body"}`.

### 4.10 🟡 `f-string` in `int_query` for untrusted input

[`int_query`](../backend/packages/shared/api_helpers.py:180) is safe (`int()`). But [`segments`](../backend/packages/shared/api_helpers.py:162) returns the raw path segments, which are then used in log messages like:
```python
logger.info("get_game_detail: STARTING for slug=%s", slug)
```
That's safe. But the path is also returned in `404` error bodies. If a client sends a 100 KB path, the response body is 100 KB. Add a path-segment length cap.

### 4.11 🟡 `match-completion` `LOG_FORMAT=json` doesn't include the `extras` dict correctly

[`logger.py:37`](../backend/packages/shared/logger.py:37) does `standard_attrs = set(logging.LogRecord("", 0, "", 0, "", (), None).__dict__.keys()) | {"message", "asctime"}` — this is fragile because Python's `LogRecord.__dict__` can change between versions. In Python 3.12+, the `taskName` attribute was added, so `extra={"taskName": "x"}` would *not* be recognized as extra. Minor but real.

**Recommended fix**: Use `record.__dict__` directly and filter out a known whitelist of standard fields, not a blacklist.

### 4.12 🟢 Minor: `pytest-asyncio` configured with `asyncio_mode = "auto"` but no `asyncio_default_fixture_loop_scope`

If anyone adds a `pytest.fixture` that creates an event loop, the warning will be loud in CI.

---

## 5. Recommendations, prioritized

### 5.1 Do these *before* production (1-2 days work)

1. **Move health check to one place.** Add `/health` to all four `main()` handlers, or factor out to a shared helper in `api_helpers.py`.
2. **Add `Retry-After` header** to 429 responses.
3. **Add `Access-Control-Max-Age`** to all CORS preflight responses.
4. **Document the UTC↔AWST mapping** in `functions.yml` and `config.py`. Remove the misleading comments in `config.py` that say "3:00 AM" without the "(= 19:00 UTC)" suffix.
5. **Wrap OpenRouter calls in `retry_with_backoff`** (port from original `BaseJob.retry_with_backoff`).
6. **Fix `parse_request` to return 400 on malformed JSON** instead of silently `{}`.
7. **Persist `historic-refresh` continuation in `generation_progress` table** in addition to Redis.

### 5.2 Do these in the next sprint (3-5 days work)

8. **Add Pydantic request validation** to all GET endpoints. Define `TipsQuery`, `GamesQuery`, `BacktestQuery` Pydantic models and call `validate_request(query, Model)`.
9. **Add Pydantic request validation** to admin POSTs (already done for triggers; do it for body shape too).
10. **Generate an OpenAPI spec** at build time, check it into `docs/`, and reference it in the README.
11. **Move `ModelOrchestrator` to module-level singleton** with lazy loading. Cuts cold-start latency in half for tip-generation.
12. **Tune DB pool** to `pool_size=2, max_overflow=3` for API functions, `NullPool` for cron handlers.
13. **Make lock expiry match the platform timeout** (900 s), not the desired runtime budget.

### 5.3 Do these in the next month (1-2 weeks work)

14. **Build a "cold-start prewarmer"** — a scheduled cron that pings all 4 API functions every 5 minutes during peak hours. This eliminates the cold-start penalty for the most-trafficked endpoints.
15. **External log shipping** — ship JSON logs to a managed log service (Logtail, Axiom, or DO Managed Logs). The 3-day retention is unacceptable for a weekly cron job.
16. **Implement a real metrics endpoint** — Prometheus-format `/metrics` for cron durations, success rates, and DB pool stats. Use `prometheus_client` (~2 MB, fits in the size budget).
17. **Implement the cron scheduler inside a single `scheduler` function** — DO Functions can't run persistent processes, but you can implement a "self-rescheduling" cron that pings itself every N minutes via the DO Functions API. This would let you decouple the schedule from the platform's cron and make it timezone-aware (e.g., always AWST).
18. **Add an integration test for the historic-refresh continuation flow** — verify that the Redis continuation marker is written, then read, and the run resumes correctly.

### 5.4 Longer-term architecture changes (consider)

19. **Hybrid FaaS + container** for cron functions. The evaluation recommends this; I agree. The 15-min timeout is a hard ceiling that no amount of code gymnastics can avoid.
20. **Replace the `__ow_args` shim with a real Python ASGI adapter** (e.g., `asgineer` or `mangum`-style adapters for OpenWhisk). This would let you port the FastAPI app as-is and inherit all its middleware, validation, and OpenAPI generation.

---

## 6. Verdict

The `backend` is a **substantial improvement** in operational maturity (real DB, real cache, structured logging, alerting, integration tests, 8 ML models, 3 data sources, CI/CD) but a **measurable regression** in defensive programming (no request validation, no OpenAPI, no automatic rate-limit headers, no retry-with-backoff in cron). The conditional-⚠️ in the FAAS-EVALUATION is still accurate.

The single biggest risk is the **`historic-refresh` continuation flow** under partial failure (§4.1). If you fix nothing else, fix that. The second biggest is **request validation** (§3.2) — every other gap can be patched later, but a public API with no input validation is a footgun.

For the API functions, the FaaS architecture is sound. For cron functions, it is **functional but fragile** — plan a hybrid migration within 6 months.
