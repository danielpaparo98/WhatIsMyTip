# FaaS Architecture Evaluation — WhatIsMyTip on DigitalOcean Functions

**Date**: 2026-06-08
**Scope**: Backend FaaS architecture review — platform constraints, operational risks, cost analysis, and go/no-go recommendation
**Verdict**: **⚠️ Conditional Go** — FaaS approach is viable for API functions but has critical issues with long-running cron jobs that must be resolved before production deployment.

> **Resolution Status (2026-06-16):** The FaaS architecture was **superseded** by the FastAPI
> reimplementation in Phases 1–5 of the `feature/fastapi-reimplementation` branch. Every
> critical, high, and medium concern from this evaluation was addressed in the new
> container-based deploy. The FaaS code under [`backend/packages/api/`](../backend/packages/api/)
> and [`backend/packages/cron/`](../backend/packages/cron/) was deleted in Phase 5. See
> **Resolution Status** at the bottom of this document for a per-finding disposition table.
>
> **This document is retained for historical reference only.** For the current architecture
> see [docs/backend.md](backend.md).

---

## Resolution Status

> **Closed by:** `feature/fastapi-reimplementation` (Phases 1–5, merged 2026-06-16)
> **Test evidence:** 878 unit tests passing, 0 failing, 3 integration skips.

| # | Severity | Original finding | Resolution | Where now |
|---|----------|------------------|------------|-----------|
| 1 | 🔴 CRITICAL | `historic-refresh` timeout exceeds platform limits | **Closed in FaaS era (batch size 2, Redis continuation), preserved in FastAPI** — `app/cron/historic_refresh.py` runs in-process with no per-invocation timeout. Total runtime is bounded by the `historical_refresh_timeout_seconds` (15 min safety cap). Continuation state stored in Redis. | [`backend/app/cron/historic_refresh.py`](../backend/app/cron/historic_refresh.py:1) |
| 2 | 🔴 CRITICAL | Cron triggers fire in UTC, not `Australia/Perth` | **Closed in Phase 4** — APScheduler uses `CronTrigger.from_crontab(expr, timezone=settings.cron_timezone)`. The container's locale is set to `Australia/Perth`, so `"0 3 * * *"` fires at 3 AM AWST exactly. | [`backend/app/core/scheduler.py`](../backend/app/core/scheduler.py:1) |
| 3 | 🟠 HIGH | Missing `limits:` overrides in `project.yml` | **Closed by the FaaS architecture being retired** — the FastAPI app is one container with one resource limit (set in the Dockerfile / App Platform spec). | [`backend/Dockerfile`](../backend/Dockerfile:1) |
| 4 | 🟡 MEDIUM | Deployment artifact ~42 MB against 48 MB limit | **Closed by the FaaS architecture being retired** — the container image is multi-stage and the deploy bundle is the standard DO App Platform size (no 48 MB function limit). | [`backend/Dockerfile`](../backend/Dockerfile:1) |
| 5 | 🟡 MEDIUM | `dispose_engine()` on every invocation | **Closed in FaaS era, preserved in FastAPI** — `dispose_engine(force=True)` is called only on shutdown (`app/core/lifespan.py`); the engine singleton lives for the process lifetime. | [`backend/app/core/lifespan.py`](../backend/app/core/lifespan.py:1), [`backend/packages/shared/db.py`](../backend/packages/shared/db.py:1) |
| 6 | 🟡 MEDIUM | 3-day log retention | **Closed in FaaS era, preserved in FastAPI** — `job_executions` table stores the full structured execution record (status, duration, error message, retry count) for `metrics_retention_days` (default 30). The application can ship logs to an external sink via the structured logger. | [`backend/packages/shared/logger.py`](../backend/packages/shared/logger.py:1), [`backend/packages/shared/crud/jobs.py`](../backend/packages/shared/crud/jobs.py:1) |
| 7 | 🟡 MEDIUM | No built-in observability or alerting | **Closed in FaaS era, preserved in FastAPI** — `AlertingService` sends webhook alerts on `TransientJobError`, `PermanentJobError`, and timeout; `GET /api/admin/metrics` returns per-job execution stats; the health endpoint reports DB + Redis status. | [`backend/packages/shared/alerting.py`](../backend/packages/shared/alerting.py:1), [`backend/app/api/admin.py`](../backend/app/api/admin.py:1) |
| 8 | 🟢 LOW | `alembic` in runtime dependencies | **Not yet closed** — `alembic` is still in runtime deps because the same container runs migrations in a one-off task during deploy. Moving it to dev would require a separate "migrations" container. | [`backend/pyproject.toml`](../backend/pyproject.toml:1) |

