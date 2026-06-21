# DigitalOcean Setup Guide

> ⚠️ **HISTORICAL / SUPPLEMENTARY**
>
> This guide is retained for the **initial DigitalOcean account setup** (Steps 1–7: provisioning
> managed PostgreSQL, managed Redis, the container registry, env-var wiring, and the first
> `alembic upgrade head`).  The **current production deployment workflow** — building the
> container, pushing it to the DO registry, and triggering the App Platform rollout — lives in
> [`docs/deployment.md`](deployment.md).  After your account and databases are provisioned, follow
> `deployment.md` for everything else.

This guide walks you through setting up WhatIsMyTip.com on DigitalOcean using App Platform (containers), managed PostgreSQL, and managed Redis. The FaaS architecture is no longer used — see the deployment guide for the full container-based flow.

## Prerequisites

- DigitalOcean account
- GitHub account (for the App Platform git integration)
- `doctl` CLI tool installed and authenticated
- `uv` (Python package manager)
- `bun` (JavaScript runtime)
- Docker (for the local image build + smoke test)
- OpenRouter API key

---

## Step 1: Set Up DigitalOcean Account

1. Sign up at [digitalocean.com](https://www.digitalocean.com)
2. Add payment method
3. Generate an API token:
   - Go to **API → Tokens/Keys**
   - Click **Generate New Token**
   - Save the token (needed for `doctl` and CI/CD)

## Step 2: Configure doctl

```bash
# Install doctl
# macOS
brew install doctl

# Linux
curl -sSL https://dl.digitalocean.com/doctl/install.sh | sh

# Windows
choco install doctl

# Authenticate
doctl auth init
```

## Step 3: Provision Managed PostgreSQL

1. Go to [DigitalOcean → Databases](https://cloud.digitalocean.com/databases)
2. Click **Create Database Cluster**
3. Select **PostgreSQL 16**
4. Choose a plan (1 GB / 1 vCPU for development)
5. Select region (e.g., `syd1` for Sydney)
6. Wait for provisioning (2-5 minutes)

**Note the connection details:**

```
Host:     db-postgresql-xxx-xxx.db.ondigitalocean.com
Port:     25060
Database: defaultdb
Username: doadmin
Password: <generated>
```

**Build the connection string:**

```
postgresql+asyncpg://doadmin:<password>@db-postgresql-xxx-xxx.db.ondigitalocean.com:25060/defaultdb?ssl=require
```

**Configure Trusted Sources:**

- In database settings, allow connections from your App Platform app (typically `0.0.0.0/0` for a simple setup, or the specific outbound IP range).

## Step 4: Provision Managed Redis

1. Go to [DigitalOcean → Databases](https://cloud.digitalocean.com/databases)
2. Click **Create Database Cluster**
3. Select **Redis 7**
4. Choose a plan (1 GB for development)
5. Select the same region as PostgreSQL

**Note the connection string:**

```
rediss://default:<password>@db-redis-xxx-xxx.db.ondigitalocean.com:25061
```

## Step 5: Create a Container Registry

1. Go to [DigitalOcean → Container Registry](https://cloud.digitalocean.com/registry)
2. Click **Create Registry**
3. Choose the same region as PostgreSQL/Redis
4. Note the registry endpoint (e.g. `registry.digitalocean.com/whatismytip`)

You'll push the FastAPI image here as part of the deploy.

## Step 6: Configure Environment

```bash
cd backend
cp .env.example .env
```

Edit `backend/.env` with your managed database, Redis, and container registry details:

```bash
DATABASE_URL=postgresql+asyncpg://doadmin:<password>@db-postgresql-xxx.db.ondigitalocean.com:25060/defaultdb?ssl=require
REDIS_URL=rediss://default:<password>@db-redis-xxx-xxx.db.ondigitalocean.com:25061
SQUIGGLE_API_BASE=https://api.squiggle.com.au
SQUIGGLE_CONTACT_EMAIL=contact@whatismytip.com
OPENROUTER_API_KEY=your_key
OPENROUTER_MODEL=google/gemma-4-26b-a4b-it:free
OPENROUTER_BASE_URL=https://openrouter.ai/api/v1
ADMIN_API_KEY=your_secure_admin_key
ENVIRONMENT=production
CORS_ORIGINS=https://whatismytip.com,https://www.whatismytip.com

# Required by scripts/deploy.sh
DO_REGISTRY=registry.digitalocean.com/whatismytip
DO_APP_ID=<leave blank for now, fill in after Step 7>
```

## Step 7: Run Database Migrations

Run migrations against the managed PostgreSQL database **before** deploying the image:

```bash
cd backend
uv run alembic upgrade head

# Verify
uv run alembic current
```

See [docs/migrations.md](migrations.md) for the full migration workflow.

## Step 8: Create the App Platform App

There are two approaches:

### Option A: Use the existing `.do/app.yaml` spec

The repository ships with a [`.do/app.yaml`](../.do/app.yaml) that declares all three App Platform components (proxy, api, static site) wired together. Apply it with:

```bash
doctl apps create --spec .do/app.yaml
```

This provisions all three components in one step. After it finishes, `doctl apps list` will show the new app — note its `ID` and put it in your `backend/.env` as `DO_APP_ID`.

### Option B: Create via the dashboard

1. Go to [DigitalOcean → App Platform](https://cloud.digitalocean.com/apps)
2. Click **Create App** → **Deploy from Git**
3. Connect your repository
4. Add three components (one at a time):

**Component 1: `whatismytip-proxy` (Docker)**

- Source directory: `backend/proxy`
- Dockerfile path: `Dockerfile`
- HTTP port: 8080
- Instance size: `basic-xxs`
- Routes: `/api`

**Component 2: `whatismytip-api` (Docker)**

- Source directory: `backend`
- Dockerfile path: `Dockerfile`
- HTTP port: 8000
- Instance size: `basic-xxs`
- Health check path: `/health`
- Env vars: `DATABASE_URL`, `REDIS_URL`, `SQUIGGLE_*`, `OPENROUTER_*`, `ADMIN_API_KEY`, `CORS_ORIGINS`, `RATE_LIMIT_PER_MINUTE`, `ENVIRONMENT`, `CRON_*` (override as needed)

**Component 3: `whatismytip-frontend` (Static Site)**

- Source directory: `frontend`
- Build command: `bun install && bun run generate`
- Output directory: `.output/public`
- Env vars: `NUXT_PUBLIC_API_BASE=https://whatismytip.com`, `NUXT_PUBLIC_UMAMI_HOST`, `NUXT_PUBLIC_UMAMI_WEBSITE_ID`, `NUXT_PUBLIC_SITE_URL`

5. **Don't** add an ingress rule for `whatismytip-api` — it should only be reached via the proxy over the private network.

### Verify the App

After the initial deployment finishes:

```bash
# Health
curl https://<app-url>/health
# Expected: {"status":"healthy",...}

# List components and their internal hostnames (you'll see fastapi:8000 etc.)
doctl apps spec get ${DO_APP_ID}
```

## Step 9: Deploy the Backend Image

Now that the app exists, push your first image and trigger a deploy:

```bash
cd backend

# (one-time) authenticate docker against the DO registry
doctl registry login

# Build + push + deploy
DO_REGISTRY=registry.digitalocean.com/whatismytip \
DO_APP_ID=<your-app-id> \
./scripts/deploy.sh
```

The script:

1. Runs the unit test suite
2. Runs `alembic upgrade head` (idempotent)
3. Builds the image from `Dockerfile`, tags it `${DO_REGISTRY}/api:${git-sha}`
4. Pushes the image
5. Triggers `doctl apps create-deployment ${DO_APP_ID} --force-rebuild`
6. Polls `https://whatismytip.com/health` for up to 60 s

To preview the plan without running anything:

```bash
./scripts/deploy.sh --dry-run
```

## Step 10: Configure Domain

### Frontend Domain

1. In App Platform, go to **Settings → Domains**
2. Add `whatismytip.com` and `www.whatismytip.com`
3. Configure DNS:
   - **A record**: `@` → App Platform IP
   - **CNAME**: `www` → App Platform URL

### SSL

App Platform includes free SSL certificates — automatically configured with custom domains.

### CORS

Ensure `CORS_ORIGINS` in your backend `.env` includes your domain:

```bash
CORS_ORIGINS=https://whatismytip.com,https://www.whatismytip.com
```

Redeploy the backend after changing this.

## Step 11: Initial Data Sync

After deployment, trigger the data sync jobs to populate the database:

```bash
# Trigger daily sync (fetches games from Squiggle API)
curl -X POST -H "X-API-Key: $ADMIN_API_KEY" \
  https://whatismytip.com/api/admin/daily-sync/trigger

# Trigger tip generation
curl -X POST -H "X-API-Key: $ADMIN_API_KEY" \
  https://whatismytip.com/api/admin/tip-generation/trigger
```

## Scheduled Jobs (in-process APScheduler)

The four cron jobs run **inside the FastAPI container** (no separate Functions namespace). Schedules are interpreted in the container's local timezone (default `Australia/Perth`, UTC+8):

| Job | Schedule (AWST) | Description |
|-----|-----------------|-------------|
| `daily-sync` | Every 15 min | Sync games from Squiggle API |
| `match-completion` | 4× per hour, offset by 5 | Detect completed matches |
| `tip-generation` | Daily 3:00 AM | Generate tips for upcoming round |
| `historic-refresh` | Weekly Sunday 4:00 AM | Refresh historical data |

Schedules can be overridden via env vars (e.g. `DAILY_SYNC_CRON`, `TIP_GENERATION_CRON`, `HISTORIC_REFRESH_CRON`, `MATCH_COMPLETION_CRON`) — see [`packages/shared/config.py`](../backend/packages/shared/config.py:1). (The old `CRON_*` env vars used by the FaaS handlers were removed in Phase 5.)

Multi-instance deploys coordinate via Postgres advisory locks (only one instance runs each job at a time).

## Cost Breakdown

| Component | Plan | Monthly Cost |
|-----------|------|-------------|
| App Platform (api) | basic-xxs | $5 |
| App Platform (proxy) | basic-xxs | $5 |
| App Platform (frontend) | static site | $3 |
| Container Registry | starter | $5 |
| Managed PostgreSQL | 1 GB / 1 vCPU | $15 |
| Managed Redis | 1 GB | $15 |
| OpenRouter API | per-usage | $5-20 |
| **Total** | | **~$53-68/month** |

## Monitoring

### App Logs

```bash
# Tail the app's combined logs (all components)
doctl apps logs ${DO_APP_ID}

# Or via the dashboard: https://cloud.digitalocean.com/apps → your app → Logs
```

### Cron Job Status

```bash
# Check job status via admin API
curl -H "X-API-Key: $ADMIN_API_KEY" \
  https://whatismytip.com/api/admin/metrics
```

### Databases

Use the DigitalOcean database dashboards for:

- Connection metrics
- Query performance
- Storage and backup status

## Troubleshooting

### Image build / push failures

- Run `doctl registry login` first
- Check `docker info` works (daemon running)
- Verify `DO_REGISTRY` matches the registry's full path (region-prefixed)
- Check the registry exists in the same region as your DO account

### App Platform rollout stuck

- `doctl apps list-deployments ${DO_APP_ID}` — see the rollout status
- `doctl apps logs ${DO_APP_ID}` — check for build/runtime errors
- The nginx proxy might not be able to reach `fastapi:8000` if the api component isn't on the same private network — verify all three components are in the same App Platform app

### Database connection errors

- Verify `?ssl=require` in `DATABASE_URL`
- Check Trusted Sources in database settings
- Confirm port (25060 for managed PostgreSQL)
- For App Platform, the api component talks to the DB over the public internet, so the DB's Trusted Sources must allow that egress (or open to `0.0.0.0/0`)

### Cron jobs not running

- Check the app logs for the APScheduler startup banner
- Verify `CRON_ENABLED=true` (default) in the env
- Inspect `job_executions` for `failed` / `skipped` runs
- If the job is locked, look at `job_locks` for stale rows (`lock_expires_at < now()`)
- Note: the cron jobs run in the FastAPI container's **local** timezone. If you redeploy to a region with a different default timezone, double-check the schedule.

### Frontend can't reach API

- Verify `NUXT_PUBLIC_API_BASE` points to the correct public URL (NOT the internal `fastapi:8000`)
- Check CORS configuration in backend `.env`
- Confirm the proxy is forwarding correctly: `curl -v https://whatismytip.com/api/games?upcoming=true`

## Scaling

- **App Platform (api / proxy)**: bump `instance_count` and/or `instance_size_slug` from `basic-xxs`
- **PostgreSQL**: upgrade plan for more connections/storage
- **Redis**: upgrade plan for more memory
- **Frontend**: static site, scales automatically
- **Cron jobs**: the in-process scheduler is shared via Postgres advisory locks, so adding instances is safe — only one instance runs each job at a time

## See Also

- [Deployment Guide](deployment.md) — Comprehensive deployment instructions (Phase 4 container-based flow)
- [Backend Architecture](backend.md) — FastAPI app architecture overview
- [Development Guide](development.md) — Local development setup
- [API Reference](api.md) — Full endpoint reference
