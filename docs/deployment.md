# WhatIsMyTip Deployment Guide

## Overview

This guide covers deploying WhatIsMyTip.com to DigitalOcean App Platform. The deployment consists of:

- **Backend**: a single FastAPI container (image built from `backend/Dockerfile`) with an in-process APScheduler running the 4 cron jobs
- **Database**: Managed PostgreSQL 16
- **Cache**: Managed Redis 7
- **Reverse proxy**: a small nginx container in front of the FastAPI backend (handles CORS-adjacent headers + a self-contained `/healthz` liveness probe)
- **Frontend**: Static Nuxt 4 site on App Platform (static site hosting)

## Prerequisites

- DigitalOcean account
- `doctl` CLI installed and authenticated (`doctl auth init`)
- Domain name (e.g., whatismytip.com)
- OpenRouter API key
- Squiggle API access (free tier available)
- Git repository (GitHub)
- `uv` (Python package manager) for running migrations
- Docker (for the local image build / smoke test)
- A DigitalOcean Container Registry (the deploy script pushes there)

## Architecture

```
┌──────────────────────────────────────────────────────────────────┐
│                      DigitalOcean Cloud                          │
│                                                                  │
│   ┌──────────────────────┐    ┌──────────────────────┐         │
│   │  App Platform         │    │  Container Registry   │         │
│   │                       │    │  (registry.digital    │         │
│   │   ┌──────────────┐   │    │   ocean.com)          │         │
│   │   │  Frontend     │   │    │   ┌──────────────┐    │         │
│   │   │  Nuxt 4 SSG   │   │    │   │  api:tag      │◀──┼──push───┤
│   │   └──────────────┘   │    │   └──────────────┘    │         │
│   │                       │    │           ▲           │         │
│   │   ┌──────────────┐   │    │           │           │         │
│   │   │  proxy (nginx)│──┼────┼───────────┘           │         │
│   │   └──────┬───────┘   │    │                       │         │
│   │          │            │    │   ┌──────────────┐    │         │
│   │   ┌──────▼───────┐   │    │   │  proxy:latest │    │         │
│   │   │  api (FastAPI)│   │    │   └──────────────┘    │         │
│   │   │  + APScheduler│   │    │                       │         │
│   │   └──────┬───────┘   │    │                       │         │
│   └──────────┼────────────┘    └──────────────────────┘         │
│              │                                                   │
│       ┌──────┴──────┐                                            │
│       ▼             ▼                                            │
│  ┌─────────┐  ┌─────────┐                                        │
│  │ Postgres│  │  Redis  │                                        │
│  │ (managed)│  │(managed)│                                        │
│  └─────────┘  └─────────┘                                        │
└──────────────────────────────────────────────────────────────────┘
```

The user-facing URL pattern is `/api/...`.  The App Platform ingress routes `/api` to the `whatismytip-proxy` service, which forwards requests to the `whatismytip-api` component (`fastapi:8000`) over the App Platform private network.  The nginx proxy is purely a pass-through — no path rewriting, just standard reverse-proxy headers and a self-contained `/healthz` for orchestrator probes.

The frontend component (Nuxt 4 static build) is served directly by the App Platform static-site component on the primary domain.

---

## Step 1: Provision Managed PostgreSQL