### What the FaaS era did well (preserved in FastAPI)

The FaaS architecture added a lot of value that the FastAPI reimplementation inherits. Items
the FaaS era closed (kept as-is in the new code) include:

- **Structured logging** ([`packages/shared/logger.py`](../backend/packages/shared/logger.py:1))
- **Advisory-lock-based job deduplication** ([`packages/shared/crud/jobs.py`](../backend/packages/shared/crud/jobs.py:1))
- **`JobExecutionCRUD` history table** for every cron run
- **Retry-with-backoff** in [`app/cron/base.py`](../backend/app/cron/base.py:1)
- **ML pipeline improvements** (incremental Elo, value-model temporal filter, single-pass orchestrator)
- **8 ML models** including weather, injury, matchup, and player-form
- **Redis 3-tier caching** with the `@cached` decorator
- **OpenRouter integration** for AI-generated tip explanations

### What's actually different now (vs. the FaaS code)

| Concern | FaaS era | FastAPI reimplementation |
|---------|----------|---------------------------|
| Function entry point | `async def main(args: dict)` returning `{"statusCode", "body"}` | `app.include_router(router)` + FastAPI handlers |
| Routing | Manual `if method == "GET" and segs == [...]` chains | `@router.get("/path")` decorators + Pydantic validation |
| Cron trigger | OpenWhisk scheduler in **UTC** (timezone bug) | APScheduler in **Australia/Perth** |
| Cron timeout | 15-minute hard platform cap | 15-minute soft `historical_refresh_timeout_seconds` safety cap |
| Bundle size | 48 MB function limit | No function limit (standard container) |
| Cold start | Every invocation (FaaS) | Only on first request after deploy (single worker) |
| Hot reload | N/A | `uvicorn --reload` in dev |
| OpenAPI | Hand-written function inventory doc | `/openapi.json` + Swagger UI at `/docs` |
| Container deploy | N/A | `backend/scripts/deploy.sh` builds + pushes + triggers App Platform |

---

---

## 1. Executive Summary

The `backend` implementation migrates the WhatIsMyTip backend from a monolithic FastAPI application to 8 DigitalOcean Functions — 4 HTTP API endpoints and 4 scheduled cron triggers. The architecture demonstrates strong fundamentals: clean separation of concerns, shared package design, Redis-backed caching, database-level job locking, and CI/CD deployment scripts.

However, several **platform-level constraints** create risks that range from data integrity issues to hard timeout kills. The most critical is a mismatch between the `historic-refresh` function's runtime budget (50 minutes) and the platform's maximum timeout, which could result in silent data corruption during weekly historical refreshes.

### Risk Summary

