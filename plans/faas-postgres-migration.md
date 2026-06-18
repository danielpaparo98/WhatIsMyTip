# WhatIsMyTip: FaaS + PostgreSQL Migration Plan

> **DESIGN HISTORY — superseded by current implementation.**  The FaaS+PostgreSQL migration
> described here was **completed and superseded** by the FastAPI monolith migration.  The
> consolidated PostgreSQL schema is now in
> [`backend/alembic/versions/2026_05_28_1613-0001_consolidated_postgresql_schema.py`](../backend/alembic/versions/2026_05_28_1613-0001_consolidated_postgresql_schema.py).
> See [`docs/migrations.md`](../docs/migrations.md).
>
> **⚠️ HISTORICAL DOCUMENT** — This plan was written during the `backend-faas` era.
> The directory has since been renamed to `backend/`. Paths like `backend-faas/...` should be read as `backend/...` today.

> **Status:** Draft — Pending Review
> **Date:** 2026-05-28  
> **Author:** Architecture Team  
> **Target:** Migrate from FastAPI + SQLite (DO App Platform) → Digital Ocean Functions + Managed PostgreSQL + Managed Redis

---

## Table of Contents

1. [Executive Summary](#1-executive-summary)
2. [Architecture Overview](#2-architecture-overview)
3. [Function Decomposition](#3-function-decomposition)
4. [Database Migration](#4-database-migration-sqlite-to-postgresql)
5. [Caching Strategy](#5-caching-strategy-in-memory-to-redis)
6. [Cron Jobs Migration](#6-cron-jobs-migration)
7. [Rate Limiting](#7-rate-limiting)
8. [ML Models Strategy](#8-ml-models-strategy)
9. [Frontend Changes](#9-frontend-changes)
10. [Testing Strategy](#10-testing-strategy)
11. [Deployment Strategy](#11-deployment-strategy)
12. [Migration Phases](#12-migration-phases)
13. [Risk Assessment](#13-risk-assessment)
14. [Cost Analysis](#14-cost-analysis)

---

## 1. Executive Summary

### Current State

The WhatIsMyTip backend runs as a single FastAPI process on Digital Ocean App Platform with:
- **SQLite** via SQLAlchemy async (aiosqlite)
- **3-tier in-memory cache** (60s / 300s / 3600s TTL)
- **In-process cron scheduler** (croniter-based, 4 jobs)
- **slowapi** rate limiting (in-memory)
- **4 ML models** — all pure Python + SQL (no numpy/scikit-learn despite being listed in deps)

### Target State

Migrate to a serverless architecture on Digital Ocean:
- **DO Functions** (Apache OpenWhisk/Nimbella-based) for all API endpoints and scheduled jobs
- **Managed PostgreSQL** for persistent storage
- **Managed Redis** for caching, rate limiting, and job locking

### Why Migrate

| Factor | Current | Target |
|--------|---------|--------|
| Scaling | Single instance, manual | Per-function auto-scaling |
| Cost | ~$5/mo always-on | Pay-per-invocation, lower idle cost |
| Cold start data | In-memory caches lost on restart | Redis persists across invocations |
| DB concurrency | SQLite single-writer lock | PostgreSQL concurrent reads/writes |
| Operational | Custom cron scheduler to maintain | Native scheduled functions |

### Critical Discovery

**numpy and scikit-learn are listed in [`pyproject.toml`](../backend/pyproject.toml:17) but are NOT imported anywhere in the codebase.** All four ML models ([`EloModel`](../backend/app/models_ml/elo.py), [`FormModel`](../backend/app/models_ml/form.py), [`HomeAdvantageModel`](../backend/app/models_ml/home_advantage.py), [`ValueModel`](../backend/app/models_ml/value.py)) use pure Python math and SQL queries. These dependencies can be dropped entirely, reducing package size by ~50MB+ and eliminating cold start concerns.

---

## 2. Architecture Overview

### Current Architecture

```
┌─────────────────────────────────────────────────────┐
│              DO App Platform - Single Container       │
│                                                       │
│  ┌──────────┐  ┌──────────┐  ┌───────────────────┐  │
│  │ FastAPI   │  │ CronJob  │  │ In-Memory Cache   │  │
│  │ Routes    │  │ Manager  │  │ 60s/300s/3600s    │  │
│  │ 15+       │  │ 4 jobs   │  │                   │  │
│  │ endpoints │  │ croniter │  │ EloModel cache    │  │
│  └─────┬─────┘  └────┬─────┘  │ HomeAdv cache     │  │
│        │              │        └────────┬──────────┘  │
│        └──────┬───────┘                 │             │
│               │                         │             │
│        ┌──────┴──────┐                  │             │
│        │ SQLAlchemy  │◄─────────────────┘             │
│        │ aiosqlite   │                                │
│        └──────┬──────┘                                │
│               │                                       │
│        ┌──────┴──────┐                                │
│        │ SQLite File │                                │
│        │ whatismytip │                                │
│        │   .db       │                                │
│        └─────────────┘                                │
└─────────────────────────────────────────────────────┘
```

### Target Architecture

```
┌──────────────────────────────────────────────────────────────────┐
│                        DO App Platform                            │
│                    Frontend - Nuxt.js Static Site                 │
└────────────────────────────┬─────────────────────────────────────┘
                             │
                             ▼
┌──────────────────────────────────────────────────────────────────┐
│                    DO Functions - Serverless                      │
│                                                                    │
│  ┌─── HTTP Functions ──────────────────────────────────────────┐  │
│  │                                                              │  │
│  │  games-fn ──── tips-fn ──── backtest-fn ──── admin-fn       │  │
│  │  GET /games     GET /tips    GET /backtest    POST trigger   │  │
│  │  GET /games/id  GET /tips/id                              │  │
│  │                                                              │  │
│  └──────────────────────────────────────────────────────────────┘  │
│                                                                    │
│  ┌─── Scheduled Functions ─────────────────────────────────────┐  │
│  │                                                              │  │
│  │  daily-sync        */15 min        timeout: 900s            │  │
│  │  match-completion  */15 min offset timeout: 300s            │  │
│  │  tip-generation    daily 3AM       timeout: 1800s           │  │
│  │  historic-refresh  weekly Sun 4AM  timeout: 3600s per chunk │  │
│  │                                                              │  │
│  └──────────────────────────────────────────────────────────────┘  │
│                                                                    │
└──────────────┬───────────────────────┬─────────────────────────────┘
               │                       │
       ┌───────┴───────┐       ┌───────┴───────┐
       │  PostgreSQL   │       │    Redis      │
       │  Managed DB   │       │  Managed      │
       │               │       │               │
       │ - Games       │       │ - API cache   │
       │ - Tips        │       │ - Elo ratings │
       │ - Predictions │       │ - Rate limits │
       │ - Backtest    │       │ - Job locks   │
       │ - Job tracking│       │ - Home adv    │
       │ - Elo cache   │       │   cache       │
       │ - Match analys│       │               │
       └───────────────┘       └───────────────┘
               ▲
               │
       ┌───────┴───────┐
       │ Squiggle API  │
       │ OpenRouter AI │
       └───────────────┘
```

### Data Flow Diagram

```mermaid
flowchart LR
    Client[Frontend Client] -->|HTTP GET| Gateway[DO Functions Gateway]
    Gateway --> GamesFn[games-fn]
    Gateway --> TipsFn[tips-fn]
    Gateway --> BacktestFn[backtest-fn]
    Gateway --> AdminFn[admin-fn]
    
    GamesFn --> PG[(PostgreSQL)]
    TipsFn --> PG
    BacktestFn --> PG
    AdminFn --> PG
    
    GamesFn --> Redis[(Redis Cache)]
    TipsFn --> Redis
    BacktestFn --> Redis
    
    Scheduler[DO Scheduler] --> DailySync[daily-sync]
    Scheduler --> MatchComp[match-completion]
    Scheduler --> TipGen[tip-generation]
    Scheduler --> HistRefresh[historic-refresh]
    
    DailySync --> PG
    DailySync --> SquiggleAPI[Squiggle API]
    TipGen --> PG
    TipGen --> OpenRouter[OpenRouter AI]
    TipGen --> Redis
    HistRefresh --> PG
```

---

## 3. Function Decomposition

### 3.1 Mapping Endpoints to Functions

The current monolithic FastAPI app exposes ~15 endpoints. These map to **4 HTTP functions** and **4 scheduled functions**.

#### HTTP Functions (Web-triggered)

| Function Name | Current Endpoints | Method | Timeout | Auth |
|---|---|---|---|---|
| `games-fn` | `/api/games`, `/api/games/{slug}`, `/api/games/{slug}/detail` | GET | 15s | None |
| `tips-fn` | `/api/tips`, `/api/tips/games-with-tips`, `/api/tips/{heuristic}`, `/api/tips/generate` | GET/POST | 30s | API key for generate |
| `backtest-fn` | `/api/backtest/*` (5 endpoints) | GET | 30s | None |
| `admin-fn` | `/api/admin/jobs/*/trigger` (4 endpoints), `/health`, `/health/cron` | GET/POST | 60s | API key |

#### Scheduled Functions (Cron-triggered)

| Function Name | Schedule | Current Timeout | DO Timeout | Notes |
|---|---|---|---|---|
| `daily-sync` | `*/15 * * * *` | 3600s | 900s | Reduce from 1hr; sync should be fast |
| `match-completion` | `5,20,35,50 * * * *` | 300s | 300s | Direct mapping |
| `tip-generation` | `0 3 * * *` | 1800s | 1800s | Within 60-min limit |
| `historic-refresh` | `0 4 * * 0` | 7200s | 3600s per chunk | **Must be chunked** |

### 3.2 Shared Code Strategy

DO Functions require each function to be self-contained. Shared code must be packaged and included in each function that needs it.

```
backend-faas/
├── packages/
│   ├── shared/                    # Shared library - symlinked or vendored
│   │   ├── __init__.py
│   │   ├── config.py              # Settings from env vars
│   │   ├── db.py                  # PostgreSQL connection pool
│   │   ├── cache.py               # Redis-backed cache
│   │   ├── models/                # SQLAlchemy ORM models
│   │   │   ├── __init__.py
│   │   │   └── ... (9 models)
│   │   ├── schemas/               # Pydantic schemas
│   │   │   ├── __init__.py
│   │   │   └── ... (5 schemas)
│   │   ├── crud/                  # CRUD operations
│   │   │   ├── __init__.py
│   │   │   └── ... (8 CRUD classes)
│   │   ├── services/              # Business logic
│   │   │   ├── __init__.py
│   │   │   └── ... (7 services)
│   │   ├── models_ml/             # ML models (pure Python)
│   │   │   ├── __init__.py
│   │   │   ├── base.py
│   │   │   ├── elo.py
│   │   │   ├── form.py
│   │   │   ├── home_advantage.py
│   │   │   └── value.py
│   │   ├── heuristics/            # Heuristic layers
│   │   │   ├── __init__.py
│   │   │   ├── base.py
│   │   │   ├── best_bet.py
│   │   │   ├── yolo.py
│   │   │   └── high_risk_high_reward.py
│   │   ├── orchestrator.py        # ModelOrchestrator
│   │   ├── squiggle/              # Squiggle API client
│   │   │   ├── __init__.py
│   │   │   ├── client.py
│   │   │   └── utils.py
│   │   ├── openrouter/            # OpenRouter AI client
│   │   │   ├── __init__.py
│   │   │   └── client.py
│   │   ├── logger.py
│   │   └── utils.py
│   │
│   ├── api/                       # HTTP-triggered functions
│   │   ├── games/
│   │   │   ├── __init__.py
│   │   │   └── main.py            # Entry point: main(args)
│   │   ├── tips/
│   │   │   ├── __init__.py
│   │   │   └── main.py
│   │   ├── backtest/
│   │   │   ├── __init__.py
│   │   │   └── main.py
│   │   └── admin/
│   │       ├── __init__.py
│   │       └── main.py
│   │
│   └── cron/                      # Scheduled functions
│       ├── daily-sync/
│       │   ├── __init__.py
│       │   └── main.py
│       ├── match-completion/
│       │   ├── __init__.py
│       │   └── main.py
│       ├── tip-generation/
│       │   ├── __init__.py
│       │   └── main.py
│       └── historic-refresh/
│           ├── __init__.py
│           └── main.py
│
├── project.yml                    # DO Functions project config
├── pyproject.toml                 # Dependencies
└── README.md
```

### 3.3 DO Functions Entry Point Pattern

Each function follows the DO Functions (OpenWhisk) convention:

```python
# packages/api/games/main.py
async def main(args):
    """DO Functions entry point for games API."""
    from shared.db import get_db_session
    from shared.config import settings
    # ... route based on args['__ow_path'] and args['__ow_method']
    return {
        "statusCode": 200,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(response_data)
    }
```

### 3.4 Routing Within Functions

Each HTTP function handles internal routing via `__ow_path`:

```python
async def main(args):
    path = args.get("__ow_path", "/")
    method = args.get("__ow_method", "GET").upper()
    
    if path == "" or path == "/":
        return await handle_list_games(args)
    elif path.startswith("/") and path.count("/") == 1:
        slug = path.lstrip("/")
        return await handle_game_detail(args, slug)
    elif path.endswith("/detail"):
        slug = path.split("/")[1]
        return await handle_game_detail_full(args, slug)
    else:
        return {"statusCode": 404, "body": json.dumps({"error": "Not found"})}
```

### 3.5 `project.yml` Configuration

```yaml
packages:
  api:
    functions:
      games:
        runtime: python:3.11
        web: true
        handler: main.main
        environment:
          DATABASE_URL: ${DATABASE_URL}
          REDIS_URL: ${REDIS_URL}
        parameters:
          __ow_cors:
            allowed_origins: ["https://whatismytip.com", "https://www.whatismytip.com"]
            allowed_methods: ["GET", "OPTIONS"]
        limits:
          timeout: 15000
          memory: 256MB
      tips:
        runtime: python:3.11
        web: true
        handler: main.main
        limits:
          timeout: 30000
          memory: 256MB
      backtest:
        runtime: python:3.11
        web: true
        handler: main.main
        limits:
          timeout: 30000
          memory: 256MB
      admin:
        runtime: python:3.11
        web: true
        handler: main.main
        limits:
          timeout: 60000
          memory: 512MB

  cron:
    functions:
      daily-sync:
        runtime: python:3.11
        handler: main.main
        triggers:
          - cron: "*/15 * * * *"
        limits:
          timeout: 900000    # 15 min
          memory: 256MB
      match-completion:
        runtime: python:3.11
        handler: main.main
        triggers:
          - cron: "5,20,35,50 * * * *"
        limits:
          timeout: 300000    # 5 min
          memory: 256MB
      tip-generation:
        runtime: python:3.11
        handler: main.main
        triggers:
          - cron: "0 3 * * *"
        limits:
          timeout: 1800000   # 30 min
          memory: 512MB
      historic-refresh:
        runtime: python:3.11
        handler: main.main
        triggers:
          - cron: "0 4 * * 0"
        limits:
          timeout: 3600000   # 60 min (max for scheduled)
          memory: 512MB
```

### 3.6 Shared Code Distribution

Since DO Functions packages each function independently, shared code must be available to each function. The recommended approach:

**Option A: Symlink/Vendor (Recommended)**
- Use a build script that copies `packages/shared/` into each function's directory before deployment
- Simple, reliable, no external dependencies
- Build script: `scripts/build-functions.sh`

**Option B: Python Package**
- Build `shared/` as a wheel and include it in each function's dependencies
- More complex but cleaner dependency management

We recommend **Option A** for simplicity. Each function deployment will include a copy of the shared code.

---

## 4. Database Migration (SQLite → PostgreSQL)

### 4.1 Connection Strategy

**Current** ([`app/db/__init__.py`](../backend/app/db/__init__.py)):
```python
engine = create_async_engine("sqlite+aiosqlite:///./whatismytip.db")
```

**Target:**
```python
# shared/db.py
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker

# Connection pool configuration for serverless
engine = create_async_engine(
    settings.database_url,  # postgresql+asyncpg://user:pass@host/db
    pool_size=5,            # Base connections per function instance
    max_overflow=10,        # Allow burst up to 15 connections
    pool_timeout=30,        # Wait 30s for connection
    pool_recycle=300,       # Recycle connections every 5 min
    pool_pre_ping=True,     # Verify connections before use
    echo=settings.environment == "development"
)

AsyncSessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
```

### 4.2 Driver Change

| Component | Current | Target |
|-----------|---------|--------|
| Driver | `aiosqlite` | `asyncpg` |
| URL scheme | `sqlite+aiosqlite:///./db` | `postgresql+asyncpg://user:pass@host/db` |
| Connection pooling | N/A (file-based) | SQLAlchemy pool + PgBouncer if needed |

### 4.3 Schema Migration Plan

**Current:** 12 Alembic migrations in [`backend/alembic/versions/`](../backend/alembic/versions/)

**Strategy:** Create a single clean migration that represents the full schema for PostgreSQL:

1. **Consolidate** all 12 migrations into one clean `001_initial_schema.py`
2. **Modify** column types for PostgreSQL compatibility:
   - `String` columns: Add explicit lengths (PostgreSQL requires them for indexed columns)
   - `DateTime(timezone=True)`: Use `TIMESTAMPTZ` (PostgreSQL native)
   - `Text`: No changes needed (PostgreSQL handles this natively)
   - `Boolean`: No changes needed
3. **Add indexes** that are currently defined across multiple migrations
4. **Run migration** against fresh PostgreSQL database

**Schema Changes Required:**

| Model | Current | Change Needed |
|-------|---------|---------------|
| [`Game`](../backend/app/models/__init__.py:8) | `slug = Column(String(12))` | No change |
| [`Game`](../backend/app/models/__init__.py:8) | `date = Column(DateTime)` | Consider `TIMESTAMPTZ` for timezone awareness |
| [`MatchAnalysis`](../backend/app/models/__init__.py:146) | `created_at = Column(DateTime, default=datetime.utcnow)` | Change to `server_default=func.now()` and use timezone-aware |
| All models | `server_default=func.now()` | Works as-is with PostgreSQL |

### 4.4 Data Migration

```mermaid
flowchart TD
    A[Export SQLite Data] --> B[Transform Schema]
    B --> C[Import to PostgreSQL]
    C --> D[Verify Data Integrity]
    D --> E[Run Alembic Migration]
    E --> F[Validate Counts]
```

**Steps:**
1. Export SQLite data as JSON/CSV using a migration script
2. Create PostgreSQL schema via consolidated Alembic migration
3. Import data using batch INSERT scripts
4. Verify row counts match for each table
5. Run application-level validation queries

**Migration Script Location:** `backend-faas/scripts/migrate_sqlite_to_pg.py`

### 4.5 Connection Pooling Consideration

For serverless functions, each function instance creates its own connection pool. With 8 functions potentially running concurrently:

- **Worst case:** 8 instances × 15 max connections = 120 connections
- **PostgreSQL managed DB default:** ~25-97 connections (depends on plan)
- **Mitigation:** Use PgBouncer in transaction mode, or configure conservative pool sizes

**Recommended pool settings per function:**
```python
pool_size=2,       # Base: 2 connections per function
max_overflow=3,    # Burst: up to 5 total per function
```

This gives a maximum of 8 × 5 = 40 connections, well within managed PostgreSQL limits.

---

## 5. Caching Strategy (In-Memory → Redis)

### 5.1 Current Cache Architecture

[`app/cache.py`](../backend/app/cache.py) defines three global cache instances:

| Instance | TTL | Max Size | Used For |
|----------|-----|----------|----------|
| `short_cache` | 60s | 500 | Frequently changing data |
| `medium_cache` | 300s | 200 | Moderately changing data (Squiggle API responses) |
| `long_cache` | 3600s | 100 | Rarely changing data |

Plus two class-level caches in ML models:
- [`EloModel._ratings_cache`](../backend/app/models_ml/elo.py:20) — Dictionary of team → rating, initialized from DB
- [`HomeAdvantageModel._cache`](../backend/app/models_ml/home_advantage.py:13) — Venue advantage data with 1hr TTL

### 5.2 Redis Cache Replacement

**New `shared/cache.py`:**

```python
import json
import redis.asyncio as redis
from typing import Any, Optional, Callable
from shared.config import settings

class RedisCache:
    """Redis-backed cache with TTL support, replacing InMemoryCache."""
    
    def __init__(self, redis_client: redis.Redis, default_ttl: float = 600.0, key_prefix: str = ""):
        self.redis = redis_client
        self.default_ttl = default_ttl
        self.key_prefix = key_prefix
    
    async def get(self, key: str) -> Optional[Any]:
        value = await self.redis.get(f"{self.key_prefix}{key}")
        if value is None:
            return None
        return json.loads(value)
    
    async def set(self, key: str, value: Any, ttl: Optional[float] = None) -> None:
        ttl = ttl if ttl is not None else self.default_ttl
        await self.redis.setex(
            f"{self.key_prefix}{key}",
            int(ttl),
            json.dumps(value, default=str)
        )
    
    async def delete(self, key: str) -> bool:
        return bool(await self.redis.delete(f"{self.key_prefix}{key}"))
    
    async def delete_pattern(self, pattern: str) -> int:
        keys = []
        async for key in self.redis.scan_iter(match=f"{self.key_prefix}{pattern}"):
            keys.append(key)
        if keys:
            return await self.redis.delete(*keys)
        return 0


# Cache instances matching current tiers
def get_short_cache(redis_client: redis.Redis) -> RedisCache:
    return RedisCache(redis_client, default_ttl=60, key_prefix="cache:short:")

def get_medium_cache(redis_client: redis.Redis) -> RedisCache:
    return RedisCache(redis_client, default_ttl=300, key_prefix="cache:medium:")

def get_long_cache(redis_client: redis.Redis) -> RedisCache:
    return RedisCache(redis_client, default_ttl=3600, key_prefix="cache:long:")
```

### 5.3 `@cached` Decorator Migration

The existing [`@cached`](../backend/app/cache.py:116) decorator needs to become async-aware with Redis:

```python
def cached(cache_getter, key_prefix: str = "", ttl: Optional[float] = None):
    """Redis-backed caching decorator."""
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        async def wrapper(*args, **kwargs):
            cache = cache_getter()  # Lazy init to get redis client
            cache_args = args[1:] if args else ()
            cache_key = f"{key_prefix}{func.__name__}:{str(cache_args)}:{str(sorted(kwargs.items()))}"
            
            cached_value = await cache.get(cache_key)
            if cached_value is not None:
                return cached_value
            
            result = await func(*args, **kwargs)
            await cache.set(cache_key, result, ttl)
            return result
        return wrapper
    return decorator
```

### 5.4 ML Model Cache Migration

**EloModel** ([`app/models_ml/elo.py`](../backend/app/models_ml/elo.py)):
- Currently: Class-level `_ratings_cache` dict, initialized from DB on first use
- Migration: Store ratings in Redis hash `elo:ratings` with TTL matching the sync schedule
- On cold start: Read from Redis instead of recomputing from all games
- Fallback: If Redis miss, compute from DB and populate Redis

```python
# New EloModel cache pattern
async def _initialize_cache(cls, db: AsyncSession):
    redis_client = get_redis()
    cached = await redis_client.hgetall("elo:ratings")
    if cached:
        cls._ratings_cache = {k.decode(): float(v) for k, v in cached.items()}
        cls._cache_initialized = True
        return
    # Fallback: compute from DB and store in Redis
    await cls._compute_and_cache_ratings(db)
    await redis_client.expire("elo:ratings", 3600)  # 1hr TTL
```

**HomeAdvantageModel** ([`app/models_ml/home_advantage.py`](../backend/app/models_ml/home_advantage.py)):
- Currently: Class-level `_cache` dict with `_cache_expiry` TTL
- Migration: Use Redis with TTL (automatic expiration)
- Key pattern: `home_adv:{game_date_iso}`

### 5.5 Cache Warming Strategy

Cold starts are the primary concern with FaaS. Strategy:

1. **Elo ratings:** Pre-computed after every daily-sync job → stored in Redis → available instantly
2. **Home advantage:** Computed on first request → cached in Redis with 1hr TTL → survives cold starts
3. **API response cache:** First request populates → subsequent requests hit Redis → no cold start penalty
4. **No explicit warming needed** — Redis persists across function invocations

### 5.6 Cache Invalidation

Replace [`invalidate_cache_pattern`](../backend/app/cache.py:207) with Redis `SCAN` + `DEL`:

```python
async def invalidate_cache_pattern(cache: RedisCache, pattern: str) -> int:
    return await cache.delete_pattern(f"*{pattern}*")
```

---

## 6. Cron Jobs Migration

### 6.1 Job Mapping

| Current Job | Current Class | DO Function | Schedule | DO Timeout Limit |
|---|---|---|---|---|
| [`DailyGameSync`](../backend/app/cron/jobs/daily_sync.py) | `DailyGameSyncJob` | `daily-sync` | `*/15 * * * *` | 900s (15 min) |
| [`MatchCompletion`](../backend/app/cron/jobs/match_completion.py) | `MatchCompletionJob` | `match-completion` | `5,20,35,50 * * * *` | 300s (5 min) |
| [`TipGeneration`](../backend/app/cron/jobs/tip_generation.py) | `TipGenerationJob` | `tip-generation` | `0 3 * * *` | 1800s (30 min) |
| [`HistoricRefresh`](../backend/app/cron/jobs/historic_refresh.py) | `HistoricDataRefreshJob` | `historic-refresh` | `0 4 * * 0` | 3600s (60 min) |

### 6.2 HistoricRefresh Chunking Strategy

**Problem:** Current timeout is 7200s (2 hours). DO Functions max for scheduled functions is 3600s (60 min).

**Solution:** Break the historic refresh into season-by-season chunks:

```mermaid
flowchart TD
    A[historic-refresh triggered] --> B{Check progress in Redis}
    B -->|No progress| C[Process seasons 2010-2013]
    B -->|Chunk 1 done| D[Process seasons 2014-2017]
    B -->|Chunk 2 done| E[Process seasons 2018-2021]
    B -->|Chunk 3 done| F[Process seasons 2022-2025]
    C --> G[Store progress in Redis]
    D --> G
    E --> G
    F --> G
    G --> H{More chunks?}
    H -->|Yes| I[Trigger self via DO API]
    H -->|No| J[Mark complete]
    I --> B
```

**Implementation:**
- Each invocation processes 3-4 seasons (~15 min per chunk)
- Store progress in Redis: `historic_refresh:progress` → `{last_season: 2013, status: "chunk_1_done"}`
- After each chunk, the function triggers itself via the DO Functions API
- If the function times out mid-chunk, the next scheduled run picks up from the last completed season

**Alternative:** Use a separate "worker" function that's triggered by the cron function and processes one season at a time in a loop, with the cron function acting as an orchestrator.

### 6.3 Job Locking Strategy

**Current:** DB-based [`JobLock`](../backend/app/models/__init__.py:115) table with expiry timestamps.

**Migration Options:**

| Approach | Pros | Cons |
|----------|------|------|
| **Keep DB-based** | Already implemented, no changes | DB connection overhead, potential for stale locks |
| **Redis-based** | Fast, atomic, auto-expiring | New dependency, need to implement |
| **DO-native** | No custom code | Limited control, may not prevent concurrent invocations |

**Recommendation:** Move to Redis-based locking:

```python
async def acquire_job_lock(redis_client, job_name: str, timeout: int = 3600) -> bool:
    """Acquire a job lock using Redis SETNX with expiry."""
    lock_key = f"job_lock:{job_name}"
    acquired = await redis_client.set(
        lock_key, 
        instance_id, 
        nx=True,  # Only set if not exists
        ex=timeout  # Auto-expire
    )
    return bool(acquired)

async def release_job_lock(redis_client, job_name: str) -> None:
    await redis_client.delete(f"job_lock:{job_name}")
```

### 6.4 Job Execution Tracking

Keep the [`JobExecution`](../backend/app/models/__init__.py:98) table in PostgreSQL for historical tracking and the `/health/cron` endpoint. Remove the `JobLock` table since locking moves to Redis.

### 6.5 Cron Function Entry Point Pattern

```python
# packages/cron/daily-sync/main.py
async def main(args):
    from shared.db import get_db_session
    from shared.cache import get_redis
    from shared.services.game_sync import GameSyncService
    from shared.squiggle import SquiggleClient
    from shared.models_ml.elo import EloModel
    from shared.config import settings
    
    redis = await get_redis()
    
    # Acquire lock
    if not await acquire_job_lock(redis, "daily_game_sync", timeout=900):
        return {"body": "Job already running, skipping"}
    
    try:
        async with get_db_session() as db:
            async with SquiggleClient() as client:
                service = GameSyncService(
                    squiggle_client=client,
                    db_session=db,
                    season=settings.current_season
                )
                stats = await service.sync_games()
                
            # Update Elo cache in Redis
            await EloModel.update_cache(db)
            
        return {"body": json.dumps(stats)}
    finally:
        await release_job_lock(redis, "daily_game_sync")
```

---

## 7. Rate Limiting

### 7.1 Current Implementation

[`slowapi`](../backend/main.py:8) with in-memory storage — rate limits reset on every restart.

### 7.2 Migration Options

| Approach | Pros | Cons |
|----------|------|------|
| **Redis sliding window** | Distributed, persistent, accurate | Requires custom implementation |
| **Redis fixed window** | Simple, fast | Less accurate at window boundaries |
| **DO App Platform rate limiting** | No code needed | Only available at platform level, not per-function |
| **Remove rate limiting** | Simplest | No protection |

**Recommendation:** Use Redis-based fixed window rate limiting (simpler, sufficient for this use case):

```python
# shared/rate_limit.py
async def check_rate_limit(redis_client, key: str, limit: int, window: int = 60) -> bool:
    """Check if request is within rate limit.
    
    Args:
        key: Rate limit key (e.g., IP address)
        limit: Max requests per window
        window: Window in seconds
    
    Returns:
        True if request is allowed, False if rate limited
    """
    current = await redis_client.incr(f"ratelimit:{key}")
    if current == 1:
        await redis_client.expire(f"ratelimit:{key}", window)
    return current <= limit
```

### 7.3 Integration Pattern

Rate limiting moves from middleware to each function's entry point:

```python
async def main(args):
    # Extract client IP from headers
    ip = args.get("__ow_headers", {}).get("x-forwarded-for", "unknown")
    
    redis = await get_redis()
    if not await check_rate_limit(redis, ip, limit=60, window=60):
        return {
            "statusCode": 429,
            "body": json.dumps({"error": "Rate limit exceeded"})
        }
    
    # ... handle request
```

---

## 8. ML Models Strategy

### 8.1 Dependency Analysis

**Critical finding:** Despite being listed in [`pyproject.toml`](../backend/pyproject.toml:17):

```toml
"numpy>=2.0.0",
"scikit-learn>=1.5.0",
```

A search of the entire `backend/app/` directory confirms **zero imports** of numpy or scikit-learn. All four models use:
- Pure Python arithmetic (Elo formula: `1.0 / (1.0 + 10.0 ** ((rating_diff) / 400.0))`)
- SQLAlchemy async queries for historical data
- Standard library `math` operations

### 8.2 Action: Remove Heavy Dependencies

| Dependency | Size | Action |
|---|---|---|
| `numpy` | ~25MB | **Remove** — not used |
| `scikit-learn` | ~30MB | **Remove** — not used |
| `aiosqlite` | ~1MB | **Replace** with `asyncpg` |
| `slowapi` | ~1MB | **Replace** with Redis rate limiting |
| `fastapi` | ~2MB | **Remove** — not needed in functions |
| `uvicorn` | ~1MB | **Remove** — not needed in functions |
| `croniter` | ~1MB | **Remove** — not needed with DO scheduling |
| `fastapi-crons` | ~0.5MB | **Remove** — not needed |

### 8.3 New Dependencies

| Dependency | Size | Purpose |
|---|---|---|
| `asyncpg` | ~5MB | PostgreSQL async driver |
| `redis[hiredis]` | ~5MB | Redis async client |
| `sqlalchemy[asyncio]` | ~5MB | ORM with async support |
| `pydantic` | ~3MB | Schema validation |
| `pydantic-settings` | ~1MB | Environment config |
| `httpx` | ~2MB | HTTP client (Squiggle, OpenRouter) |
| `openai` | ~5MB | OpenRouter client |

**Estimated total:** ~26MB — well within the ~100MB DO Functions limit.

### 8.4 Cold Start Impact

With the heavy dependencies removed:
- **Cold start estimate:** 1-3 seconds (Python runtime + SQLAlchemy + Redis connection)
- **Warm invocations:** <100ms overhead
- **Mitigation:** Use provisioned concurrency for the `games-fn` (highest traffic) if cold starts are problematic

### 8.5 Model Initialization

Each ML model currently initializes from DB queries. In the FaaS context:

- **EloModel:** Read ratings from Redis hash → instant initialization. Fallback to DB computation on Redis miss.
- **FormModel:** No caching, pure SQL queries → no change needed.
- **HomeAdvantageModel:** Read from Redis → instant initialization. Fallback to DB computation.
- **ValueModel:** No caching, pure SQL queries → no change needed.

---

## 9. Frontend Changes

### 9.1 API URL Configuration

The frontend uses [`useApi.ts`](../frontend/composables/useApi.ts) for API calls. Currently configured via Nuxt runtime config.

**Current** ([`nuxt.config.ts`](../frontend/nuxt.config.ts)):
```typescript
// API calls go to /api/* on the same domain (proxied by DO App Platform)
```

**Change Required:**
- DO Functions URLs follow a different pattern: `https://faas.syd1.digitaloceanspaces.com/api-games?path=/games`
- OR use DO App Platform's ingress rules to route `/api/*` to the functions
- **Recommended:** Keep the same URL pattern by using DO App Platform ingress routing

### 9.2 API Contract Changes

The API contract should remain identical. All endpoints return the same JSON structure.

| Aspect | Current | Target | Change |
|--------|---------|--------|--------|
| Response format | JSON | JSON | None |
| Error format | `{"detail": "..."}` | `{"error": "..."}` | Minor key name change |
| CORS | FastAPI middleware | DO Functions CORS config | Config change only |
| Rate limit headers | `X-RateLimit-*` | Remove or custom | Optional |

### 9.3 CORS Configuration

Move from FastAPI middleware to DO Functions configuration:

```yaml
# In project.yml
parameters:
  __ow_cors:
    allowed_origins: ["https://whatismytip.com", "https://www.whatismytip.com"]
    allowed_methods: ["GET", "POST", "OPTIONS"]
    allowed_headers: ["Content-Type", "Authorization"]
```

### 9.4 Frontend Deployment

The frontend remains as a DO App Platform static site. Update the ingress rules in [`.do/app.yaml`](../.do/app.yaml) to route `/api/*` to the functions instead of the backend service.

---

## 10. Testing Strategy

### 10.1 Unit Tests

**Current:** Tests use SQLite in-memory via [`conftest.py`](../backend/tests/conftest.py).

**Migration:**
- Continue using SQLite in-memory for unit tests (fast, no external dependencies)
- Add PostgreSQL-specific test fixtures for integration tests
- Mock Redis for unit tests, use real Redis for integration tests

```python
# tests/conftest.py additions
import pytest
from testcontainers.postgres import PostgresContainer
from testcontainers.redis import RedisContainer

@pytest.fixture(scope="session")
async def pg_engine():
    """PostgreSQL engine for integration tests."""
    async with PostgresContainer("postgres:16") as postgres:
        engine = create_async_engine(postgres.get_connection_url())
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        yield engine
        await engine.dispose()

@pytest.fixture(scope="session")
async def redis_client():
    """Redis client for integration tests."""
    async with RedisContainer("redis:7") as redis:
        client = redis.async_client()
        yield client
        await client.close()
```

### 10.2 Function-Level Tests

Each function gets its own test file:

```
tests/
├── unit/
│   ├── test_cache.py          # Redis cache unit tests (mocked)
│   ├── test_models.py         # SQLAlchemy model tests
│   ├── test_heuristics.py     # Heuristic tests (unchanged)
│   ├── test_squiggle_utils.py # Squiggle utils (unchanged)
│   └── test_cron_utils.py     # Cron utils (unchanged)
├── integration/
│   ├── test_games_fn.py       # HTTP function integration tests
│   ├── test_tips_fn.py
│   ├── test_backtest_fn.py
│   ├── test_admin_fn.py
│   ├── test_daily_sync.py     # Scheduled function tests
│   ├── test_match_completion.py
│   ├── test_tip_generation.py
│   └── test_historic_refresh.py
└── conftest.py
```

### 10.3 Function Test Pattern

```python
# tests/integration/test_games_fn.py
import pytest
from packages.api.games.main import main

@pytest.mark.asyncio
async def test_get_games(db_session, redis_client):
    # Seed test data
    await seed_games(db_session, [...])
    
    # Invoke function
    result = await main({
        "__ow_method": "GET",
        "__ow_path": "/",
        "__ow_headers": {},
    })
    
    assert result["statusCode"] == 200
    data = json.loads(result["body"])
    assert len(data["games"]) > 0
```

### 10.4 Load Testing

Use `locust` or `k6` to validate:
- Cold start latency (<3s target)
- Warm invocation latency (<500ms for reads)
- Concurrent function scaling
- Redis cache hit rates

---

## 11. Deployment Strategy

### 11.1 CI/CD Pipeline

```mermaid
flowchart LR
    Push[Push to feature/*] --> Test[Run Tests]
    Test --> Lint[Ruff + MyPy]
    Lint --> Build[Build Functions]
    Build --> Deploy[Deploy to Staging]
    Deploy --> Verify[Smoke Tests]
    Verify --> Merge[Merge to main]
    Merge --> Prod[Deploy to Production]
```

### 11.2 Deployment Commands

```bash
# Install doctl CLI
# https://docs.digitalocean.com/reference/doctl/

# Deploy all functions
doctl serverless deploy backend-faas

# Deploy single function (faster iteration)
doctl serverless deploy backend-faas --function api/games

# View function logs
doctl serverless activations logs --function api/games --follow

# List functions
doctl serverless functions list
```

### 11.3 Environment Management

| Environment | PostgreSQL | Redis | Functions |
|---|---|---|---|
| Development | Local Docker PostgreSQL | Local Docker Redis | `doctl` local dev |
| Staging | DO Managed DB (dev) | DO Managed Redis (dev) | DO Functions namespace (staging) |
| Production | DO Managed DB (prod) | DO Managed Redis (prod) | DO Functions namespace (prod) |

### 11.4 Rollback Strategy

1. **Function rollback:** `doctl serverless deploy` previous version
2. **Database rollback:** Alembic `downgrade` to previous migration
3. **Full rollback:** Switch DNS/ingress back to old FastAPI backend (keep running during migration window)

### 11.5 Monitoring

- **DO Functions:** Built-in metrics (invocations, errors, latency, duration)
- **PostgreSQL:** DO Managed Database monitoring
- **Redis:** DO Managed Redis monitoring
- **Application logs:** Structured JSON logs via `doctl serverless activations logs`
- **Alerting:** DO monitoring alerts for error rate > 5%, latency p99 > 5s

---

## 12. Migration Phases

### Phase 1: Infrastructure Setup

**Goal:** Provision PostgreSQL and Redis on Digital Ocean.

**Tasks:**
- [ ] Provision DO Managed PostgreSQL database (syd1 region)
- [ ] Provision DO Managed Redis instance (syd1 region)
- [ ] Configure network rules (functions → PostgreSQL, functions → Redis)
- [ ] Create consolidated Alembic migration for PostgreSQL
- [ ] Run migration against new PostgreSQL database
- [ ] Migrate data from SQLite to PostgreSQL
- [ ] Verify data integrity (row counts, spot checks)
- [ ] Set up DO Functions namespace

**Milestone:** PostgreSQL and Redis accessible, schema migrated, data verified.

**Rollback:** Destroy provisioned resources. No impact on current system.

### Phase 2: Shared Code Refactoring

**Goal:** Extract shared code and make it work with PostgreSQL + Redis.

**Tasks:**
- [ ] Create `backend-faas/` project structure
- [ ] Create `shared/` package with PostgreSQL-compatible `db.py`
- [ ] Create `shared/cache.py` with Redis-backed caching
- [ ] Create `shared/config.py` (update settings for PostgreSQL/Redis URLs)
- [ ] Copy and adapt all models, CRUD, services, schemas
- [ ] Remove numpy and scikit-learn from dependencies
- [ ] Replace aiosqlite with asyncpg
- [ ] Replace slowapi with Redis rate limiting
- [ ] Migrate EloModel class-level cache to Redis
- [ ] Migrate HomeAdvantageModel class-level cache to Redis
- [ ] Update all CRUD classes to use Redis cache instead of in-memory
- [ ] Write unit tests for shared package
- [ ] Verify all existing tests pass with new shared code

**Milestone:** Shared code works with PostgreSQL + Redis. All unit tests pass.

**Rollback:** Revert to original `backend/` code. No infrastructure changes.

### Phase 3: Core Read Functions

**Goal:** Deploy read-only API functions and validate with frontend.

**Tasks:**
- [ ] Implement `games-fn` (GET /games, /games/{slug}, /games/{slug}/detail)
- [ ] Implement `tips-fn` (GET /tips, /tips/games-with-tips, /tips/{heuristic})
- [ ] Implement `backtest-fn` (GET /backtest/*)
- [ ] Add CORS configuration to all functions
- [ ] Add Redis rate limiting to all functions
- [ ] Write integration tests for each function
- [ ] Deploy functions to staging namespace
- [ ] Configure DO App Platform ingress to route `/api/*` to functions (staging)
- [ ] Run smoke tests against staging
- [ ] Load test read functions

**Milestone:** All read endpoints working via DO Functions. Frontend can read data.

**Rollback:** Switch ingress back to FastAPI backend.

### Phase 4: Write/Admin Functions

**Goal:** Deploy admin and write functions.

**Tasks:**
- [ ] Implement `admin-fn` (POST /api/admin/jobs/*/trigger, GET /health, /health/cron)
- [ ] Add API key authentication to admin function
- [ ] Implement on-demand tip generation (POST /api/tips/generate)
- [ ] Write integration tests for admin function
- [ ] Deploy to staging
- [ ] Validate admin endpoints work correctly

**Milestone:** Admin and write endpoints working via DO Functions.

**Rollback:** Switch ingress back to FastAPI backend.

### Phase 5: Scheduled Functions

**Goal:** Migrate cron jobs to DO scheduled functions.

**Tasks:**
- [ ] Implement `daily-sync` scheduled function
- [ ] Implement `match-completion` scheduled function
- [ ] Implement `tip-generation` scheduled function
- [ ] Implement `historic-refresh` scheduled function with chunking
- [ ] Move job locking from DB to Redis
- [ ] Write integration tests for each scheduled function
- [ ] Deploy to staging
- [ ] Validate scheduled functions run correctly
- [ ] Monitor first full cycle of all scheduled functions

**Milestone:** All scheduled functions running on DO Functions. No more in-process cron.

**Rollback:** Re-enable cron jobs in FastAPI backend. Disable DO scheduled functions.

### Phase 6: Cutover and Cleanup

**Goal:** Switch production traffic to DO Functions and decommission old backend.

**Tasks:**
- [ ] Update production DO App Platform ingress to route `/api/*` to functions
- [ ] Verify production traffic flows correctly
- [ ] Monitor error rates and latency for 24 hours
- [ ] Remove old backend service from DO App Platform
- [ ] Remove SQLite database
- [ ] Remove FastAPI, uvicorn, aiosqlite, slowapi, croniter, fastapi-crons from deps
- [ ] Update documentation (docs/, README.md)
- [ ] Update CI/CD pipeline for function deployments
- [ ] Archive old `backend/` directory

**Milestone:** Production fully migrated. Old backend decommissioned.

**Rollback:** Re-deploy old backend, switch ingress back. PostgreSQL and Redis remain.

---

## 13. Risk Assessment

### High Risk

| Risk | Impact | Likelihood | Mitigation |
|------|--------|------------|------------|
| **Cold start latency** | Users experience slow first requests | Medium | Use provisioned concurrency for `games-fn`; Redis caching eliminates most DB queries on warm |
| **HistoricRefresh timeout** | Data incomplete after 60 min | Medium | Chunking strategy with self-triggering; resume from last completed season |
| **Connection pool exhaustion** | Functions fail to connect to PostgreSQL | Low | Conservative pool sizes (2 base + 3 overflow); PgBouncer if needed |
| **Data migration errors** | Data loss or corruption | Low | Full SQLite backup before migration; row count verification; run in parallel during validation |

### Medium Risk

| Risk | Impact | Likelihood | Mitigation |
|------|--------|------------|------------|
| **DO Functions runtime limitations** | Unexpected constraints | Medium | Test early with a simple function; have rollback plan |
| **Redis as single point of failure** | All caching fails | Low | DO Managed Redis has auto-failover; functions degrade gracefully (query DB directly) |
| **Package size exceeds limit** | Deployment fails | Low | Verified ~26MB total; well under 100MB limit |
| **CORS issues** | Frontend blocked | Medium | Test CORS config in staging before cutover |
| **Scheduled function timezone issues** | Jobs run at wrong time | Medium | DO Functions cron uses UTC; convert schedule times |

### Low Risk

| Risk | Impact | Likelihood | Mitigation |
|------|--------|------------|------------|
| **API contract changes** | Frontend breaks | Low | Keep identical JSON responses; integration tests validate contracts |
| **Rate limiting differences** | Users blocked or unprotected | Low | Redis-based is more accurate than in-memory; monitor rate limit metrics |
| **Cost overrun** | Higher than expected bill | Low | Monitor invocation counts; set billing alerts |

---

## 14. Cost Analysis

### Current Costs (DO App Platform)

| Resource | Plan | Monthly Cost |
|----------|------|-------------|
| Backend (App Platform) | Basic 512MB RAM | $5.00 |
| Frontend (App Platform) | Static site | Free |
| **Total** | | **$5.00/mo** |

### Target Costs (DO Functions + Managed DB + Redis)

| Resource | Plan | Monthly Cost (Estimate) |
|----------|------|------------------------|
| DO Functions | Free tier: 1M invocations, 1M GB-s | Free initially |
| DO Managed PostgreSQL | Basic plan (256MB RAM) | $15.00 |
| DO Managed Redis | Basic plan (256MB RAM) | $15.00 |
| DO App Platform (frontend only) | Static site | Free |
| **Total** | | **~$30.00/mo** |

### Cost Breakdown After Free Tier

Once the Functions free tier is exceeded:

| Metric | Estimated Monthly | Unit Cost |
|--------|------------------|-----------|
| Invocations | ~100K (mostly games + tips reads) | Included in free tier |
| GB-seconds | ~50K (short-lived functions) | Included in free tier |
| PostgreSQL | Fixed | $15.00 |
| Redis | Fixed | $15.00 |

### Cost Comparison

| Scenario | Current | Target | Delta |
|----------|---------|--------|-------|
| Low traffic (<1K/day) | $5.00 | $30.00 | +$25.00 |
| Medium traffic (<10K/day) | $5.00 | $30.00 | +$25.00 |
| High traffic (<100K/day) | $5.00 + scale concerns | $30.00 + minimal function cost | +$25-35.00 |

### Cost Justification

The ~$25/mo increase provides:
- **PostgreSQL:** Concurrent reads/writes, proper indexing, no SQLite locking
- **Redis:** Persistent caching across function invocations, shared rate limiting
- **Auto-scaling:** Functions scale to zero when idle, scale up under load
- **Operational simplicity:** No cron scheduler to maintain, no long-running process

**Note:** If cost is a primary concern, consider keeping the FastAPI backend on App Platform but migrating to PostgreSQL + Redis. This gives most benefits at lower cost. The full FaaS migration is most valuable when traffic is spiky or when operational simplicity is prioritized.

---

## Appendix A: Dependency Comparison

### Current Dependencies (backend/pyproject.toml)

```
fastapi>=0.115.0          # REMOVE - not needed in functions
uvicorn[standard]>=0.32.0 # REMOVE - not needed in functions
httpx>=0.28.0             # KEEP - HTTP client
sqlalchemy>=2.0.0         # KEEP - ORM
aiosqlite>=0.20.0         # REPLACE with asyncpg
pydantic>=2.10.0          # KEEP - validation
pydantic-settings>=2.6.0  # KEEP - config
slowapi>=0.1.9            # REPLACE with Redis rate limiting
openai>=1.57.0            # KEEP - OpenRouter client
python-dotenv>=1.0.0      # KEEP - env loading
numpy>=2.0.0              # REMOVE - not used
scikit-learn>=1.5.0       # REMOVE - not used
alembic>=1.14.0           # KEEP - migrations
fastapi-crons>=0.1.0      # REMOVE - not needed
croniter>=2.0.0           # REMOVE - not needed
tzdata>=2024.1            # KEEP - timezone data
```

### New Dependencies (backend-faas/pyproject.toml)

```
sqlalchemy[asyncio]>=2.0.0
asyncpg>=0.29.0
redis[hiredis]>=5.0.0
pydantic>=2.10.0
pydantic-settings>=2.6.0
httpx>=0.28.0
openai>=1.57.0
python-dotenv>=1.0.0
alembic>=1.14.0
tzdata>=2024.1
```

## Appendix B: DO Functions Quick Reference

### Function Signature

```python
def main(args: dict) -> dict:
    """
    Args:
        args: Dictionary containing:
            - __ow_method: HTTP method (GET, POST, etc.)
            - __ow_path: URL path
            - __ow_headers: HTTP headers
            - __ow_query: Query string parameters
            - __ow_body: Request body
            - Other keys: Function parameters
    
    Returns:
        Dictionary with:
            - statusCode: HTTP status code
            - headers: Response headers dict
            - body: Response body (string)
    """
```

### Useful Commands

```bash
# Create functions namespace
doctl serverless namespaces create --label whatismytip --region syd1

# Deploy
doctl serverless deploy backend-faas

# Invoke function manually
doctl serverless functions invoke api/games --param path "/"

# View logs
doctl serverless activations logs --follow

# Get function details
doctl serverless functions get api/games
```

## Appendix C: Decision Log

| Decision | Options Considered | Chosen | Rationale |
|----------|-------------------|--------|-----------|
| ML dependency strategy | Keep numpy/sklearn, pre-compute, remove | Remove | Not used anywhere in codebase |
| Cache backend | Memcached, Redis, DB-based | Redis | Multi-purpose: cache + rate limit + job locks |
| Job locking | DB-based, Redis, DO-native | Redis | Fast, atomic, auto-expiring |
| HistoricRefresh strategy | Single long function, chunked, external orchestrator | Chunked with self-trigger | Stays within DO timeout limits |
| Shared code distribution | Python package, symlink/vendor, monorepo | Symlink/vendor with build script | Simplest, most reliable |
| Connection pooling | Per-function pool, PgBouncer, connectionless | Per-function pool (conservative) | Simplest; scale if needed |
