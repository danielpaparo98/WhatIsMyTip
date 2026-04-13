# WhatIsMyTip Backend Documentation

## Overview

The WhatIsMyTip backend is a FastAPI-based application that provides AI-powered AFL tipping predictions. It uses ML models, heuristic layers, and AI explanations to generate accurate footy tips. The system includes a comprehensive cron-based data collection infrastructure for automated data synchronization, match completion detection, tip generation, and historical data refresh.

## Project Structure

````
backend/
├── main.py                 # FastAPI application entry point
├── pyproject.toml          # Python project configuration and dependencies
├── uv.lock                 # Locked dependencies
├── .env.example            # Environment variables template
├── app/
│   ├── __init__.py
│   ├── config.py           # Application settings and configuration
│   ├── api/                # API route handlers
│   │   ├── __init__.py
│   │   ├── tips.py         # Tips generation and retrieval endpoints
│   │   ├── games.py        # Games data endpoints
│   │   ├── sync.py         # Data synchronization endpoints
│   │   ├── backtest.py     # Backtesting endpoints
│   │   └── admin/          # Admin endpoints
│   │       ├── __init__.py
│   │       └── jobs.py     # Cron job management endpoints
│   ├── crud/               # Database CRUD operations
│   │   ├── __init__.py
│   │   ├── tips.py         # Tip database operations
│   │   ├── games.py        # Game database operations
│   │   ├── jobs.py         # Job execution and lock operations
│   │   ├── elo_cache.py    # Elo cache operations
│   │   ├── generation_progress.py  # Generation progress tracking
│   │   └── backtest.py     # Backtest result database operations
│   ├── db/                 # Database session management
│   │   └── __init__.py
│   ├── models/             # Database models
│   │   └── __init__.py
│   ├── models_ml/          # ML prediction models
│   │   ├── __init__.py
│   │   ├── base.py         # Abstract base class for ML models
│   │   ├── elo.py          # Elo rating model
│   │   ├── form.py         # Team form model
│   │   ├── home_advantage.py # Home advantage model
│   │   └── value.py        # Value betting model
│   ├── heuristics/         # Heuristic layers
│   │   ├── __init__.py
│   │   ├── base.py         # Abstract base class for heuristics
│   │   ├── best_bet.py     # Conservative best bet heuristic
│   │   ├── yolo.py         # High-risk YOLO heuristic
│   │   └── high_risk_high_reward.py # Balanced high-risk heuristic
│   ├── openrouter/         # OpenRouter AI client
│   │   ├── __init__.py
│   │   └── client.py       # AI explanation generation
│   ├── schemas/            # Pydantic schemas for validation
│   │   ├── __init__.py
│   │   ├── tips.py         # Tip schemas
│   │   ├── games.py        # Game schemas
│   │   ├── cron.py         # Cron job schemas
│   │   └── backtest.py     # Backtest schemas
│   ├── services/           # Business logic services
│   │   ├── __init__.py
│   │   ├── explanation.py  # AI explanation service
│   │   ├── game_sync.py    # Game synchronization service
│   │   ├── tip_generation.py  # Tip generation service
│   │   ├── match_completion.py # Match completion detection service
│   │   └── historic_data_refresh.py  # Historical data refresh service
│   └── cron/               # Cron job infrastructure
│       ├── __init__.py
│       ├── base.py         # Base cron job class
│       └── jobs/           # Individual cron jobs
│           ├── __init__.py
│           ├── daily_sync.py
│           ├── match_completion.py
│           ├── tip_generation.py
│           └── historic_refresh.py
└── squiggle/               # Squiggle API client
    ├── __init__.py
    └── client.py           # Squiggle API integration
````

## Project Structure