| # | Severity | Issue | Impact |
|---|----------|-------|--------|
| 1 | 🔴 CRITICAL | `historic-refresh` timeout exceeds platform limits | Hard kill mid-batch → partial data, dangling locks |
| 2 | 🔴 CRITICAL | Cron triggers fire in UTC, not `Australia/Perth` | Tip generation runs at 11 AM AWST instead of 3 AM AWST |
| 3 | 🟠 HIGH | Missing `limits:` overrides in `project.yml` | All functions default to 60s timeout, 256 MB memory |
| 4 | 🟡 MEDIUM | Deployment artifact ~42 MB against 48 MB limit | Any dependency addition could breach the limit |
| 5 | 🟡 MEDIUM | `dispose_engine()` on every invocation | Negates connection pooling, adds latency per cold start |
| 6 | 🟡 MEDIUM | 3-day log retention | Insufficient for production incident investigation |
| 7 | 🟡 MEDIUM | No built-in observability or alerting | Silent failures go undetected |
| 8 | 🟢 LOW | `alembic` in runtime dependencies | ~3 MB wasted, increases deployment size pressure |

---

## 2. What's Working Well

The FaaS architecture has several significant strengths that are worth acknowledging.

### 2.1 Cost Efficiency

| Resource | Monthly Cost | Notes |
|----------|-------------|-------|
| DO Functions compute | ~$1.00 | Well within free tier (3M GB-s included) |
| Managed PostgreSQL | ~$12.00 | Basic db-s-1vcpu-1gb |
| Managed Redis | ~$2.00 | Smallest instance |
| **Total** | **~$15/month** | Excellent for current scale |

This represents a significant cost reduction compared to running a container-based backend ($20–40/month on DO App Platform) for the same workload.

### 2.2 Architectural Strengths

| Area | Assessment | Detail |
|------|-----------|--------|
| **Shared package** | ✅ Good | `packages/shared/` provides clean code reuse across all 8 functions with a well-structured module layout |
| **Redis caching** | ✅ Good | 3-tier TTL strategy (60s, 300s, 3600s) via [`cache.py`](../backend/packages/shared/cache.py) prevents redundant DB queries |
| **Job locking** | ✅ Good | Database-level advisory locks via [`JobLockCRUD`](../backend/packages/shared/crud/jobs.py) prevent concurrent cron execution |
| **Execution tracking** | ✅ Good | [`JobExecutionCRUD`](../backend/packages/shared/crud/jobs.py) records run status, duration, and results for each cron job |
| **ML pipeline** | ✅ Good | Pure-Python models (no NumPy/scipy) keep deployment size small; [`ModelOrchestrator`](../backend/packages/shared/orchestrator.py) runs all 4 models in parallel |
| **API routing** | ✅ Adequate | [`parse_request()`](../backend/packages/shared/api_helpers.py:11) cleanly extracts method/path/query/body from DO's `args` dict |
| **Admin auth** | ✅ Good | [`verify_api_key()`](../backend/packages/shared/api_helpers.py:39) uses `secrets.compare_digest()` for timing-attack resistance |
| **CORS handling** | ✅ Good | Origin-aware CORS resolution prevents wildcard `*` responses in production |
| **Deployment** | ✅ Good | [`deploy.sh`](../backend/scripts/deploy.sh) script with `doctl serverless deploy` is simple and repeatable |
| **Configuration** | ✅ Good | Pydantic Settings in [`config.py`](../backend/packages/shared/config.py) with environment variable injection |

### 2.3 Function Inventory

| Function | Type | Trigger | Purpose |
|----------|------|---------|---------|
| [`api/games`](../backend/packages/api/games/__init__.py) | HTTP | Web request | Game listings, scores, slugs |
| [`api/tips`](../backend/packages/api/tips/__init__.py) | HTTP | Web request | ML predictions, tip explanations |
| [`api/backtest`](../backend/packages/api/backtest/__init__.py) | HTTP | Web request | Historical model accuracy data |
| [`api/admin`](../backend/packages/api/admin/__init__.py) | HTTP | Web request | Admin operations, job management |
| [`cron/daily-sync`](../backend/packages/cron/daily-sync/__init__.py) | Scheduled | `*/15 * * * *` | Sync games from Squiggle API |
| [`cron/match-completion`](../backend/packages/cron/match-completion/__init__.py) | Scheduled | `5,20,35,50 * * * *` | Detect completed matches, trigger analysis |
| [`cron/tip-generation`](../backend/packages/cron/tip-generation/__init__.py) | Scheduled | `0 3 * * *` | Daily ML predictions + AI explanations |
| [`cron/historic-refresh`](../backend/packages/cron/historic-refresh/__init__.py) | Scheduled | `0 4 * * 0` | Weekly full historical data refresh |

