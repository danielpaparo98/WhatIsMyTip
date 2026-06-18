# WhatIsMyTip Development Guide

Local development for the FastAPI backend and Nuxt 4 frontend. For
architecture, deployment, and runbook details, see the linked docs at
the bottom.

## Prerequisites

- **Bun** (JavaScript) — [bun.sh](https://bun.sh)
- **uv** (Python) — `pipx install uv`
- **Python 3.12+**, **Node.js 18+**
- **Docker Desktop** or **Podman** (local PostgreSQL + Redis)

## Quick Start (full stack via Docker)

The fastest path to a running app: `docker compose` brings up Postgres,
Redis, FastAPI, and the Nuxt frontend in one command. The dev script
auto-detects Docker then Podman.

```bash
# macOS / Linux / WSL
git clone https://github.com/danielpaparo98/WhatIsMyTip.git
cd WhatIsMyTip
./scripts/dev.sh up --logs

# Windows + PowerShell
.\scripts\dev.ps1 up -Up -Logs
```

| Service     | Port  | Notes |
|-------------|-------|-------|
| `postgres`  | 5432  | `wimt / wimt_dev_password` (dev only) |
| `redis`     | 6379  | No password |
| `api`       | 8000  | FastAPI + Uvicorn (`--reload`) |
| `frontend`  | 3000  | Nuxt 4 dev server (HMR) |
| `init-data` | n/a   | One-shot: migrations + CSV load, then exits 0 |

URLs: frontend `http://localhost:3000` · API `http://localhost:8000` ·
Swagger UI `http://localhost:8000/docs`.

### Common `dev.sh` commands

| Command | Effect |
|---------|--------|
| `up --logs` | Start the stack and follow logs |
| `down` | Stop (volumes preserved) |
| `reset` | Stop **and** delete the database volume |
| `logs api` | Tail logs of a specific service |
| `shell api` | Bash into the `api` container |
| `psql` | Open a psql shell against the dev DB |
| `redis` | Open a redis-cli shell against the dev cache |
| `config` | Validate `docker-compose.yml` |

To load real AFL data into the dev DB, see
[`docs/data-loading.md`](data-loading.md).

## Backend (without Docker, optional)

```bash
cd backend
uv sync                              # install Python deps
cp .env.example .env                 # then edit
uv run alembic upgrade head          # apply migrations
uv run uvicorn main:app --reload     # API on :8000
```

The full env-var reference is [`backend/.env.example`](../backend/.env.example).

## Frontend (without Docker, optional)

```bash
cd frontend
bun install
cp .env.example .env                 # set API_BASE_URL
bun run dev                          # dev server on :3000
```

## Testing

```bash
cd backend
./scripts/dev.sh                     # start Postgres + Redis
uv run pytest tests/unit/ -v         # unit tests (no external deps)
uv run pytest tests/integration/ -v  # integration tests (need Docker)
```

End-to-end browser tests:

```bash
cd frontend
bunx playwright test                 # see frontend/tests/
```

## Scheduled Jobs (in-process APScheduler)

Four jobs run inside the FastAPI container — see
[`backend/app/cron/`](../backend/app/cron/) and
[`backend/app/core/scheduler.py`](../backend/app/core/scheduler.py:1).
Schedules (AWST by default) are read from
[`packages/shared/config.py`](../backend/packages/shared/config.py:41).

| Job | Default schedule | Env var |
|-----|------------------|---------|
| `daily-sync` | `*/15 * * * *` | `DAILY_SYNC_CRON` |
| `match-completion` | `5,20,35,50 * * * *` | `MATCH_COMPLETION_CRON` |
| `tip-generation` | `0 3 * * *` | `TIP_GENERATION_CRON` |
| `historic-refresh` | `0 4 * * 0` | `HISTORIC_REFRESH_CRON` |

Disable locally with `CRON_ENABLED=false` (default in `docker-compose.yml`).

Manual trigger (requires `X-API-Key` matching `ADMIN_API_KEY`):

```bash
curl -X POST -H "X-API-Key: $ADMIN_API_KEY" \
  http://localhost:8000/api/admin/daily-sync/trigger
curl -X POST -H "X-API-Key: $ADMIN_API_KEY" \
  http://localhost:8000/api/admin/tip-generation/trigger
```

## Linting & Type Checks

| Stack | Command | Where |
|-------|---------|-------|
| Backend lint | `uv run ruff check .` | [`backend/pyproject.toml`](../backend/pyproject.toml) |
| Backend format | `uv run ruff format .` | same |
| Backend types | `uv run mypy packages/` | same |
| Frontend lint | `bun run lint` | [`frontend/package.json`](../frontend/package.json) |
| Frontend types | `bun run typecheck` | same |

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| `Cannot connect to Docker daemon` | Start Docker Desktop (or `podman machine start`). |
| Port 5432 / 6379 / 8000 / 3000 in use | Stop the conflicting process or edit `ports:` in `docker-compose.yml`. |
| `init-data` keeps restarting | `docker compose logs init-data` — usually a CSV parse error or stale `data/`. |
| Frontend can't reach the API | Confirm `NUXT_PUBLIC_API_BASE=http://localhost:8000` in compose (browser hits host's localhost, not the container network). |
| Want a fresh database | `./scripts/dev.sh reset`. |
| Cron jobs not running | See [`docs/operations.md`](operations.md#cron-jobs-not-running). |
| Auth / 401 errors on admin endpoints | See [`docs/security-model.md`](security-model.md). |

## See Also

- [`docs/architecture` — Backend architecture](backend.md)
- [`docs/architecture` — Frontend structure](frontend.md)
- [`docs/deployment.md`](deployment.md) — Production deployment
- [`docs/api.md`](api.md) — API endpoint reference
- [`docs/operations.md`](operations.md) — Production runbook
- [`docs/security-model.md`](security-model.md) — Auth, rate limiting, secrets
- [`docs/migrations.md`](migrations.md) — Alembic workflow
- [`docs/data-loading.md`](data-loading.md) — Seeding real AFL data
