# WhatIsMyTip Backend Documentation

> **Architecture**: FastAPI monolith + in-process APScheduler (Phase 4)
> **Runtime**: Python 3.12+ (uvicorn / gunicorn-uvicorn-worker)
> **Container**: multi-stage Docker image (see `backend/Dockerfile`)
> **Database**: PostgreSQL 16 (asyncpg driver)
> **Cache**: Redis 7 (3-tier TTL strategy)

## Overview

The WhatIsMyTip backend is a **single FastAPI application** (Phase 4) that provides AI-powered AFL tipping predictions through one process. HTTP traffic is served by FastAPI routers mounted at `/api/...`; the 4 cron jobs run in the same process via an in-process APScheduler. The whole thing is deployed as a single container image (see `backend/Dockerfile`).

The system uses 8 ML models, 3 heuristic strategies, and AI-powered explanations via OpenRouter to generate accurate footy tips. Data is collected automatically through scheduled jobs that sync fixtures, detect match completion, generate tips, and refresh historical data.

Multi-instance deploys coordinate cron-job execution via Postgres advisory locks (see [`app/core/scheduler.py`](../backend/app/core/scheduler.py:1)) so only one instance runs each job at a time.

See [`docs/operations.md`](operations.md) for runtime/deployment operations and [`docs/security-model.md`](security-model.md) for the security model.

---

## Table of Contents

