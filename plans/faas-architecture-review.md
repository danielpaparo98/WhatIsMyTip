# Architecture Review: Digital Ocean Functions (FaaS) Migration

> **⚠️ HISTORICAL DOCUMENT** — This plan was written during the `backend-faas` era.
> The directory has since been renamed to `backend/`. Paths like `backend-faas/...` should be read as `backend/...` today.

**Project:** WhatIsMyTip
**Reviewer:** Architecture Review  
**Date:** 2026-05-29  
**Branch:** `feature/faas-migration`

---

## 1. Executive Summary

### Overall Assessment: **Ready — with Recommendations**

The FaaS migration is well-architected and demonstrates thoughtful adaptation of a monolithic FastAPI application to Digital Ocean's serverless platform. The codebase shows strong engineering discipline: consistent patterns across all 8 function entry points, proper resource cleanup, Redis-backed state management, and pure-Python ML models that avoid heavy dependencies.

The architecture is **production-viable** but would benefit from targeted improvements in job lock atomicity, cache key correctness, security hardening, and observability before handling production traffic at scale.

**Strengths:**
- Clean decomposition into 4 API + 4 cron functions
- Proper serverless patterns: stateless design, Redis for shared state, connection cleanup
- Pure Python ML models — no numpy/scikit-learn cold start penalty
- Comprehensive job execution tracking and locking
- Graceful degradation when Redis is unavailable
- Well-structured CI/CD pipeline

**Areas for Improvement:**
- Job lock race condition (check-then-create not atomic)
- `@cached` decorator cache keys include non-deterministic session objects
- API key comparison vulnerable to timing attacks
- No health check endpoint
- Historic refresh timeout risk with batch approach
- POST `/generate` tips endpoint lacks authentication

---

## 2. Architecture Diagram

```
┌─────────────────────────────────────────────────────────────────────┐
│                        Digital Ocean Platform                        │
│                                                                      │
│  ┌──────────────────── API Functions ─────────────────────┐         │
│  │                                                         │         │
│  │  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌────────┐│         │
│  │  │  games   │  │   tips   │  │ backtest │  │ admin  ││         │
│  │  │ GET/opts │  │GET/POST  │  │   GET    │  │POST/GET││         │
│  │  └────┬─────┘  └────┬─────┘  └────┬─────┘  └───┬────┘│         │
│  │       │              │              │             │     │         │
│  └───────┼──────────────┼──────────────┼─────────────┼─────┘         │
│          │              │              │             │                │
│  ┌───────┴──────────────┴──────────────┴─────────────┴─────┐         │
│  │              Cron Functions - Scheduled                  │         │
│  │                                                          │         │
│  │  ┌────────────┐ ┌─────────────────┐ ┌───────────────┐  │         │
│  │  │ daily-sync │ │match-completion │ │tip-generation │  │         │
│  │  │  */15 min  │ │ */15 min offset │ │  Daily 3AM    │  │         │
│  │  └─────┬──────┘ └───────┬─────────┘ └──────┬────────┘  │         │
│  │        │                │                    │           │         │
│  │  ┌─────┴──────────────────────────────────────┐         │         │
│  │  │        historic-refresh - Sunday 4AM       │         │         │
│  │  │        Batch: 4 seasons per invocation     │         │         │
│  │  └────────────────────┬───────────────────────┘         │         │
│  └───────────────────────┼─────────────────────────────────┘         │
│                          │                                           │
│  ┌───────────────────────┴─────────────────────────────────┐         │
│  │              Shared Package - packages/shared/           │         │
│  │                                                          │         │
│  │  ┌──────────┐ ┌───────┐ ┌───────┐ ┌──────────────────┐ │         │
│  │  │ Services │ │ CRUD  │ │Models │ │  ML + Heuristics │ │         │
│  │  └────┬─────┘ └───┬───┘ └───┬───┘ └────────┬─────────┘ │         │
│  │       │           │         │              │            │         │
│  │  ┌────┴───────────┴─────────┴──────────────┴──────────┐ │         │
│  │  │  db.py  │  cache.py  │  config.py  │  orchestrator │ │         │
│  │  └────────────────────────────────────────────────────┘ │         │
│  └─────────────────────────────────────────────────────────┘         │
│                          │                                           │
│  ┌───────────────────────┴─────────────────────────────────┐         │
│  │              External Integrations                       │         │
│  │                                                          │         │
│  │  ┌──────────────────┐  ┌──────────────────────────────┐ │         │
│  │  │  Squiggle API    │  │  OpenRouter API - AI expl    │ │         │
│  │  │  + Rate Limiter  │  │  + Fallback explanations     │ │         │
│  │  └──────────────────┘  └──────────────────────────────┘ │         │
│  └─────────────────────────────────────────────────────────┘         │
└─────────────────────────────────────────────────────────────────────┘
                    │                           │
          ┌─────────┴─────────┐       ┌─────────┴─────────┐
          │  PostgreSQL       │       │  Redis             │
          │  Managed DB       │       │  Managed Cache     │
          │  - 9 tables       │       │  - 3 cache tiers   │
          │  - Alembic migr.  │       │  - Rate limiting   │
          │  - Job locks      │       │  - Elo ratings     │
          └───────────────────┘       └─────────────────────┘
```

