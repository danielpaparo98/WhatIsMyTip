# DigitalOcean Setup Guide

This guide walks you through setting up WhatIsMyTip.com on DigitalOcean using serverless Functions, managed PostgreSQL, and managed Redis.

## Prerequisites

- DigitalOcean account
- GitHub account
- `doctl` CLI tool installed and authenticated
- `uv` (Python package manager)
- `bun` (JavaScript runtime)
- Docker (for running database migrations)
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
- In database settings, allow connections from your Functions namespace

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

## Step 5: Create a Functions Namespace

```bash
# Install the Functions support in doctl
doctl serverless install --hosting-region syd1

# Connect to/create your namespace
doctl serverless connect

# Verify
doctl serverless status
```

**Note the gateway URL and namespace** — all function URLs follow the pattern:
```
https://faas.syd1.digitaloceanspaces.com/<namespace>/api/<function>
```

## Step 6: Configure Environment

```bash
cd backend
cp .env.example .env
```

Edit `.env` with your managed database and Redis credentials:

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
```

## Step 7: Run Database Migrations

Run migrations against the managed PostgreSQL database **before** deploying functions:

```bash
cd backend
export DATABASE_URL="postgresql+asyncpg://doadmin:<password>@db-postgresql-xxx.db.ondigitalocean.com:25060/defaultdb?ssl=require"
uv run alembic upgrade head

# Verify
uv run alembic current
```

See [docs/migrations.md](migrations.md) for the full migration workflow.

## Step 8: Deploy Functions

### Using the Deploy Script

```bash
cd backend
./scripts/deploy.sh
```

This runs tests, migrations, deploys all functions, and verifies health.

### Manual Deployment

```bash
cd backend
doctl serverless deploy . --env .env
```

### Verify Deployment

```bash
# List deployed functions
doctl serverless functions list

# Test health endpoint
curl https://faas.syd1.digitaloceanspaces.com/<namespace>/api/games/health
```

## Step 9: Deploy Frontend (App Platform)

1. Go to [DigitalOcean App Platform](https://cloud.digitalocean.com/apps)
2. Click **Create App** → **Deploy from Git**
3. Connect your repository
4. Configure:
   - Source directory: `frontend/`
   - Build command: `bun install && bun run build`
   - Output directory: `.output/public`
5. Add environment variable:
   - `API_BASE_URL=https://faas.syd1.digitaloceanspaces.com/<namespace>`
6. Deploy

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

## Step 11: Initial Data Sync

After deployment, trigger the data sync functions:

```bash
# Trigger daily sync (fetches games from Squiggle API)
curl -X POST -H "X-Admin-API-Key: $ADMIN_API_KEY" \
  https://faas.syd1.digitaloceanspaces.com/<namespace>/api/admin/jobs/daily-sync/trigger

# Trigger tip generation
curl -X POST -H "X-Admin-API-Key: $ADMIN_API_KEY" \
  https://faas.syd1.digitaloceanspaces.com/<namespace>/api/admin/jobs/tip-generation/trigger
```

## Scheduled Functions

Scheduled functions (cron jobs) are configured in [`project.yml`](../backend/project.yml:1) and deployed automatically. No separate setup is needed.

| Function | Schedule (UTC) | Description |
|----------|----------------|-------------|
| `daily-sync` | Every 15 min | Sync games from Squiggle API |
| `match-completion` | 4× per hour | Detect completed matches |
| `tip-generation` | 3 AM AWST daily | Generate tips for upcoming round |
| `historic-refresh` | 4 AM AWST Saturday | Refresh historical data |

## Cost Breakdown

| Component | Plan | Monthly Cost |
|-----------|------|-------------|
| DO Functions (8 functions) | Pay-per-invocation | $1-5 |
| Managed PostgreSQL | 1 GB / 1 vCPU | $15 |
| Managed Redis | 1 GB | $15 |
| App Platform (frontend) | Static site | $3 |
| OpenRouter API | Per-usage | $5-20 |
| **Total** | | **~$39-58/month** |

## Monitoring

### Functions

```bash
# View activation logs
doctl serverless activations list
doctl serverless activations get <activation_id>

# Check job status via admin API
curl -H "X-Admin-API-Key: $ADMIN_API_KEY" \
  https://faas.syd1.digitaloceanspaces.com/<namespace>/api/admin/jobs/status
```

### Databases

Use the DigitalOcean database dashboards for:
- Connection metrics
- Query performance
- Storage and backup status

## Troubleshooting

### Functions not responding
- Check activation logs: `doctl serverless activations list`
- Verify `DATABASE_URL` and `REDIS_URL` are correct
- Ensure trusted sources allow Functions connections

### Database connection errors
- Verify `?ssl=require` in `DATABASE_URL`
- Check Trusted Sources in database settings
- Confirm port (25060 for managed PostgreSQL)

### Cron jobs not running
- Verify schedules in [`project.yml`](../backend/project.yml:1)
- Check for stale advisory locks in `job_locks` table
- Review activation logs

### Frontend can't reach API
- Verify `API_BASE_URL` points to the Functions gateway
- Check CORS configuration
- Ensure function is deployed (`doctl serverless functions list`)

## Scaling

- **Functions**: Automatically scale per-invocation (serverless)
- **PostgreSQL**: Upgrade plan for more connections/storage
- **Redis**: Upgrade plan for more memory
- **Frontend**: Static site, scales automatically

## See Also

- [Deployment Guide](deployment.md) — Comprehensive deployment instructions
- [Backend Architecture](backend.md) — FaaS architecture overview
- [Development Guide](development.md) — Local development setup