---

## 3. Critical Issues (Must Fix Before Production)

### 3.1 🔴 Historic Refresh Timeout Risk

**Files**: [`historic-refresh/__init__.py`](../backend/packages/cron/historic-refresh/__init__.py:88), [`project.yml`](../backend/project.yml:83)

**Problem**: The `historic-refresh` function sets `MAX_RUNTIME_SECONDS = 3000` (50 minutes) and processes seasons in batches of 4 across 4 batches (2010–2025). The code's own docstring claims a "60-minute max timeout for scheduled functions", but DigitalOcean Functions has a documented maximum execution time of **15 minutes** (900,000 ms) for all function types. This is the default and maximum — it cannot be overridden.

Even if the 60-minute assumption were correct, the 50-minute budget with 4 batches of ~30 minutes each means the function *depends* on multi-invocation continuation, but there is no self-trigger mechanism — the next batch only runs on the following week's cron trigger.

**Impact**:
- Hard kill at 15 minutes → partial season data, dangling database locks
- Lock expiry set to 3300s (55 min) → lock won't auto-expire for 55 minutes after kill
- Subsequent invocations blocked by unexpired lock
- No continuation mechanism — each week starts from the first batch again

**Evidence from code** ([`historic-refresh/__init__.py:86-88`](../backend/packages/cron/historic-refresh/__init__.py:88)):
```python
# Maximum runtime before yielding to avoid exceeding DO Functions timeout.
# 50 minutes leaves a 10-minute buffer under the 60-minute scheduled limit.
MAX_RUNTIME_SECONDS = 3000
```

**Recommended Fix** (priority order):
1. **Reduce batch size** to 2 seasons per batch → ~15 min per batch, fits within 15-min limit
2. **Implement self-triggering** — after completing a batch, invoke the same function via the DO Functions API with `start_season` for the next batch
3. **Reduce lock expiry** to 900s (15 min) — matches platform timeout
4. **Add batch tracking** to `generation_progress` table so next invocation can resume from the correct batch

---

### 3.2 🔴 Cron Timezone Bug

**Files**: [`project.yml`](../backend/project.yml:80-82), [`config.py`](../backend/packages/shared/config.py:53)

**Problem**: DigitalOcean Functions cron triggers fire in **UTC only**. The `config.py` defines `cron_timezone: str = "Australia/Perth"` (AWST, UTC+8), but this setting is **never used by the platform** — it's purely informational. The cron expressions in `project.yml` are interpreted as UTC:

| Function | Cron Expression | Intended Time (AWST) | Actual Time (AWST) | Offset |
|----------|----------------|----------------------|---------------------|--------|
| `tip-generation` | `0 3 * * *` | 3:00 AM | **11:00 AM** | +8h |
| `historic-refresh` | `0 4 * * 0` | 4:00 AM Sunday | **12:00 PM Sunday** | +8h |
| `daily-sync` | `*/15 * * * *` | Every 15 min | Every 15 min | No issue (UTC-aligned) |
| `match-completion` | `5,20,35,50 * * * *` | Offset every 15 min | Offset every 15 min | No issue (UTC-aligned) |

**Impact**:
- Tip generation runs at 11 AM AWST — during peak usage hours, not the intended 3 AM off-peak window
- Historic refresh runs at noon AWST on Sundays — competing with user traffic
- No data loss, but degraded user experience and potential API rate-limit contention

