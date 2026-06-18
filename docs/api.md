# WhatIsMyTip API Documentation

> **Path reference verified against `app.routes`** (see [`backend/list_routes.py`](../backend/list_routes.py:1) for the exact route list).
> **Interactive API**: `GET /docs` (Swagger UI) and `GET /redoc` (ReDoc) on the live app.
> **Auth model**: see [`docs/security-model.md`](security-model.md).

## Overview

WhatIsMyTip provides a RESTful API for AFL tipping predictions, backtesting, and game data. The API is served by a single FastAPI application (Phase 4) — 4 HTTP routers mounted at `/api/...` plus a `/health` liveness probe, backed by managed PostgreSQL and Redis.  An in-process APScheduler runs the 4 cron jobs in the same process.

## API Access

| Environment | URL pattern |
|-------------|-------------|
| Production | `https://<your-domain>/api/...` (FastAPI service, same-origin via App Platform routing) |
| Local dev | `http://localhost:8000/api/...` |
| OpenAPI 3 spec | `GET /openapi.json` |
| Swagger UI | `GET /docs` |
| ReDoc | `GET /redoc` |

## Authentication

The API is **public by default** with per-IP rate limiting. Admin endpoints (manual cron triggers, backtest runs, metrics) require an `X-API-Key` header that matches `ADMIN_API_KEY` (env var).  Full auth model in [`docs/security-model.md`](security-model.md).

## Rate Limiting

| Scope | Default |
|-------|---------|
| General endpoints | 60 req/min per IP |
| `POST /api/tips/generate` | 10 req/min per IP (enforced by slowapi) |
| `POST /api/backtest/run` | 5 req/min per IP (admin only) |

Rate limits are enforced by FastAPI middleware (see [`app/core/rate_limit.py`](../backend/app/core/rate_limit.py:1)).

## Error Codes

| Code | Meaning |
|------|---------|
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

**Response shape**:

| Field | Type | Notes |
|-------|------|-------|
| `status` | string | `healthy` \| `degraded` |
| `db` | string | `ok` \| `error` |
| `redis` | string | `ok` \| `error` |
| `version` | string | App version |
| `request_id` | string | Correlates with `X-Request-ID` response header |

### Games

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `GET` | `/api/games` | public | List games (filter: `season`, `round`, `upcoming`, `latest`, `team`, `limit`, `offset`) |
| `GET` | `/api/games/{slug}` | public | Get a single game by slug (e.g. `richmond-v-carlton-r1-2025`) |
| `GET` | `/api/games/{slug}/detail` | public | Full game detail with tips, predictions, weather, and analysis |
| `GET` | `/api/games/health` | public | Games router health |

**Example**:

```bash
curl http://localhost:8000/api/games
curl 'http://localhost:8000/api/games?season=2025&round=1'
curl 'http://localhost:8000/api/games?upcoming=true'
curl 'http://localhost:8000/api/games?latest=true'
```

### Tips

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `GET` | `/api/tips` | public | List tips (filter: `heuristic`, `season`, `round`, `limit`) |
| `GET` | `/api/tips/games-with-tips` | public | Games with their best-bet tips for a round (filter: `season`, `round`, `heuristic` default `best_bet`) |
| `GET` | `/api/tips/{heuristic}` | public | Tips for one heuristic (`best_bet` \| `yolo` \| `high_risk_high_reward`); filter: `limit` |
| `POST` | `/api/tips/generate` | public (rate-limited) | Generate tips for a round. Body: `season`, `round_id`, `heuristics` (optional, comma-separated), `regenerate` (default `false`). See [`app/api/tips.py`](../backend/app/api/tips.py:261). |
| `POST` | `/api/tips/explanations/generate` | public | Generate AI explanations for a round |

**Example**:

```bash
# Generate for a round
curl -X POST 'http://localhost:8000/api/tips/generate?season=2025&round=1'
curl -X POST 'http://localhost:8000/api/tips/generate?season=2025&round=1&heuristics=best_bet,yolo&regenerate=true'
```

### Backtesting

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `GET` | `/api/backtest` | public | List backtest results (filter: `heuristic`, `season`, `limit`) |
| `GET` | `/api/backtest/seasons` | public | List seasons with backtest data |
| `GET` | `/api/backtest/current-season` | public | Current-season performance across all heuristics |
| `GET` | `/api/backtest/table` | public | Per-round table data (query: `season`) |
| `GET` | `/api/backtest/{heuristic}/performance` | public | Heuristic performance metrics |
| `GET` | `/api/backtest/compare` | public | Compare all heuristics for a season (query: `season`) |
| `GET` | `/api/backtest/model-compare` | public | Compare individual ML models (query: `season`, optional `models` list) |
| `POST` | `/api/backtest/run` | admin | Trigger a backtest. Query: `season` (required), `round` (optional), `heuristic` (optional) |

**Example**:

```bash
curl -X POST -H "X-API-Key: $ADMIN_API_KEY" 'http://localhost:8000/api/backtest/run?season=2024'
curl -X POST -H "X-API-Key: $ADMIN_API_KEY" 'http://localhost:8000/api/backtest/run?season=2024&round=5'
```

### Admin