```
backend/
├── main.py                 # FastAPI application entry point
├── pyproject.toml          # Python project configuration and dependencies
├── uv.lock                 # Locked dependencies
├── .env.example            # Environment variables template
├── app/
│   ├── __init__.py
│   ├── config.py           # Application settings and configuration
│   ├── api/                # API route handlers
│   │   ├── __init__.py
│   │   ├── tips.py         # Tips generation and retrieval endpoints
│   │   ├── games.py        # Games data endpoints
│   │   ├── sync.py         # Data synchronization endpoints
│   │   └── backtest.py     # Backtesting endpoints
│   ├── crud/               # Database CRUD operations
│   │   ├── __init__.py
│   │   ├── tips.py         # Tip database operations
│   │   ├── games.py        # Game database operations
│   │   └── backtest.py     # Backtest result database operations
│   ├── db/                 # Database session management
│   │   └── __init__.py
│   ├── models/             # Database models
│   │   └── __init__.py
│   ├── models_ml/          # ML prediction models
│   │   ├── __init__.py
│   │   ├── base.py         # Abstract base class for ML models
│   │   ├── elo.py          # Elo rating model
│   │   ├── form.py         # Team form model
│   │   ├── home_advantage.py # Home advantage model
│   │   └── value.py        # Value betting model
│   ├── heuristics/         # Heuristic layers
│   │   ├── __init__.py
│   │   ├── base.py         # Abstract base class for heuristics
│   │   ├── best_bet.py     # Conservative best bet heuristic
│   │   ├── yolo.py         # High-risk YOLO heuristic
│   │   └── high_risk_high_reward.py # Balanced high-risk heuristic
│   ├── openrouter/         # OpenRouter AI client
│   │   ├── __init__.py
│   │   └── client.py       # AI explanation generation
│   ├── schemas/            # Pydantic schemas for validation
│   │   ├── __init__.py
│   │   ├── tips.py         # Tip schemas
│   │   ├── games.py        # Game schemas
│   │   └── backtest.py     # Backtest schemas
│   └── services/           # Business logic services
│       ├── __init__.py
│       ├── explanation.py  # AI explanation service
│       └── backtest.py     # Backtesting service
└── squiggle/               # Squiggle API client
    ├── __init__.py
    └── client.py           # Squiggle API integration
```

## Dependencies

This project uses **uv** for Python dependency management. The dependencies are defined in [`pyproject.toml`](backend/pyproject.toml:1).

### Core Dependencies

- **fastapi** (>=0.115.0) - Modern web framework for building APIs
- **uvicorn[standard]** (>=0.32.0) - ASGI server for running FastAPI
- **httpx** (>=0.28.0) - Async HTTP client for API requests
- **sqlalchemy** (>=2.0.0) - Async ORM for database operations
- **aiosqlite** (>=0.20.0) - Async SQLite driver
- **pydantic** (>=2.10.0) - Data validation and settings management
- **pydantic-settings** (>=2.6.0) - Settings management
- **slowapi** (>=0.1.9) - Rate limiting middleware
- **openai** (>=1.57.0) - OpenAI-compatible API client (used with OpenRouter)
- **python-dotenv** (>=1.0.0) - Environment variable management
- **numpy** (>=2.0.0) - Numerical computing
- **scikit-learn** (>=1.5.0) - Machine learning utilities

### Development Dependencies

- **pytest** (>=8.3.0) - Testing framework
- **pytest-asyncio** (>=0.24.0) - Async test support
- **ruff** (>=0.8.0) - Fast Python linter and formatter
- **mypy** (>=1.13.0) - Static type checker

## Installation

### Prerequisites

- **uv** (Python package manager)
- **Python 3.11+**

### Install Dependencies

```bash
cd backend
uv sync
```

This command will install all dependencies from [`pyproject.toml`](backend/pyproject.toml:1) into a virtual environment.

## Configuration

### Environment Variables

Create a `.env` file based on the example in [`.env.example`](backend/.env.example:1):

```bash
cp .env.example .env
```

### Configuration Options

| Variable | Description | Default | Required |
|----------|-------------|---------|----------|
| `DATABASE_URL` | SQLite database connection string | `sqlite+aiosqlite:///./whatismytip.db` | No |
| `API_HOST` | API server host | `0.0.0.0` | No |
| `API_PORT` | API server port | `8000` | No |
| `CORS_ORIGINS` | Allowed origins for CORS | `http://localhost:3000` | No |
| `RATE_LIMIT_PER_MINUTE` | Rate limit per IP (requests) | `60` | No |
| `SQUIGGLE_API_BASE` | Squiggle API base URL | `https://api.squiggle.com.au` | No |
| `OPENROUTER_API_KEY` | OpenRouter API key | - | Yes |
| `OPENROUTER_MODEL` | AI model for explanations | `gptoss-120b` | No |
| `OPENROUTER_BASE_URL` | OpenRouter API base URL | `https://openrouter.ai/api/v1` | No |
| `ENVIRONMENT` | Environment (development/production) | `development` | No |

### Database

The backend uses SQLite for local development. The database file is created automatically when the first query is executed.

For production, consider migrating to PostgreSQL or MySQL for better performance and scalability.

## Cron Jobs System

The WhatIsMyTip backend includes a comprehensive cron-based data collection system for automated operations. This system provides reliable, scheduled data scraping, match completion detection, tip generation, and historical data refresh capabilities.