---

## 3. Detailed Findings

### 3.1 Serverless Fitness

**Rating: Good (8/10)**

The codebase is well-adapted for serverless execution with several intentional design choices:

**Strengths:**
- **Stateless design:** All state is externalized to PostgreSQL and Redis. No in-memory state survives between invocations. The [`RedisCache`](backend-faas/packages/shared/cache.py:43) class replaces the original in-memory cache.
- **Pure Python ML models:** The [`EloModel`](backend-faas/packages/shared/models_ml/elo.py:22), `FormModel`, `HomeAdvantageModel`, and `ValueModel` avoid numpy/scikit-learn, eliminating heavy dependency cold starts.
- **Conservative connection pooling:** [`db.py`](backend-faas/packages/shared/db.py:13) uses `pool_size=2, max_overflow=3` — appropriate for serverless where each function instance needs minimal connections.
- **Proper cleanup:** Every function entry point calls [`dispose_engine()`](backend-faas/packages/shared/db.py:48) and [`close_redis_pool()`](backend-faas/packages/shared/cache.py:205) in `finally` blocks.
- **Redis-backed Elo ratings:** [`EloModel`](backend-faas/packages/shared/models_ml/elo.py:22) stores computed ratings in Redis with 1-hour TTL, avoiding expensive recomputation on cold starts.

**Concerns:**

1. **Cold start import chain:** Every function does `sys.path.insert(0, ...)` at module level then imports from `packages.shared.*`. This triggers a cascade of imports including SQLAlchemy, Redis, Pydantic, httpx, and OpenAI. While unavoidable in the current DO Functions structure, the cold start time could be 2-5 seconds. **Lazy imports** are used in some route handlers (e.g., [`admin/__init__.py`](backend-faas/packages/api/admin/__init__.py:52) imports `SquiggleClient` inside `_handle_daily_sync`), which is a good pattern that could be expanded.

2. **Module-level singletons:** [`settings = Settings()`](backend-faas/packages/shared/config.py:97) is instantiated at import time. Similarly, [`_squiggle_rate_limiter`](backend-faas/packages/shared/squiggle/client.py:101) is created at module level. These are fine for serverless (created once per cold start) but add to cold start latency.

3. **`asyncio.Lock()` in class variable:** [`EloModel._cache_lock`](backend-faas/packages/shared/models_ml/elo.py:38) is a class-level asyncio Lock. This is safe within a single function invocation (single event loop) but provides no coordination across separate function instances. This appears intentional for intra-invocation concurrency only.

