# WhatIsMyTip API Documentation

> **Path reference verified against `app.routes`** (see [`backend/list_routes.py`](../backend/list_routes.py:1) for the exact route list).
> **Interactive API**: `GET /docs` (Swagger UI) and `GET /redoc` (ReDoc) on the live app.

## Overview

WhatIsMyTip provides a RESTful API for AFL tipping predictions, backtesting, and game data. The API is served by a single FastAPI application (Phase 4) — 4 HTTP routers mounted at `/api/...` plus a `/health` liveness probe, backed by managed PostgreSQL and Redis.  An in-process APScheduler runs the 4 cron jobs in the same process.

## API Access

The API is served by the FastAPI app behind the nginx reverse proxy:

```
https://<your-domain>/api/...
```

### Local development

```
http://localhost:8000/api/...
```

### Interactive docs

- `GET /openapi.json` — the raw OpenAPI 3 spec
- `GET /docs` — Swagger UI (interactive)
- `GET /redoc` — ReDoc (read-only)

## Authentication

The API is currently public with rate limiting. Admin endpoints require an `X-API-Key` header that matches `ADMIN_API_KEY` (env var). No authentication is required for basic operations.

## Rate Limiting

- **General Endpoints**: 60 requests per minute per IP address
- **Tip generation / backtest runs**: 5-10 requests per minute per IP address (admin-only)

Rate limits are enforced by FastAPI middleware (see [`app/core/rate_limit.py`](../backend/app/core/rate_limit.py:1)).

## Error Codes

