# WhatIsMyTip FaaS Backend

Serverless backend for WhatIsMyTip AFL tipping application, running on Digital Ocean Functions with PostgreSQL and Redis.

## Architecture

- **Functions:** 4 HTTP + 4 Scheduled (Digital Ocean Functions)
- **Database:** PostgreSQL (Digital Ocean Managed Database)
- **Cache:** Redis (Digital Ocean Managed Redis)
- **ML Models:** Pure Python (no numpy/scikit-learn dependency)

## Project Structure

```
backend/
├── project.yml          # DO Functions project + function configuration
├── packages/
│   ├── shared/          # Shared code (models, CRUD, services, etc.)
│   ├── api/             # HTTP-triggered functions
│   │   ├── games/       # Game endpoints
│   │   ├── tips/        # Tips endpoints
│   │   ├── backtest/    # Backtest endpoints
│   │   └── admin/       # Admin endpoints (API key auth)
│   └── cron/            # Scheduled functions
│       ├── daily-sync/  # Every 15 min
│       ├── match-completion/  # Every 15 min (offset)
│       ├── tip-generation/    # Daily 3 AM AWST
│       └── historic-refresh/  # Sunday 4 AM AWST
├── alembic/             # Database migrations
├── tests/               # Unit tests
└── scripts/             # Deployment and utility scripts
```

## Quick Start

### Prerequisites

- Python 3.12+
- [uv](https://docs.astral.sh/uv/) (Python package manager)
- [doctl](https://docs.digitalocean.com/reference/doctl/how-to/install/) CLI (for deployment)
- Docker (for local PostgreSQL/Redis)

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

# Run tests
uv run pytest tests/unit/ -v
```

### Run Tests

```bash
# All unit tests
uv run pytest tests/unit/ -v

# Specific test file
uv run pytest tests/unit/test_api_games.py -v

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
# Full deployment (tests + migrations + deploy)
./scripts/deploy.sh

# Just run migrations
./scripts/run-migrations.sh

# Database setup (first time)
./scripts/setup-db.sh
```

## API Endpoints

### Public

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/api/games` | List games |
| GET | `/api/api/games/{slug}` | Get game by slug |
| GET | `/api/api/games/{slug}/detail` | Full game detail |
| GET | `/api/api/tips` | List tips |
| GET | `/api/api/tips/games-with-tips` | Games with tips |
| GET | `/api/api/tips/{heuristic}` | Tips by heuristic |
| POST | `/api/api/tips/generate` | Generate tips |
| GET | `/api/api/backtest/performance` | Backtest performance |
| GET | `/api/api/backtest/summary` | Backtest summary |
| GET | `/api/api/backtest/leaderboard` | Backtest leaderboard |

### Admin (requires `X-API-Key` header)

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/api/admin/daily-sync/trigger` | Trigger daily sync |
| POST | `/api/api/admin/match-completion/trigger` | Trigger match completion |
| POST | `/api/api/admin/tip-generation/trigger` | Trigger tip generation |
| POST | `/api/api/admin/historic-refresh/trigger` | Trigger historic refresh |
| GET | `/api/api/admin/historic-refresh/progress` | Get refresh progress |

## Scheduled Functions

| Function | Schedule | Description |
|----------|----------|-------------|
| `daily-sync` | Every 15 min | Syncs games and tips from Squiggle API |
| `match-completion` | Every 15 min (offset) | Detects completed matches, updates results |
| `tip-generation` | Daily 3 AM AWST | Generates tips for upcoming games |
| `historic-refresh` | Sunday 4 AM AWST | Refreshes historic data and recalibrates models |

## Environment Variables

See [`.env.example`](.env.example) for all configuration options:

| Variable | Required | Description |
|----------|----------|-------------|
| `DATABASE_URL` | Yes | PostgreSQL connection string |
| `REDIS_URL` | Yes | Redis connection string |
| `SQUIGGLE_API_BASE` | Yes | Squiggle API base URL |
| `SQUIGGLE_CONTACT_EMAIL` | Yes | Contact email for Squiggle API |
| `OPENROUTER_API_KEY` | No | API key for OpenRouter (AI explanations) |
| `OPENROUTER_MODEL` | No | Model to use for explanations |
| `ADMIN_API_KEY` | Yes | API key for admin endpoints |
| `ENVIRONMENT` | No | `development` or `production` |

## CI/CD

The [GitHub Actions workflow](../.github/workflows/deploy-faas.yml) automatically:

1. Runs tests, linting, and type checking on push to `main` or `dev`
2. Deploys functions to Digital Ocean on merge to `main`
3. Runs database migrations after deployment

### Required GitHub Secrets

- `DIGITALOCEAN_ACCESS_TOKEN` — DO API token
- `DATABASE_URL` — Production database connection string

## Migration from FastAPI

This FaaS backend replaces the original FastAPI + SQLite backend (`backend/`). Key changes:

- **Database:** SQLite → PostgreSQL (with asyncpg driver)
- **Cache:** In-memory → Redis
- **Runtime:** Long-running server → Serverless functions
- **ML Models:** Removed numpy/scikit-learn dependencies (pure Python)
- **Deployment:** Manual → CI/CD with GitHub Actions