1. Go to [DigitalOcean → Databases](https://cloud.digitalocean.com/databases)
2. Click **Create Database Cluster**
3. Select **PostgreSQL 16**
4. Choose a plan (e.g., 1 GB / 1 vCPU for development, scale as needed)
5. Select a region close to your App Platform app (e.g., `syd1` for Sydney)
6. Wait for provisioning to complete (2-5 minutes)

### Connection Details

After provisioning, note the following from the **Connection Details** tab:

- **Host**: `db-postgresql-xxx-xxx.db.ondigitalocean.com`
- **Port**: `25060`
- **Database**: `defaultdb`
- **Username**: `doadmin`
- **Password**: *(generated)*

### Build Connection String

```
postgresql+asyncpg://doadmin:<password>@db-postgresql-xxx-xxx.db.ondigitalocean.com:25060/defaultdb?ssl=require
```

> **Important**: The `?ssl=require` parameter is required for managed PostgreSQL.

### Configure Trusted Sources

In the database settings, add the App Platform app's outbound IP range to the Trusted Sources (or open the cluster to all DO sources, which is fine for a self-contained project).

---

## Step 2: Provision Managed Redis

1. Go to [DigitalOcean → Databases](https://cloud.digitalocean.com/databases)
2. Click **Create Database Cluster**
3. Select **Redis 7**
4. Choose a plan (e.g., 1 GB for development)
5. Select the same region as your PostgreSQL instance
6. Wait for provisioning to complete

### Connection Details

Note the Redis connection string:

```
rediss://default:<password>@db-redis-xxx-xxx.db.ondigitalocean.com:25061
```

> **Note**: Managed Redis uses TLS (`rediss://` protocol).

---

## Step 3: Create a DigitalOcean Container Registry

1. Go to [DigitalOcean → Container Registry](https://cloud.digitalocean.com/registry)
2. Click **Create Registry**
3. Choose the same region as your PostgreSQL/Redis
4. Note the registry endpoint (e.g. `registry.digitalocean.com/whatismytip`)

You'll push the FastAPI image here as part of the deploy.

---

## Step 4: Configure Environment Variables

### Create `backend/.env` File

```bash
cd backend
cp .env.example .env
```

Edit `.env`:

```bash
# Database (Managed PostgreSQL via asyncpg — ssl=require for managed)
DATABASE_URL=postgresql+asyncpg://doadmin:<password>@db-postgresql-xxx.db.ondigitalocean.com:25060/defaultdb?ssl=require

# Cache (Managed Redis via TLS)
REDIS_URL=rediss://default:<password>@db-redis-xxx-xxx.db.ondigitalocean.com:25061

# Squiggle API
SQUIGGLE_API_BASE=https://api.squiggle.com.au
SQUIGGLE_CONTACT_EMAIL=contact@whatismytip.com

# OpenRouter (AI explanations)
OPENROUTER_API_KEY=your_openrouter_api_key
OPENROUTER_MODEL=google/gemma-4-26b-a4b-it:free
OPENROUTER_BASE_URL=https://openrouter.ai/api/v1

# Admin
ADMIN_API_KEY=your_secure_admin_api_key

# Environment
ENVIRONMENT=production
CORS_ORIGINS=https://whatismytip.com,https://www.whatismytip.com
RATE_LIMIT_PER_MINUTE=60

# Required by the deploy script
DO_REGISTRY=registry.digitalocean.com/whatismytip
DO_APP_ID=<your-app-platform-app-id>
```

The cron schedules themselves (`DAILY_SYNC_CRON`, `TIP_GENERATION_CRON`, `HISTORIC_REFRESH_CRON`, `MATCH_COMPLETION_CRON`) are read directly by the in-process APScheduler — they default to the Australia/Perth schedules baked into `packages/shared/config.py` and can be overridden per-environment. (The older `CRON_DAILY_SYNC` / `CRON_TIP_GENERATION` / `CRON_HISTORICAL_REFRESH` / `CRON_MATCH_COMPLETION_CHECK` env vars used by the FaaS handlers were removed in Phase 5 alongside the FaaS code itself.)

---

## Step 5: Run Database Migrations

Run Alembic migrations against the managed PostgreSQL database **before** deploying the image:

```bash
cd backend
uv run alembic upgrade head
```

Verify the schema was applied:

```bash
uv run alembic current
# Should show: 0002_weather_players_injuries (head)
```

See [docs/migrations.md](migrations.md) for the full migration workflow.

---

## Step 6: Deploy the Backend

### Using the Deploy Script

The [`scripts/deploy.sh`](../backend/scripts/deploy.sh:1) script handles the full deploy:

```bash
cd backend
./scripts/deploy.sh
```

This script:

1. **Pre-flight**: checks for `docker`, `doctl`, `uv`
2. Loads `.env` (so `DO_REGISTRY` and `DO_APP_ID` are set)
3. Runs the unit test suite (`uv run pytest tests/unit/ -v`)
4. Runs database migrations (`uv run alembic upgrade head`)
5. Builds the image: `docker build -f Dockerfile -t ${DO_REGISTRY}/api:${IMAGE_TAG} .`
6. Pushes the image: `docker push ${DO_REGISTRY}/api:${IMAGE_TAG}`
7. Triggers an App Platform deploy: `doctl apps create-deployment ${DO_APP_ID} --force-rebuild`
8. Polls `${APP_URL}/health` (defaults to `https://whatismytip.com`) for up to 60 s

`IMAGE_TAG` defaults to the current git short SHA so every build is uniquely tagged.

#### Dry-run

To see the full deploy plan without running anything:

```bash
./scripts/deploy.sh --dry-run
```

#### Manual Steps (if you don't want the script)

```bash
cd backend

# Build
docker build -f Dockerfile -t ${DO_REGISTRY}/api:${IMAGE_TAG:-manual} .

# Push
docker push ${DO_REGISTRY}/api:${IMAGE_TAG:-manual}

# Trigger deploy
doctl apps create-deployment ${DO_APP_ID} --force-rebuild
```

### Verify Deployment

Once the App Platform rollout finishes (a few minutes), verify the API:

```bash
curl https://whatismytip.com/health
# Expected: {"status":"healthy","db":"ok","redis":"ok",...}
```

---

## Step 7: Configure the Frontend (App Platform)

### Step 1: Create App for Frontend

1. Go to [DigitalOcean → App Platform](https://cloud.digitalocean.com/apps)
2. Click **Create App**
3. Select **Deploy from Git** → connect your repository
4. Set source directory to `frontend/`

### Step 2: Configure Build Settings

| Setting | Value |
|---------|-------|
| **Build Command** | `bun install && bun run generate` |
| **Output Directory** | `.output/public` |
| **Environment** | Static site |

### Step 3: Configure Environment Variables

| Variable | Value |
|----------|-------|
| `NUXT_PUBLIC_API_BASE` | `https://whatismytip.com` |
| `NUXT_PUBLIC_UMAMI_HOST` | (optional) Umami host for analytics |
| `NUXT_PUBLIC_UMAMI_WEBSITE_ID` | (optional) Umami website ID |
| `NUXT_PUBLIC_SITE_URL` | `https://whatismytip.com` |

### Step 4: Deploy

1. Click **Deploy**
2. Wait for the build to complete (3-5 minutes)
3. Verify the frontend is accessible

---

## Cron Jobs (in-process APScheduler)

The four cron jobs run in the **same process** as the FastAPI app (no separate FaaS handlers).  Schedules are interpreted in the FastAPI container's local timezone (default `Australia/Perth`, UTC+8):

| Job | Schedule (AWST) | AWST Description |
|-----|-----------------|------------------|
| `daily-sync` | `*/15 * * * *` | Every 15 minutes |
| `match-completion` | `5,20,35,50 * * * *` | 4× per hour, offset by 5 |
| `tip-generation` | `0 3 * * *` | 3:00 AM daily |
| `historic-refresh` | `0 4 * * 0` | 4:00 AM Sunday |

Schedules can be overridden via env vars (`DAILY_SYNC_CRON`, `TIP_GENERATION_CRON`, `HISTORIC_REFRESH_CRON`, `MATCH_COMPLETION_CRON`) — see [`packages/shared/config.py`](../backend/packages/shared/config.py:1). (The old `CRON_*` env vars used by the FaaS handlers were removed in Phase 5.)

> **Why this changed**: Phase 3 moved the cron jobs in-process (from per-FaaS OpenWhisk triggers) so the deployments don't need a separate FaaS namespace.  The single long-lived FastAPI process owns the locks; multi-instance deploys share the locks via Postgres advisory locks (see [`app/core/scheduler.py`](../backend/app/core/scheduler.py:1)).

---

## Domain Configuration

### Frontend Domain

1. In App Platform, go to **Settings** → **Domains**
2. Add `whatismytip.com` and `www.whatismytip.com`
3. Configure DNS records (A record for `@`, CNAME for `www`)
4. SSL is automatic

### CORS Configuration

Update `CORS_ORIGINS` in your `.env` and redeploy:

```bash
CORS_ORIGINS=https://whatismytip.com,https://www.whatismytip.com
```

---

## OpenRouter API Setup

### Step 1: Get API Key

1. Sign up at [OpenRouter](https://openrouter.ai/)
2. Navigate to **API Keys**
3. Generate a new API key

### Step 2: Configure

Add to `backend/.env`:

```bash
OPENROUTER_API_KEY=your_key
OPENROUTER_MODEL=google/gemma-4-26b-a4b-it:free
```

### Step 3: Cost Management

- Explanations are cached in PostgreSQL — only generated once per tip
- The `tip-generation` cron runs nightly, batching all games
- Monitor usage in the OpenRouter dashboard

---

## Monitoring and Logging

### App Platform Logs

```bash
# Tail logs for the running app
doctl apps logs ${DO_APP_ID}

# Or via the dashboard: https://cloud.digitalocean.com/apps → your app → Logs
```

### Cron Job Monitoring

1. **Admin API** — query job status:

```bash
curl -H "X-API-Key: $ADMIN_API_KEY" \
  https://whatismytip.com/api/admin/jobs/status
```

2. **Database queries** — check the `job_executions` table:

```sql
SELECT job_name, status, started_at, completed_at, error_message
FROM job_executions
ORDER BY started_at DESC
LIMIT 20;
```

3. **Alerting webhooks** — configure `ALERT_WEBHOOK_URL` to receive failure notifications

### Database Monitoring

Use the DigitalOcean database dashboard for:

- Connection count
- Query performance
- Storage usage
- Backup status

---

## Health Checks

### Backend

The FastAPI app exposes two health endpoints:

- `GET /health` — JSON status with DB + Redis connectivity (liveness/readiness for orchestrator)
- `GET /healthz` (on the proxy) — static `200 "ok"` (doesn't require the FastAPI container to be reachable)

App Platform health check should target `GET /health` on the api component.

### Frontend

App Platform serves the static site at `/`.  The default static-site health check (HEAD `/`) is sufficient.

---

## Security Considerations

### Environment Variables

- Never commit `.env` files to Git
- Rotate API keys regularly
- Use strong, unique values for `ADMIN_API_KEY`

### Database Security

- Managed PostgreSQL requires TLS (`?ssl=require`)
- Configure **Trusted Sources** to restrict connections
- Enable automatic daily backups

### CORS Configuration

```bash
CORS_ORIGINS=https://whatismytip.com,https://www.whatismytip.com
```

### Rate Limiting

- The FastAPI app enforces per-IP rate limits (default 60 req/min)
- Configurable via `RATE_LIMIT_PER_MINUTE`

---

## Backup Strategy

### Database Backups

Managed PostgreSQL includes:

- **Automatic daily backups** (7-day retention on basic plans)
- **Point-in-time recovery** (up to 7 days on basic, longer on higher tiers)
- **Manual backups** via `pg_dump` if needed

### Container Image Backups

- The git repository is the source of truth
- Use feature branches for testing
- Tag production releases
- Container images are pushed to the DO Container Registry with git-SHA tags

---

## Deployment Process

### Pre-Deployment Checklist

- [ ] Local tests pass (`uv run pytest tests/unit/ -v`)
- [ ] `backend/.env` updated with production credentials
- [ ] Database migrations tested
- [ ] Git commit pushed to `main`
- [ ] Frontend env vars updated with new API URL

### Deployment Steps

```bash
# 1. Deploy backend (image build + push + App Platform rollout)
cd backend
./scripts/deploy.sh

# 2. Deploy frontend (App Platform dashboard or via doctl)
#    (the .do/app.yaml spec can be applied with `doctl apps update`)
```

### Post-Deployment Testing

```bash
# Health
curl https://whatismytip.com/health
# Expected: {"status":"healthy",...}

# Sample endpoint
curl https://whatismytip.com/api/games?upcoming=true

# Frontend
curl -I https://whatismytip.com
```

### Rollback Procedure

```bash
# List the last 10 deployments
doctl apps list-deployments ${DO_APP_ID}

# Roll back to a specific deployment by ID
doctl apps create-deployment ${DO_APP_ID} --deployment-id <previous-deployment-id>
```

For database rollbacks:

```bash
cd backend
uv run alembic downgrade -1
```

---

## Troubleshooting

### Image build / push failures

- Verify `docker info` works (daemon running)
- Verify `doctl account get` succeeds
- Check the registry exists in the same region as your DO account
- Check `DO_REGISTRY` matches the registry's full path

### App not healthy after deploy

- `doctl apps logs ${DO_APP_ID}` — look for Python tracebacks
- Verify the database and Redis are reachable from the App Platform app (Trusted Sources)
- Curl the `/health` endpoint directly to see the structured error
- The deploy script's `/health` poll is best-effort — the deploy itself is async

### Cron jobs not running

- Check the app logs for the APScheduler startup banner
- Verify `CRON_ENABLED=true` (default) in the env
- Inspect `job_executions` for `failed` / `skipped` runs
- If the job is locked, look at `job_locks` for stale rows (`lock_expires_at < now()`)

### Database connection errors

- Verify `?ssl=require` in `DATABASE_URL`
- Check Trusted Sources in database settings
- Confirm port (25060 for managed PostgreSQL)

### Frontend can't reach API

- Verify `NUXT_PUBLIC_API_BASE` is set correctly in the static-site envs
- Check CORS configuration in backend `.env`
- Curl `/api/games?upcoming=true` directly to confirm the proxy passes through

---

## Cost Estimation

| Component | Plan | Monthly Cost |
|-----------|------|--------------|
| App Platform (api) | basic-xxs | $5 |
| App Platform (proxy) | basic-xxs | $5 |
| App Platform (frontend) | static site | $3 |
| Container Registry | starter | $5 |
| Managed PostgreSQL | 1 GB | $15 |
| Managed Redis | 1 GB | $15 |
| OpenRouter API | per-usage | $5-20 |
| **Total** | | **~$53-68/month** |

Costs scale with usage. The App Platform basic-xxs tier provides 512 MB RAM and shared CPU per container.

---

## CI/CD

The repository includes a GitHub Actions workflow at [`.github/workflows/ci.yml`](../.github/workflows/ci.yml) that runs the unit tests on every push/PR.

The deployment workflow (`.github/workflows/deploy.yml`) builds the image, pushes to the DO Container Registry, and triggers an App Platform deployment. Required GitHub secrets:

| Secret | Description |
|--------|-------------|
| `DIGITALOCEAN_ACCESS_TOKEN` | DO API token for `doctl` |
| `DO_REGISTRY` | Registry hostname (e.g. `registry.digitalocean.com/whatismytip`) |
| `DO_APP_ID` | App Platform app ID |
| `DATABASE_URL` | Production PostgreSQL connection string |
| `REDIS_URL` | Production Redis connection string |
| `OPENROUTER_API_KEY` | OpenRouter API key |
| `ADMIN_API_KEY` | Admin API key |
| Other env vars | As needed (see `backend/.env.example`) |

---

## Support and Resources

- [DigitalOcean App Platform Docs](https://docs.digitalocean.com/products/app-platform/)
- [DigitalOcean Container Registry Docs](https://docs.digitalocean.com/products/container-registry/)
- [DigitalOcean Managed Databases](https://docs.digitalocean.com/products/databases/)
- [Nuxt Deployment Guide](https://nuxt.com/docs/getting-started/deployment)
- [doctl CLI Reference](https://docs.digitalocean.com/reference/doctl/)

See also:
- [docs/digital-ocean-setup.md](digital-ocean-setup.md) — One-time setup walkthrough
- [docs/backend.md](backend.md) — Backend architecture
- [docs/development.md](development.md) — Local development setup
- [docs/migrations.md](migrations.md) — Database migration guide
