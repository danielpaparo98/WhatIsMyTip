# WhatIsMyTip Deployment Guide

## Overview

This guide covers deploying WhatIsMyTip.com to DigitalOcean. The deployment consists of:

- **Backend**: 8 serverless functions on DigitalOcean Functions (4 HTTP + 4 scheduled)
- **Database**: Managed PostgreSQL 16
- **Cache**: Managed Redis 7
- **Frontend**: Static Nuxt 4 site on DigitalOcean App Platform (static site hosting)

## Prerequisites

- DigitalOcean account
- `doctl` CLI installed and authenticated (`doctl auth init`)
- Domain name (e.g., whatismytip.com)
- OpenRouter API key
- Squiggle API access (free tier available)
- Git repository (GitHub)
- `uv` (Python package manager) for running migrations
- Docker (for running migrations locally, or use a management droplet)

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                      DigitalOcean Cloud                          │
│                                                                  │
│   ┌──────────────────────┐    ┌──────────────────────┐         │
│   │   DO Functions        │    │   App Platform        │         │
│   │   (Apache OpenWhisk)  │    │                       │         │
│   │                        │    │   ┌──────────────┐   │         │
│   │   ┌─HTTP Functions─┐  │    │   │  Frontend     │   │         │
│   │   │  • games        │  │    │   │  Nuxt 4 SSG   │   │         │
│   │   │  • tips         │  │    │   └──────────────┘   │         │
│   │   │  • backtest     │  │    └──────────────────────┘         │
│   │   │  • admin        │  │                                      │
│   │   └────────────────┘  │                                      │
│   │                        │                                      │
│   │   ┌─Cron Functions──┐  │                                      │
│   │   │  • daily-sync   │  │                                      │
│   │   │  • match-compl. │  │                                      │
│   │   │  • tip-gen      │  │                                      │
│   │   │  • historic-ref │  │                                      │
│   │   └────────────────┘  │                                      │
│   └───────┬───────┬───────┘                                      │
│           │       │                                                │
│           ▼       ▼                                                │
│   ┌──────────┐  ┌──────────┐                                     │
│   │PostgreSQL│  │  Redis   │                                     │
│   │ Managed  │  │ Managed  │                                     │
│   └──────────┘  └──────────┘                                     │
└─────────────────────────────────────────────────────────────────┘
```

---

## Step 1: Provision Managed PostgreSQL

1. Go to [DigitalOcean → Databases](https://cloud.digitalocean.com/databases)
2. Click **Create Database Cluster**
3. Select **PostgreSQL 16**
4. Choose a plan (e.g., 1 GB / 1 vCPU for development, scale as needed)
5. Select a region close to your Functions namespace (e.g., `syd1` for Sydney)
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

In the database settings, add your Functions namespace IP or set **Trusted Sources** to allow connections from DO Functions.

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

## Step 3: Create a Functions Namespace

1. Install `doctl` if not already installed:
   ```bash
   # macOS
   brew install doctl
   # Linux/Windows — see https://docs.digitalocean.com/reference/doctl/how-to/install/
   ```

2. Authenticate:
   ```bash
   doctl auth init
   ```

3. Create a Functions namespace:
   ```bash
   doctl serverless install --hosting-region syd1
   doctl serverless connect
   ```

4. Note the namespace and gateway URL:
   ```bash
   doctl serverless status
   ```

   Output includes:
   ```
   API host:    https://faas.syd1.digitaloceanspaces.com
   Namespace:   <your-namespace>
   ```

---

## Step 4: Configure Environment Variables

### Create `.env` File

Copy the template and fill in your managed database credentials:

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
```

### Environment Variables in functions.yml

The function-specific configuration (memory, timeout, schedules) is defined in [`functions.yml`](../backend/functions.yml:1). Sensitive values (API keys, passwords) are passed via the `.env` file during deployment.

> **Note:** The config file is named `functions.yml` (not the doctl default `project.yml`) to prevent DigitalOcean App Platform from auto-parsing it as an app spec when the `backend/` directory is the source root. The deploy script passes `--functions-config functions.yml` explicitly.

---

## Step 5: Run Database Migrations

Run Alembic migrations against the managed PostgreSQL database **before** deploying functions:

```bash
cd backend

# Set the DATABASE_URL for the migration (or use .env)
export DATABASE_URL="postgresql+asyncpg://doadmin:<password>@...?ssl=require"

# Run migrations
uv run alembic upgrade head
```