### 3.2 Function Decomposition

**Rating: Good (8/10)**

**Strengths:**
- The 4+4 split is well-considered. API functions handle HTTP routing; cron functions handle scheduled jobs.
- Each function has a clear, single responsibility.
- Manual routing via [`segments(path)`](backend-faas/packages/shared/api_helpers.py:57) is simple and effective for the small number of routes per function.

**Concerns:**

1. **Historic refresh timeout risk:** The [`historic-refresh`](backend-faas/packages/cron/historic-refresh/__init__.py:7) function documents a 60-minute DO Functions timeout but estimates ~2 hours for a full refresh. The batch approach (4 seasons per invocation) mitigates this, but:
   - There's no mechanism to chain batches automatically. Only the first batch runs on the scheduled trigger.
   - The [`_resolve_batch()`](backend-faas/packages/cron/historic-refresh/__init__.py:52) function defaults to the first batch, meaning batches 2-4 require manual triggering or external orchestration.

2. **Tips function has dual responsibility:** The [`tips/__init__.py`](backend-faas/packages/api/tips/__init__.py:178) handles both read operations (GET tips) and write operations (POST generate). The POST `/generate` endpoint creates tips via the ML pipeline, which is a heavier operation. Consider separating read/write functions for independent scaling.

3. **Daily sync frequency:** The cron schedule `*/15 * * * *` runs 96 times/day. During AFL season this is appropriate, but the off-season check at [`daily-sync/__init__.py:74`](backend-faas/packages/cron/daily-sync/__init__.py:74) only reduces to a 2-hour window. Each invocation still acquires a DB lock, creates an execution record, and checks the time — consuming resources.

### 3.3 Data Layer Architecture

**Rating: Good (7/10)**

**Database Connection Management:**

The [`db.py`](backend-faas/packages/shared/db.py) module uses a singleton pattern with conservative settings:
- `pool_size=2, max_overflow=3` → max 5 connections per function instance
- `pool_pre_ping=True` → validates stale connections
- `pool_recycle=300` → recycles connections every 5 minutes

This is appropriate for serverless. Each function instance maintains its own engine, and the small pool size prevents connection exhaustion on the managed PostgreSQL instance.

**Redis Caching Strategy:**

Three cache tiers in [`cache.py`](backend-faas/packages/shared/cache.py:111):
- `short_cache`: 60s TTL — for game queries, upcoming games
- `medium_cache`: 300s TTL — for season data, Squiggle API responses
- `long_cache`: 3600s TTL — for Elo ratings

**Concerns:**

1. **Cache key includes session object:** The [`@cached` decorator](backend-faas/packages/shared/cache.py:145) builds cache keys using `args[1:]` to skip the db session. However, for static methods like `GameCRUD.get_by_round(db, season, round_id)`, `args[1:]` is `(season, round_id)` which is correct. But if a developer passes additional kwargs or non-serializable objects, cache keys could be non-deterministic. The `str()` conversion on line 146 is fragile.

2. **Aggressive cache invalidation:** [`GameCRUD.create_or_update_with_tracking()`](backend-faas/packages/shared/crud/games.py:174) invalidates 5 cache patterns on every write. This is correct but broad — a single game update invalidates all game caches, not just the affected ones. For a low-write application this is acceptable.

3. **Redis `scan_iter` in `clear()`:** The [`clear()`](backend-faas/packages/shared/cache.py:101) method and [`invalidate_cache_pattern()`](backend-faas/packages/shared/cache.py:176) use `scan_iter` which can be slow on large datasets. For this application's scale, it's fine.

4. **Migration strategy:** The consolidated migration [`0001_consolidated_postgresql_schema.py`](backend-faas/alembic/versions/2026_05_28_1613-0001_consolidated_postgresql_schema.py) creates all 9 tables from scratch. This is correct for a fresh database but means there's no upgrade path from the original SQLite-backed schema. The `down_revision = None` confirms this is a standalone starting point.

