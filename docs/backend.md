# WhatIsMyTip Backend Documentation

> **Architecture**: Serverless (DigitalOcean Functions — Apache OpenWhisk)
> **Runtime**: Python 3.12+
> **Database**: PostgreSQL 16 (asyncpg driver)
> **Cache**: Redis 7 (3-tier TTL strategy)

## Overview

The WhatIsMyTip backend is a **serverless FaaS application** running on DigitalOcean Functions. It provides AI-powered AFL tipping predictions through **4 HTTP-triggered functions** and **4 scheduled (cron) functions**, backed by managed PostgreSQL and Redis.

The system uses 8 ML models, 3 heuristic strategies, and AI-powered explanations via OpenRouter to generate accurate footy tips. Data is collected automatically through scheduled jobs that sync fixtures, detect match completion, generate tips, and refresh historical data.

---

## Table of Contents

1. [Architecture Overview](#architecture-overview)
2. [Project Structure](#project-structure)
3. [Technology Stack](#technology-stack)
4. [Configuration & Environment Variables](#configuration--environment-variables)
5. [DigitalOcean Functions Runtime](#digitalocean-functions-runtime)
6. [HTTP Functions](#http-functions)
7. [Scheduled Functions](#scheduled-functions)
8. [Database Layer](#database-layer)
9. [Caching Layer](#caching-layer)
10. [ML Models & Orchestrator](#ml-models--orchestrator)
11. [Alerting](#alerting)
12. [Error Handling](#error-handling)
13. [Testing](#testing)
14. [Scripts](#scripts)

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────────┐
│                        DigitalOcean Functions                        │
│                                                                      │
│   ┌─────────────────────┐    ┌─────────────────────────────────┐   │
│   │   HTTP Functions     │    │      Scheduled (Cron) Functions  │   │
│   │                      │    │                                  │   │
│   │  • games             │    │  • daily-sync      (*/15 min)    │   │
│   │  • tips              │    │  • match-completion (5,20,35,50) │   │
│   │  • backtest          │    │  • tip-generation  (0 19 * * *)  │   │
│   │  • admin             │    │  • historic-refresh(0 20 * * 6)  │   │
│   └──────────┬───────────┘    └───────────────┬─────────────────┘   │
│              │                                │                      │
│              └────────────┬───────────────────┘                      │
│                           ▼                                          │
│              ┌──────────────────────────┐                           │
│              │   packages/shared/        │                           │
│              │  (config, db, cache,      │                           │
│              │   crud, services,         │                           │
│              │   models_ml, heuristics,  │                           │
│              │   schemas, squiggle,      │                           │
│              │   weather, openrouter)    │                           │
│              └─────────┬──────┬──────────┘                           │
│                        │      │                                       │
│           ┌────────────┘      └──────────────┐                       │
│           ▼                                  ▼                       │
│   ┌─────────────────┐              ┌──────────────────┐             │
│   │  PostgreSQL 16   │              │     Redis 7      │             │
│   │  (managed, async)│              │  (3-tier TTL)    │             │
│   └─────────────────┘              └──────────────────┘             │
└─────────────────────────────────────────────────────────────────────┘
```

All functions share a common code base under `packages/shared/`. Each function is independently deployable with its own memory and timeout configuration defined in [`functions.yml`](../backend/functions.yml:1).

---

## Project Structure

```
backend/
├── functions.yml                    # DO Functions project + function configuration
├── pyproject.toml                   # Python project config, dependencies, tool settings
├── uv.lock                          # Locked dependencies
├── .env.example                     # Environment variables template
├── alembic.ini                      # Alembic configuration
├── packages/
│   ├── api/                         # HTTP-triggered functions
│   │   ├── games/__init__.py        # Games data + detail endpoints
│   │   ├── tips/__init__.py         # Tip generation and retrieval
│   │   ├── backtest/__init__.py     # Backtesting endpoints
│   │   └── admin/__init__.py        # Admin endpoints (job status, manual triggers)
│   ├── cron/                        # Scheduled functions
│   │   ├── daily-sync/__init__.py
│   │   ├── match-completion/__init__.py
│   │   ├── tip-generation/__init__.py
│   │   └── historic-refresh/__init__.py
│   └── shared/                      # Shared code across all functions
│       ├── __init__.py
│       ├── config.py                # Pydantic Settings (all env vars + cron config)
│       ├── db.py                    # Async SQLAlchemy engine + session factory
│       ├── cache.py                 # Redis connection pool + cached() decorator
│       ├── alerting.py              # AlertingService for webhook notifications
│       ├── exceptions.py            # Custom exceptions + classify_error()
│       ├── logger.py                # Structured logging
│       ├── utils.py                 # Shared utilities
│       ├── api_helpers.py           # parse_request, segments, response helpers
│       ├── orchestrator.py          # ModelOrchestrator (8 models, 3 heuristics)
│       ├── crud/                    # Database CRUD operations
│       │   ├── games.py
│       │   ├── tips.py
│       │   ├── jobs.py              # JobExecutionCRUD + JobLockCRUD
│       │   ├── elo_cache.py
│       │   ├── backtest.py
│       │   ├── generation_progress.py
│       │   ├── model_predictions.py
│       │   └── match_analysis.py
│       ├── models/                  # SQLAlchemy database models
│       │   └── __init__.py
│       ├── models_ml/               # ML prediction models (8 models)
│       │   ├── base.py              # Abstract base class
│       │   ├── elo.py
│       │   ├── form.py
│       │   ├── home_advantage.py
│       │   ├── value.py
│       │   ├── weather_impact.py
│       │   ├── injury_impact.py
│       │   ├── matchup.py
│       │   └── player_form.py
│       ├── heuristics/              # Prediction strategy layers
│       │   ├── base.py              # BaseHeuristic abstract class
│       │   ├── best_bet.py
│       │   ├── yolo.py
│       │   └── high_risk_high_reward.py
│       ├── schemas/                 # Pydantic validation schemas
│       │   ├── games.py
│       │   ├── tips.py
│       │   ├── cron.py
│       │   ├── backtest.py
│       │   ├── admin.py
│       │   ├── match_analysis.py
│       │   └── query.py
│       ├── services/                # Business logic services
│       │   ├── game_sync.py         # Squiggle API sync
│       │   ├── tip_generation.py    # Tip generation pipeline
│       │   ├── match_completion.py  # Match completion detection
│       │   ├── historic_data_refresh.py
│       │   ├── explanation.py       # OpenRouter AI explanations
│       │   ├── backtest.py
│       │   └── match_analysis.py
│       ├── squiggle/                # Squiggle API client
│       ├── afl_data/                # AFLTables + FootyWire clients
│       ├── weather/                 # Open-Meteo weather client
│       └── openrouter/              # OpenRouter AI client
├── alembic/                         # Database migrations
│   ├── env.py
│   └── versions/
│       ├── 0001_consolidated_postgresql_schema.py
│       └── 0002_weather_players_injuries.py
├── tests/
│   ├── unit/                        # Unit tests (pytest)
│   └── integration/                 # Integration tests
└── scripts/
    ├── deploy.sh                    # Full deployment script
    ├── dev.sh                       # Local dev environment (Docker)
    ├── run-migrations.sh            # Alembic migration runner
    └── ...                          # Utility scripts
```

---

## Technology Stack

| Component | Technology | Details |
|-----------|-----------|---------|
| **Runtime** | DigitalOcean Functions | Apache OpenWhisk serverless platform |
| **Language** | Python 3.12+ | Full async/await support |
| **Package Manager** | uv | Fast Python package management |
| **Database** | PostgreSQL 16 | Managed, via asyncpg driver |
| **ORM** | SQLAlchemy 2.0 | Async sessions (`AsyncSession`) |
| **Migrations** | Alembic | Consolated baseline + incremental |
| **Cache** | Redis 7 | 3-tier TTL (60s / 300s / 3600s) |
| **Validation** | Pydantic v2 + Pydantic Settings | Schema validation + config management |
| **HTTP Client** | httpx | Async HTTP for external APIs |
| **AI** | OpenRouter | AI-powered explanations (model configurable) |
| **Data Sources** | Squiggle API, AFLTables, FootyWire, Open-Meteo | AFL data + weather |
| **Testing** | pytest, pytest-asyncio, pytest-cov, testcontainers | Unit + integration |
| **Linting** | ruff | Fast Python linter/formatter |
| **Type Checking** | mypy | Static type analysis |

---

## Configuration & Environment Variables

All configuration is managed through [`packages/shared/config.py`](../backend/packages/shared/config.py:1) using Pydantic Settings. Variables are loaded from the environment (or `.env` file locally).

### Core Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `DATABASE_URL` | `postgresql+asyncpg://localhost/whatismytip` | PostgreSQL connection string (asyncpg driver) |
| `REDIS_URL` | `redis://localhost:6379/0` | Redis connection URL |
| `CORS_ORIGINS` | `*` | Comma-separated allowed origins |
| `RATE_LIMIT_PER_MINUTE` | `60` | Requests per minute per IP |
| `ENVIRONMENT` | `development` | `development` or `production` |
| `SEASON` | *(current year)* | AFL season year |

### External API Configuration

| Variable | Description |
|----------|-------------|
| `SQUIGGLE_API_BASE` | Squiggle API base URL (`https://api.squiggle.com.au`) |
| `SQUIGGLE_CONTACT_EMAIL` | Contact email for Squiggle API identification |
| `OPENROUTER_API_KEY` | OpenRouter API key for AI explanations |
| `OPENROUTER_MODEL` | Model identifier (e.g., `google/gemma-4-26b-a4b-it:free`) |
| `OPENROUTER_BASE_URL` | OpenRouter API base URL (`https://openrouter.ai/api/v1`) |

### Admin & Security

| Variable | Description |
|----------|-------------|
| `ADMIN_API_KEY` | API key for admin endpoints (manual triggers, status checks) |

### Cron Job Configuration

All cron schedules, timeouts, and lock expiry values are defined in [`config.py`](../backend/packages/shared/config.py:1) and can be overridden via environment variables. See the [Scheduled Functions](#scheduled-functions) section for details.

---

## DigitalOcean Functions Runtime

### Entry-Point Contract

Every function (HTTP or cron) follows the DigitalOcean Functions entry-point contract:

```python
async def main(args: dict) -> dict:
    """DO Function entry point.

    Args:
        args: Dictionary containing HTTP method, path, query, body, and headers.

    Returns:
        dict with keys: statusCode, headers, body.
    """
```

### HTTP Function Entry Point

HTTP functions receive the request via `args` and return a standard HTTP response:

```python
from packages.shared.api_helpers import parse_request, segments, response
from packages.shared.db import factory

async def main(args: dict) -> dict:
    method, path, query, body = parse_request(args)
    segs = segments(path)
    had_error = False

    async with factory() as session:
        try:
            # Route by method + path segments
            if method == "GET" and segs == []:
                return await _handle_list(session, query)
            if method == "GET" and len(segs) == 1:
                return await _handle_get(session, segs[0])
            return response(404, {"error": "Not found"})
        except Exception:
            had_error = True
            raise
        finally:
            await close_redis_pool(force=had_error)
            await dispose_engine(force=had_error)
```

### Cron Function Entry Point

Scheduled functions use advisory locking and execution tracking:

```python
from packages.shared.db import factory
from packages.shared.crud.jobs import JobLockCRUD, JobExecutionCRUD
from packages.shared.exceptions import classify_error, TransientJobError
from packages.shared.alerting import AlertingService
from packages.shared.cache import invalidate_cache_pattern, close_redis_pool

async def main(args: dict) -> dict:
    async with factory() as session:
        execution = None
        try:
            # Track execution start
            execution = await JobExecutionCRUD.create_execution(session, job_name="my-job")

            # Acquire advisory lock (prevents concurrent runs)
            lock = await JobLockCRUD.acquire_lock(session, job_name="my-job")
            if not lock:
                return {"statusCode": 200, "body": '{"status": "skipped", "reason": "locked"}'}

            # ... business logic ...

            # Release lock + mark success
            await JobLockCRUD.release_lock(session, job_name="my-job")
            await JobExecutionCRUD.update_execution(session, execution.id, status="success")
            await session.commit()

        except TransientJobError as e:
            # Retryable — leave lock to expire, mark for retry
            await AlertingService.send_failure_alert(session, job_name="my-job", error=str(e))
            ...
        except Exception as e:
            await AlertingService.send_failure_alert(session, job_name="my-job", error=str(e))
            ...
        finally:
            await close_redis_pool(force=True)
    return {"statusCode": 200, "body": '{"status": "ok"}'}
```

### Helper Functions

From [`packages/shared/api_helpers.py`](../backend/packages/shared/api_helpers.py:1):

| Helper | Description |
|--------|-------------|
| [`parse_request(args)`](../backend/packages/shared/api_helpers.py:1) | Extracts `method`, `path`, `query`, `body` from the function args |
| [`segments(path)`](../backend/packages/shared/api_helpers.py:1) | Splits URL path into a list of path segments |
| [`response(status_code, body)`](../backend/packages/shared/api_helpers.py:1) | Constructs the return dict with `statusCode`, `headers` (CORS), and JSON `body` |

### Resource Cleanup

Functions **must** clean up connections in `finally` blocks:

- **Redis**: `await close_redis_pool(force=had_error)` — closes the shared Redis connection pool
- **Database**: `await dispose_engine(force=had_error)` — disposes the SQLAlchemy engine

The `force` parameter ensures cleanup even when a global pool/engine may be reused across warm invocations.

---

## HTTP Functions

The backend exposes **4 HTTP-triggered functions**, each deployed as an independent DO Functions package:

| Function | Path | Memory | Timeout |
|----------|------|--------|---------|
| [`games`](../backend/packages/api/games/__init__.py:1) | `api/games` | 256 MB | 5 min |
| [`tips`](../backend/packages/api/tips/__init__.py:1) | `api/tips` | 256 MB | 5 min |
| [`backtest`](../backend/packages/api/backtest/__init__.py:1) | `api/backtest` | 256 MB | 5 min |
| [`admin`](../backend/packages/api/admin/__init__.py:1) | `api/admin` | 256 MB | 5 min |

### Games Function

Source: [`packages/api/games/__init__.py`](../backend/packages/api/games/__init__.py:1)

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/` | List games (with filtering by round, team, status) |
| `GET` | `/{slug}` | Get a single game by slug |
| `GET` | `/{slug}/detail` | Full game detail with tips, predictions, and analysis |
| `GET` | `/health` | Health check endpoint |

### Tips Function

Source: [`packages/api/tips/__init__.py`](../backend/packages/api/tips/__init__.py:1)

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/{game_slug}` | Get tips for a specific game |
| `POST` | `/generate` | Generate tips (requires `ADMIN_API_KEY`) |

### Backtest Function

Source: [`packages/api/backtest/__init__.py`](../backend/packages/api/backtest/__init__.py:1)

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/results` | List backtest results |
| `GET` | `/{heuristic}/performance` | Get performance metrics for a specific heuristic |
| `POST` | `/run` | Trigger a new backtest (requires `ADMIN_API_KEY`) |

### Admin Function

Source: [`packages/api/admin/__init__.py`](../backend/packages/api/admin/__init__.py:1)

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/jobs/status` | Get status of all cron jobs (requires `ADMIN_API_KEY`) |
| `GET` | `/jobs/{job_name}/executions` | Get execution history for a job (requires `ADMIN_API_KEY`) |
| `POST` | `/jobs/{job_name}/trigger` | Manually trigger a cron job (requires `ADMIN_API_KEY`) |

### Function URLs

In production, functions are accessible via the DO Functions gateway:

```
https://faas.syd1.digitaloceanspaces.com/{namespace}/api/{function}
```

> **Note:** There is no automatic Swagger/OpenAPI UI. See [docs/api.md](api.md) for the complete endpoint reference.

---

## Scheduled Functions

The backend runs **4 scheduled (cron) functions** for automated data collection and tip generation. All schedules are in **UTC**.

| Function | Schedule (UTC) | Memory | Timeout | Description |
|----------|----------------|--------|---------|-------------|
| [`daily-sync`](../backend/packages/cron/daily-sync/__init__.py:1) | `*/15 * * * *` (every 15 min) | 256 MB | 10 min | Sync games/teams from Squiggle API |
| [`match-completion`](../backend/packages/cron/match-completion/__init__.py:1) | `5,20,35,50 * * * *` (4×/hour) | 256 MB | 10 min | Detect completed matches and update scores |
| [`tip-generation`](../backend/packages/cron/tip-generation/__init__.py:1) | `0 19 * * *` (3 AM AWST) | 512 MB | 15 min | Generate tips for upcoming games |
| [`historic-refresh`](../backend/packages/cron/historic-refresh/__init__.py:1) | `0 20 * * 6` (4 AM Sun AWST) | 512 MB | 15 min | Refresh historical player/injury/weather data |

### daily-sync

**Purpose**: Synchronises games and team data from the Squiggle API into the database.

- Runs every 15 minutes during the AFL season
- Uses [`services/game_sync.py`](../backend/packages/shared/services/game_sync.py:1) to fetch and upsert game data
- Caches frequently accessed data in Redis
- Tracks execution status via [`JobExecutionCRUD`](../backend/packages/shared/crud/jobs.py:1)

### match-completion

**Purpose**: Detects when matches have been completed and updates final scores.

- Runs 4 times per hour (at minutes 5, 20, 35, 50)
- Uses [`services/match_completion.py`](../backend/packages/shared/services/match_completion.py:1) to check game status
- Updates game records with final scores and completion timestamps
- Triggers cache invalidation for affected games

### tip-generation

**Purpose**: Generates tips for all upcoming games in the current round.

- Runs nightly at 3:00 AM AWST (19:00 UTC)
- Uses [`services/tip_generation.py`](../backend/packages/shared/services/tip_generation.py:1) which invokes the [`ModelOrchestrator`](../backend/packages/shared/orchestrator.py:1)
- Runs all 8 ML models in parallel, applies 3 heuristic strategies
- Generates AI explanations via OpenRouter
- Requires 512 MB memory due to concurrent model execution
- Uses advisory locking via [`JobLockCRUD`](../backend/packages/shared/crud/jobs.py:1) to prevent concurrent runs

### historic-refresh

**Purpose**: Refreshes historical data including player stats, injury lists, and weather data.

- Runs weekly on Saturday at 4:00 AM AWST (20:00 UTC Saturday)
- Uses [`services/historic_data_refresh.py`](../backend/packages/shared/services/historic_data_refresh.py:1)
- Fetches data from AFLTables, FootyWire, and Open-Meteo
- Requires 512 MB memory due to heavy data processing

---

## Database Layer

### Connection Management

Database connections are managed by [`packages/shared/db.py`](../backend/packages/shared/db.py:1), which provides:

- **Async SQLAlchemy engine** using the asyncpg driver (`postgresql+asyncpg://`)
- **Session factory** (`factory()`) — context manager that yields an `AsyncSession`
- **Engine disposal** — `dispose_engine(force=...)` for cleanup in `finally` blocks

```python
from packages.shared.db import factory

async def do_work():
    async with factory() as session:
        # session is automatically committed on success
        # and rolled back on exception
        ...
```

### Database Models

SQLAlchemy models are defined in [`packages/shared/models/`](../backend/packages/shared/models/__init__.py:1):

- **Game** — AFL match data (teams, round, venue, scores, status)
- **Tip** — Generated tips (team, confidence, margin, heuristic)
- **ModelPrediction** — Individual model predictions per game
- **MatchAnalysis** — Detailed match analysis (weather, injuries, player data)
- **JobExecution** — Cron job execution records (status, timing, error details)
- **JobLock** — Advisory locks for preventing concurrent job runs
- **EloCache** — Cached Elo ratings for teams
- **GenerationProgress** — Tip generation progress tracking
- **BacktestResult** — Backtesting performance metrics

### Migrations

Database migrations are managed via Alembic. The baseline is a **consolidated migration** (not individual historical migrations):

| Migration | Description |
|-----------|-------------|
| [`0001_consolidated_postgresql_schema`](../backend/alembic/versions/2026_05_28_1613-0001_consolidated_postgresql_schema.py:1) | Full schema baseline (PostgreSQL) |
| [`0002_weather_players_injuries`](../backend/alembic/versions/2026_06_10_0600-0002_weather_players_injuries.py:1) | Weather, player, and injury tables |

See [docs/migrations.md](migrations.md) for the full migration workflow.

---

## Caching Layer

### Redis Cache Architecture

Caching is implemented in [`packages/shared/cache.py`](../backend/packages/shared/cache.py:1) using a **3-tier TTL strategy**:

| Tier | TTL | Use Case |
|------|-----|----------|
| **Short** | 60 seconds | Frequently changing data (live game status, recent results) |
| **Medium** | 300 seconds (5 min) | Semi-static data (tips for current round, team standings) |
| **Long** | 3600 seconds (1 hour) | Rarely changing data (team info, historical stats) |

### Connection Pooling

Redis connections use a shared connection pool managed by [`_get_pool()`](../backend/packages/shared/cache.py:1):

```python
from packages.shared.cache import RedisCache

# Get a cache instance with a specific tier
cache = RedisCache(prefix="games", ttl=300)  # medium tier
value = await cache.get("key")
await cache.set("key", value)
```

### Cached Decorator

The [`@cached`](../backend/packages/shared/cache.py:1) decorator automatically caches function results:

```python
from packages.shared.cache import cached

@cached(prefix="games", ttl=300)
async def get_games(round_num: int):
    # Expensive DB query — result cached for 5 minutes
    ...
```

### Cache Invalidation

Targeted cache invalidation via [`invalidate_cache_pattern()`](../backend/packages/shared/cache.py:1):

```python
from packages.shared.cache import invalidate_cache_pattern, RedisCache

# Invalidate all games cache entries
await invalidate_cache_pattern(RedisCache(prefix="games"), "*")
```

### Resource Cleanup

```python
from packages.shared.cache import close_redis_pool

# Must be called in finally blocks
await close_redis_pool(force=had_error)
```

---

## ML Models & Orchestrator

### ModelOrchestrator

The [`ModelOrchestrator`](../backend/packages/shared/orchestrator.py:1) coordinates all prediction models and heuristics:

```python
from packages.shared.orchestrator import ModelOrchestrator

orchestrator = ModelOrchestrator()

# Single heuristic prediction
result = await orchestrator.predict(game, db=session, heuristic="best_bet")

# All heuristics
results = await orchestrator.predict_all(game, db=session)
# Returns: {"best_bet": {...}, "yolo": {...}, "high_risk_high_reward": {...}}
```

### ML Models (8 models)

All models inherit from [`BaseModel`](../backend/packages/shared/models_ml/base.py:1):

| Model | File | Description |
|-------|------|-------------|
| **Elo** | [`elo.py`](../backend/packages/shared/models_ml/elo.py:1) | Team strength tracking via Elo rating system |
| **Form** | [`form.py`](../backend/packages/shared/models_ml/form.py:1) | Recent team performance (last N games) |
| **Home Advantage** | [`home_advantage.py`](../backend/packages/shared/models_ml/home_advantage.py:1) | Venue-specific advantages |
| **Value** | [`value.py`](../backend/packages/shared/models_ml/value.py:1) | Value-based betting analysis |
| **Weather Impact** | [`weather_impact.py`](../backend/packages/shared/models_ml/weather_impact.py:1) | Weather conditions impact on game outcomes |
| **Injury Impact** | [`injury_impact.py`](../backend/packages/shared/models_ml/injury_impact.py:1) | Team injury lists and player availability |
| **Matchup** | [`matchup.py`](../backend/packages/shared/models_ml/matchup.py:1) | Head-to-head historical performance |
| **Player Form** | [`player_form.py`](../backend/packages/shared/models_ml/player_form.py:1) | Individual player form metrics |

### Heuristics (3 strategies)

All heuristics inherit from [`BaseHeuristic`](../backend/packages/shared/heuristics/base.py:1):

| Heuristic | File | Description |
|-----------|------|-------------|
| **Best Bet** | [`best_bet.py`](../backend/packages/shared/heuristics/best_bet.py:1) | Conservative picks with highest confidence |
| **YOLO** | [`yolo.py`](../backend/packages/shared/heuristics/yolo.py:1) | High-risk, high-reward selections |
| **High Risk High Reward** | [`high_risk_high_reward.py`](../backend/packages/shared/heuristics/high_risk_high_reward.py:1) | Balanced approach for adventurous tippers |

The orchestrator runs all 8 models in parallel using `asyncio.gather()`, then each heuristic combines the model outputs into a final prediction with a team, confidence score, and predicted margin.

---

## Alerting

The [`AlertingService`](../backend/packages/shared/alerting.py:1) sends webhook notifications when cron jobs fail or encounter errors:

```python
from packages.shared.alerting import AlertingService

# Failure alert (transient or permanent errors)
await AlertingService.send_failure_alert(
    session, job_name="tip-generation", error="Database connection timeout"
)

# Timeout alert (job exceeded its time limit)
await AlertingService.send_timeout_alert(
    session, job_name="historic-refresh", elapsed_seconds=900
)
```

Alerting is configured via:
- `ALERT_WEBHOOK_URL` — webhook endpoint for notifications
- `ALERT_ENABLED` — enable/disable alerting (default: `true`)
- `ALERT_TIMEOUT_SECONDS` — HTTP client timeout for sending alerts

---

## Error Handling

### Custom Exceptions

Defined in [`packages/shared/exceptions.py`](../backend/packages/shared/exceptions.py:1):

| Exception | Description |
|-----------|-------------|
| `TransientJobError` | Retryable error (network timeout, DB connection issue) |
| `PermanentJobError` | Non-retryable error (schema mismatch, invalid config) |
| `JobTimeoutError` | Job exceeded its configured time limit |

### Error Classification

The [`classify_error()`](../backend/packages/shared/exceptions.py:1) function inspects an exception and returns the appropriate error type:

```python
from packages.shared.exceptions import classify_error

try:
    await do_work()
except Exception as e:
    error_type = classify_error(e)  # TransientJobError | PermanentJobError | JobTimeoutError
```

### Cron Job Error Flow

1. **Transient errors**: The advisory lock is left to expire naturally (prevents immediate re-run), the execution is marked as `failed_retryable`, and an alert is sent.
2. **Permanent errors**: The lock is released, the execution is marked as `failed`, and an alert is sent.
3. **Timeout errors**: The lock is released, the execution is marked as `timeout`, and an alert is sent.

---

## Testing

### Test Structure

```
backend/tests/
├── conftest.py                     # Shared fixtures
├── unit/                           # Unit tests (fast, no external deps)
│   ├── test_api_games.py
│   ├── test_api_tips.py
│   ├── test_api_backtest.py
│   ├── test_api_admin.py
│   ├── test_api_helpers_security.py
│   ├── test_cron_daily_sync.py
│   ├── test_cron_match_completion.py
│   ├── test_cron_tip_generation.py
│   ├── test_cron_historic_refresh.py
│   ├── test_models.py
│   ├── test_new_models.py
│   ├── test_heuristics.py
│   ├── test_cache.py
│   ├── test_alerting.py
│   ├── test_exceptions.py
│   ├── test_schemas_validation.py
│   └── ...                         # Model-specific tests, client tests
├── integration/                    # Integration tests (DB, Redis, API)
│   ├── conftest.py
│   ├── test_api_integration.py
│   ├── test_cache_integration.py
│   └── test_db_integration.py
```

### Running Tests

```bash
cd backend

# Start local PostgreSQL + Redis
./scripts/dev.sh

# Run unit tests
uv run pytest tests/unit/ -v

# Run with coverage
uv run pytest tests/unit/ --cov

# Run integration tests
uv run pytest tests/integration/ -v

# Run all tests
uv run pytest -v
```

### Test Configuration

Test configuration is defined in [`pyproject.toml`](../backend/pyproject.toml:1):

- `pytest-asyncio` for async test support
- `pytest-cov` for coverage reporting
- `testcontainers` for integration test infrastructure (PostgreSQL/Redis containers)

---

## Scripts

### Deployment Script

[`scripts/deploy.sh`](../backend/scripts/deploy.sh:1) — Full deployment pipeline:

1. **Run tests** — `uv run pytest tests/unit/ -v`
2. **Run migrations** — `uv run alembic upgrade head`
3. **Deploy functions** — `doctl serverless deploy . --env .env`
4. **Health verification** — Checks deployed function URLs

```bash
cd backend
./scripts/deploy.sh
```

### Local Development Script

[`scripts/dev.sh`](../backend/scripts/dev.sh:1) — Starts local infrastructure via Docker:

- **PostgreSQL 16** (`postgres:16-alpine`)
- **Redis 7** (`redis:7-alpine`)

```bash
cd backend
./scripts/dev.sh
```

### Migration Scripts

[`scripts/run-migrations.sh`](../backend/scripts/run-migrations.sh:1) — Runs Alembic migrations:

```bash
cd backend
./scripts/run-migrations.sh
```

### Utility Scripts

| Script | Description |
|--------|-------------|
| [`scripts/check_db_data.py`](../backend/scripts/check_db_data.py:1) | Inspect database contents |
| [`scripts/seed_data.py`](../backend/scripts/seed_data.py:1) | Seed initial data |
| [`scripts/seed_player_data.py`](../backend/scripts/seed_player_data.py:1) | Seed player data |
| [`scripts/load_csv_to_db.py`](../backend/scripts/load_csv_to_db.py:1) | Load CSV data into database |
| [`scripts/export_seed_csv.py`](../backend/scripts/export_seed_csv.py:1) | Export data to CSV |
| [`scripts/scrape_to_csv.py`](../backend/scripts/scrape_to_csv.py:1) | Scrape AFL data to CSV |
| [`scripts/sync_squiggle_games.py`](../backend/scripts/sync_squiggle_games.py:1) | Sync games from Squiggle API |
| [`scripts/run_model_backtest.py`](../backend/scripts/run_model_backtest.py:1) | Run ML model backtests |
| [`scripts/run_next_round_predictions.py`](../backend/scripts/run_next_round_predictions.py:1) | Generate next round predictions |
| [`scripts/show_predictions.py`](../backend/scripts/show_predictions.py:1) | Display stored predictions |
| [`scripts/migrate_and_seed.py`](../backend/scripts/migrate_and_seed.py:1) | Migrate and seed in one step |
| [`scripts/_reset_matches.py`](../backend/scripts/_reset_matches.py:1) | Reset match data (development) |