Verify the schema was applied:

```bash
uv run alembic current
# Should show: 0002_weather_players_injuries (head)
```

See [docs/migrations.md](migrations.md) for the full migration workflow.

---

## Step 6: Deploy Functions

### Using the Deploy Script

The [`scripts/deploy.sh`](../backend/scripts/deploy.sh:1) script handles the full deployment:

```bash
cd backend
./scripts/deploy.sh
```

This script:
1. Runs unit tests (`uv run pytest tests/unit/ -v`)
2. Runs database migrations (`uv run alembic upgrade head`)
3. Deploys all functions (`doctl serverless deploy . --env .env`)
4. Verifies health endpoints

### Manual Deployment

To deploy manually:

```bash
cd backend
doctl serverless deploy . --env .env
```

### Verify Deployment

After deployment, check the function URLs:

```bash
doctl serverless functions list
```

Test the health endpoint:

```bash
curl https://faas.syd1.digitaloceanspaces.com/<namespace>/api/games/health
# Expected: {"status": "healthy"}
```

---

## Step 7: Configure Cron Function Schedules

Scheduled functions are configured in [`functions.yml`](../backend/functions.yml:1) and deployed with the functions. No separate scheduling step is needed.

| Function | Schedule (UTC) | AWST Equivalent |
|----------|----------------|-----------------|
| `daily-sync` | `*/15 * * * *` | Every 15 minutes |
| `match-completion` | `5,20,35,50 * * * *` | 4× per hour |
| `tip-generation` | `0 19 * * *` | 3:00 AM daily |
| `historic-refresh` | `0 20 * * 6` | 4:00 AM Saturday |

Schedules can be overridden via environment variables (see [`config.py`](../backend/packages/shared/config.py:1)).

---

## Frontend Deployment (DigitalOcean App Platform)

### Step 1: Create App for Frontend