### 3.4 Error Handling & Resilience

**Rating: Good (7/10)**

**Strengths:**
- Every cron function follows a consistent pattern: acquire lock → create execution → execute → mark success/failure → release lock → cleanup resources.
- Partial failures are handled well: individual game processing errors are caught and logged without failing the entire job (e.g., [`tip_generation.py:126`](backend-faas/packages/shared/services/tip_generation.py:126)).
- Graceful degradation: [`RedisCache.get()`](backend-faas/packages/shared/cache.py:60) returns `None` on Redis errors, falling through to the database. The [`_RedisRateLimiter`](backend-faas/packages/shared/squiggle/client.py:14) allows requests through if Redis is down.
- AI explanation failures don't break tip generation ([`tip_generation.py:315`](backend-faas/packages/shared/services/tip_generation.py:315)).

**Concerns:**

1. **Job lock race condition:** The [`JobLockCRUD.acquire_lock()`](backend-faas/packages/shared/crud/jobs.py:228) method does a check-then-create:
   ```python
   existing_lock = await self.db.execute(select(JobLock)...)
   if existing_lock:
       if existing_lock.expires_at > now:
           return None  # Locked
       else:
           await self.db.delete(existing_lock)  # Expired
   # Create new lock
   lock = JobLock(...)
   ```
   Between the SELECT and INSERT, another function instance could acquire the same lock. This should use `INSERT ... ON CONFLICT` or `SELECT ... FOR UPDATE` for true atomicity. With DO Functions running every 15 minutes, the race window is small but non-zero.

2. **Lock release in finally block:** If the function times out (DO kills the process), the `finally` block may not execute, leaving the lock in place. The lock expiry mechanism mitigates this, but stale locks could delay the next scheduled run.

3. **No retry mechanism:** Failed cron jobs are recorded but not automatically retried. The `job_max_retries` config field exists but isn't used in the cron entry points.

4. **Error exposure in API responses:** Some API functions return raw error strings in responses (e.g., [`backtest/__init__.py:199`](backend-faas/packages/api/backtest/__init__.py:199): `error=str(e)`), while admin functions properly sanitize: `error="Internal server error"`. Inconsistent error handling could leak internal details.

### 3.5 Security

**Rating: Needs Improvement (5/10)**

**Authentication:**
- Admin endpoints use API key authentication via [`_verify_api_key()`](backend-faas/packages/api/admin/__init__.py:36), checking the `X-API-Key` header against `ADMIN_API_KEY`.
- **Timing attack vulnerability:** The comparison `api_key == settings.admin_api_key` on line 43 uses Python's `==` operator, which short-circuits on first character mismatch. Use `hmac.compare_digest()` or `secrets.compare_digest()` for constant-time comparison.

**Authorization:**
- **POST `/generate` tips endpoint is unauthenticated:** The [`tips/__init__.py`](backend-faas/packages/api/tips/__init__.py:178) function's POST `/generate` endpoint triggers tip generation without any authentication. This allows any caller to trigger expensive ML computation + AI explanation generation.
- Public API functions (games, tips GET, backtest) are intentionally unauthenticated, which is appropriate for a public tipping app.

