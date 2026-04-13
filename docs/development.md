# WhatIsMyTip Development Guide

## Overview

This guide covers setting up and working with the WhatIsMyTip development environment. It includes instructions for local development, testing, and contributing to the project. The project now includes a comprehensive cron-based data collection system for automated operations.

## Development Environment Setup

### Prerequisites

- **Bun** (JavaScript runtime and package manager)
- **uv** (Python package manager)
- **Python 3.11+**
- **Node.js 18+** (for Nuxt 4)
- **Git** (for version control)
- **Croniter** (for cron schedule validation) - Python package

## Development Environment Setup

### Prerequisites

- **Bun** (JavaScript runtime and package manager)
- **uv** (Python package manager)
- **Python 3.11+**
- **Node.js 18+** (for Nuxt 4)
- **Git** (for version control)

### Install Tools

1. **Install Bun**:
   ```bash
   # macOS/Linux
   curl -fsSL https://bun.sh/install | bash

   # Windows
   powershell -c "irm bun.sh/install.ps1 | iex"
   ```

2. **Install uv**:
   ```bash
   pipx install uv
   ```

3. **Install Node.js**:
   - Download from [nodejs.org](https://nodejs.org/)
   - Or use nvm: `nvm install 18`

### Clone Repository

```bash
git clone github.com/danielpaparo98/WhatIsMyTip.git
cd whatismytip
```

## Local Development Setup

### Frontend Setup

1. **Install Dependencies**:
   ```bash
   cd frontend
   bun install
   ```

2. **Configure Environment**:
   ```bash
   cp .env.example .env
   # Edit .env with your configuration
   ```

3. **Start Development Server**:
   ```bash
   bun run dev
   ```

The frontend will be available at `http://localhost:3000`

### Backend Setup

1. **Install Dependencies**:
   ```bash
   cd backend
   uv sync
   ```

2. **Configure Environment**:
   ```bash
   cp .env.example .env
   # Edit .env with your configuration
   ```

3. **Start Development Server**:
   ```bash
   uv run uvicorn main:app --reload
   ```

The backend API will be available at `http://localhost:8000`

4. **Access API Documentation**:
   - Swagger UI: `http://localhost:8000/docs`
   - ReDoc: `http://localhost:8000/redoc`

### Running Cron Jobs

#### Starting with Cron Jobs Enabled

By default, cron jobs are enabled in development mode. The FastAPI application automatically registers all cron jobs on startup.

**Start the application:**
```bash
cd backend
uv run uvicorn main:app --reload
```

**Verify cron jobs are registered:**
```bash
curl http://localhost:8000/api/health/cron
```

Expected response:
```json
{
  "status": "healthy",
  "timestamp": "2026-04-02T15:00:00.000Z",
  "jobs": [
    {
      "name": "daily_game_sync",
      "status": "enabled",
      "last_run": null,
      "next_run": "2026-04-03T02:00:00.000Z"
    },
    {
      "name": "match_completion_detector",
      "status": "enabled",
      "last_run": null,
      "next_run": "2026-04-02T15:15:00.000Z"
    },
    {
      "name": "tip_generation",
      "status": "enabled",
      "last_run": null,
      "next_run": "2026-04-03T03:00:00.000Z"
    },
    {
      "name": "historical_data_refresh",
      "status": "enabled",
      "last_run": null,
      "next_run": "2026-04-06T04:00:00.000Z"
    }
  ]
}
```

#### Cron Job Registration

Cron jobs are registered in [`backend/app/main.py`](backend/app/main.py) using the `fastapi-crons` library:

```python
from fastapi_crons import CronJob

@app.on_event("startup")
async def startup_cron_jobs():
    from app.cron.jobs import (
        daily_game_sync,
        match_completion_detector,
        tip_generation,
        historical_data_refresh
    )
```

Each job is defined in [`backend/app/cron/jobs/`](backend/app/cron/jobs/) with a cron schedule and implementation.

#### Manual Job Triggering

You can manually trigger any cron job via the admin API:

```bash
# Trigger daily game sync
curl -X POST http://localhost:8000/api/admin/jobs/daily-sync/trigger

# Trigger match completion detection
curl -X POST http://localhost:8000/api/admin/jobs/match-completion/trigger

# Trigger tip generation
curl -X POST http://localhost:8000/api/admin/jobs/tip-generation/trigger

# Trigger historical data refresh
curl -X POST http://localhost:8000/api/admin/jobs/historic-refresh/trigger

# Check historical refresh progress
curl http://localhost:8000/api/admin/jobs/historic-refresh/progress
```

#### Testing Cron Jobs Locally

**Option 1: Trigger Manually**
Trigger jobs via the admin API endpoints as shown above.

**Option 2: Wait for Scheduled Time**
Cron jobs run on their scheduled times. You can verify they're running by:
1. Checking application logs for job execution
2. Querying the `job_executions` table in the database
3. Checking the health endpoint after scheduled times

**Option 3: Use a Cron Simulator**
For faster testing, you can modify the cron schedule temporarily:

Edit [`backend/app/config.py`](backend/app/config.py) to change schedules:
```python
# Change to run every minute for testing
daily_sync_schedule: str = "*/1 * * * *"
```

After testing, revert to the original schedule.

#### Disabling Cron Jobs

To disable all cron jobs temporarily:

1. Set environment variable:
   ```bash
   export CRON_ENABLED=false
   ```

2. Restart the application:
   ```bash
   uv run uvicorn main:app --reload
   ```

To enable again:
   ```bash
   export CRON_ENABLED=true
   uv run uvicorn main:app --reload
   ```

To disable individual jobs, use the admin API:
```bash
curl -X POST http://localhost:8000/api/admin/jobs/daily-sync/disable
```

### Running Both Frontend and Backend

Use a terminal multiplexer like **tmux** or **screen** to run both servers:

```bash
# Terminal 1 - Frontend
cd frontend
bun run dev

# Terminal 2 - Backend
cd backend
uv run uvicorn main:app --reload
```

Or use a single command with **concurrently**:
```bash
# Install concurrently globally
bun add -g concurrently

# Create package.json scripts
cd frontend && bun run dev &
cd backend && uv run uvicorn main:app --reload
```

## Project Structure

```
whatismytip/
├── frontend/              # Nuxt 4 frontend
│   ├── app.vue
│   ├── nuxt.config.ts
│   ├── package.json
│   ├── assets/
│   │   └── css/main.css   # Design system
│   ├── components/        # Vue components
│   ├── composables/       # Vue composables
│   └── pages/             # Page routes
├── backend/               # FastAPI backend
│   ├── main.py
│   ├── pyproject.toml
│   ├── uv.lock
│   ├── .env.example
│   ├── app/
│   │   ├── api/           # API endpoints
│   │   ├── crud/          # Database operations
│   │   ├── db/            # Database sessions
│   │   ├── models/        # Database models
│   │   ├── models_ml/     # ML models
│   │   ├── heuristics/    # Heuristic layers
│   │   ├── openrouter/    # AI client
│   │   ├── schemas/       # Pydantic schemas
│   │   └── services/      # Business logic
│   └── squiggle/          # Squiggle API client
├── docs/                  # Documentation
├── README.md
└── LICENSE
```

## Development Workflow

### Creating a New Feature

1. **Create a Feature Branch**:
   ```bash
   git checkout -b feature/your-feature-name
   ```

2. **Make Changes**:
   - Update frontend in `frontend/`
   - Update backend in `backend/`
   - Update documentation in `docs/`

3. **Test Your Changes**:
   - Run frontend tests: `cd frontend && bun run lint && bun run typecheck`
   - Run backend tests: `cd backend && uv run pytest`
   - Test manually in browser
   - Test cron jobs if applicable

4. **Commit Changes**:
   ```bash
   git add .
   git commit -m "feat: add your feature"
   ```

5. **Push to Remote**:
   ```bash
   git push origin feature/your-feature-name
   ```

6. **Create Pull Request**:
   - Go to GitHub/GitLab
   - Create PR from your branch
   - Request review

### Code Style Guidelines

#### Frontend (TypeScript/Nuxt)

- Use TypeScript for type safety
- Follow ESLint rules configured in `frontend/.eslintrc`
- Use Nuxt conventions for components and pages
- Use descriptive variable and function names

#### Backend (Python)

- Follow PEP 8 style guide
- Use type hints for all functions
- Follow ruff rules configured in `backend/pyproject.toml`
- Use descriptive variable and function names
- Use async/await for all database operations
- Use croniter for schedule validation

#### Documentation

- Use clear, concise language
- Document all functions and classes
- Include code examples where appropriate
- Use Markdown formatting

### Adding New Cron Jobs

#### 1. Create Cron Job File

Create a new file in [`backend/app/cron/jobs/`](backend/app/cron/jobs/):

```python
# backend/app/cron/jobs/my_new_job.py
from fastapi_crons import CronJob
from app.services.my_service import MyService
from app.logger import get_logger

logger = get_logger(__name__)

@app.on_event("startup")
async def register_my_job():
    @CronJob(app, schedule="0 4 * * *")  # Daily at 4 AM
    async def my_cron_job():
        logger.info("Starting my cron job")
        
        try:
            service = MyService()
            result = await service.execute()
            logger.info(f"Job completed successfully: {result}")
        except Exception as e:
            logger.error(f"Job failed: {e}", exc_info=True)
```

#### 2. Create Service

Create the corresponding service in [`backend/app/services/`](backend/app/services/):

```python
# backend/app/services/my_service.py
from app.logger import get_logger

logger = get_logger(__name__)

class MyService:
    async def execute(self) -> dict:
        """Execute the service logic."""
        logger.info("MyService executing")
        # Your logic here
        return {"status": "success", "items_processed": 10}
```

#### 3. Add Environment Variables

Update [`backend/app/config.py`](backend/app/config.py) with configuration:

```python
class Settings(BaseSettings):
    # ... existing settings ...
    
    # New cron job configuration
    my_job_enabled: bool = True
    my_job_schedule: str = "0 4 * * *"
    my_job_timeout_seconds: int = 1800
```

#### 4. Update Documentation

Update relevant documentation files:
- [`docs/backend.md`](backend.md) - Add service description
- [`docs/development.md`](development.md) - Add usage instructions
- [`docs/api.md`](api.md) - Add API endpoints if needed

#### 5. Test the Cron Job

```bash
# Start the application
cd backend
uv run uvicorn main:app --reload

# Trigger manually
curl -X POST http://localhost:8000/api/admin/jobs/my-job/trigger

# Check job execution history
sqlite3 whatismytip.db "SELECT * FROM job_executions WHERE job_name='my_job' ORDER BY created_at DESC LIMIT 10;"
```

### Modifying Existing Cron Jobs

#### Update Schedule

Edit the cron schedule in the job definition:

```python
# Change from daily to hourly
@CronJob(app, schedule="0 * * * *")
async def my_job():
    pass
```

#### Update Configuration

Update environment variables in [`.env.example`](backend/.env.example):

```bash
MY_JOB_SCHEDULE="0 * * * *"
MY_JOB_ENABLED=true
```

#### Update Service Logic

Modify the service implementation in [`backend/app/services/`](backend/app/services/).

#### Test Changes

```bash
# Restart the application to register changes
cd backend
uv run uvicorn main:app --reload

# Trigger manually to test
curl -X POST http://localhost:8000/api/admin/jobs/my-job/trigger
```

### Testing Cron Jobs

#### Unit Tests

Write unit tests for cron jobs:

```python
# backend/tests/test_cron_jobs.py
import pytest
from httpx import AsyncClient
from main import app

@pytest.mark.asyncio
async def test_daily_sync_job():
    async with AsyncClient(app=app, base_url="http://test") as ac:
        response = await ac.post("/api/admin/jobs/daily-sync/trigger")
        assert response.status_code == 200
        assert response.json()["status"] == "success"
```

#### Integration Tests

Test the complete cron job workflow:

```python
# backend/tests/test_cron_integration.py
import pytest
from app.services.game_sync import GameSyncService

@pytest.mark.asyncio
async def test_game_sync_service():
    service = GameSyncService()
    result = await service.sync_current_season(db)
    assert result.games_synced > 0
    assert result.duration_seconds > 0
```

#### Manual Testing

1. Start the application with cron jobs enabled
2. Trigger jobs via admin API
3. Verify execution in logs
4. Check database for job execution records
5. Verify data was processed correctly

### Debugging Cron Job Issues

#### Check Job Status

```bash
curl http://localhost:8000/api/health/cron
```

#### View Job Execution History

```bash
cd backend
sqlite3 whatismytip.db

# View recent executions
SELECT * FROM job_executions ORDER BY created_at DESC LIMIT 10;

# View failed executions
SELECT * FROM job_executions WHERE status='failed' ORDER BY created_at DESC LIMIT 10;

# View specific job executions
SELECT * FROM job_executions WHERE job_name='daily_game_sync';
```

#### Check Job Locks

```bash
# View active locks
SELECT * FROM job_locks WHERE expires_at > datetime('now');
```

#### Review Application Logs

```bash
# Check for job execution logs
grep "daily_game_sync" logs/app.log

# Check for errors
grep "ERROR" logs/app.log | grep "cron"
```

#### Common Issues

**Issue**: Cron job not running
- Check `CRON_ENABLED=true`
- Verify cron schedule is valid
- Check application logs for startup errors

**Issue**: Job stuck in "running" status
- Check for stale locks: `SELECT * FROM job_locks WHERE expires_at < datetime('now');`
- Remove stale locks: `DELETE FROM job_locks WHERE expires_at < datetime('now');`
- Check job timeout configuration

**Issue**: Job failing repeatedly
- Check error messages in job_executions table
- Review application logs
- Verify service logic
- Check API rate limits

## Adding New ML Models

### 1. Create Model Class

Create a new file in `backend/app/models_ml/`:

```python
# backend/app/models_ml/your_model.py
from typing import Dict, Any, Tuple
from app.models import Game
from app.models_ml.base import BaseModel


class YourModel(BaseModel):
    """Your ML model description."""

    def __init__(self):
        # Initialize model parameters
        pass

    async def predict(self, game: Game) -> Tuple[str, float, int]:
        """Predict winner, confidence, and margin.

        Args:
            game: Game to predict

        Returns:
            Tuple of (winner_team, confidence, predicted_margin)
        """
        # Your prediction logic here
        winner = "home_team"
        confidence = 0.65
        margin = 15

        return winner, confidence, margin

    def get_name(self) -> str:
        """Get the model name."""
        return "your_model"
```

### 2. Register Model in Orchestrator

Update `backend/app/orchestrator.py`:

```python
from app.models_ml import YourModel

class ModelOrchestrator:
    def __init__(self):
        self.models: List[BaseModel] = [
            EloModel(),
            FormModel(),
            HomeAdvantageModel(),
            ValueModel(),
            YourModel(),  # Add your model
        ]
```

### 3. Test Your Model

```bash
cd backend
uv run uvicorn main:app --reload
```

Test with:
```bash
curl "http://localhost:8000/api/tips/generate?season=2025&round=1&heuristics=your_model"
```

## Adding New Heuristics

### 1. Create Heuristic Class

Create a new file in `backend/app/heuristics/`:

```python
# backend/app/heuristics/your_heuristic.py
from typing import Dict, Any, Tuple
from app.models import Game
from app.heuristics.base import BaseHeuristic


class YourHeuristic(BaseHeuristic):
    """Your heuristic description."""

    def __init__(self, models: List[BaseModel]):
        super().__init__(models)

    async def apply(
        self, game: Game, model_predictions: Dict[str, Tuple[str, float, int]]
    ) -> Tuple[str, float, int]:
        """Apply heuristic to model predictions.

        Args:
            game: Game to predict
            model_predictions: Dict of model_name -> (winner, confidence, margin)

        Returns:
            Tuple of (winner, confidence, margin)
        """
        # Your heuristic logic here
        winner = "home_team"
        confidence = 0.60
        margin = 10

        return winner, confidence, margin

    def get_name(self) -> str:
        """Get the heuristic name."""
        return "your_heuristic"
```

### 2. Register Heuristic in Orchestrator

Update `backend/app/orchestrator.py`:

```python
from app.heuristics import YourHeuristic

class ModelOrchestrator:
    def __init__(self):
        self.heuristics: Dict[str, BaseHeuristic] = {
            "best_bet": BestBetHeuristic(self.models),
            "yolo": YOLOHeuristic(self.models),
            "high_risk_high_reward": HighRiskHighRewardHeuristic(self.models),
            "your_heuristic": YourHeuristic(self.models),  # Add your heuristic
        }
```

### 3. Test Your Heuristic

```bash
curl "http://localhost:8000/api/tips/generate?season=2025&round=1&heuristics=your_heuristic"
```

## Testing Guidelines

### Frontend Testing

#### Linting

```bash
cd frontend
bun run lint
```

#### Type Checking

```bash
cd frontend
bun run typecheck
```

#### Manual Testing

1. Start development server: `bun run dev`
2. Open browser: `http://localhost:3000`
3. Test all pages and features
4. Check browser console for errors

### Backend Testing

#### Run All Tests

```bash
cd backend
uv run pytest
```

#### Run Specific Test

```bash
cd backend
uv run pytest tests/test_api.py
```

#### Run with Coverage

```bash
cd backend
uv run pytest --cov=app --cov-report=html
```

#### Test with Coverage Report

```bash
# Run tests with coverage
uv run pytest --cov=app --cov-report=html

# Open coverage report
open htmlcov/index.html
```

### Test Structure

```python
# backend/tests/test_api.py
import pytest
from httpx import AsyncClient
from main import app


@pytest.mark.asyncio
async def test_health_check():
    async with AsyncClient(app=app, base_url="http://test") as ac:
        response = await ac.get("/health")
        assert response.status_code == 200
        assert response.json() == {"status": "healthy"}


@pytest.mark.asyncio
async def test_get_tips():
    async with AsyncClient(app=app, base_url="http://test") as ac:
        response = await ac.get("/api/tips?heuristic=best_bet")
        assert response.status_code == 200
        assert "tips" in response.json()
```

## Database Operations

### Create Database

The database is created automatically on first run.

### Reset Database

```bash
cd backend
rm whatismytip.db
uv run uvicorn main:app --reload
```

### Run Database Migrations

(Not yet implemented, will be added in future)

## API Testing

### Using curl

```bash
# Health check
curl http://localhost:8000/health

# Get tips
curl "http://localhost:8000/api/tips?heuristic=best_bet"

# Generate tips
curl -X POST "http://localhost:8000/api/tips/generate?season=2025&round=1&heuristics=best_bet"

# Run backtest
curl -X POST "http://localhost:8000/api/backtest/run?season=2024&heuristic=best_bet"
```

### Using Swagger UI

1. Open `http://localhost:8000/docs`
2. Explore all endpoints
3. Test endpoints interactively
4. View request/response examples

### Using Postman

Import the OpenAPI spec:
```bash
curl http://localhost:8000/openapi.json > openapi.json
```

Then import `openapi.json` into Postman.

## Debugging

### Frontend Debugging

1. **Browser DevTools**:
   - Open browser console (F12)
   - Check Network tab for API requests
   - Use React DevTools for component debugging

2. **Vue DevTools**:
   - Install Vue DevTools browser extension
   - Inspect components and state
   - Monitor component lifecycle

3. **Bun Debugging**:
   ```bash
   bun run dev --inspect
   ```

### Backend Debugging

1. **Python Debugging**:
   ```bash
   cd backend
   uv run uvicorn main:app --reload --log-level debug
   ```

2. **Breakpoints**:
   - Use IDE breakpoints (VS Code, PyCharm)
   - Add debug prints for troubleshooting

3. **Logging**:
   ```python
   import logging
   logging.basicConfig(level=logging.DEBUG)
   logging.debug("Debug message")
   ```

### Common Issues

**Issue**: Frontend not connecting to backend
- **Solution**: Check `API_BASE_URL` environment variable

**Issue**: Backend database errors
- **Solution**: Check database URL and file permissions

**Issue**: ML models not working
- **Solution**: Check model initialization and data availability

## Code Review Checklist

### Frontend

- [ ] TypeScript types are correct
- [ ] ESLint passes without errors
- [ ] Components are properly scoped
- [ ] Responsive design works
- [ ] Accessibility features are present
- [ ] Code follows Nuxt conventions

### Backend

- [ ] Type hints are present
- [ ] PEP 8 style guide is followed
- [ ] Ruff passes without errors
- [ ] Async/await is used correctly
- [ ] Error handling is present
- [ ] Documentation is complete

### Documentation

- [ ] README is updated
- [ ] Code comments are clear
- [ ] API documentation is accurate
- [ ] Examples are provided

## Performance Optimization

### Frontend Optimization

1. **Lazy Load Components**:
   ```typescript
   const MyComponent = defineAsyncComponent(() => import('./MyComponent.vue'))
   ```

2. **Image Optimization**:
   ```vue
   <NuxtImg src="/image.jpg" loading="lazy" />
   ```

3. **Code Splitting**: Nuxt handles this automatically

### Backend Optimization

1. **Database Indexing**:
   ```python
   # Add indexes to frequently queried columns
   from sqlalchemy import Index
   Index('idx_game_round', Game.season, Game.round_id)
   ```

2. **Connection Pooling**:
   ```python
   # Configure in config.py
   engine = create_async_engine(
       DATABASE_URL,
       pool_size=10,
       max_overflow=20
   )
   ```

3. **Caching**: Implement Redis caching for API responses

## Continuous Integration

### GitHub Actions (Example)

Create `.github/workflows/test.yml`:

```yaml
name: Test

on: [push, pull_request]

jobs:
  test:
    runs-on: ubuntu-latest

    steps:
    - uses: actions/checkout@v3

    - name: Setup Bun
      uses: oven-sh/setup-bun@v1

    - name: Setup Python
      uses: actions/setup-python@v4
      with:
        python-version: '3.11'

    - name: Install Dependencies
      run: |
        cd frontend && bun install
        cd ../backend && uv sync

    - name: Run Tests
      run: |
        cd backend && uv run pytest
```

## Contributing

### How to Contribute

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Test thoroughly
5. Commit and push
6. Create a pull request

### Pull Request Process

1. **Open PR** with clear description
2. **Address Feedback** from reviewers
3. **Update Changes** as needed
4. **Get Approval** from maintainers
5. **Merge** to main branch

### Issue Reporting

When reporting issues:

1. **Check Existing Issues** first
2. **Provide Details**:
   - Steps to reproduce
   - Expected behavior
   - Actual behavior
   - Environment information
3. **Attach Screenshots** if applicable
4. **Include Logs** if relevant

## Best Practices

### Frontend

- Use TypeScript for type safety
- Follow Vue 3 Composition API
- Use Nuxt conventions
- Keep components small and focused
- Use composables for shared logic

### Backend

- Use async/await for all I/O operations
- Follow Clean Architecture principles
- Use dependency injection
- Implement proper error handling
- Write unit tests

### Documentation

- Keep documentation up to date
- Use clear and concise language
- Include code examples
- Document all public APIs

## Resources

### Learning Resources

- [Nuxt Documentation](https://nuxt.com/docs)
- [FastAPI Documentation](https://fastapi.tiangolo.com/)
- [Vue 3 Documentation](https://vuejs.org/)
- [Python Type Hints](https://docs.python.org/3/library/typing.html)
- [Squiggle API](https://api.squiggle.com.au/)

### Tools

- [Bun](https://bun.sh/) - JavaScript runtime and package manager
- [uv](https://github.com/astral-sh/uv) - Python package manager
- [VS Code](https://code.visualstudio.com/) - Code editor
- [PyCharm](https://www.jetbrains.com/pycharm/) - Python IDE
- [Postman](https://www.postman.com/) - API testing
- [Swagger UI](https://swagger.io/tools/swagger-ui/) - API documentation

## Next Steps

After setting up development:

1. **Explore the Codebase**: Read through the code to understand the structure
2. **Run the Application**: Test all features manually
3. **Read Documentation**: Review [`docs/backend.md`](backend.md) and [`docs/frontend.md`](frontend.md)
4. **Make Changes**: Start with small changes and work up
5. **Test Thoroughly**: Ensure all tests pass
6. **Contribute**: Share your improvements with the community

## Getting Help

If you need help:

1. Check the documentation
2. Search existing issues
3. Ask in the project's community
4. Open a new issue with details
