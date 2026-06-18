# WhatIsMyTip Backend

FastAPI backend for the WhatIsMyTip AFL tipping application, backed by managed PostgreSQL, Redis, and an in-process APScheduler for cron jobs. Deployed as a container on DigitalOcean App Platform (Phase 4).

## Architecture

- **Runtime:** Single FastAPI process (`uvicorn main:app`) per container
- **Scheduling:** In-process [APScheduler](https://apscheduler.readthedocs.io/) — cron jobs run in the same process as the API
- **Database:** PostgreSQL 16 (DigitalOcean Managed, asyncpg driver)
- **Cache:** Redis 7 (DigitalOcean Managed, 3-tier TTL)
- **ML Models:** 8 pure-Python models (no numpy/scikit-learn dependency)
- **Container:** Multi-stage Dockerfile (Python 3.12-slim + uv)

## Project Structure

```
backend/
├── main.py                      # FastAPI app entry point
├── Dockerfile                   # Multi-stage container image
├── pyproject.toml               # Python project config + dependencies
├── uv.lock                      # Locked dependencies
├── alembic/                     # Database migrations
├── app/                         # FastAPI app — routers, middleware, scheduler
│   ├── api/                     # HTTP routers
│   │   ├── games.py
│   │   ├── tips.py
│   │   ├── backtest.py
│   │   ├── admin.py
│   │   └── health.py
│   ├── core/                    # Middleware, lifespan, security, scheduler
│   │   ├── lifespan.py          # App startup/shutdown (DB pool, scheduler, …)
│   │   ├── scheduler.py         # APScheduler wiring for the 4 cron jobs
│   │   ├── middleware.py
│   │   ├── security.py
│   │   └── rate_limit.py
│   └── cron/                    # Job function objects bound to the scheduler
│       ├── daily_sync.py
│       ├── match_completion.py
│       ├── tip_generation.py
│       └── historic_refresh.py
├── packages/                    # Shared business logic (DB models, services, ML)
│   └── shared/                  # Imported as `packages.shared.*`
│       ├── config.py
│       ├── db.py
│       ├── cache.py
│       ├── models/
│       ├── models_ml/           # 8 ML models
│       ├── heuristics/          # 3 heuristic strategies
│       ├── services/            # Business logic (sync, tip-gen, refresh, …)
│       ├── schemas/             # Pydantic request/response models
│       └── …
├── tests/
│   ├── unit/                    # Fast unit tests (no external deps)
│   └── integration/             # Integration tests (PostgreSQL + Redis via testcontainers)
├── proxy/                       # nginx reverse proxy (used by App Platform)
│   ├── nginx.conf               # Forwards /api/... to the FastAPI container
│   └── Dockerfile               # nginx:1.27-alpine image
└── scripts/                     # dev.sh, deploy.sh, test_dockerfile.sh, …
```

> **Phase 5 cleanup:** the legacy `packages/api/` and `packages/cron/` FaaS handler
> directories were deleted in Phase 5 (June 2026). The FastAPI app in `app/` is the
> single source of truth for HTTP routes and scheduled jobs.

### Destructive scripts

These scripts mutate or wipe database data — read the source first and run against a **non-production** DB:

| Script | What it does |
|--------|--------------|
| [`scripts/_reset_matches.py`](scripts/_reset_matches.py:1) | Clears `afltables_match_id` for a season (use to re-scrape matches) |
| [`scripts/load_csv_to_db.py`](scripts/load_csv_to_db.py:1) (with `--clear`) | Wipes tables before loading CSVs |
| [`scripts/migrate_and_seed.py`](scripts/migrate_and_seed.py:1) (with `--clear`) | Wipes tables before synthetic seeding |

There is **no `WIMT_ALLOW_DESTRUCTIVE_SCRIPTS` env var** — destructive scripts run unconditionally. Use the right environment and back up before running them.

## Quick Start

### Prerequisites

- Python 3.12+
- [uv](https://docs.astral.sh/uv/) (Python package manager)
- [doctl](https://docs.digitalocean.com/reference/doctl/how-to/install/) CLI (for deployment)
- Docker (for local PostgreSQL/Redis + the Dockerfile smoke test)

### Setup

```bash
cd backend
cp .env.example .env
# Edit .env with your configuration
uv sync
```

### Local Development

```bash
# Start local PostgreSQL and Redis via Docker
./scripts/dev.sh

# Run migrations
uv run alembic upgrade head

# Run the FastAPI app locally (hot-reload)
uv run uvicorn main:app --reload

# Run unit tests
uv run pytest tests/unit/ -v
```

### Run Tests

```bash
# All unit tests
uv run pytest tests/unit/ -v

# Specific test file
uv run pytest tests/unit/test_app_api_games.py -v

# With coverage
uv run pytest tests/unit/ -v --cov=packages
```

### Run Migrations

```bash
# Apply all pending migrations
uv run alembic upgrade head

# Check current migration state
uv run alembic current

# Generate a new migration
uv run alembic revision --autogenerate -m "description"
```

### Deploy

```bash
# Build the image, push to DO Container Registry, trigger an App Platform deploy
./scripts/deploy.sh

# Just run migrations (one-off task)
./scripts/run-migrations.sh

# Database setup (first time)
./scripts/setup-db.sh
```

## API Endpoints

All endpoints are mounted directly under `/api/...` (no path rewriting).

### Public

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/health` | Liveness probe (DB + Redis status) |
| GET | `/api/games` | List games (filter by `season`, `round`, `upcoming`, `latest`) |
| GET | `/api/games/{slug}` | Get a single game by slug |
| GET | `/api/games/{slug}/detail` | Full game detail (tips, predictions, weather, analysis) |
| GET | `/api/games/health` | Games router health |
| GET | `/api/tips` | List tips (filter by `heuristic`, `season`, `round`, `limit`) |
| GET | `/api/tips/{heuristic}` | Tips for one heuristic |
| GET | `/api/tips/games-with-tips` | Games with their best-bet tips |
| POST | `/api/tips/generate` | Generate tips for a round |
| GET | `/api/backtest` | List backtest results |
| GET | `/api/backtest/{heuristic}/performance` | Heuristic performance |
| GET | `/api/backtest/compare` | Compare all heuristics for a season |
| POST | `/api/backtest/run` | Run a backtest |

### Admin (requires `X-API-Key` header)

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/admin/{job_name}/trigger` | Manually trigger `daily-sync`, `match-completion`, `tip-generation`, or `historic-refresh` |
| GET | `/api/admin/historic-refresh/progress` | Current progress of the historic-refresh job |
| GET | `/api/admin/metrics` | Per-job execution metrics |

The full machine-readable schema is available at `/openapi.json` (Swagger UI at `/docs`, ReDoc at `/redoc`).

## Scheduled Jobs (in-process APScheduler)

| Job | Schedule (AWST) | Description |
|-----|-----------------|-------------|
| `daily-sync` | Every 15 min | Syncs games from the Squiggle API |
| `match-completion` | Every 15 min, offset by 5 | Detects completed matches and updates results |
| `tip-generation` | Daily 03:00 | Generates tips + AI explanations for the next round |
| `historic-refresh` | Sunday 04:00 | Refreshes historical data and recalibrates models |

Schedules are configured via the cron expressions in [`packages/shared/config.py`](packages/shared/config.py) and can be overridden per-environment with `TIP_GENERATION_CRON` / `HISTORIC_REFRESH_CRON` / `DAILY_SYNC_CRON` / `MATCH_COMPLETION_CRON` env vars (the in-process APScheduler reads these directly — there are no FaaS-style `CRON_*` env vars any more).

## Environment Variables

See [`.env.example`](.env.example) for the full template. Highlights:

| Variable | Required | Description |
|----------|----------|-------------|
| `DATABASE_URL` | Yes | PostgreSQL connection string (`postgresql+asyncpg://…`) |
| `REDIS_URL` | Yes | Redis connection string (`rediss://…` for managed) |
| `SQUIGGLE_API_BASE` | Yes | Squiggle API base URL |
| `SQUIGGLE_CONTACT_EMAIL` | Yes | Contact email for Squiggle API |
| `OPENROUTER_API_KEY` | No | API key for OpenRouter (AI explanations) |
| `OPENROUTER_MODEL` | No | Model to use for explanations |
| `ADMIN_API_KEY` | Yes | API key for admin endpoints (sent as `X-API-Key` header) |
| `ENVIRONMENT` | No | `development` or `production` |
| `CORS_ORIGINS` | No | Comma-separated allowed origins (default: `*`) |
| `RATE_LIMIT_PER_MINUTE` | No | Per-IP rate limit (default: `60`) |

## CI/CD

The GitHub Actions workflow at [`.github/workflows/ci.yml`](../.github/workflows/ci.yml) runs the unit tests on every push/PR.

The deployment workflow (`.github/workflows/deploy.yml`) builds the image, pushes to the DigitalOcean Container Registry, and triggers an App Platform deployment. Required GitHub secrets:

- `DIGITALOCEAN_ACCESS_TOKEN` — DO API token
- `DATABASE_URL` — Production database connection string
- `REDIS_URL` — Production Redis connection string
- `OPENROUTER_API_KEY`, `ADMIN_API_KEY`, `SQUIGGLE_*` — app secrets

## See Also

- [docs/backend.md](../docs/backend.md) — Architecture deep-dive
- [docs/api.md](../docs/api.md) — Full API reference
- [docs/deployment.md](../docs/deployment.md) — Production deployment guide
- [docs/development.md](../docs/development.md) — Local development workflow
