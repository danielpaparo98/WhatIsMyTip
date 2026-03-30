# WhatIsMyTip Deployment Guide

## Overview

This guide covers deploying WhatIsMyTip.com to Digital Ocean. The deployment consists of:
- **Backend**: FastAPI application on Digital Ocean App Platform
- **Frontend**: Static Nuxt 4 site on Digital Ocean App Platform (static site hosting)

## Prerequisites

- Digital Ocean account
- Domain name (e.g., whatismytip.com)
- OpenRouter API key
- Squiggle API (free tier available)
- Git repository (GitHub/GitLab)

## Architecture

```
┌─────────────────┐
│   User Browser   │
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│  Digital Ocean  │
│  App Platform   │
│                 │
│  ┌───────────┐  │
│  │ Backend   │  │
│  │ FastAPI   │  │
│  │ SQLite DB │  │
│  └───────────┘  │
│                 │
│  ┌───────────┐  │
│  │ Frontend  │  │
│  │ Nuxt 4    │  │
│  │ Static    │  │
│  └───────────┘  │
└─────────────────┘
```

## Backend Deployment (Digital Ocean App Platform)

### Step 1: Create Digital Ocean App

1. Log in to [Digital Ocean App Platform](https://cloud.digitalocean.com/apps)
2. Click **Create App**
3. Select **Deploy from Git**
4. Connect your Git repository

### Step 2: Configure Build Settings

**Build Command**:
```bash
cd backend && uv sync
```

**Deploy Context**:
```bash
/home/dotnet/app
```

### Step 3: Configure Environment Variables

Add the following environment variables in the App Platform dashboard:

| Variable | Value |
|----------|-------|
| `DATABASE_URL` | `sqlite+aiosqlite:///./whatismytip.db` |
| `API_HOST` | `0.0.0.0` |
| `API_PORT` | `8000` |
| `CORS_ORIGINS` | `https://whatismytip.com` |
| `RATE_LIMIT_PER_MINUTE` | `60` |
| `SQUIGGLE_API_BASE` | `https://api.squiggle.com.au` |
| `OPENROUTER_API_KEY` | `your_openrouter_api_key` |
| `OPENROUTER_MODEL` | `gptoss-120b` |
| `OPENROUTER_BASE_URL` | `https://openrouter.ai/api/v1` |
| `ENVIRONMENT` | `production` |

### Step 4: Configure Scale

**Scale Settings**:
- **Minimum Scale**: 1 instance
- **Maximum Scale**: 2 instances (for high availability)
- **Scale Down**: 1 instance (saves costs during low traffic)

### Step 5: Deploy

1. Click **Deploy**
2. Wait for the build to complete (5-10 minutes)
3. Monitor the deployment status in the dashboard

### Step 6: Verify Backend

After deployment, verify the backend is running:

```bash
curl https://whatismytip.com/api/health
```

Expected response:
```json
{"status": "healthy"}
```

### Step 7: Access API Documentation

Visit `https://whatismytip.com/docs` for interactive API documentation.

## Frontend Deployment (Digital Ocean App Platform)

### Step 1: Create Separate App for Frontend

1. In Digital Ocean App Platform, create a new app
2. Select **Deploy from Git**
3. Use the same repository

### Step 2: Configure Build Settings

**Build Command**:
```bash
bun install && bun run build
```

**Deploy Context**:
```bash
/home/dotnet/app
```

**Output Directory**:
```bash
.output/public
```

### Step 3: Configure Environment Variables

Add the following environment variables:

| Variable | Value |
|----------|-------|
| `API_BASE_URL` | `https://whatismytip.com` |

### Step 4: Configure Scale

**Scale Settings**:
- **Minimum Scale**: 1 instance
- **Maximum Scale**: 1 instance (static site, no scaling needed)
- **Scale Down**: 1 instance

### Step 5: Deploy

1. Click **Deploy**
2. Wait for the build to complete (3-5 minutes)
3. Monitor the deployment status

### Step 6: Verify Frontend

Visit `https://whatismytip.com` to verify the frontend is accessible.

## Domain Configuration

### Step 1: Add Custom Domain

1. In the App Platform dashboard, go to **Settings** → **Domains**
2. Add your domain: `whatismytip.com`
3. Configure DNS records:

**A Record**:
- Type: `A`
- Name: `@`
- Value: Digital Ocean provides this automatically

**CNAME Record** (if using App Platform subdomain):
- Type: `CNAME`
- Name: `www`
- Value: `app-name.app_platform_domain`

### Step 2: SSL Certificate

Digital Ocean App Platform provides automatic SSL certificates for custom domains.

### Step 3: DNS Propagation

DNS changes may take 5-30 minutes to propagate.

## Database Setup

### SQLite (Recommended for Production)

The backend uses SQLite by default. For production:

1. **Enable Read-Only Replicas** (if needed):
   - Digital Ocean App Platform supports read replicas
   - Configure for read-heavy workloads

2. **Database Backups**:
   - Enable automatic backups in App Platform
   - Schedule daily backups
   - Keep backups for 30 days

3. **Performance**:
   - SQLite performs well for read-heavy workloads
   - For write-heavy workloads, consider PostgreSQL

### PostgreSQL Alternative

If you need PostgreSQL:

1. Create a Digital Ocean Managed Database
2. Update `DATABASE_URL`:
   ```bash
   DATABASE_URL=postgresql+asyncpg://user:password@host:port/database
   ```
3. Update `pyproject.toml` dependencies:
   ```bash
   uv add asyncpg
   ```

## Squiggle API Rate Limits

The Squiggle API has rate limits. The backend implements its own rate limiting:

- **60 requests per minute** per IP address

**Cost Optimization**:
- Cache Squiggle API responses in SQLite
- Implement background data synchronization
- Use API rate limiting to avoid exceeding limits

## OpenRouter API Setup

### Step 1: Get OpenRouter API Key

1. Sign up at [OpenRouter](https://openrouter.ai/)
2. Navigate to API Keys section
3. Generate a new API key

### Step 2: Configure in App Platform

Add the API key as an environment variable:
- `OPENROUTER_API_KEY`

### Step 3: Cost Management

**Estimated Costs**:
- Model: `gptoss-120b`
- Cost per 1M tokens: ~$0.15
- Estimated monthly cost: $5-20 (depending on usage)

**Cost Optimization Tips**:
1. **Cache Explanations**: Store generated explanations in database
2. **Rate Limit**: Only generate explanations when needed
3. **Batch Requests**: Generate explanations in batches
4. **Monitor Usage**: Set up alerts for unusual usage

### Step 4: Monitor Usage

Check OpenRouter dashboard for usage statistics and costs.

## Monitoring and Logging

### App Platform Monitoring

Digital Ocean App Platform provides:

- **Resource Usage**: CPU, memory, disk usage
- **Logs**: Access logs and application logs
- **Metrics**: Request count, response time, error rate

### Application Logs

View logs in the App Platform dashboard:
- **Application Logs**: Application output
- **Access Logs**: HTTP request logs

### Log Retention

Logs are retained for 7 days by default.

## Health Checks

### Backend Health Check

Configure health check in App Platform:
- **Path**: `/health`
- **Interval**: 30 seconds
- **Timeout**: 5 seconds
- **Unhealthy Threshold**: 3 consecutive failures

### Frontend Health Check

Configure health check in App Platform:
- **Path**: `/`
- **Interval**: 30 seconds
- **Timeout**: 5 seconds
- **Unhealthy Threshold**: 3 consecutive failures

## Scaling Strategy

### Auto Scaling

Enable auto scaling based on CPU usage:
- **Scale Up**: When CPU > 70% for 5 minutes
- **Scale Down**: When CPU < 30% for 10 minutes

### Best Practices

1. **Backend**: Scale to 2 instances for high availability
2. **Frontend**: Keep at 1 instance (static site)
3. **Database**: Use read replicas if needed

## Security Considerations

### Environment Variables

- Never commit `.env` files to Git
- Use App Platform environment variables for sensitive data
- Rotate API keys regularly

### CORS Configuration

Update `CORS_ORIGINS` to include all domains:
```bash
CORS_ORIGINS=https://whatismytip.com,https://www.whatismytip.com
```

### Rate Limiting

The backend implements rate limiting:
- 60 requests per minute per IP
- Adjust based on expected traffic

### HTTPS

Digital Ocean App Platform provides automatic HTTPS:
- SSL certificates are managed automatically
- Redirect HTTP to HTTPS

## Backup Strategy

### Database Backups

1. **Automatic Backups**: Enable daily backups in App Platform
2. **Retention**: Keep backups for 30 days
3. **Manual Backups**: Create manual backups before updates

### Code Backups

1. **Git Repository**: Push to GitHub/GitLab regularly
2. **Branches**: Use feature branches for testing
3. **Tag Releases**: Tag production releases

## Deployment Process

### Pre-Deployment Checklist

- [ ] Update environment variables
- [ ] Test in development
- [ ] Create Git commit
- [ ] Push to repository
- [ ] Create GitHub/GitLab release
- [ ] Review deployment logs

### Deployment Steps

1. **Push Changes**:
   ```bash
   git add .
   git commit -m "feat: update feature"
   git push origin feature/backend-squiggle-api-integration
   ```

2. **Create Release** (if using releases):
   ```bash
   git tag v0.1.0
   git push origin v0.1.0
   ```

3. **Monitor Deployment**:
   - Check App Platform dashboard
   - Review deployment logs
   - Verify health checks pass

4. **Post-Deployment Testing**:
   - Test API endpoints
   - Test frontend pages
   - Verify database operations
   - Check error logs

### Rollback Procedure

If deployment fails:

1. Go to App Platform dashboard
2. Select the app
3. Click **Rollback**
4. Choose previous version
5. Confirm rollback

## Troubleshooting

### Backend Issues

**Problem**: Backend not starting
- **Solution**: Check logs for errors, verify environment variables

**Problem**: Database errors
- **Solution**: Check database URL, verify SQLite file permissions

**Problem**: API calls failing
- **Solution**: Check Squiggle API status, verify rate limits

### Frontend Issues

**Problem**: Frontend not loading
- **Solution**: Check build logs, verify static files

**Problem**: API calls failing
- **Solution**: Verify `API_BASE_URL` environment variable

### Common Issues

**Issue**: 502 Bad Gateway
- **Solution**: Backend not running or unhealthy

**Issue**: 503 Service Unavailable
- **Solution**: Backend is scaling or restarting

**Issue**: Database locked
- **Solution**: SQLite file locked, check for concurrent access

## Performance Optimization

### Backend Optimization

1. **Database Indexing**: Add indexes to frequently queried columns
2. **Connection Pooling**: Configure SQLAlchemy connection pool
3. **Caching**: Implement Redis caching for API responses
4. **Background Tasks**: Use background workers for heavy operations

### Frontend Optimization

1. **Code Splitting**: Already enabled by Nuxt
2. **Image Optimization**: Use Nuxt's image optimization
3. **CDN**: Enable CDN for static assets
4. **Lazy Loading**: Implement lazy loading for images

## Cost Optimization

### Backend Costs

- **App Platform**: ~$6/month (1 instance)
- **Database**: Free (SQLite)
- **OpenRouter**: ~$5-20/month (depending on usage)

### Frontend Costs

- **App Platform**: ~$3/month (static site)
- **Total Estimated**: ~$14-29/month

### Cost Saving Tips

1. **Scale Down**: Reduce instances during low traffic
2. **Use SQLite**: Avoid PostgreSQL costs
3. **Monitor Usage**: Track OpenRouter API usage
4. **Cache Responses**: Reduce API calls

## Maintenance

### Regular Maintenance Tasks

1. **Daily**:
   - Monitor application health
   - Check error logs
   - Review usage metrics

2. **Weekly**:
   - Review database performance
   - Check backup status
   - Update dependencies

3. **Monthly**:
   - Rotate API keys
   - Review security settings
   - Optimize performance

### Dependency Updates

Update dependencies regularly:
```bash
cd backend
uv sync --upgrade

cd frontend
bun update
```

## Support and Resources

### Documentation

- [Digital Ocean App Platform Docs](https://docs.digitalocean.com/products/app-platform/)
- [FastAPI Deployment Guide](https://fastapi.tiangolo.com/deployment/)
- [Nuxt Deployment Guide](https://nuxt.com/docs/getting-started/deployment)

### Community

- [Digital Ocean Community](https://www.digitalocean.com/community)
- [FastAPI Discord](https://discord.gg/fastapi)
- [Nuxt Discord](https://discord.com/invite/nuxt)

## Next Steps

After deployment:

1. **Setup Monitoring**: Configure alerts and monitoring
2. **Setup Analytics**: Add Google Analytics or similar
3. **Setup Error Tracking**: Use Sentry or similar
4. **Create Documentation**: Document deployment process
5. **Set up CI/CD**: Automate deployments
