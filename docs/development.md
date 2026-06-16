# WhatIsMyTip Development Guide

## Overview

This guide covers setting up and working with the WhatIsMyTip development environment. The backend is a **single FastAPI Python process** that runs locally with `uvicorn`, talks to local PostgreSQL + Redis (via Docker), and is deployed to DigitalOcean App Platform as a container.

## Prerequisites

- **Bun** (JavaScript runtime and package manager)
- **uv** (Python package manager)
- **Python 3.12+**
- **Node.js 18+** (for Nuxt 4)
- **Docker** (for local PostgreSQL + Redis)
- **doctl** (DigitalOcean CLI — for deploying functions)
- **Git** (for version control)

## Install Tools

### 1. Install Bun

```bash
# macOS/Linux
curl -fsSL https://bun.sh/install | bash

# Windows
powershell -c "irm bun.sh/install.ps1 | iex"
```

### 2. Install uv

```bash
pipx install uv
```

### 3. Install Docker

Download from [docker.com](https://www.docker.com/get-started/) and ensure Docker Desktop is running.

### 4. Install doctl

```bash
# macOS
brew install doctl
# Other platforms: https://docs.digitalocean.com/reference/doctl/how-to/install/

# Authenticate
doctl auth init
```

### 5. Install Node.js

- Download from [nodejs.org](https://nodejs.org/)
- Or use nvm: `nvm install 18`

## Clone Repository

```bash
git clone https://github.com/danielpaparo98/WhatIsMyTip.git
cd whatismytip
```

---

## Local Development Setup

### Frontend Setup

1. **Install dependencies**:
   ```bash
   cd frontend
   bun install
   ```

2. **Configure environment**:
   ```bash
   cp .env.example .env
   # Edit .env — set API_BASE_URL to your Functions gateway URL or local proxy
   ```

3. **Start development server**:
   ```bash
   bun run dev
   ```

The frontend will be available at `http://localhost:3000`

### Backend Setup

The FastAPI backend runs locally with `uvicorn main:app --reload` (which auto-reloads on code change). Tests run against Docker services — see [Quick Start](#quick-start) below.

1. **Install Python dependencies**:
   ```bash
   cd backend
   uv sync
   ```

2. **Configure environment**:
   ```bash
   cp .env.example .env
   # Edit .env with your configuration
   ```

3. **Start local PostgreSQL + Redis via Docker**:
   ```bash
   ./scripts/dev.sh
   ```

   This starts:
   - **PostgreSQL 16** (`postgres:16-alpine`) on port 5432
   - **Redis 7** (`redis:7-alpine`) on port 6379

4. **Apply database migrations**:
   ```bash
   uv run alembic upgrade head
   ```

5. **Run unit tests**:
   ```bash
   uv run pytest tests/unit/ -v
   ```

### Running Both Frontend and Backend

```bash
# Terminal 1 — Frontend
cd frontend
bun run dev

# Terminal 2 — Backend (Docker services + tests)
cd backend
./scripts/dev.sh
uv run pytest tests/unit/ -v
```

---

## Local Development with Docker (full stack)

Phase 6 introduces a single `docker compose` workflow that brings up the
**entire stack** (Postgres + Redis + FastAPI + Nuxt frontend) on your
local machine so you can exercise every endpoint and every heuristic
against a real database.  This is the recommended way to develop and
manually test the application end-to-end.

### Prerequisites

- **Docker Desktop** (Windows / macOS) **or Podman** (Linux / macOS)
  - Docker: <https://www.docker.com/products/docker-desktop/>
  - Podman: <https://podman.io/docs/installation>
- **Git** (already installed)
- *(Optional)* `curl` and `psql` for poking at the running services

The dev script ([`scripts/dev.sh`](../scripts/dev.sh:1) /
[`scripts/dev.ps1`](../scripts/dev.ps1:1)) auto-detects Docker first,
then Podman, then errors out cleanly.  Override with
`WIMT_RUNTIME=docker` / `WIMT_RUNTIME=podman` (or `$env:WIMT_RUNTIME` on
PowerShell) when you have both installed.

### Quick start

```bash
# macOS / Linux / WSL
git clone https://github.com/danielpaparo98/WhatIsMyTip.git
cd WhatIsMyTip
git checkout feature/local-docker-stack    # this branch
./scripts/dev.sh up
# Open http://localhost:3000  (frontend)
# Open http://localhost:8000  (API)
# Open http://localhost:8000/docs  (Swagger UI)
```

```powershell
# Windows + PowerShell
git clone https://github.com/danielpaparo98/WhatIsMyTip.git
cd WhatIsMyTip
git checkout feature/local-docker-stack    # this branch
.\scripts\dev.ps1 up
# Open http://localhost:3000  (frontend)
# Open http://localhost:8000  (API)
# Open http://localhost:8000/docs  (Swagger UI)
```

The first `up` builds the `whatismytip-api:local` image from
[`backend/Dockerfile`](../backend/Dockerfile:1) and pulls `postgres:16-alpine`,
`redis:7-alpine`, and `oven/bun:1.1`.  Subsequent ups are seconds-fast.

### What you get

| Service     | Port  | Notes |
|-------------|-------|-------|
| `postgres`  | 5432  | `wimt / wimt_dev_password` (dev only) |
| `redis`     | 6379  | No password |
| `api`       | 8000  | FastAPI + Uvicorn (`--reload` for hot-reload) |
| `frontend`  | 3000  | Nuxt 4 dev server (HMR) |
| `init-data` | n/a   | One-shot: runs migrations + CSV load, then exits 0 |

The `init-data` service runs **before** `api` starts (via
`service_completed_successfully`), so the API never serves traffic
against an unmigrated database.  The init container is bind-mounted
against `./data/` at the project root, so dropping CSVs there and
restarting the init container is enough to seed real data.

### Common commands

```bash
# Bash (macOS / Linux / WSL)
./scripts/dev.sh up --logs       # start + follow logs
./scripts/dev.sh down            # stop (volumes preserved)
./scripts/dev.sh reset           # stop AND delete the database volume
./scripts/dev.sh logs api        # tail logs of a specific service
./scripts/dev.sh ps              # show running containers
./scripts/dev.sh shell api       # bash into the api container
./scripts/dev.sh psql            # psql shell
./scripts/dev.sh redis           # redis-cli shell
./scripts/dev.sh config          # validate docker-compose.yml
```

```powershell
# PowerShell (Windows)
.\scripts\dev.ps1 up -Up -Logs
.\scripts\dev.ps1 down
.\scripts\dev.ps1 reset
.\scripts\dev.ps1 logs api
.\scripts\dev.ps1 ps
.\scripts\dev.ps1 shell api
.\scripts\dev.ps1 psql
.\scripts\dev.ps1 redis
.\scripts\dev.ps1 config
```

### Loading real CSV data

The `init-data` service bind-mounts `./data/` (at the project root) to
`/data` in the container, then runs:

```bash
python scripts/migrate_and_seed.py --from-csv --csv-dir=/data --no-seed --verbose
```

To load real data, drop the CSVs that
[`scrape_to_csv.py`](../backend/scripts/scrape_to_csv.py:1) produces
into `./data/` and re-run the init step:

```bash
docker compose run --rm init-data          # bash
# or simply reset and bring the stack back up
./scripts/dev.sh reset && ./scripts/dev.sh up
```

`scripts/migrate_and_seed.py` accepts three new flags for the Docker
flow:

- `--from-csv` — load CSVs from `--csv-dir` (or auto-discovered
  location) instead of running the synthetic seeder.
- `--csv-dir PATH` — explicit CSV directory (defaults to auto-discovery
  via `find_csv_seed_dir()`).
- `--skip-migrations` — skip `alembic upgrade head` (used when the
  schema is already up to date).
- `--no-seed` — skip the synthetic `seed_data.py` run.

### Disabling the in-process cron jobs

The FastAPI app runs four APScheduler cron jobs in-process (see
[Scheduled Jobs](#scheduled-jobs-in-process-apscheduler) below).  For
local dev these are usually noise — set `CRON_ENABLED=false` in the
`api` service environment to silence them.  It is set to `false` by
default in [`docker-compose.yml`](../docker-compose.yml:1).  To
exercise the cron paths, set `CRON_ENABLED=true` and restart the api
container.

### Hot-reload

- **Backend:** `api` bind-mounts `./backend` and runs
  `uvicorn main:app --reload`.  Edits to anything under `backend/`
  trigger an automatic restart.
- **Frontend:** `frontend` bind-mounts `./frontend` and runs
  `bun run dev --host 0.0.0.0`.  Nuxt HMR is enabled by default.

### Smoke test (CI-friendly)

A pure-Python + bash smoke test lives at
[`scripts/smoke_local.sh`](../scripts/smoke_local.sh:1) (and
[`scripts/smoke_local.ps1`](../scripts/smoke_local.ps1:1) for Windows).
It validates the compose file without bringing the stack up:

```bash
./scripts/smoke_local.sh
# (optionally also bring the stack up and check /health)
./scripts/smoke_local.sh --up --health
```

If no container runtime is detected, the smoke test falls back to
validating the YAML structure with Python (handy in CI sandboxes).

### Troubleshooting

| Symptom | Fix |
|---------|-----|
| `Cannot connect to Docker daemon` | Start Docker Desktop (or `podman machine init && podman machine start`). |
| Port 5432 / 6379 / 8000 / 3000 already in use | Stop the conflicting process or edit the `ports:` mapping in `docker-compose.yml`. |
| `init-data` keeps restarting | Inspect `docker compose logs init-data` — usually a CSV parse error or a stale `data/` directory. |
| Frontend can't reach the API | Check `NUXT_PUBLIC_API_BASE=http://localhost:8000` (set in compose).  The browser hits the host's localhost, NOT the container network. |
| Want a fresh database | `./scripts/dev.sh reset` (or `.\scripts\dev.ps1 reset`). |

---

## Testing Functions Locally

### Unit Tests

Unit tests are the primary way to develop and verify backend logic:

```bash
cd backend

# Run all unit tests
uv run pytest tests/unit/ -v

# Run specific test file
uv run pytest tests/unit/test_app_cron_tip_generation.py -v

# Run with coverage
uv run pytest tests/unit/ --cov

# Run a single test
uv run pytest tests/unit/test_api_games.py::test_list_games -v
```

### Integration Tests

Integration tests require running PostgreSQL + Redis:

```bash
cd backend

# Ensure Docker services are running
./scripts/dev.sh

# Run integration tests
uv run pytest tests/integration/ -v
```

### Testing Against DO Functions

To test functions end-to-end, deploy to your DO Functions namespace:

```bash
cd backend
./scripts/deploy.sh          # Build image, push to DO Container Registry, trigger App Platform deploy

# Then test against the deployed URL (deploy.sh polls /health before returning):
curl https://whatismytip.com/api/games
curl https://whatismytip.com/health
```

---

## Scheduled Jobs (in-process APScheduler)

The 4 scheduled jobs run **in-process** via APScheduler inside the FastAPI container. They are
not separate FaaS handlers — they live in [`backend/app/cron/`](../backend/app/cron/) and are
registered in [`backend/app/core/scheduler.py`](../backend/app/core/scheduler.py:1). Schedules
are read from [`packages/shared/config.py`](../backend/packages/shared/config.py:1).

### Job Schedules (Australia/Perth timezone)

| Job | Default Schedule | Env var |
|-----|------------------|---------|
| `daily-sync` | `*/15 * * * *` | `DAILY_SYNC_CRON` |
| `match-completion` | `5,20,35,50 * * * *` | `MATCH_COMPLETION_CRON` |
| `tip-generation` | `0 3 * * *` | `TIP_GENERATION_CRON` |
| `historic-refresh` | `0 4 * * 0` | `HISTORIC_REFRESH_CRON` |

### Manual Job Triggering

Trigger jobs via the FastAPI admin API (requires `X-Admin-API-Key` header matching `ADMIN_API_KEY`):

```bash
# Production
curl -X POST -H "X-Admin-API-Key: $ADMIN_API_KEY" \
  https://whatismytip.com/api/admin/daily-sync/trigger
curl -X POST -H "X-Admin-API-Key: $ADMIN_API_KEY" \
  https://whatismytip.com/api/admin/match-completion/trigger
curl -X POST -H "X-Admin-API-Key: $ADMIN_API_KEY" \
  https://whatismytip.com/api/admin/tip-generation/trigger
curl -X POST -H "X-Admin-API-Key: $ADMIN_API_KEY" \
  https://whatismytip.com/api/admin/historic-refresh/trigger
curl -H "X-Admin-API-Key: $ADMIN_API_KEY" \
  https://whatismytip.com/api/admin/metrics
```

### Testing Scheduled Functions

**Option 1: Unit Tests**

Write unit tests that directly call the function's `main()`:

```python
# tests/unit/test_cron_my_job.py
import pytest
from packages.cron.my_job import main

@pytest.mark.asyncio
async def test_my_job_succeeds(mock_session):
    result = await main({})
    assert result["statusCode"] == 200
```

**Option 2: Deploy and Trigger**

Deploy to your namespace and manually trigger via the admin API.

---

## Project Structure

```
whatismytip/
├── frontend/               # Nuxt 4 frontend
│   ├── app.vue
│   ├── nuxt.config.ts
│   ├── package.json
│   ├── assets/
│   │   └── css/main.css    # Design system
│   ├── components/         # Vue components
│   ├── composables/        # Vue composables
│   └── pages/              # Page routes
├── backend/                # FaaS backend (DigitalOcean Functions)
│   ├── project.yml         # DO Functions project configuration
│   ├── pyproject.toml      # Python dependencies and tool config
│   ├── packages/
│   │   ├── api/            # HTTP-triggered functions
│   │   │   ├── games/
│   │   │   ├── tips/
│   │   │   ├── backtest/
│   │   │   └── admin/
│   │   ├── cron/           # Scheduled functions
│   │   │   ├── daily-sync/
│   │   │   ├── match-completion/
│   │   │   ├── tip-generation/
│   │   │   └── historic-refresh/
│   │   └── shared/         # Shared code
│   │       ├── config.py   # Pydantic Settings
│   │       ├── db.py       # Async SQLAlchemy
│   │       ├── cache.py    # Redis cache
│   │       ├── crud/       # Database operations
│   │       ├── models/     # Database models
│   │       ├── models_ml/  # 8 ML models
│   │       ├── heuristics/ # 3 heuristic strategies
│   │       ├── schemas/    # Pydantic validation
│   │       ├── services/   # Business logic
│   │       └── ...
│   ├── alembic/            # Database migrations
│   ├── tests/              # Unit + integration tests
│   └── scripts/            # deploy.sh, dev.sh, etc.
├── docs/                   # Documentation
├── CONTRIBUTING.md
├── README.md
└── LICENSE
```

---

## Development Workflow

### Creating a New Feature

1. **Create a feature branch**:
   ```bash
   git checkout -b feature/your-feature-name
   ```

2. **Make changes**:
   - Update frontend in `frontend/`
   - Update backend in `backend/`
   - Update documentation in `docs/`

3. **Test your changes**:
   ```bash
   # Frontend
   cd frontend
   bun run lint && bun run typecheck

   # Backend
   cd backend
   ./scripts/dev.sh
   uv run pytest tests/unit/ -v
   ```

4. **Commit changes**:
   ```bash
   git add .
   git commit -m "feat: add your feature"
   ```

5. **Push to remote**:
   ```bash
   git push origin feature/your-feature-name
   ```

### Code Style Guidelines

#### Frontend (TypeScript/Nuxt)

- Use TypeScript for type safety
- Follow ESLint rules
- Use Nuxt conventions for components and pages
- Use descriptive variable and function names

#### Backend (Python)

- Follow PEP 8 style guide
- Use type hints for all functions
- Follow ruff rules configured in [`pyproject.toml`](../backend/pyproject.toml:1)
- Use async/await for all database operations
- Import from `packages.shared.*` (not `app.*`)
- Use the FaaS entry-point contract: `main(args) -> {"statusCode", "headers", "body"}`
- Always clean up Redis pools and SQLAlchemy engines in `finally` blocks

---

## Adding New Backend Components

### Adding a New Cron Job

1. **Create the function directory**: `backend/packages/cron/my-new-job/`

2. **Implement the entry point** in `__init__.py`:

```python
"""DigitalOcean Scheduled Function: My New Job."""
from packages.shared.db import factory
from packages.shared.crud.jobs import JobLockCRUD, JobExecutionCRUD
from packages.shared.config import settings


async def main(args: dict) -> dict:
    """Scheduled function entry point."""
    async with factory() as session:
        execution = await JobExecutionCRUD.create_execution(session, "my-new-job")
        lock = await JobLockCRUD.acquire_lock(session, "my-new-job", expire_seconds=3600)

        if not lock:
            return {"statusCode": 200, "body": '{"status": "skipped"}'}

        try:
            # Your job logic here
            ...
            await JobExecutionCRUD.update_execution(session, execution.id, status="success")
            await session.commit()
        finally:
            await JobLockCRUD.release_lock(session, "my-new-job")

    return {"statusCode": 200, "body": '{"status": "ok"}'}
```

3. **Register in** [`project.yml`](../backend/project.yml:1) with a `schedule` trigger:

```yaml
packages:
  - name: cron/my-new-job
    functions:
      - name: main
        runtime: python:3.12
        web: false
        memory: 256
        timeout: 600
        environment:
          ...
        triggers:
          - type: schedule
            value: "0 4 * * *"  # 4 AM UTC daily
```

4. **Add config** in [`config.py`](../backend/packages/shared/config.py:1) (schedule, timeout, lock expiry).

5. **Write tests** in `backend/tests/unit/test_cron_my_new_job.py`.

### Adding a New API Endpoint

1. **Create the function directory**: `backend/packages/api/my-feature/`

2. **Implement the entry point** in `__init__.py`:

```python
"""DigitalOcean Function: My Feature API."""
from packages.shared.api_helpers import parse_request, segments, response
from packages.shared.db import factory
from packages.shared.cache import close_redis_pool


async def main(args: dict) -> dict:
    """DO Function entry point."""
    method, path, query, body = parse_request(args)
    segs = segments(path)
    had_error = False

    async with factory() as session:
        try:
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
```

3. **Register in** [`project.yml`](../backend/project.yml:1) with an `http` trigger (`web: true`).

4. **Write tests** in `backend/tests/unit/test_api_my_feature.py`.

### Database Changes

1. **Update models** in [`backend/packages/shared/models/`](../backend/packages/shared/models/__init__.py)
2. **Generate migration**: `uv run alembic revision --autogenerate -m "description"`
3. **Apply migration**: `uv run alembic upgrade head`
4. **Update CRUD** in [`backend/packages/shared/crud/`](../backend/packages/shared/crud/__init__.py)
5. **Write tests**

See [docs/migrations.md](migrations.md) for the full migration workflow.

---

## Testing

### Frontend Testing

```bash
cd frontend
bun run lint          # Check for linting errors
bun run typecheck     # Check TypeScript types
bun run dev           # Run development server
```

### Backend Testing

```bash
cd backend
./scripts/dev.sh                  # Start local PostgreSQL + Redis
uv run pytest tests/unit/ -v      # Run unit tests
uv run pytest tests/unit/ --cov   # Run with coverage
uv run pytest tests/integration/ -v  # Run integration tests
```

### Test Structure

```
backend/tests/
├── unit/          # Fast unit tests (no external dependencies)
├── integration/   # Integration tests (requires Docker services)
└── conftest.py    # Shared fixtures
```

---

## Linting and Type Checking

### Backend

```bash
cd backend

# Lint with ruff
uv run ruff check .

# Format with ruff
uv run ruff format .

# Type check with mypy
uv run mypy packages/
```

### Frontend

```bash
cd frontend

# Lint
bun run lint

# Type check
bun run typecheck
```

---

## Debugging

### Checking Database State

```bash
cd backend

# Connect to local PostgreSQL
docker exec -it $(docker ps -qf "ancestor=postgres:16-alpine") psql -U whatismytip -d whatismytip

# Check recent job executions
SELECT job_name, status, started_at, error_message FROM job_executions ORDER BY started_at DESC LIMIT 10;

# Check active locks
SELECT * FROM job_locks;
```

### Checking Redis State

```bash
# Connect to local Redis
docker exec -it $(docker ps -qf "ancestor=redis:7-alpine") redis-cli

# List keys
KEYS *

# Check a specific key
GET games:round:5
TTL games:round:5
```

### Viewing Function Logs

```bash
# List recent activations
doctl serverless activations list

# Get details of a specific activation
doctl serverless activations get <activation_id>

# Get the last activation result
doctl serverless activations result --last
```

---

## Environment Variables

### Backend (`.env`)

See [`backend/.env.example`](../backend/.env.example) for the full template:

```bash
# Database (PostgreSQL via asyncpg)
DATABASE_URL=postgresql+asyncpg://whatismytip:whatismytip@localhost:5432/whatismytip

# Redis
REDIS_URL=redis://localhost:6379/0

# External APIs
SQUIGGLE_API_BASE=https://api.squiggle.com.au
SQUIGGLE_CONTACT_EMAIL=contact@whatismytip.com
OPENROUTER_API_KEY=your_key
OPENROUTER_MODEL=google/gemma-4-26b-a4b-it:free
OPENROUTER_BASE_URL=https://openrouter.ai/api/v1

# Admin
ADMIN_API_KEY=your_admin_key

# Environment
ENVIRONMENT=development
```

### Frontend (`.env`)

```bash
# Point to local or deployed Functions gateway
API_BASE_URL=http://localhost:8000
# Production:
# API_BASE_URL=https://faas.syd1.digitaloceanspaces.com/<namespace>
```

---

## Additional Resources

- [Backend Architecture](backend.md)
- [Deployment Guide](deployment.md)
- [Database Migrations](migrations.md)
- [API Reference](api.md)
- [DigitalOcean Functions Docs](https://docs.digitalocean.com/products/functions/)
- [doctl CLI Reference](https://docs.digitalocean.com/reference/doctl/)