### Cron Job Overview

The system manages four main cron jobs:

| Job Name | Schedule | Purpose | Dependencies |
|----------|----------|---------|--------------|
| `daily_game_sync` | `0 2 * * *` (2:00 AM daily) | Sync all games for current season, update Elo cache | None |
| `match_completion_detector` | `*/15 * * * *` (every 15 minutes) | Detect and scrape completed matches | None |
| `tip_generation` | `0 3 * * *` (3:00 AM daily) | Generate tips for upcoming rounds | `daily_game_sync` |
| `historical_data_refresh` | `0 4 * * 0` (4:00 AM Sundays) | Refresh historical data for missing seasons | None |

### Job Execution Tracking

All cron jobs track their execution history in the [`job_executions`](docs/migrations.md#job-executions-table) table, including:
- Execution status (pending, running, completed, failed)
- Start and completion timestamps
- Duration in seconds
- Items processed, succeeded, and failed
- Error messages and details
- Metadata for debugging

### Job Locking

The system uses the [`job_locks`](docs/migrations.md#job-locks-table) table to prevent concurrent execution of the same job, ensuring data integrity and preventing race conditions.

### Admin API Endpoints

The cron system provides admin endpoints for manual job triggering and monitoring:

- `POST /api/admin/jobs/daily-sync/trigger` - Manually trigger daily game sync
- `POST /api/admin/jobs/match-completion/trigger` - Manually trigger match completion detection
- `POST /api/admin/jobs/tip-generation/trigger` - Manually trigger tip generation
- `POST /api/admin/jobs/historic-refresh/trigger` - Manually trigger historical data refresh
- `GET /api/admin/jobs/historic-refresh/progress` - Check historical refresh progress
- `GET /api/health/cron` - Cron job health check endpoint

### Job Monitoring

Cron jobs are monitored through:
- Application logs with structured JSON format
- Job execution history in the database
- Health check endpoint at `/api/health/cron`
- Job metrics including success rates and durations

## Services

### GameSyncService ([`game_sync.py`](backend/app/services/game_sync.py))

Responsible for syncing games from the Squiggle API.

**Key Methods:**
- `sync_current_season()` - Sync all games for the current season
- `sync_season()` - Sync games for a specific season
- `sync_round()` - Sync games for a specific round
- `update_elo_cache()` - Update Elo ratings cache after sync

### MatchCompletionDetector ([`match_completion.py`](backend/app/services/match_completion.py))

Detects and processes completed matches.

**Key Methods:**
- `detect_completed_matches()` - Identify games ready for completion scraping
- `scrape_completed_match()` - Fetch final scores from Squiggle API
- `is_match_buffer_elapsed()` - Check if buffer time has passed
- `process_completed_matches()` - Process all completed matches

### TipGenerationService ([`tip_generation.py`](backend/app/services/tip_generation.py))

Generates tips using ML models and heuristics.

**Key Methods:**
- `generate_for_round()` - Generate tips for a specific round
- `generate_for_game()` - Generate tips for a single game
- `generate_batch()` - Generate tips for multiple games
- `regenerate_for_round()` - Regenerate tips for an existing round

### HistoricDataRefreshService ([`historic_data_refresh.py`](backend/app/services/historic_data_refresh.py))

Refreshes historical data for missing seasons/rounds.

**Key Methods:**
- `refresh_all_seasons()` - Refresh all historical seasons
- `refresh_season()` - Refresh a specific season
- `refresh_round()` - Refresh a specific round
- `get_missing_data()` - Identify missing historical data
- `track_progress()` - Track refresh progress

## Database Schema

### New Tables

#### JobExecutions ([`job_executions`](backend/app/models/__init__.py))

Tracks execution history of all cron jobs.

**Columns:**
- `id` - Primary key
- `job_name` - Name of the cron job
- `status` - Execution status (pending, running, completed, failed)
- `started_at` - When the job started
- `completed_at` - When the job completed (if successful)
- `duration_seconds` - Execution duration
- `items_processed` - Total items processed
- `items_succeeded` - Items that succeeded
- `items_failed` - Items that failed
- `error_message` - Error message (if failed)
- `error_details` - Structured error information (JSON)
- `metadata` - Additional context (JSON)
- `created_at` - Record creation timestamp

#### JobLocks ([`job_locks`](backend/app/models/__init__.py))

Prevents concurrent execution of the same job.

**Columns:**
- `id` - Primary key
- `job_name` - Name of the cron job (unique)
- `locked_at` - When the lock was acquired
- `locked_by` - Hostname/pod identifier
- `expires_at` - When the lock expires
- `created_at` - Record creation timestamp

#### EloCache ([`elo_cache`](backend/app/models/__init__.py))

Persists Elo ratings cache for faster initialization.

**Columns:**
- `id` - Primary key
- `team_name` - Team name (unique)
- `rating` - Current Elo rating
- `games_played` - Number of games played
- `last_updated` - Last rating update timestamp
- `created_at` - Record creation timestamp

### Modified Tables

#### Games

**New Columns:**
- `last_synced_at` - Track when the game was last synced from Squiggle
- `sync_count` - Number of syncs for this game
- `sync_version` - Version of sync data

#### GenerationProgress

**New Columns:**
- `job_execution_id` - Link to job execution record
- `current_item` - Track current item being processed
- `estimated_remaining_seconds` - Estimated time remaining

## ML Models

The backend implements four ML models for predictions:

### 1. Elo Model ([`elo.py`](backend/app/models_ml/elo.py:1))

**Purpose**: Track team strength over time using the Elo rating system.

**How it works**:
- Teams start with a base rating of 1500
- Winners gain points based on the margin of victory and opponent's rating
- Losers lose points based on the same factors
- Adjustments are scaled by home advantage factor

**Key features**:
- Historical rating tracking
- Margin-based point adjustments
- Configurable home advantage factor

### 2. Form Model ([`form.py`](backend/app/models_ml/form.py:1))

**Purpose**: Predict based on recent team performance.

**How it works**:
- Teams receive points for recent wins (3 points for a win, 1 point for a loss)
- Points are weighted more heavily for recent games
- The model calculates a "form score" for each team

**Key features**:
- Recent performance weighting
- Simple and interpretable
- Good for short-term predictions

### 3. Home Advantage Model ([`home_advantage.py`](backend/app/models_ml/home_advantage.py:1))

**Purpose**: Account for the advantage of playing at home.

**How it works**:
- Teams get a bonus based on home ground advantage
- The bonus is calculated as a percentage of the Elo rating
- The bonus is added to the home team's rating

**Key features**:
- Configurable home advantage factor
- Simple adjustment to Elo ratings
- Considers venue-specific advantages

### 4. Value Model ([`value.py`](backend/app/models_ml/value.py:1))

**Purpose**: Identify value bets based on odds.

**How it works**:
- Calculates expected value based on model predictions and odds
- Only selects teams with positive expected value
- Uses a threshold to filter out low-value bets

**Key features**:
- Expected value calculation
- Odds integration
- Value-focused predictions

## Heuristic Layers

Heuristics wrap ML models to create different prediction strategies:

### 1. Best Bet Heuristic ([`best_bet.py`](backend/app/heuristics/best_bet.py:1))

**Strategy**: Conservative, high-confidence picks.

**How it works**:
- Requires consensus across multiple models
- Only selects teams with high confidence (>60%)
- Uses average of model predictions

**Best for**: Long-term betting strategies

### 2. YOLO Heuristic ([`yolo.py`](backend/app/heuristics/yolo.py:1))

**Strategy**: High-risk, high-reward selections.

**How it works**:
- Selects the team with the highest confidence
- No consensus requirement
- Ignores confidence thresholds

**Best for**: Adventurous bettors looking for big wins

### 3. High Risk High Reward Heuristic ([`high_risk_high_reward.py`](backend/app/heuristics/high_risk_high_reward.py:1))

**Strategy**: Balanced approach for adventurous tippers.

**How it works**:
- Selects teams with moderate confidence (40-70%)
- Requires some model consensus
- Balances risk and reward

**Best for**: Moderate-risk betting strategies

## API Endpoints

### Health Check

```
GET /health
```

Returns the health status of the API.

**Response**:
```json
{
  "status": "healthy"
}
```

### Games

```
GET /api/games
GET /api/games/{game_id}
```

Get games data from the Squiggle API.

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

### Tips

```
GET /api/tips
GET /api/tips/{heuristic}
POST /api/tips/generate
POST /api/tips/explanations/generate
```

**Generate Tips**:
```bash
POST /api/tips/generate?season=2025&round=1&heuristics=best_bet,yolo&generate_explanations=true
```

**Generate Explanations**:
```bash
POST /api/tips/explanations/generate?season=2025&round=1
```

**Response**:
```json
{
  "message": "Generated 10 tips for round 1, 2025",
  "heuristics_used": ["best_bet", "yolo"],
  "tips_count": 10,
  "explanations_generating": true
}
```

### Backtesting

```
GET /api/backtest
GET /api/backtest/{heuristic}
POST /api/backtest/run
GET /api/backtest/compare
```

**Run Backtest**:
```bash
# Backtest entire season for all heuristics
POST /api/backtest/run?season=2024

# Backtest specific round
POST /api/backtest/run?season=2024&round=5

# Backtest specific heuristic
POST /api/backtest/run?season=2024&heuristic=best_bet
```

**Compare Heuristics**:
```bash
GET /api/backtest/compare?season=2024
```

**Response**:
```json
{
  "season": 2024,
  "comparison": {
    "best_bet": {
      "overall_accuracy": 0.62,
      "total_profit": 1250.00,
      "total_bets": 100
    },
    "yolo": {
      "overall_accuracy": 0.55,
      "total_profit": 800.00,
      "total_bets": 100
    }
  },
  "best_overall": {
    "heuristic": "best_bet",
    "accuracy": 0.62,
    "profit": 1250.00
  }
}
```

## Running the Backend

### Development Mode

```bash
cd backend
uv run uvicorn main:app --reload
```

The API will be available at `http://localhost:8000`.

### Production Mode

```bash
cd backend
uv run uvicorn main:app --host 0.0.0.0 --port 8000 --workers 4
```

### API Documentation

Once running, visit `http://localhost:8000/docs` for interactive API documentation (Swagger UI).

## Testing

### Run Tests

```bash
cd backend
uv run pytest
```

### Run Specific Test File

```bash
uv run pytest tests/test_api.py
```

### Run Tests with Coverage

```bash
uv run pytest --cov=app --cov-report=html
```

## Architecture

### Model Orchestrator ([`orchestrator.py`](backend/app/orchestrator.py:1))

The [`ModelOrchestrator`](backend/app/orchestrator.py:8) class coordinates ML models and heuristic layers:

1. **Initialization**: Creates instances of all ML models and heuristic layers
2. **Prediction**: Takes a game and heuristic name, generates prediction
3. **Model Predictions**: Runs all ML models on the game
4. **Heuristic Application**: Applies the selected heuristic to model predictions

### Data Flow

```
User Request → API Endpoint → ModelOrchestrator
                                      ↓
                                  ML Models
                                      ↓
                                  Model Predictions
                                      ↓
                                  Heuristic Layer
                                      ↓
                                  Final Prediction
                                      ↓
                                  Database Storage
```

## Squiggle API Integration

The backend integrates with the [Squiggle API](https://api.squiggle.com.au/) for AFL data.

### API Client ([`squiggle/client.py`](backend/app/squiggle/client.py:1))

The Squiggle client handles:
- Fetching fixtures and results
- Team information
- Historical data

### Rate Limits

Squiggle API has rate limits. The backend implements its own rate limiting:
- **60 requests per minute** per IP address

## OpenRouter Integration

The backend uses OpenRouter with the `gptoss-120b` model for AI-powered explanations.

### Configuration

- **Model**: `gptoss-120b`
- **Base URL**: `https://openrouter.ai/api/v1`
- **Rate Limit**: 5 requests per minute for explanations

### Explanation Generation

The [`ExplanationService`](backend/app/services/explanation.py:8) generates explanations by:
1. Gathering model predictions and game data
2. Sending context to OpenRouter API
3. Storing generated explanations in the database

## Margin Calculation

All ML models calculate predicted winning margins. The margin is:
- Based on Elo rating differences
- Adjusted by home advantage
- Used for backtesting and tip display

## Error Handling

The API includes comprehensive error handling:
- HTTP 404 for not found resources
- HTTP 429 for rate limit exceeded
- HTTP 500 for server errors
- Detailed error messages in responses

## Security

- **CORS**: Configurable allowed origins
- **Rate Limiting**: Per-IP rate limiting
- **Input Validation**: Pydantic schemas validate all inputs
- **Environment Variables**: Sensitive data stored in `.env`

## Performance

- **Async Database**: Uses SQLAlchemy async for efficient database operations
- **Background Tasks**: Explanation generation runs in background
- **Connection Pooling**: Reuses database connections

## Next Steps

- [x] Add database migrations (Alembic)
- [x] Implement cron-based data collection system
- [x] Add job execution tracking
- [x] Implement job locking mechanism
- [x] Add admin API endpoints for job management
- [ ] Implement user authentication
- [ ] Add caching layer (Redis)
- [ ] Set up comprehensive logging and monitoring
- [ ] Implement database backups
- [ ] Add unit tests for all services
- [ ] Performance optimization for large datasets
- [ ] Add alerting for job failures