| Code | Description |
|------|-------------|
| 200 | Success |
| 400 | Bad Request (invalid parameters) |
| 401 | Unauthorized (missing or invalid `X-API-Key` for admin endpoints) |
| 404 | Not Found (resource doesn't exist) |
| 422 | Unprocessable Entity (validation error) |
| 429 | Rate Limit Exceeded |
| 500 | Internal Server Error |

## Common Response Format

Successful responses are JSON; the exact shape depends on the endpoint.  Error responses use a consistent envelope:

```json
{
  "code": "internal_error",
  "message": "An internal error occurred",
  "request_id": "..."
}
```

Validation errors (422) include the structured `errors` array from Pydantic.

---

## Endpoints

### Health Check

```
GET /health
```

Check the liveness of the API.  Returns 200 in all cases (degraded/healthy) — the body's `status` field signals overall health so a load balancer can route traffic without taking the pod out of rotation for transient dependency hiccups.

**Response**:
```json
{
  "status": "healthy",
  "db": "ok",
  "redis": "ok",
  "version": "0.1.0",
  "request_id": "..."
}
```

### Games

#### List games

```
GET /api/games
```

Get games data.  Query parameters: `season`, `round`, `upcoming`, `latest`, `team`, `limit`, `offset`.

**Example**:
```bash
# All games
curl http://localhost:8000/api/games

# Specific season + round
curl 'http://localhost:8000/api/games?season=2025&round=1'

# Upcoming only
curl 'http://localhost:8000/api/games?upcoming=true'

# Latest round
curl 'http://localhost:8000/api/games?latest=true'
```

#### Get game by slug

```
GET /api/games/{slug}
```

Get a single game by its slug (e.g. `richmond-v-carlton-r1-2025`).

#### Get game detail

```
GET /api/games/{slug}/detail
```

Full game detail with tips, predictions, weather, and analysis.

### Tips

#### List tips

```
GET /api/tips
```

Get tips.  Query parameters: `heuristic`, `season`, `round`, `limit`.

#### Games with tips

```
GET /api/tips/games-with-tips
```

Get games with their best-bet tips for a round.  Query parameters: `season`, `round`, `heuristic` (default: `best_bet`).

#### Tips by heuristic

```
GET /api/tips/{heuristic}
```

Get tips for one heuristic (`best_bet`, `yolo`, or `high_risk_high_reward`).  Query parameters: `limit`.

#### Generate tips

```
POST /api/tips/generate
```

Generate tips for a round.  **Requires `X-API-Key` header.**  Query parameters: `season`, `round`, `heuristics` (optional, comma-separated), `regenerate` (default: `false`).

**Example**:
```bash
curl -X POST -H "X-API-Key: $ADMIN_API_KEY" \
  'http://localhost:8000/api/tips/generate?season=2025&round=1'

curl -X POST -H "X-API-Key: $ADMIN_API_KEY" \
  'http://localhost:8000/api/tips/generate?season=2025&round=1&heuristics=best_bet,yolo&regenerate=true'
```

### Backtesting

#### List backtest results

```
GET /api/backtest
```

List backtest results.  Query parameters: `heuristic`, `season`, `limit`.

#### Compare heuristics for a season

```
GET /api/backtest/compare
```

Compare all heuristics for a season.  Query parameter: `season` (required).

#### Model compare

```
GET /api/backtest/model-compare
```

Compare individual ML models (not heuristics).  Query parameters: `season`, `models` (optional, comma-separated).

#### Per-round table data

```
GET /api/backtest/table
```

Per-round table data for a season.  Query parameter: `season` (required).

#### List seasons with backtest data

```
GET /api/backtest/seasons
```

List all seasons that have backtest data.

#### Current-season performance

```
GET /api/backtest/current-season
```

Current-season performance across all heuristics.

#### Run a backtest

```
POST /api/backtest/run
```

Trigger a backtest.  **Requires `X-API-Key` header.**  Query parameters: `season` (required), `round` (optional), `heuristic` (optional).

**Example**:
```bash
# Entire season
curl -X POST -H "X-API-Key: $ADMIN_API_KEY" \
  'http://localhost:8000/api/backtest/run?season=2024'

# Single round
curl -X POST -H "X-API-Key: $ADMIN_API_KEY" \
  'http://localhost:8000/api/backtest/run?season=2024&round=5'
```

### Admin

All admin endpoints **require `X-API-Key` header**.

#### Trigger a cron job

```
POST /api/admin/{job_name}/trigger
```

Manually trigger one of the 4 cron jobs.

**Job names**:
- `daily-sync`
- `match-completion`
- `tip-generation`
- `historic-refresh`

**Example**:
```bash
curl -X POST -H "X-API-Key: $ADMIN_API_KEY" \
  http://localhost:8000/api/admin/daily-sync/trigger

curl -X POST -H "X-API-Key: $ADMIN_API_KEY" \
  http://localhost:8000/api/admin/tip-generation/trigger
```

#### Historic-refresh progress

```
GET /api/admin/historic-refresh/progress
```

Get the current progress of the historic-refresh job (current season, current round, items processed, ETA, etc.).

#### Per-job execution metrics

```
GET /api/admin/metrics
```

Get per-job execution metrics: success rate, last-run timestamp, average duration, etc.

---

## Heuristics

The API supports three heuristic strategies:

### 1. Best Bet

**Strategy**: Conservative, high-confidence picks

- Requires consensus across multiple models
- Only selects teams with high confidence (>60%)
- Uses average of model predictions

**Best for**: Long-term betting strategies

### 2. YOLO

**Strategy**: High-risk, high-reward selections

- Selects the team with the highest confidence
- No consensus requirement
- Ignores confidence thresholds

**Best for**: Adventurous bettors looking for big wins

### 3. High Risk High Reward

**Strategy**: Balanced approach for adventurous tippers

- Selects teams with moderate confidence (40-70%)
- Requires some model consensus
- Balances risk and reward

**Best for**: Moderate-risk betting strategies

## ML Models

The API uses 8 ML models for predictions:

| Model | Description |
|-------|-------------|
| **Elo** | Team strength tracking via Elo rating system |
| **Form** | Recent team performance (last N games) |
| **Home Advantage** | Venue-specific advantages |
| **Value** | Value-based betting analysis |
| **Weather Impact** | Weather conditions impact on game outcomes |
| **Injury Impact** | Team injury lists and player availability |
| **Matchup** | Head-to-head historical performance |
| **Player Form** | Individual player form metrics |

## AI Explanations

The API can generate AI-powered explanations for tips using OpenRouter.  Explanations are generated on-demand by the tip-generation cron job and cached in PostgreSQL (one explanation per tip).

### Cost Management

- Cache explanations in database
- Only generate explanations when needed
- Monitor usage in OpenRouter dashboard

## Integration Examples

### Python

```python
import httpx

async def get_tips():
    async with httpx.AsyncClient() as client:
        response = await client.get(
            "http://localhost:8000/api/tips",
            params={"heuristic": "best_bet", "limit": 10}
        )
        return response.json()

# Run
import asyncio
tips = asyncio.run(get_tips())
print(tips)
```

### JavaScript/TypeScript

```typescript
async function getTips(): Promise<any> {
  const response = await fetch(
    'http://localhost:8000/api/tips?heuristic=best_bet&limit=10'
  );
  return response.json();
}

// Run
getTips().then(tips => console.log(tips));
```

### cURL

```bash
# Get tips
curl "http://localhost:8000/api/tips?heuristic=best_bet&limit=10"

# Generate tips
curl -X POST -H "X-API-Key: $ADMIN_API_KEY" \
  "http://localhost:8000/api/tips/generate?season=2025&round=1&heuristics=best_bet,yolo"

# Run backtest
curl -X POST -H "X-API-Key: $ADMIN_API_KEY" \
  "http://localhost:8000/api/backtest/run?season=2024&heuristic=best_bet"

# Compare heuristics
curl "http://localhost:8000/api/backtest/compare?season=2024"
```

## Squiggle API

The backend integrates with the [Squiggle API](https://api.squiggle.com.au/) for AFL data.

### Rate Limits

- **60 requests per minute** per IP address (enforced by FastAPI middleware)

### Data Sources

The Squiggle API provides:
- Fixtures and results
- Team information
- Historical data
- Player statistics

## OpenRouter API

The backend uses OpenRouter for AI-powered explanations (configurable model).

### Configuration

- **Model**: configurable via `OPENROUTER_MODEL` env var (e.g., `google/gemma-4-26b-a4b-it:free`)
- **Base URL**: `https://openrouter.ai/api/v1` (configurable via `OPENROUTER_BASE_URL`)
- **Rate Limit**: 5 requests per minute for explanations

### Cost Management

- Cache explanations in database
- Only generate explanations when needed
- Monitor usage in OpenRouter dashboard

## Best Practices

1. **Use Rate Limits**: Respect API rate limits to avoid blocking
2. **Cache Responses**: Cache frequently accessed data
3. **Error Handling**: Implement proper error handling in your application
4. **Pagination**: Use limit and offset for large result sets
5. **Timeouts**: Set appropriate timeouts for API calls
6. **Retry Logic**: Implement retry logic for transient failures

## Troubleshooting

### Common Issues

**Issue**: 404 Not Found
- **Solution**: Check endpoint URL and parameters

**Issue**: 401 Unauthorized
- **Solution**: Add the `X-API-Key` header for admin endpoints

**Issue**: 422 Unprocessable Entity
- **Solution**: Check the request body / query parameters

**Issue**: 429 Rate Limit Exceeded
- **Solution**: Wait and retry, or implement caching

**Issue**: 500 Internal Server Error
- **Solution**: Check backend logs, report to maintainers

**Issue**: Empty results
- **Solution**: Check season/round parameters, verify data exists

## Support

For API support:
1. Check this documentation
2. Check `/docs` (Swagger UI) for the live schema
3. Review [docs/backend.md](backend.md) for backend architecture details
4. Open an issue on GitHub
5. Contact the development team

## Versioning

The API is currently in development. Versioning may be implemented in the future.

## License

See the [LICENSE](../LICENSE) file for details.