1. [Architecture Overview](#architecture-overview)
2. [Project Structure](#project-structure)
3. [Running Locally](#running-locally)
4. [Technology Stack](#technology-stack)
5. [Configuration & Environment Variables](#configuration--environment-variables)
6. [HTTP Routers](#http-routers)
7. [Scheduled Jobs](#scheduled-jobs)
8. [Database Layer](#database-layer)
9. [Caching Layer](#caching-layer)
10. [ML Models & Orchestrator](#ml-models--orchestrator)
11. [Alerting](#alerting)
12. [Error Handling](#error-handling)
13. [Testing](#testing)
14. [Scripts](#scripts)

---

## Architecture Overview

The FastAPI app is one process; multi-instance deploys coordinate cron execution via Postgres advisory locks. All HTTP endpoints and cron jobs share a common code base under `packages/shared/`.

### Component split

| Concern | File(s) |
|---------|---------|
| HTTP routers | `backend/app/api/*.py` |
| Middleware, lifespan, security, rate limiting | `backend/app/core/*.py` |
| Cron job objects (bound to the scheduler) | `backend/app/cron/*.py` |
| Scheduler wiring | `backend/app/core/scheduler.py` |
| Shared business logic, DB, cache, ML, services | `backend/packages/shared/` |
| Container image | `backend/Dockerfile` |

---

## Project Structure

```
backend/
├── main.py                            # FastAPI app entry point
├── Dockerfile                         # Multi-stage container image
├── pyproject.toml                     # Python project config, dependencies, tool settings
├── uv.lock                            # Locked dependencies
├── .env.example                       # Environment variables template
├── alembic.ini                        # Alembic configuration
├── app/                               # FastAPI app — routers, middleware, scheduler
│   ├── api/                           # HTTP routers
│   │   ├── health.py                  # /health liveness probe
│   │   ├── games.py                   # mounted at /api/games
│   │   ├── tips.py                    # mounted at /api/tips
│   │   ├── backtest.py                # mounted at /api/backtest
│   │   └── admin.py                   # mounted at /api/admin (X-API-Key required)
│   ├── core/                          # App-level concerns
│   │   ├── lifespan.py                # Startup/shutdown: DB pool, scheduler, …
│   │   ├── scheduler.py               # APScheduler wiring for the 4 cron jobs
│   │   ├── middleware.py              # SecurityHeaders, RequestSize, RequestID
│   │   ├── security.py                # require_admin_key, etc.
│   │   ├── rate_limit.py              # slowapi limiter
│   │   ├── db_deps.py                 # FastAPI dependency: get_db()
│   │   └── exceptions.py              # BackendServiceError + http_error helper
│   └── cron/                          # Job function objects bound to the scheduler
│       ├── base.py                    # BaseJob (retry, lock, alerting, execution)
│       ├── daily_sync.py
│       ├── match_completion.py
│       ├── tip_generation.py
│       └── historic_refresh.py
├── packages/                          # Shared business logic
│   └── shared/                        # Imported as `packages.shared.*`
│       ├── config.py                  # Pydantic Settings (env vars + cron expressions)
│       ├── db.py                      # Async SQLAlchemy engine + session factory
│       ├── cache.py                   # Redis connection pool + cached() decorator
│       ├── alerting.py                # AlertingService for webhook notifications
│       ├── exceptions.py              # TransientJobError / PermanentJobError / …
│       ├── orchestrator.py            # ModelOrchestrator (8 models, 3 heuristics)
│       ├── crud/                      # Database CRUD operations
│       ├── models/                    # SQLAlchemy database models
│       ├── models_ml/                 # 8 ML prediction models
│       ├── heuristics/                # 3 heuristic strategies
│       ├── schemas/                   # Pydantic validation schemas
│       ├── services/                  # Business logic services
│       ├── squiggle/                  # Squiggle API client
│       ├── afl_data/                  # AFLTables + FootyWire clients
│       ├── weather/                   # Open-Meteo weather client
│       └── openrouter/                # OpenRouter AI client
├── alembic/                           # Database migrations
├── tests/
│   ├── unit/                          # Unit tests (fast, no external deps)
│   └── integration/                   # Integration tests (PostgreSQL + Redis)
└── scripts/                           # deploy.sh, dev.sh, test_dockerfile.sh, …
```

---

## Running Locally

### Prerequisites

- Python 3.12+
- [uv](https://docs.astral.sh/uv/) (Python package manager)
- Docker (for local PostgreSQL + Redis — see `scripts/dev.sh`)

### Quick start

```bash
# 1. Clone & install
cd backend
uv sync

# 2. Configure env
cp .env.example .env
# (edit .env if you need non-default DB/Redis URLs)

# 3. Start local PostgreSQL + Redis via Docker
./scripts/dev.sh

# 4. Apply migrations
uv run alembic upgrade head

# 5. Run the FastAPI app locally (hot-reload)
uv run uvicorn main:app --reload

# 6. Verify
curl http://localhost:8000/health
# Expected: {"status":"healthy","db":"ok","redis":"ok",...}

# 7. Interactive API docs
#    Open http://localhost:8000/docs  (Swagger UI)
#    or    http://localhost:8000/redoc (ReDoc)
```

### Production (gunicorn)

```bash
uv run gunicorn main:app -k uvicorn.workers.UvicornWorker --workers 2
```

### In Docker

```bash
# Build the image
docker build -t whatismytip-api -f Dockerfile .

# Run it (pass env vars from your .env)
docker run --rm -p 8000:8000 --env-file .env whatismytip-api

# Smoke test the build
./scripts/test_dockerfile.sh
```

---

## Technology Stack

| Component | Technology | Details |
|-----------|-----------|---------|
| **Runtime** | FastAPI + uvicorn | Single ASGI process; runs under gunicorn in production |
| **Language** | Python 3.12+ | Full async/await support |
| **Package Manager** | uv | Fast Python package management |
| **Container** | Docker (multi-stage) | python:3.12-slim + uv venv (see `backend/Dockerfile`) |
| **Scheduler** | APScheduler | In-process; cron expressions in `packages/shared/config.py` |
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
| `CURRENT_SEASON` | *(current year)* | AFL season year |
| `CRON_ENABLED` | `true` | Master switch for the in-process scheduler |
| `CRON_TIMEZONE` | `Australia/Perth` | Timezone for the scheduler (cron expressions are interpreted in this tz) |

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
| `ADMIN_API_KEY` | API key for admin endpoints (sent as `X-API-Key` header) |

### Cron Job Configuration

All cron schedules, timeouts, and lock expiry values are defined in [`config.py`](../backend/packages/shared/config.py:1) and can be overridden via environment variables. Schedules are interpreted in `CRON_TIMEZONE` (default `Australia/Perth`). See [Scheduled Jobs](#scheduled-jobs) section for details.

---

## HTTP Routers

The FastAPI app exposes **4 HTTP routers**, each mounted at `/api/...`.  The 5th router (`/health`) is at the root.  All paths and response field names are preserved 1:1 from the FaaS era (see [docs/api.md](api.md)) so the frontend and any existing clients keep working without changes.

| Router | Mount | File | Description |
|--------|-------|------|-------------|
| `health` | `/health` | [`app/api/health.py`](../backend/app/api/health.py:1) | Liveness probe (DB + Redis status) |
| `games` | `/api/games` | [`app/api/games.py`](../backend/app/api/games.py:1) | Game listings, scores, slugs, detail |
| `tips` | `/api/tips` | [`app/api/tips.py`](../backend/app/api/tips.py:1) | Tip retrieval, generation, games-with-tips |
| `backtest` | `/api/backtest` | [`app/api/backtest.py`](../backend/app/api/backtest.py:1) | Backtest results, performance, compare |
| `admin` | `/api/admin` | [`app/api/admin.py`](../backend/app/api/admin.py:1) | Manual job triggers, job status, metrics (X-API-Key required) |

### Games Router

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/` | List games (with filtering by season, round, upcoming, latest) |
| `GET` | `/{slug}` | Get a single game by slug |
| `GET` | `/{slug}/detail` | Full game detail with tips, predictions, and analysis |
| `GET` | `/health` | Games router health |

### Tips Router

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/` | List tips (with filtering) |
| `GET` | `/games-with-tips` | Games with their best-bet tips for a round |
| `GET` | `/{heuristic}` | Tips for a specific heuristic |
| `POST` | `/generate` | Generate tips (requires `X-API-Key`) |
| `POST` | `/explanations/generate` | Generate AI explanations for a round |

### Backtest Router

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/` | List backtest results |
| `GET` | `/seasons` | List seasons with backtest data |
| `GET` | `/current-season` | Current-season performance |
| `GET` | `/table` | Per-round table data |
| `GET` | `/{heuristic}/performance` | Heuristic performance metrics |
| `GET` | `/compare` | Compare all heuristics for a season |
| `GET` | `/model-compare` | Compare individual ML models for a season |
| `POST` | `/run` | Trigger a backtest (requires `X-API-Key`) |

### Admin Router

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/{job_name}/trigger` | Manually trigger `daily-sync`, `match-completion`, `tip-generation`, or `historic-refresh` (requires `X-API-Key`) |
| `GET` | `/historic-refresh/progress` | Current progress of the historic-refresh job |
| `GET` | `/metrics` | Per-job execution metrics |

### OpenAPI / Swagger

The full machine-readable schema is available at:

- `GET /openapi.json` — the raw OpenAPI 3 spec
- `GET /docs` — Swagger UI (interactive)
- `GET /redoc` — ReDoc (read-only)

---

## Scheduled Jobs

The backend runs **4 scheduled jobs** in-process via APScheduler (see [`app/core/scheduler.py`](../backend/app/core/scheduler.py:1)).  The scheduler is started in the FastAPI lifespan (see [`app/core/lifespan.py`](../backend/app/core/lifespan.py:1)) and stopped on shutdown.

All schedules are in **`CRON_TIMEZONE`** (default `Australia/Perth`, UTC+8).  Schedules can be overridden per-environment with the env vars listed in the table below.

| Job | Default Schedule | Env var | Service / Description |
|-----|------------------|---------|----------------------|
| `daily-sync` | `*/15 * * * *` | `DAILY_SYNC_CRON` | [`game_sync.py`](../backend/packages/shared/services/game_sync.py:1) — Syncs games/teams from Squiggle API; caches frequently accessed data in Redis |
| `match-completion` | `5,20,35,50 * * * *` | `MATCH_COMPLETION_CRON` | [`match_completion.py`](../backend/packages/shared/services/match_completion.py:1) — Detects completed matches, updates final scores, invalidates affected game caches |
| `tip-generation` | `0 3 * * *` | `TIP_GENERATION_CRON` | [`tip_generation.py`](../backend/packages/shared/services/tip_generation.py:1) — Runs 8 models in parallel via [`ModelOrchestrator`](../backend/packages/shared/orchestrator.py:1), applies 3 heuristics, generates AI explanations |
| `historic-refresh` | `0 4 * * 0` | `HISTORIC_REFRESH_CRON` | [`historic_data_refresh.py`](../backend/packages/shared/services/historic_data_refresh.py:1) — Fetches player stats, injuries, and weather from AFLTables, FootyWire, Open-Meteo |

### Concurrency / multi-instance safety

Each job is wrapped in a Postgres advisory lock (see [`packages/shared/crud/jobs.py`](../backend/packages/shared/crud/jobs.py:1)) so when the FastAPI app is deployed as multiple instances (e.g. for HA), only one instance runs each job at a time.  Lock TTL is `JOB_LOCK_EXPIRE_SECONDS` (default 900 s = 15 min).

### Job lifecycle

1. The APScheduler triggers the job function on its schedule
2. The `BaseJob` wrapper (see [`app/cron/base.py`](../backend/app/cron/base.py:1)) acquires the advisory lock
3. If the lock is held, the job is skipped (and a `skipped` execution is recorded)
4. The job runs with retry-with-backoff (`JOB_MAX_RETRIES`, `JOB_RETRY_DELAY_SECONDS`)
5. On success: lock released, `job_executions` row marked `success`
6. On `TransientJobError`: lock left to expire, row marked `failed_retryable`, alert sent
7. On `PermanentJobError` / timeout: lock released, row marked `failed`/`timeout`, alert sent

For runtime monitoring and on-call procedures, see [`docs/operations.md`](operations.md).

---

## Database Layer

### Connection Management

Database connections are managed by [`packages/shared/db.py`](../backend/packages/shared/db.py:1), which provides:

- **Async SQLAlchemy engine** using the asyncpg driver (`postgresql+asyncpg://`)
- **Session factory** (`factory()`) — context manager that yields an `AsyncSession`
- **Engine disposal** — `dispose_engine(force=...)` for shutdown

```python
from packages.shared.db import factory

async def do_work():
    async with factory() as session:
        # session is automatically committed on success
        # and rolled back on exception
        ...
```

In FastAPI routes, use the `get_db` dependency instead:

```python
from app.core.db_deps import get_db
from sqlalchemy.ext.asyncio import AsyncSession

@router.get("/")
async def handler(db: AsyncSession = Depends(get_db)):
    ...
```

### Database Models

SQLAlchemy models are defined in [`packages/shared/models/`](../backend/packages/shared/models/__init__.py):

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

Database migrations are managed via Alembic. The baseline is a **consolidated migration** (not individual historical migrations). See [docs/migrations.md](migrations.md) for the full migration workflow.

---

## Caching Layer

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

cache = RedisCache(prefix="games", ttl=300)
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

The FastAPI lifespan handles Redis pool teardown on shutdown (see [`app/core/lifespan.py`](../backend/app/core/lifespan.py:1)):

```python
from packages.shared.cache import close_redis_pool

await close_redis_pool(force=had_error)
```

---

## ML Models & Orchestrator

The 8 prediction models and 3 heuristic strategies are documented in the source-of-truth files below. See the source files for per-class implementation details and the public attributes (e.g. confidence ranges, required input data).

### ML Models (8)

All models inherit from [`BaseModel`](../backend/packages/shared/models_ml/base.py:1):

| Model | Source |
|-------|--------|
| **Elo** | [`models_ml/elo.py`](../backend/packages/shared/models_ml/elo.py:1) |
| **Form** | [`models_ml/form.py`](../backend/packages/shared/models_ml/form.py:1) |
| **Home Advantage** | [`models_ml/home_advantage.py`](../backend/packages/shared/models_ml/home_advantage.py:1) |
| **Value** | [`models_ml/value.py`](../backend/packages/shared/models_ml/value.py:1) |
| **Weather Impact** | [`models_ml/weather_impact.py`](../backend/packages/shared/models_ml/weather_impact.py:1) |
| **Injury Impact** | [`models_ml/injury_impact.py`](../backend/packages/shared/models_ml/injury_impact.py:1) |
| **Matchup** | [`models_ml/matchup.py`](../backend/packages/shared/models_ml/matchup.py:1) |
| **Player Form** | [`models_ml/player_form.py`](../backend/packages/shared/models_ml/player_form.py:1) |

### Heuristics (3)

All heuristics inherit from [`BaseHeuristic`](../backend/packages/shared/heuristics/base.py:1):

| Heuristic | Source |
|-----------|--------|
| **Best Bet** | [`heuristics/best_bet.py`](../backend/packages/shared/heuristics/best_bet.py:1) |
| **YOLO** | [`heuristics/yolo.py`](../backend/packages/shared/heuristics/yolo.py:1) |
| **High Risk High Reward** | [`heuristics/high_risk_high_reward.py`](../backend/packages/shared/heuristics/high_risk_high_reward.py:1) |

The [`ModelOrchestrator`](../backend/packages/shared/orchestrator.py:1) runs all 8 models in parallel using `asyncio.gather()`, then each heuristic combines the model outputs into a final prediction with a team, confidence score, and predicted margin.

```python
from packages.shared.orchestrator import ModelOrchestrator

orchestrator = ModelOrchestrator()

# Single heuristic prediction
result = await orchestrator.predict(game, db=session, heuristic="best_bet")

# All heuristics
results = await orchestrator.predict_all(game, db=session)
# Returns: {"best_bet": {...}, "yolo": {...}, "high_risk_high_reward": {...}}
```

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
| `BackendServiceError` | FastAPI-layer error mapped to an HTTP status code |

### Error Classification

The [`classify_error()`](../backend/packages/shared/exceptions.py:1) function inspects an exception and returns the appropriate error type:

```python
from packages.shared.exceptions import classify_error

try:
    await do_work()
except Exception as e:
    error_type = classify_error(e)  # TransientJobError | PermanentJobError | JobTimeoutError
```

### FastAPI Exception Handlers

See [`main.py`](../backend/main.py:1) for the global exception handlers:

- `BackendServiceError` → mapped to its declared `status_code` (with `Retry-After` for 429)
- `RequestValidationError` → 422 with structured `errors` array
- `Exception` (catch-all) → 500 with sanitized message + request ID

---

## Testing

### Test Structure

```
backend/tests/
├── conftest.py                         # Shared fixtures
├── unit/                               # Unit tests (fast, no external deps)
└── integration/                        # Integration tests (DB, Redis, API)
```

### Running Tests

```bash
cd backend

# Start local PostgreSQL + Redis (for integration tests)
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

[`scripts/deploy.sh`](../backend/scripts/deploy.sh:1) — Container-based deploy pipeline:

1. **Pre-flight** — checks for `docker`, `doctl`, `uv`
2. **Loads `.env`** — picks up `DO_REGISTRY`, `DO_APP_ID`, etc.
3. **Unit tests** — `uv run pytest tests/unit/ -v`
4. **Migrations** — `uv run alembic upgrade head`
5. **Build** — `docker build -f Dockerfile -t ${DO_REGISTRY}/api:${IMAGE_TAG} .`
6. **Push** — `docker push ${DO_REGISTRY}/api:${IMAGE_TAG}`
7. **Trigger deploy** — `doctl apps create-deployment ${DO_APP_ID} --force-rebuild`
8. **Health poll** — best-effort curl of `/health` for up to 60 s

`IMAGE_TAG` defaults to the current git short SHA.  Pass `--dry-run` to print every command without executing.  Pass `SKIP_TESTS=1` or `SKIP_MIGRATE=1` to skip the corresponding step.

```bash
cd backend
./scripts/deploy.sh                     # full deploy
./scripts/deploy.sh --dry-run           # show the plan
SKIP_TESTS=1 ./scripts/deploy.sh        # skip tests (e.g. for a quick hotfix)
IMAGE_TAG=my-feature ./scripts/deploy.sh
```

### Dockerfile Smoke Test

[`scripts/test_dockerfile.sh`](../backend/scripts/test_dockerfile.sh:1) — builds the image, runs the container, and curls `/health` to confirm the process starts.  Gracefully skips when Docker is unavailable.

```bash
./scripts/test_dockerfile.sh
```

### Local Development Script

[`scripts/dev.sh`](../backend/scripts/dev.sh:1) — Starts local infrastructure via Docker:

- **PostgreSQL 16** (`postgres:16-alpine`) on port 5432
- **Redis 7** (`redis:7-alpine`) on port 6379

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
| [`scripts/migrate_and_seed.py`](../backend/scripts/migrate_and_seed.py:1) | Migrate and seed in one step (accepts `--seed-dir`, `--from-csv`, `--csv-dir`, `--no-seed`, `--skip-migrations`) |
| [`scripts/_reset_matches.py`](../backend/scripts/_reset_matches.py:1) | Reset match data (development) |