**Evidence from code** ([`config.py:53`](../backend/packages/shared/config.py:53)):
```python
cron_tip_generation: str = "0 3 * * *"  # 3:00 AM daily
```
Comment says "3:00 AM" but this fires at 3:00 AM UTC = 11:00 AM AWST.

**Recommended Fix**:
1. **Correct cron expressions** to UTC equivalents:
   - Tip generation (3 AM AWST): `"0 19 * * *"` (19:00 UTC previous day = 3:00 AWST)
   - Historic refresh (4 AM AWST Sunday): `"0 20 * * 6"` (20:00 UTC Saturday = 4:00 AWST Sunday)
2. **Add a comment** in `project.yml` documenting the UTC→AWST conversion
3. **Consider removing** `cron_timezone` from config since it's misleading — or implement runtime timezone validation

---

### 3.3 🟠 Missing `limits:` Overrides in project.yml

**File**: [`project.yml`](../backend/project.yml)

**Problem**: No function in `project.yml` specifies `limits:` for timeout or memory. All functions use DigitalOcean's defaults:

| Function | Default Timeout | Actual Need | Risk |
|----------|----------------|-------------|------|
| `api/games` | 60s | ~5s | Wasteful but safe |
| `api/tips` | 60s | ~10s | Wasteful but safe |
| `api/backtest` | 60s | ~30s | Wasteful but safe |
| `api/admin` | 60s | ~5–60s | Borderline for job triggers |
| `cron/daily-sync` | 60s | Up to 5 min | **Will timeout on large syncs** |
| `cron/match-completion` | 60s | ~2–5 min | **May timeout** |
| `cron/tip-generation` | 60s | ~20–30 min | **Will timeout** |
| `cron/historic-refresh` | 60s | ~15–50 min | **Will timeout** |

**Impact**: All cron functions will likely hit the default 60-second timeout and be killed before completing.

**Recommended Fix**: Add `limits:` to each function in `project.yml`:

```yaml
# Example for tip-generation
tip-generation:
  handler: packages/cron/tip-generation/__init__.py
  runtime: python:3
  web: false
  limits:
    timeout: 1800000    # 30 minutes in milliseconds
    memory: 512         # 512 MB
```

> **Note**: Even with `limits:` overrides, DO Functions enforces a maximum timeout. Verify the platform's actual maximum for your account/tier.

---

## 4. Medium-Priority Issues

### 4.1 🟡 Deployment Size Pressure

**File**: [`pyproject.toml`](../backend/pyproject.toml:6)

**Problem**: DigitalOcean Functions has a **48 MB built function size limit**. Current dependencies are estimated at ~42 MB, leaving only ~6 MB of headroom. Adding any new dependency (e.g., `numpy`, `pandas`, `scikit-learn`) would breach this limit.

| Dependency | Estimated Size | Required at Runtime? |
|------------|---------------|---------------------|
| `sqlalchemy[asyncio]` | ~8 MB | ✅ Yes |
| `asyncpg` | ~5 MB | ✅ Yes |
| `pydantic` + `pydantic-settings` | ~4 MB | ✅ Yes |
| `httpx` + dependencies | ~6 MB | ✅ Yes |
| `redis` | ~3 MB | ✅ Yes |
| `openai` | ~10 MB | ✅ Yes (OpenRouter client) |
| `alembic` | **~3 MB** | ❌ No — only needed for migrations |
| Other (stdlib, etc.) | ~3 MB | — |
| **Total** | **~42 MB** | **48 MB limit** |

**Recommended Fix**:
1. Move `alembic` from `dependencies` to `dev` dependencies — run migrations via a separate script, not inside functions
2. Consider replacing `openai` SDK with direct `httpx` calls to OpenRouter API (saves ~8 MB)
3. Monitor bundle size in CI with a size-check step

---

### 4.2 🟡 Connection Pool Disposal Pattern

**File**: [`db.py`](../backend/packages/shared/db.py:48)