1. Go to [DigitalOcean App Platform](https://cloud.digitalocean.com/apps)
2. Click **Create App**
3. Select **Deploy from Git** → connect your repository
4. Set source directory to `frontend/`

### Step 2: Configure Build Settings

| Setting | Value |
|---------|-------|
| **Build Command** | `bun install && bun run build` |
| **Output Directory** | `.output/public` |
| **Environment** | Static site |

### Step 3: Configure Environment Variables

| Variable | Value |
|----------|-------|
| `API_BASE_URL` | `https://faas.syd1.digitaloceanspaces.com/<namespace>` |

### Step 4: Deploy

1. Click **Deploy**
2. Wait for the build to complete (3-5 minutes)
3. Verify the frontend is accessible

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

Add to `.env`:
```bash
OPENROUTER_API_KEY=your_key
OPENROUTER_MODEL=google/gemma-4-26b-a4b-it:free
```

### Step 3: Cost Management

| Metric | Value |
|--------|-------|
| Cost per 1M tokens | ~$0.15 |
| Estimated monthly cost | $5-20 (depending on usage) |

**Optimisation tips:**
- Explanations are cached in PostgreSQL — only generated once per tip
- The `tip-generation` cron runs nightly, batching all games
- Monitor usage in the OpenRouter dashboard

---

## Monitoring and Logging

### Functions Monitoring

DigitalOcean Functions provides:
- **Invocation logs** — per-function execution logs accessible via `doctl` or the dashboard
- **Metrics** — invocation count, duration, errors
- **Alerts** — the `AlertingService` sends webhook notifications on job failures

```bash
# View function activation logs
doctl serverless activations list
doctl serverless activations get <activation_id>
```

### Cron Job Monitoring

Monitor scheduled functions through:

1. **Admin API** — query job status:
   ```bash
   curl -H "X-Admin-API-Key: $ADMIN_API_KEY" \
     https://faas.syd1.digitaloceanspaces.com/<namespace>/api/admin/jobs/status
   ```

2. **Database queries** — check `job_executions` table:
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

### Backend (Functions)

Health check endpoints are available on each HTTP function:

```bash
curl https://faas.syd1.digitaloceanspaces.com/<namespace>/api/games/health
```

Expected response:
```json
{"status": "healthy"}
```

### Frontend

Configure health check in App Platform:
- **Path**: `/`
- **Interval**: 30 seconds

---

## Security Considerations

### Environment Variables

- Never commit `.env` files to Git
- Use the `.env` file passed to `doctl serverless deploy`
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

- HTTP functions enforce 60 requests per minute per IP
- Configurable via `RATE_LIMIT_PER_MINUTE`

---

## Backup Strategy

### Database Backups

Managed PostgreSQL includes:
- **Automatic daily backups** (7-day retention on basic plans)
- **Point-in-time recovery** (up to 7 days on basic, longer on higher tiers)
- **Manual backups** via `pg_dump` if needed

### Code Backups

- Git repository is the source of truth
- Use feature branches for testing
- Tag production releases

---

## Deployment Process

### Pre-Deployment Checklist

- [ ] Local tests pass (`uv run pytest tests/unit/ -v`)
- [ ] `.env` updated with production credentials
- [ ] Database migrations tested
- [ ] Git commit pushed
- [ ] Frontend `.env` updated with new API URLs

### Deployment Steps

```bash
# 1. Deploy backend functions
cd backend
./scripts/deploy.sh

# 2. Deploy frontend (via App Platform dashboard or CLI)
cd ../frontend
bun run build
# Deploy .output/public to hosting provider
```

### Post-Deployment Testing

```bash
# Test health endpoint
curl https://faas.syd1.digitaloceanspaces.com/<namespace>/api/games/health

# Test games endpoint
curl https://faas.syd1.digitaloceanspaces.com/<namespace>/api/games

# Test frontend
curl -I https://whatismytip.com
```

### Rollback Procedure

For functions, redeploy the previous version:

```bash
cd backend
git checkout <previous-commit>
doctl serverless deploy . --env .env
```

For database rollbacks:

```bash
uv run alembic downgrade -1
```

---

## Troubleshooting

### Function Issues

**Problem**: Function returns 502/503
- **Solution**: Check activation logs via `doctl serverless activations list`
- **Solution**: Verify `DATABASE_URL` and `REDIS_URL` are correct and reachable

**Problem**: Function times out
- **Solution**: Check timeout settings in [`functions.yml`](../backend/functions.yml:1)
- **Solution**: Optimise slow database queries

**Problem**: Cron job not running
- **Solution**: Verify schedule in [`functions.yml`](../backend/functions.yml:1)
- **Solution**: Check for stale advisory locks in `job_locks` table
- **Solution**: Review activation logs

### Database Issues

**Problem**: Connection refused
- **Solution**: Verify Trusted Sources includes Functions namespace
- **Solution**: Check `?ssl=require` in connection string

**Problem**: Migration failures
- **Solution**: Check current revision: `uv run alembic current`
- **Solution**: Review migration scripts in `alembic/versions/`

### Frontend Issues

**Problem**: API calls failing
- **Solution**: Verify `API_BASE_URL` points to correct Functions gateway
- **Solution**: Check CORS configuration in backend `.env`

---

## Cost Estimation

| Component | Estimated Monthly Cost |
|-----------|----------------------|
| DO Functions (8 functions, low traffic) | $1-5 (pay-per-invocation) |
| Managed PostgreSQL (1 GB plan) | $15 |
| Managed Redis (1 GB plan) | $15 |
| App Platform (frontend static site) | $3 |
| OpenRouter API | $5-20 |
| **Total** | **~$39-58/month** |

Costs scale with usage. Functions billing is based on invocations and execution time.

---

## CI/CD

The repository includes a GitHub Actions workflow at [`.github/workflows/deploy.yml`](../.github/workflows/deploy.yml:1) that automates deployment on push to `main`.

Required GitHub secrets:

| Secret | Description |
|--------|-------------|
| `DIGITALOCEAN_ACCESS_TOKEN` | DO API token for `doctl` |
| `DATABASE_URL` | Production PostgreSQL connection string |
| `REDIS_URL` | Production Redis connection string |
| `OPENROUTER_API_KEY` | OpenRouter API key |
| `ADMIN_API_KEY` | Admin API key |
| Other env vars | As needed (see `.env.example`) |

---

## Support and Resources

- [DigitalOcean Functions Docs](https://docs.digitalocean.com/products/functions/)
- [DigitalOcean Managed Databases](https://docs.digitalocean.com/products/databases/)
- [Nuxt Deployment Guide](https://nuxt.com/docs/getting-started/deployment)
- [doctl CLI Reference](https://docs.digitalocean.com/reference/doctl/)

See also:
- [docs/backend.md](backend.md) — Backend architecture
- [docs/development.md](development.md) — Local development setup
- [docs/migrations.md](migrations.md) — Database migration guide