All admin endpoints **require `X-API-Key` header**.

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/admin/{job_name}/trigger` | Manually trigger one of: `daily-sync`, `match-completion`, `tip-generation`, `historic-refresh` |
| `GET` | `/api/admin/historic-refresh/progress` | Current progress of the historic-refresh job (current season, current round, items processed, ETA, etc.) |
| `GET` | `/api/admin/metrics` | Per-job execution metrics: success rate, last-run timestamp, average duration, etc. |

**Example**:

```bash
curl -X POST -H "X-API-Key: $ADMIN_API_KEY" http://localhost:8000/api/admin/daily-sync/trigger
curl -X POST -H "X-API-Key: $ADMIN_API_KEY" http://localhost:8000/api/admin/tip-generation/trigger
```

---

## Heuristics

The API supports three heuristic strategies. Per-class implementation details (confidence thresholds, consensus rules) live in the source files.

| Heuristic | Source | Description |
|-----------|--------|-------------|
| **Best Bet** | [`heuristics/best_bet.py`](../backend/packages/shared/heuristics/best_bet.py:1) | Conservative picks with high confidence. Best for long-term betting. |
| **YOLO** | [`heuristics/yolo.py`](../backend/packages/shared/heuristics/yolo.py:1) | High-risk, high-reward selections. Ignores confidence thresholds. |
| **High Risk High Reward** | [`heuristics/high_risk_high_reward.py`](../backend/packages/shared/heuristics/high_risk_high_reward.py:1) | Balanced approach for adventurous tippers. |

## ML Models

The API uses 8 ML models for predictions. Per-class implementation lives in the source files.

| Model | Source |
|-------|--------|
| **Elo** | [`models_ml/elo.py`](../backend/packages/shared/models_ml/elo.py:1) |
| **Form** | [`models_ml/form.py`](../backend/packages/shared/models_ml/form.py:1) |
| **Home Advantage** | [`models_ml/home_advantage.py`](../backend/packages/shared/models_ml/home_advantage.py:1) |
| **Value** | [`models_ml/value.py`](../backend/packages/shared/models_ml/value.py:1) |
| **Weather Impact** | [`models_ml/weather_impact.py`](../backend/packages/shared/models_ml/weather_impact.py:1) |
| **Injury Impact** | [`models_ml/injury_impact.py`](../backend/packages/shared/models_ml/injury_impact.py:1) |
| **Matchup** | [`models_ml/matchup.py`](../backend/packages/shared/models_ml/matchup.py:1) |
| **Player Form** | [`models_ml/player_form.py`](../backend/packages/shared/models_ml/player_form.py:1) |

## AI Explanations

The API can generate AI-powered explanations for tips using OpenRouter.  Explanations are generated on-demand by the tip-generation cron job and cached in PostgreSQL (one explanation per tip).

Cost management: explanations are cached in DB and only generated when needed. Monitor usage in the OpenRouter dashboard. Model configurable via `OPENROUTER_MODEL` (e.g. `google/gemma-4-26b-a4b-it:free`).

## Integration Examples

### cURL

```bash
# Get tips
curl "http://localhost:8000/api/tips?heuristic=best_bet&limit=10"

# Generate tips
curl -X POST 'http://localhost:8000/api/tips/generate?season=2025&round=1&heuristics=best_bet,yolo'

# Run backtest
curl -X POST -H "X-API-Key: $ADMIN_API_KEY" \
  "http://localhost:8000/api/backtest/run?season=2024&heuristic=best_bet"

# Compare heuristics
curl "http://localhost:8000/api/backtest/compare?season=2024"
```

### Python (httpx)

```python
import httpx

async def get_tips():
    async with httpx.AsyncClient() as client:
        response = await client.get(
            "http://localhost:8000/api/tips",
            params={"heuristic": "best_bet", "limit": 10},
        )
        return response.json()
```

### JavaScript / TypeScript

```typescript
const tips = await fetch('http://localhost:8000/api/tips?heuristic=best_bet&limit=10')
  .then(r => r.json());
```

## Data Sources

| Source | Use | Config |
|--------|-----|--------|
| [Squiggle API](https://api.squiggle.com.au/) | AFL fixtures, results, team info | `SQUIGGLE_API_BASE`, `SQUIGGLE_CONTACT_EMAIL` |
| [OpenRouter](https://openrouter.ai/) | AI-powered tip explanations | `OPENROUTER_API_KEY`, `OPENROUTER_MODEL` |
| AFLTables / FootyWire | Historical player stats, injuries (via scraper) | n/a — internal |
| Open-Meteo | Match-day weather (via scraper) | n/a — internal |

## Best Practices

1. **Respect rate limits** to avoid 429s
2. **Cache responses** in your client for stable data (team info, historical results)
3. **Implement retry with backoff** for transient 5xx
4. **Use `limit` + `offset`** for large result sets
5. **Set appropriate timeouts** (5–10 s for read endpoints; 60+ s for `POST /tips/generate`)

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| 404 Not Found | Check endpoint URL and parameters |
| 401 Unauthorized | Add `X-API-Key: $ADMIN_API_KEY` for admin endpoints |
| 422 Unprocessable Entity | Check request body / query parameters (Pydantic `errors[]` array gives the field) |
| 429 Rate Limit Exceeded | Wait and retry, or implement client-side caching |
| 500 Internal Server Error | Check `backend/logs/` or contact maintainers; the response includes a `request_id` to correlate with logs |
| Empty results | Check `season` / `round` parameters; verify data exists |

## Support

1. Check this document
2. Check `/docs` (Swagger UI) for the live schema
3. See [`docs/backend.md`](backend.md) for backend architecture
4. See [`docs/security-model.md`](security-model.md) for the auth model
5. Open an issue on GitHub