**Problem**: Every cron function calls `await dispose_engine()` in its `finally` block (see [`historic-refresh/__init__.py:245`](../backend/packages/cron/historic-refresh/__init__.py:245)). This destroys the SQLAlchemy engine and its connection pool after every invocation. On the next invocation, a new engine must be created, new TCP connections established, and the pool warmed up again.

The [`get_engine()`](../backend/packages/shared/db.py:13) function uses a singleton pattern designed for cold starts, but `dispose_engine()` nullifies this optimization by forcing a cold start on every invocation.

**Impact**:
- ~100–300ms overhead per invocation for engine creation
- Unnecessary TCP handshake load on PostgreSQL
- Connection pool settings (`pool_size=2`, `max_overflow=3`, `pool_recycle=300`) are effectively unused

**Recommended Fix**:
1. **Remove `dispose_engine()` from `finally` blocks** — let the FaaS runtime handle cleanup when the container is evicted
2. **Keep `dispose_engine()` available** for use in admin/shutdown endpoints only
3. **Add health check logging** to verify the engine singleton persists across warm invocations

---

### 4.3 🟡 Log Retention

**Problem**: DigitalOcean Functions provides only **3-day log retention**. For a production system with weekly cron jobs (historic-refresh runs Sundays), this means:
- Weekend failures may be logged out by Tuesday
- No historical performance data for trend analysis
- Cannot debug issues reported by users after 3 days

