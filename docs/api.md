# WhatIsMyTip API Documentation

## Overview

WhatIsMyTip provides a RESTful API for AFL tipping predictions, backtesting, and game data. The API is built with FastAPI and includes ML models, heuristic layers, and AI-powered explanations.

## Base URL

```
https://whatismytip.com/api
```

For local development:
```
http://localhost:8000/api
```

## Authentication

The API is currently public with rate limiting. No authentication is required for basic operations.

## Rate Limiting

- **General Endpoints**: 60 requests per minute per IP address
- **Generate Tips**: 10 requests per minute per IP address
- **Generate Explanations**: 5 requests per minute per IP address
- **Run Backtest**: 5 requests per minute per IP address
- **Compare Heuristics**: 30 requests per minute per IP address

Rate limits are enforced using the `slowapi` library.

## Error Codes

| Code | Description |
|------|-------------|
| 200 | Success |
| 400 | Bad Request (invalid parameters) |
| 404 | Not Found (resource doesn't exist) |
| 429 | Rate Limit Exceeded |
| 500 | Internal Server Error |

## Common Response Format

All successful responses follow this format:

```json
{
  "message": "Operation completed successfully",
  "data": { ... }
}
```

Error responses:

```json
{
  "detail": "Error message"
}
```

## Endpoints

### Health Check

```
GET /health
```

Check the health status of the API.

**Response**:
```json
{
  "status": "healthy"
}
```

### Games

```
GET /games
GET /games/{game_id}
```

Get games data from the Squiggle API.

**Query Parameters**:
- `season` (optional): Filter by season year
- `round` (optional): Filter by round number

**Response**:
```json
{
  "games": [
    {
      "id": 123,
      "season": 2025,
      "round_id": 1,
      "home_team": "Richmond",
      "away_team": "Carlton",
      "venue": "MCG",
      "date": "2025-03-21T18:20:00Z"
    }
  ],
  "count": 1
}
```

**Example**:
```bash
# Get all games
curl http://localhost:8000/api/games

# Get games for specific season
curl http://localhost:8000/api/games?season=2025

# Get games for specific round
curl http://localhost:8000/api/games?season=2025&round=1
```

### Tips

#### Get Tips

```
GET /tips
GET /tips/{heuristic}
```

Retrieve tips with optional filtering.

**Query Parameters**:
- `heuristic` (optional): Filter by heuristic type (best_bet, yolo, high_risk_high_reward)
- `season` (optional): Filter by season year
- `round` (optional): Filter by round number
- `limit` (optional): Maximum number of results (default: 100, max: 500)

**Response**:
```json
{
  "tips": [
    {
      "id": 1,
      "game_id": 123,
      "season": 2025,
      "round_id": 1,
      "home_team": "Richmond",
      "away_team": "Carlton",
      "heuristic": "best_bet",
      "selected_team": "Richmond",
      "confidence": 0.65,
      "margin": 15,
      "explanation": "Based on Elo ratings and recent form..."
    }
  ],
  "count": 1
}
```

**Example**:
```bash
# Get recent tips
curl http://localhost:8000/api/tips

# Get tips by heuristic
curl http://localhost:8000/api/tips/best_bet

# Get tips with limit
curl http://localhost:8000/api/tips?limit=50

# Get tips for specific round
curl http://localhost:8000/api/tips?season=2025&round=1
```

#### Generate Tips

```
POST /tips/generate
```

Generate tips for a round using ML models and heuristics.

**Query Parameters**:
- `season` (required): Season year
- `round` (required): Round number
- `heuristics` (optional): Comma-separated list of heuristics (default: all)
- `generate_explanations` (optional): Generate AI explanations (default: true)

**Response**:
```json
{
  "message": "Generated 10 tips for round 1, 2025",
  "heuristics_used": ["best_bet", "yolo"],
  "tips_count": 10,
  "explanations_generating": true
}
```

**Example**:
```bash
# Generate tips for all heuristics
curl -X POST "http://localhost:8000/api/tips/generate?season=2025&round=1"

# Generate tips for specific heuristics
curl -X POST "http://localhost:8000/api/tips/generate?season=2025&round=1&heuristics=best_bet,yolo"

# Generate tips without explanations
curl -X POST "http://localhost:8000/api/tips/generate?season=2025&round=1&generate_explanations=false"
```

#### Generate Explanations

```
POST /tips/explanations/generate
```

Generate AI explanations for tips in a round.

**Query Parameters**:
- `season` (required): Season year
- `round` (required): Round number

**Response**:
```json
{
  "message": "Generated 10 explanations for round 1, 2025",
  "count": 10
}
```

**Example**:
```bash
curl -X POST "http://localhost:8000/api/tips/explanations/generate?season=2025&round=1"
```

### Backtesting

#### Get Backtest Results

```
GET /backtest
GET /backtest/{heuristic}
```

Retrieve backtest results with optional filtering.

**Query Parameters**:
- `heuristic` (optional): Filter by heuristic type
- `season` (optional): Filter by season year
- `limit` (optional): Maximum number of results (default: 50, max: 200)

**Response**:
```json
{
  "results": [
    {
      "id": 1,
      "season": 2024,
      "round_id": 5,
      "heuristic": "best_bet",
      "predicted_team": "Richmond",
      "actual_team": "Richmond",
      "correct": true,
      "margin_difference": 5,
      "confidence": 0.65,
      "predicted_margin": 15
    }
  ],
  "count": 1
}
```

**Example**:
```bash
# Get latest backtest results
curl http://localhost:8000/api/backtest

# Get backtest by heuristic
curl http://localhost:8000/api/backtest/best_bet

# Get backtest for specific season
curl http://localhost:8000/api/backtest?season=2024

# Get backtest with limit
curl http://localhost:8000/api/backtest?limit=100
```

#### Run Backtest

```
POST /backtest/run
```

Run backtest for specified parameters.

**Query Parameters**:
- `season` (required): Season year to backtest
- `round` (optional): Round to backtest (if None, entire season)
- `heuristic` (optional): Heuristic to backtest (if None, all)

**Response**:
```json
{
  "message": "Backtest completed for best_bet",
  "heuristic": "best_bet",
  "season": 2024,
  "round": null,
  "results_count": 20,
  "summary": {
    "total_bets": 20,
    "correct": 12,
    "incorrect": 8,
    "accuracy": 0.6,
    "total_profit": 500.0,
    "total_stake": 1000.0
  }
}
```

**Example**:
```bash
# Backtest entire season for all heuristics
curl -X POST "http://localhost:8000/api/backtest/run?season=2024"

# Backtest specific round
curl -X POST "http://localhost:8000/api/backtest/run?season=2024&round=5"

# Backtest specific heuristic
curl -X POST "http://localhost:8000/api/backtest/run?season=2024&heuristic=best_bet"

# Backtest specific heuristic for specific round
curl -X POST "http://localhost:8000/api/backtest/run?season=2024&round=5&heuristic=best_bet"
```

#### Compare Heuristics

```
GET /backtest/compare
```

Compare all heuristics for a season.

**Query Parameters**:
- `season` (required): Season year to compare

**Response**:
```json
{
  "season": 2024,
  "comparison": {
    "best_bet": {
      "overall_accuracy": 0.62,
      "total_profit": 1250.00,
      "total_bets": 100,
      "win_rate": 0.62,
      "total_stake": 1000.0,
      "profit_per_bet": 12.5
    },
    "yolo": {
      "overall_accuracy": 0.55,
      "total_profit": 800.00,
      "total_bets": 100,
      "win_rate": 0.55,
      "total_stake": 1000.0,
      "profit_per_bet": 8.0
    },
    "high_risk_high_reward": {
      "overall_accuracy": 0.58,
      "total_profit": 1000.00,
      "total_bets": 100,
      "win_rate": 0.58,
      "total_stake": 1000.0,
      "profit_per_bet": 10.0
    }
  },
  "best_overall": {
    "heuristic": "best_bet",
    "accuracy": 0.62,
    "profit": 1250.00
  }
}
```

**Example**:
```bash
curl "http://localhost:8000/api/backtest/compare?season=2024"
```

## Heuristics

The API supports three heuristic strategies:

### 1. Best Bet

**Strategy**: Conservative, high-confidence picks

**Characteristics**:
- Requires consensus across multiple models
- Only selects teams with high confidence (>60%)
- Uses average of model predictions

**Best for**: Long-term betting strategies

### 2. YOLO

**Strategy**: High-risk, high-reward selections

**Characteristics**:
- Selects the team with the highest confidence
- No consensus requirement
- Ignores confidence thresholds

**Best for**: Adventurous bettors looking for big wins

### 3. High Risk High Reward

**Strategy**: Balanced approach for adventurous tippers

**Characteristics**:
- Selects teams with moderate confidence (40-70%)
- Requires some model consensus
- Balances risk and reward

**Best for**: Moderate-risk betting strategies

## ML Models

The API uses four ML models for predictions:

### 1. Elo Model

Tracks team strength over time using the Elo rating system.

**Features**:
- Historical rating tracking
- Margin-based point adjustments
- Configurable home advantage factor

### 2. Form Model

Predicts based on recent team performance.

**Features**:
- Recent performance weighting
- Simple and interpretable
- Good for short-term predictions

### 3. Home Advantage Model

Accounts for the advantage of playing at home.

**Features**:
- Configurable home advantage factor
- Simple adjustment to Elo ratings
- Considers venue-specific advantages

### 4. Value Model

Identifies value bets based on odds.

**Features**:
- Expected value calculation
- Odds integration
- Value-focused predictions

## AI Explanations

The API can generate AI-powered explanations for tips using OpenRouter with the `gptoss-120b` model.

### How It Works

1. Gather model predictions and game data
2. Send context to OpenRouter API
3. Generate explanation based on:
   - Model predictions
   - Game context
   - Heuristic strategy
   - Historical performance

### Response Format

```json
{
  "explanation": "Based on Elo ratings, Richmond has a significant advantage over Carlton. Richmond's current Elo rating of 1580 is 30 points higher than Carlton's 1550. Additionally, Richmond has won their last 3 games, while Carlton has lost 2 of their last 3. The home advantage further tips the scales in Richmond's favor."
}
```

### Cost

- Model: `gptoss-120b`
- Cost per 1M tokens: ~$0.15
- Estimated cost: $5-20/month depending on usage

## Data Models

### Game

```json
{
  "id": 123,
  "season": 2025,
  "round_id": 1,
  "home_team": "Richmond",
  "away_team": "Carlton",
  "venue": "MCG",
  "date": "2025-03-21T18:20:00Z"
}
```

### Tip

```json
{
  "id": 1,
  "game_id": 123,
  "season": 2025,
  "round_id": 1,
  "home_team": "Richmond",
  "away_team": "Carlton",
  "heuristic": "best_bet",
  "selected_team": "Richmond",
  "confidence": 0.65,
  "margin": 15,
  "explanation": "Based on Elo ratings and recent form..."
}
```

### Backtest Result

```json
{
  "id": 1,
  "season": 2024,
  "round_id": 5,
  "heuristic": "best_bet",
  "predicted_team": "Richmond",
  "actual_team": "Richmond",
  "correct": true,
  "margin_difference": 5,
  "confidence": 0.65,
  "predicted_margin": 15
}
```

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
curl -X POST "http://localhost:8000/api/tips/generate?season=2025&round=1&heuristics=best_bet,yolo"

# Run backtest
curl -X POST "http://localhost:8000/api/backtest/run?season=2024&heuristic=best_bet"

# Compare heuristics
curl "http://localhost:8000/api/backtest/compare?season=2024"
```

## Squiggle API

The backend integrates with the [Squiggle API](https://api.squiggle.com.au/) for AFL data.

### Rate Limits

Squiggle API has rate limits. The backend implements its own rate limiting:
- **60 requests per minute** per IP address

### Data Sources

The Squiggle API provides:
- Fixtures and results
- Team information
- Historical data
- Player statistics

## OpenRouter API

The backend uses OpenRouter with the `gptoss-120b` model for AI-powered explanations.

### Configuration

- **Model**: `gptoss-120b`
- **Base URL**: `https://openrouter.ai/api/v1`
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

**Issue**: 429 Rate Limit Exceeded
- **Solution**: Wait and retry, or implement caching

**Issue**: 500 Internal Server Error
- **Solution**: Check backend logs, report to maintainers

**Issue**: Empty results
- **Solution**: Check season/round parameters, verify data exists

## Support

For API support:
1. Check this documentation
2. Review API documentation at `/docs`
3. Open an issue on GitHub
4. Contact the development team

## Versioning

The API is currently in development. Versioning may be implemented in the future.

## License

See the [LICENSE](../LICENSE) file for details.
