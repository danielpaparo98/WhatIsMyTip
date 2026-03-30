# Digital Ocean Setup Guide

This guide walks you through setting up WhatIsMyTip.com on Digital Ocean.

## Prerequisites

- Digital Ocean account
- GitHub account
- doctl CLI tool installed
- OpenRouter API key

## Step 1: Set Up Digital Ocean Account

1. Sign up at https://www.digitalocean.com
2. Add payment method
3. Generate API token:
   - Go to API > Tokens/Keys
   - Click "Generate New Token"
   - Save the token

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

## Step 3: Deploy Backend

### Option A: Using App Platform Spec

1. Update `.do/app.yaml` with your GitHub repo details
2. Deploy using doctl:
```bash
doctl apps create --spec .do/app.yaml
```

### Option B: Using Digital Ocean Dashboard

1. Go to Apps > Create App
2. Connect your GitHub repository
3. Configure:
   - Source: `backend/` directory
   - Build Command: `uv sync`
   - Run Command: `uv run uvicorn main:app --host 0.0.0.0 --port $PORT`
   - Instance Size: Basic XXS ($5/month)
4. Add environment variables (see `.env.production`)
5. Add PostgreSQL database (Dev Database: $7/month)
6. Deploy

## Step 4: Deploy Frontend

### Option A: Using App Platform Spec

1. Update `.do/frontend.yaml` with your GitHub repo details
2. Deploy using doctl:
```bash
doctl apps create --spec .do/frontend.yaml
```

### Option B: Using Digital Ocean Dashboard

1. Go to Apps > Create App
2. Connect your GitHub repository
3. Configure:
   - Source: `frontend/` directory
   - Build Command: `bun install && bun run generate`
   - Output Directory: `.output/public`
4. Add environment variable: `API_BASE_URL=https://api.whatismytip.com`
5. Deploy

## Step 5: Configure Domain

1. Go to Networking > Domains
2. Add domain: `whatismytip.com`
3. Add records:
   - A record: `@` → frontend app IP
   - CNAME: `api` → backend app URL
   - CNAME: `www` → frontend app URL

## Step 6: Set Up SSL

Digital Ocean App Platform includes free SSL certificates. They are automatically configured when you add a custom domain.

## Step 7: Initial Data Sync

After deployment, sync historical data:

```bash
# Initialize database
curl -X POST https://api.whatismytip.com/api/sync/database

# Sync last few years of data
curl -X POST "https://api.whatismytip.com/api/sync/games?year=2024"
curl -X POST "https://api.whatismytip.com/api/sync/games?year=2023"
curl -X POST "https://api.whatismytip.com/api/sync/games?year=2022"
```

## Step 8: Set Up Scheduled Tasks

Use Digital Ocean Functions or GitHub Actions to:
- Sync games weekly (Monday morning)
- Generate tips weekly (Monday morning)
- Run backtests weekly

Example GitHub Actions workflow in `.github/workflows/scheduled.yml`.

## Cost Breakdown

- Backend App (Basic XXS): $5/month
- Database (Dev Database): $7/month
- Frontend App (Static Site): Free
- **Total: ~$12/month**

## Monitoring

- Check App Platform dashboard for logs
- Set up uptime monitoring
- Monitor OpenRouter API costs

## Troubleshooting

### Backend won't start
- Check logs in App Platform dashboard
- Verify environment variables
- Ensure database connection is correct

### Frontend can't reach API
- Check CORS configuration
- Verify API_BASE_URL environment variable
- Check API is running and accessible

### Database connection errors
- Verify DATABASE_URL is correct
- Check database is running
- Ensure firewall allows connections

## Scaling

If needed, you can scale:
- Backend: Increase instance size or count
- Database: Upgrade to managed database
- Frontend: Already static, scales automatically