**Recommended Fix**:
1. **Ship logs to external service** — use a lightweight logging integration (e.g., Logtail, Axiom, or DO's own Managed Logs)
2. **Write critical logs to the database** — extend `job_executions` table to store structured log entries for cron functions
3. **Add log-export script** to the CI/CD pipeline for post-mortem analysis

---

### 4.4 🟡 No Built-in Observability

**Problem**: There are no metrics, dashboards, or alerting for function execution. The `config.py` defines `metrics_enabled: bool = True` and `alert_webhook_url: Optional[str] = None`, but these settings are **not consumed** by any function — they are dead configuration.

| Missing Capability | Impact |
|--------------------|--------|
| Invocation count / error rate | Cannot detect anomalies |
| P95/P99 latency | Cannot identify performance degradation |
| Memory utilization | Cannot right-size function limits |
| Alerting on failures | Silent cron failures go unnoticed |
| Distributed tracing | Cannot correlate API → DB → Redis calls |

**Recommended Fix**:
1. **Implement the webhook alerting** defined in config — send structured alerts to Slack/Discord on cron failures
2. **Use `job_executions` table** as a metrics source — build a simple admin dashboard query
3. **Add DO Functions monitoring** via DO's built-in metrics (limited but better than nothing)
4. **Consider** a lightweight APM integration (e.g., Sentry for errors, Hyperdx for logs+traces)

---

### 4.5 🟡 Alembic as Runtime Dependency

**File**: [`pyproject.toml`](../backend/pyproject.toml:14)

**Problem**: `alembic>=1.14.0` is listed in the runtime `dependencies` array but is only needed for database migrations, which are run separately via [`scripts/run-migrations.sh`](../backend/scripts/run-migrations.sh). Including it in every function's deployment bundle wastes ~3 MB.

**Recommended Fix**:
```toml
# Move from dependencies to dev group
[project.optional-dependencies]
dev = [
    # ... existing dev deps ...
    "alembic>=1.14.0",
]
```

---

## 5. Low-Priority / Nice-to-Have Improvements

### 5.1 Integration Tests

**Current state**: Tests in [`backend/tests/`](../backend/tests/) are unit tests only. There are no integration tests that exercise functions end-to-end against a real database.

**Recommendation**: Add integration tests using `testcontainers` (already a dev dependency) to verify:
- API functions return correct HTTP responses
- Cron functions complete full job lifecycle (lock → execute → record → unlock)
- Cache invalidation works correctly
- Error handling produces expected HTTP status codes

---

### 5.2 Consistent Import Patterns

**Current state**: All functions use the same `sys.path` manipulation pattern:

```python
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from packages.shared.db import ...
```

This works but is fragile — any directory restructuring breaks all imports silently.

**Recommendation**: Consider one of:
- A shared `path_setup.py` module that centralizes the `sys.path` manipulation
- A `setup.py` / `pyproject.toml` editable install of the shared package
- DO Functions layer support (if available) for shared dependencies

---

### 5.3 Historic Refresh Continuation Mechanism

**Current state**: The [`historic-refresh`](../backend/packages/cron/historic-refresh/__init__.py:52) function has a batch selection mechanism (`_resolve_batch()`) that can accept `start_season` from args, but there's no automation to trigger the next batch.

**Recommendation**: Implement a continuation mechanism:
1. After each batch completes, use the DO Functions API to self-trigger with `start_season` for the next batch
2. Store batch progress in the `generation_progress` table
3. On invocation, check if a previous run has incomplete batches and resume from there
4. This is closely related to fixing the timeout issue (§3.1)

---

### 5.4 Request Validation

**Current state**: API functions use manual request parsing via [`parse_request()`](../backend/packages/shared/api_helpers.py:11) but lack schema validation for query parameters and request bodies.

**Recommendation**: Add Pydantic model validation at the API boundary to catch malformed requests early and return proper 422 responses.

---

## 6. Decision Matrix

Three possible paths forward, evaluated across key dimensions:

| Criterion | Keep FaaS as-is | Hybrid (FaaS API + Container cron) | Full Container Migration |
|-----------|----------------|-------------------------------------|--------------------------|
| **Monthly cost** | ~$15 | ~$25–30 | ~$30–40 |
| **Operational complexity** | Low (single platform) | Medium (two platforms) | Medium (single platform) |
| **Timeout safety** | ❌ Cron functions at risk | ✅ Cron in containers = no limit | ✅ No platform limits |
| **Observability** | ❌ Minimal | ⚠️ Split across platforms | ✅ Full control |
| **Scalability** | ✅ Auto-scale to zero | ⚠️ API scales, cron fixed | ❌ Manual scaling |
| **Cold start latency** | ⚠️ 200–500ms | ⚠️ API only | ✅ Persistent process |
| **Deployment simplicity** | ✅ Single `doctl deploy` | ⚠️ Two deploy pipelines | ⚠️ Container registry + deploy |
| **Connection pooling** | ❌ Negated by disposal | ✅ Cron benefits, API same | ✅ Full pooling |
| **Log retention** | ❌ 3 days | ⚠️ API 3 days, cron full | ✅ Full control |
| **Maintenance burden** | ✅ Low | ⚠️ Dual codebases/styles | ⚠️ Full infrastructure |

### Scoring (1–5, 5 = best)

| Criterion | Weight | FaaS as-is | Hybrid | Full Container |
|-----------|--------|-----------|--------|---------------|
| Cost | 20% | 5 | 3 | 2 |
| Timeout safety | 25% | 1 | 5 | 5 |
| Observability | 15% | 1 | 3 | 4 |
| Scalability | 10% | 5 | 3 | 2 |
| Deployment simplicity | 10% | 5 | 2 | 3 |
| Connection pooling | 5% | 1 | 3 | 5 |
| Log retention | 5% | 1 | 3 | 5 |
| Maintenance | 10% | 4 | 2 | 3 |
| **Weighted Score** | — | **2.85** | **3.40** | **3.45** |

> **Note**: The hybrid and full container approaches score nearly identically. The deciding factor is the hybrid approach's ability to keep the cost-effective FaaS API while solving the critical timeout issue with containers for cron jobs.

---

## 7. Recommendation

### Primary Recommendation: **Fix Critical Issues + Plan Hybrid Migration**

The FaaS architecture is **sound for API functions** — they are short-lived, stateless, and benefit from auto-scaling. The critical issues are isolated to **cron functions** that require longer runtimes than the platform allows.

#### Phase 1: Immediate Fixes (Before Production)

Priority-ordered action items to make the current FaaS deployment production-safe:

| # | Action | Issue | Effort | Impact |
|---|--------|-------|--------|--------|
| 1 | Add `limits:` overrides to all functions in `project.yml` | §3.3 | 30 min | Prevents default 60s timeout kills |
| 2 | Fix cron expressions to UTC equivalents | §3.2 | 15 min | Correct scheduling for AWST |
| 3 | Reduce `historic-refresh` batch size to 2 seasons | §3.1 | 1 hr | Fits within platform timeout |
| 4 | Reduce lock expiry to match platform timeout | §3.1 | 15 min | Prevents dangling locks |
| 5 | Move `alembic` from runtime to dev dependencies | §4.5 | 15 min | Saves ~3 MB deployment size |
| 6 | Remove `dispose_engine()` from cron `finally` blocks | §4.2 | 30 min | Restores connection pooling |
| 7 | Implement webhook alerting for cron failures | §4.4 | 2 hr | Enables failure detection |

**Total estimated effort**: ~4.5 hours

#### Phase 2: Short-Term Improvements (Next Sprint)

| # | Action | Issue | Effort |
|---|--------|-------|--------|
| 1 | Add batch continuation mechanism for `historic-refresh` | §5.3 | 4 hr |
| 2 | Set up external log shipping (Logtail/Axiom) | §4.3 | 2 hr |
| 3 | Add integration tests with `testcontainers` | §5.1 | 8 hr |
| 4 | Add CI size-check step for deployment bundle | §4.1 | 1 hr |
| 5 | Replace `openai` SDK with direct `httpx` calls | §4.1 | 3 hr |

#### Phase 3: Hybrid Migration (If Cron Functions Need More Headroom)

If reducing batch sizes doesn't solve the timeout issue, or if new long-running cron jobs are needed:

1. **Keep API functions on DO Functions** — they work well and are cost-effective
2. **Migrate cron functions to a lightweight container** (DO App Platform or a small Droplet)
3. Cron functions benefit from persistent connections, unlimited timeouts, and full logging
4. Shared package (`packages/shared/`) is reused across both platforms

This preserves the FaaS cost advantage for API traffic while eliminating the timeout risk for background processing.

---

## Appendix A: Platform Constraint Reference

| Constraint | Value | Source |
|-----------|-------|--------|
| Max function timeout | 15 minutes (900,000 ms) | DO Functions documentation |
| Max function memory | 4,096 MB | DO Functions documentation |
| Max deployment size | 48 MB (built) | DO Functions documentation |
| Log retention | 3 days | DO Functions documentation |
| Cron timezone | UTC only | DO Functions documentation |
| Runtimes | Python 3.x, Node.js, Go, PHP, etc. | DO Functions documentation |
| Free tier | 3M GB-s + 50,000 requests/month | DO Functions pricing |

## Appendix B: File Reference

| File | Purpose | Lines |
|------|---------|-------|
| [`project.yml`](../backend/project.yml) | DO Functions project configuration | 109 |
| [`config.py`](../backend/packages/shared/config.py) | Pydantic settings with cron configuration | 97 |
| [`db.py`](../backend/packages/shared/db.py) | Database engine + session factory | 58 |
| [`cache.py`](../backend/packages/shared/cache.py) | Redis-backed 3-tier caching | 244 |
| [`api_helpers.py`](../backend/packages/shared/api_helpers.py) | HTTP request parsing, CORS, auth | 160 |
| [`orchestrator.py`](../backend/packages/shared/orchestrator.py) | ML model orchestration | 156 |
| [`historic-refresh/__init__.py`](../backend/packages/cron/historic-refresh/__init__.py) | Weekly historical data refresh cron | 246 |
| [`pyproject.toml`](../backend/pyproject.toml) | Dependencies and build config | 43 |