**CORS:**
- [`api_helpers.py:49`](backend-faas/packages/shared/api_helpers.py:49) sets `Access-Control-Allow-Origin` to `settings.cors_origins[0]` — only the first configured origin. If multiple origins are configured, only the first works.
- No dynamic origin validation (checking the request's `Origin` header against the allowed list).

**Network Security:**
- `require-whisk-auth: false` on all functions in [`project.yml`](backend-faas/project.yml:12) — DO Functions' built-in authentication is disabled. Security relies entirely on application-level checks.
- No rate limiting on API functions (the original FastAPI app used `slowapi`). The `rate_limit_per_minute` config field exists but isn't enforced.

**Data Security:**
- SQLAlchemy's parameterized queries protect against SQL injection.
- Environment variables are properly externalized. The CI/CD workflow correctly notes that secrets should be configured at the DO namespace level, not in the workflow.

### 3.6 Scalability & Performance

**Rating: Good (7/10)**

**Scaling Characteristics:**
- API functions scale horizontally automatically via DO Functions.
- Cron functions are singleton executions (one at a time per job), which is appropriate.
- Database is the primary bottleneck: with `pool_size=2` per function instance, N concurrent instances = 2N connections. DO Managed PostgreSQL typically allows ~25-50 connections on basic tiers.

**Bottleneck Analysis:**

| Component | Limit | Impact |
|-----------|-------|--------|
| PostgreSQL connections | ~25-50 on basic tier | 10+ concurrent function instances could exhaust connections |
| Redis connections | `max_connections=10` per pool | Low risk; Redis handles thousands of connections |
| Squiggle API | Rate limited to 10 req/min | Historic refresh processes 4 seasons × ~200 games = heavy API usage |
| OpenRouter API | Pay-per-request | Cost scales with tip generation frequency |
| DO Functions timeout | 60 min scheduled, 10 min HTTP | Historic refresh is the primary risk |

**Cost Implications:**
- FaaS model: Pay per invocation. With 96 daily syncs + 96 match completions + 1 tip generation + 0.14 historic refresh per day = ~193 invocations/day baseline.
- The monolithic approach runs 24/7, so FaaS is likely cheaper at low traffic volumes.
- At high traffic volumes, the per-invocation cost of API functions could exceed a fixed-cost VPS.

### 3.7 Operational Concerns

**Rating: Needs Improvement (6/10)**

**Observability:**
- Logging is consistent via [`get_logger()`](backend-faas/packages/shared/logger.py:6) with structured-ish format: `%(asctime)s - %(name)s - %(levelname)s - %(message)s`.
- **No structured logging:** Logs are plain text, making parsing and aggregation harder.
- **No distributed tracing:** No request IDs or correlation IDs across function calls.
- **No health check endpoint:** There's no `/health` or `/ready` endpoint for monitoring.
- Job execution history is tracked in the `job_executions` table with metrics — good for operational visibility.

**Deployment:**
- [`deploy.sh`](backend-faas/scripts/deploy.sh) follows a safe sequence: tests → migrations → deploy → verify.
- CI/CD via [`deploy-faas.yml`](.github/workflows/deploy-faas.yml) runs tests, linting, and type checking before deploy.
- **Migration timing:** Migrations run AFTER function deployment (line 68-73 of workflow). This means new code could run against an old schema briefly. Consider running migrations before deployment.
- **No rollback mechanism:** If a deployment fails or introduces a bug, there's no automated rollback. DO Functions doesn't natively support rollback — you'd need to redeploy the previous version.
- **No blue/green or canary deployment:** All functions update simultaneously.

**Database Migrations:**
- Alembic is properly configured with async-to-sync URL conversion in [`env.py`](backend-faas/alembic/env.py:31).
- The consolidated migration approach is clean for initial setup.
- **No migration rollback testing:** The `downgrade()` function exists but isn't tested in CI.

### 3.8 Code Organization

**Rating: Very Good (8/10)**

**Strengths:**
- Clean separation: entry points → services → CRUD → models.
- Shared package approach avoids code duplication across functions.
- Consistent patterns across all 8 entry points (error handling, cleanup, routing).
- Well-structured test suite with proper mocking ([`conftest.py`](backend-faas/tests/conftest.py) provides `mock_redis`, `mock_db_session`, and sample data factories).

**Concerns:**

1. **`sys.path` manipulation:** Every function does `sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))`. This is fragile and would break if the directory structure changes. DO Functions requires this pattern, so it's unavoidable, but it should be documented as a constraint.

2. **Circular dependency risk:** The import chain is: entry point → shared.{db,cache,config} → shared.models. Services import from CRUD, models, and other services. The `explanation.py` imports `ModelOrchestrator` which imports all models and heuristics. This is a wide but shallow dependency graph — low circular dependency risk currently, but worth monitoring.

3. **Test coverage:** Tests exist for all 8 function entry points plus unit tests for cache, heuristics, models, and squiggle utils. However, there are no integration tests (the `tests/integration/` directory is empty). The test suite relies entirely on mocks.

4. **Route duplication:** The URL structure `/api/api/games` (documented in README) has a doubled `api/` prefix. This comes from the DO Functions routing: the function is named `api/games` and mounted under `/api/`. This is cosmetically unfortunate but functionally correct.

---

## 4. Risk Assessment

| # | Risk | Likelihood | Impact | Mitigation |
|---|------|-----------|--------|------------|
| 1 | **Job lock race condition** — two instances acquire the same lock | Low | Medium — duplicate job execution | Use `INSERT ... ON CONFLICT DO NOTHING` for atomic lock acquisition |
| 2 | **Historic refresh timeout** — batch exceeds 60-min limit | Medium | High — incomplete data refresh | Add self-chaining mechanism to trigger next batch; monitor execution duration |
| 3 | **DB connection exhaustion** — high concurrency drains pool | Low | High — API errors | Increase managed DB connection limit; add connection pool monitoring |
| 4 | **Unauthenticated tip generation** — POST /generate is public | Medium | Medium — resource abuse, cost | Add API key auth or rate limiting to POST /generate |
| 5 | **Timing attack on admin API key** | Low | Low — key recovery | Use `secrets.compare_digest()` |
| 6 | **Cold start latency** — heavy import chain | High | Low — slow first request | Expand lazy imports; consider dependency pruning |
| 7 | **Stale lock after function timeout** — process killed before finally | Low | Medium — job skipped for lock duration | Lock expiry mechanism already mitigates; reduce expiry time |
| 8 | **Migration runs after deploy** — brief schema/code mismatch | Low | Medium — errors during migration window | Reorder CI/CD to migrate first |
| 9 | **Cache key collision** — non-deterministic keys with session objects | Low | Medium — stale/wrong data served | Improve cache key generation to use only serializable args |
| 10 | **No rollback capability** — failed deployment requires manual fix | Medium | Medium — extended downtime | Add deployment versioning and manual rollback script |

---

## 5. Recommendations

### Priority 1 — Should Fix Before Production

1. **Fix job lock atomicity** in [`JobLockCRUD.acquire_lock()`](backend-faas/packages/shared/crud/jobs.py:228): Use a raw SQL `INSERT ... ON CONFLICT` statement or `SELECT ... FOR UPDATE` to make lock acquisition truly atomic.

2. **Add authentication to POST `/generate`** in [`tips/__init__.py`](backend-faas/packages/api/tips/__init__.py:270): Either require API key (like admin) or add rate limiting to prevent abuse.

3. **Fix API key comparison** in [`admin/__init__.py:43`](backend-faas/packages/api/admin/__init__.py:43): Replace `api_key == settings.admin_api_key` with `secrets.compare_digest(api_key, settings.admin_api_key)`.

4. **Reorder CI/CD migrations** in [`deploy-faas.yml`](.github/workflows/deploy-faas.yml:68): Run migrations before function deployment to avoid schema/code mismatch.

### Priority 2 — Should Fix Soon

5. **Implement historic refresh batch chaining:** After completing a batch, the function should trigger itself with the next batch's `start_season` parameter. This can be done via the DO Functions API or by having the function call its own HTTP trigger.

6. **Fix CORS handling** in [`api_helpers.py:49`](backend-faas/packages/shared/api_helpers.py:49): Dynamically validate the request's `Origin` header against the full `cors_origins` list, returning the matching origin in the response.

7. **Add a health check endpoint:** Create a simple `/health` route in one of the API functions (or a dedicated function) that verifies DB and Redis connectivity.

8. **Improve cache key generation** in [`cache.py:146`](backend-faas/packages/shared/cache.py:146): Use explicit key builders instead of `str(args)` to avoid non-deterministic keys.

### Priority 3 — Nice to Have

9. **Add request correlation IDs:** Generate a UUID per request and include it in all log entries for distributed tracing.

10. **Expand lazy imports:** Move heavy imports (e.g., `sqlalchemy.select`, `ModelOrchestrator`) inside function handlers where possible to reduce cold start time.

11. **Add structured logging:** Switch from plain text to JSON logging for easier parsing by log aggregation tools.

12. **Add integration tests:** The `tests/integration/` directory is empty. Add tests that verify function behavior with real database and Redis (using testcontainers).

13. **Implement retry mechanism for failed jobs:** The `job_max_retries` config exists but isn't used. Add retry logic to cron entry points.

14. **Fix doubled `/api/api/` URL path:** Either rename the DO Functions package from `api/` to something else, or configure the route prefix to avoid duplication.

---

## 6. Monolithic vs. FaaS Comparison

| Dimension | Monolithic FastAPI | FaaS - Digital Ocean Functions |
|-----------|-------------------|-------------------------------|
| **Database** | SQLite + aiosqlite | PostgreSQL + asyncpg |
| **Cache** | In-memory dict | Redis with TTL tiers |
| **ML Models** | numpy/scikit-learn available | Pure Python only |
| **Cron Jobs** | In-process scheduler (APScheduler) | DO platform scheduler |
| **Scaling** | Vertical (single process) | Horizontal (per function) |
| **Cold Starts** | None (always running) | 2-5 seconds per function |
| **Cost at Idle** | Fixed (VPS running 24/7) | Near-zero (pay per invocation) |
| **Cost at Scale** | Fixed | Increases with traffic |
| **Deployment** | Manual scripts | CI/CD with GitHub Actions |
| **State Management** | In-process (simple) | Externalized to Redis/DB (complex) |
| **Observability** | Single process logs | Distributed logs across functions |
| **Timeout Risk** | None (long-running process) | 10 min HTTP, 60 min scheduled |
| **Development UX** | Fast refresh, hot reload | Slower iteration, deploy to test |
| **Operational Complexity** | Low (single service) | Medium (8 functions + shared package) |

**When FaaS Wins:**
- Low, sporadic traffic (this app — AFL tipping is seasonal)
- Need for automatic scaling during peak times (round start/end)
- Want zero maintenance overhead during off-season
- Team prefers CI/CD over manual deployment

**When Monolithic Wins:**
- Consistent high traffic (not this app's pattern)
- Need for sub-100ms response times (cold starts preclude this)
- Complex stateful processing (historic refresh is borderline)
- Simpler debugging and local development

**Verdict for WhatIsMyTip:** The FaaS migration is a good fit. The application has bursty traffic patterns (AFL season vs. off-season), the business logic is naturally decomposable into discrete operations, and the team has properly adapted the state management for serverless. The primary tradeoff is cold start latency on first requests, which is acceptable for a tipping application.

---

## Appendix: File Inventory

| Category | Files Reviewed | Lines of Code |
|----------|---------------|---------------|
| Project Config | 3 files | ~250 |
| API Entry Points | 4 functions | ~850 |
| Cron Entry Points | 4 functions | ~700 |
| Shared Core | 7 modules | ~650 |
| Services | 3 key services | ~900 |
| CRUD Layer | 3 modules | ~1000 |
| Models & Schemas | 3 modules | ~250 |
| External Integrations | 2 clients | ~450 |
| Database/Migration | 2 files | ~350 |
| Deployment/CI | 2 scripts | ~160 |
| Tests | 1 conftest | ~130 |
| **Total** | **34 files** | **~5,690** |
