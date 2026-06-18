# Contributing to WhatIsMyTip

Thank you for your interest in contributing to WhatIsMyTip! We welcome contributions from the community.

## Code of Conduct

This project adheres to a code of conduct that all contributors are expected to follow. Please be respectful, inclusive, and constructive in all interactions.

## How to Contribute

### Reporting Bugs

1. Check if the issue has already been reported
2. Search for existing issues to avoid duplicates
3. Create a new issue with:
   - Clear, descriptive title
   - Detailed description of the bug
   - Steps to reproduce
   - Expected behavior
   - Actual behavior
   - Environment information (OS, browser, Python version, etc.)
   - Screenshots or logs if applicable

**Example Issue Template**:

```markdown
## Bug Report
**Title**: [Bug title]
**Environment**: Windows 11, Chrome, Python 3.12+

**Description**:
[Detailed description of the bug]

**Steps to Reproduce**:
1. [Step 1]
2. [Step 2]
3. [Step 3]

**Expected Behavior**:
[What should happen]

**Actual Behavior**:
[What actually happens]

**Screenshots**:
[Attach screenshots if applicable]

**Logs**:
[Attach relevant logs]
```

### Suggesting Enhancements

1. Check if the enhancement has already been suggested
2. Create a new issue with:
   - Clear, descriptive title
   - Detailed description of the enhancement
   - Why this enhancement would be valuable
   - How it would improve the project

**Example Enhancement Template**:

```markdown
## Enhancement Suggestion
**Title**: [Enhancement title]

**Description**:
[Detailed description of the enhancement]

**Why this would be valuable**:
[Explain the benefits]

**Proposed Implementation**:
[How you think this could be implemented]

**Alternatives Considered**:
[Other approaches you considered]
```

### Submitting Code

We follow **TDD** (write the failing test first) + **gitmoji** commit prefixes + a `feature/` (or `fix/`, `chore/`, …) branch naming convention.  The toolchain is **bun** (frontend) and **uv** (backend).  See [`docs/development.md`](docs/development.md) for the local setup walkthrough.

#### Quick start

1. Create a branch: `git checkout -b feature/your-feature-name` (or `fix/`, `chore/`, `refactor/`, `docs/`, `test/`)
2. **Write the failing test first** (TDD).  Run the existing suite to confirm red: `cd backend && uv run pytest tests/unit/ -v` (or `cd frontend && bun run test`).
3. Implement the change until the test passes.  Run the full suite + linter.
4. Commit with a gitmoji prefix: `git commit -m "✨ feat(api): add new endpoint"`.
5. Push: `git push origin feature/your-feature-name`.
6. Open a PR against `dev` (not `main`).

#### Development Setup

Follow the [Development Guide](docs/development.md) to set up your development environment.

#### Branch Naming

Use descriptive branch names:

- `feature/your-feature-name` - New features
- `fix/your-bug-fix` - Bug fixes
- `docs/your-documentation` - Documentation updates
- `refactor/your-refactor` - Code refactoring
- `test/your-test` - Test additions
- `chore/your-task` - Maintenance tasks

#### Commit Messages

Use **gitmoji** commit messages (emoji + conventional commit scope):

- `✨ feat: add new ML model`
- `🐛 fix: resolve database connection issue`
- `📝 docs: update API documentation`
- `♻️ refactor: improve code structure`
- `✅ test: add unit tests`
- `🎨 style: format code`
- `🔧 chore: update dependencies`

The full gitmoji catalogue is at <https://gitmoji.dev/>.  When in doubt, match the prefix to the closest gitmoji entry.

#### Pull Request Process

1. **Open a Pull Request**:
   - Go to your fork on GitHub/GitLab
   - Click "New Pull Request"
   - Select your branch
   - Provide a clear description

2. **PR Description**:
   - Briefly describe the changes
   - Link to related issues
   - Explain the reasoning behind the changes
   - List any breaking changes

3. **Address Feedback**:
   - Respond to reviewer comments
   - Make requested changes
   - Update the PR description if needed

4. **Get Approval**:
   - Wait for at least one maintainer to review
   - Address any feedback
   - Get approval to merge

5. **Merge**:
   - Once approved, the PR can be merged
   - The maintainer will merge your changes

#### Code Review Checklist

Before submitting a PR, ensure:

**Frontend**:
- [ ] TypeScript types are correct
- [ ] ESLint passes without errors
- [ ] Components are properly scoped
- [ ] Responsive design works
- [ ] Accessibility features are present
- [ ] Code follows Nuxt conventions
- [ ] Tests pass (if applicable)

**Backend**:
- [ ] Type hints are present
- [ ] PEP 8 style guide is followed
- [ ] Ruff passes without errors
- [ ] Async/await is used correctly
- [ ] Error handling is present
- [ ] Documentation is complete
- [ ] Tests pass (if applicable)

**Documentation**:
- [ ] README is updated
- [ ] Code comments are clear
- [ ] API documentation is accurate
- [ ] Examples are provided

## Development Workflow

### 1. Fork and Clone

```bash
# Fork the repository on GitHub/GitLab
# Clone your fork
git clone https://github.com/danielpaparo98/WhatIsMyTip.git
cd whatismytip
```

### 2. Create a Branch

```bash
git checkout -b feature/your-feature-name
```

### 3. Make Changes

- Update frontend in `frontend/`
- Update backend in `backend/`
- Update documentation in `docs/`
- Write tests
- Add comments

### 4. Test Your Changes

```bash
# Frontend
cd frontend
bun run lint
bun run typecheck
bun run dev

# Backend
cd backend
./scripts/dev.sh          # Start local PostgreSQL + Redis via Docker
uv run pytest tests/unit/ -v
```

### 5. Commit Changes

```bash
cd ..
git add .
git commit -m "feat: add your feature"
```

### 6. Push to Remote

```bash
git push origin feature/your-feature-name
```

### 7. Create Pull Request

1. Go to your fork on GitHub/GitLab
2. Click "New Pull Request"
3. Select your branch
4. Fill in the PR description
5. Submit the PR

## Project Structure

### Frontend (`frontend/`)

- **Components**: Reusable Vue components
- **Pages**: Route-specific pages
- **Composables**: Reusable logic
- **Assets**: Static files and styles

### Backend (`backend/`)

- **main.py** — FastAPI app entry point
- **app/** — FastAPI application code
  - `app/api/` — HTTP routers (`games`, `tips`, `backtest`, `admin`, `health`)
  - `app/core/` — Lifespan, middleware, scheduler, security, rate limit
  - `app/cron/` — Job classes bound to the in-process APScheduler
- **packages/shared/**: Shared business logic (DB models, services, ML)
  - `crud/` — Database operations
  - `services/` — Business logic
  - `models/` — Database models
  - `models_ml/` — Machine learning models (8 models)
  - `heuristics/` — Prediction strategies (BestBet, YOLO, HighRiskHighReward)
  - `schemas/` — Pydantic validation schemas
  - `squiggle/`, `weather/`, `openrouter/` — External data clients
  - `cache.py`, `config.py`, `db.py`, `alerting.py` — Infrastructure modules
- **proxy/**: nginx reverse proxy (used by App Platform)
- **alembic/**: Database migrations
- **tests/**: Unit (`tests/unit/`) and integration (`tests/integration/`) tests
- **scripts/**: Deployment and utility scripts (`deploy.sh`, `dev.sh`)
- **Dockerfile** — Multi-stage container image for the FastAPI app

### Documentation (`docs/`)

- `backend.md` - Backend documentation
- `frontend.md` - Frontend documentation
- `deployment.md` - Deployment guide
- `development.md` - Development guide
- `api.md` - API reference

## Coding Standards

### Frontend

- Use TypeScript for type safety
- Follow Vue 3 Composition API
- Use Nuxt conventions
- Keep components small and focused
- Use composables for shared logic
- Follow ESLint rules

### Backend

- Use async/await for all I/O operations
- Follow PEP 8 style guide
- Use type hints for all functions
- Follow ruff rules
- Import from `packages.shared.*` (for shared models / services) and `app.*` (for the FastAPI app)
- Always close Redis pools and dispose SQLAlchemy engines in the lifespan shutdown handler
- Raise `BackendServiceError` (from `app.core.exceptions`) for expected error paths
- Implement proper error handling at the router level
- Write Pydantic request/response models — no raw `dict` returns

### Documentation

- Keep documentation up to date
- Use clear and concise language
- Include code examples
- Document all public APIs

## Testing

### Frontend Testing

```bash
cd frontend
bun run lint          # Check for linting errors
bun run typecheck     # Check TypeScript types
bun run dev           # Run development server
```

### Backend Testing

```bash
cd backend
./scripts/dev.sh                # Ensure local PostgreSQL + Redis are running
uv run pytest tests/unit/ -v    # Run unit tests (verbose)
uv run pytest tests/unit/ --cov # Run with coverage
```

### Manual Testing

- Test all features manually in the browser
- Check browser console for errors
- Verify API responses
- Test responsive design

## Adding Tests

### Frontend Tests

Create test files in `frontend/tests/`:

```typescript
// frontend/tests/example.test.ts
import { describe, it, expect } from 'vitest'

describe('Example Test', () => {
  it('should pass', () => {
    expect(true).toBe(true)
  })
})
```

### Backend Tests

Create test files in `backend/tests/unit/`:

```python
# backend/tests/unit/test_example.py
import pytest

@pytest.mark.asyncio
async def test_example():
    assert True
```

## Adding New Backend Components

### Adding a New Cron Job

Add a new job to the in-process APScheduler:

1. **Create the job class** at `backend/app/cron/my_new_job.py`, inheriting from
   [`BaseJob`](backend/app/cron/base.py:1):

```python
"""My new job."""

from __future__ import annotations
from app.cron.base import BaseJob


class MyNewJob(BaseJob):
    """Job docstring."""

    JOB_NAME = "my-new-job"
    LOCKED_BY = "api-my-new-job"

    async def _execute(self, session) -> dict:
        # Your job logic here. Raise TransientJobError for retryable failures.
        ...
        return {"status": "ok"}
```

2. **Register the job** in [`backend/app/core/scheduler.py`](backend/app/core/scheduler.py:1):

```python
scheduler.add_job(
    MyNewJob(session_factory).execute,
    CronTrigger.from_crontab(settings.my_new_job_cron, timezone=settings.cron_timezone),
    id="my-new-job",
    name="My new job",
    replace_existing=True,
    max_instances=1,
    coalesce=True,
)
```

3. **Add config** (cron expression, enabled flag) in
   [`backend/packages/shared/config.py`](backend/packages/shared/config.py:1).

4. **Write tests** in `backend/tests/unit/test_app_cron_my_new_job.py`.

### Adding a New API Endpoint

Add a new router under `backend/app/api/`:

1. **Create the router file** at `backend/app/api/my_feature.py`:

```python
"""My feature API."""
from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.db_deps import get_session

router = APIRouter()


@router.get("")
async def list_items(
    season: int | None = Query(None, ge=2010, le=2030),
    session: AsyncSession = Depends(get_session),
) -> list[dict]:
    # Your handler here. Use Pydantic models for the response.
    return []
```

2. **Mount the router** in [`backend/main.py`](backend/main.py:1):

```python
from app.api.my_feature import router as my_feature_router

app.include_router(my_feature_router, prefix="/api/my-feature", tags=["my-feature"])
```

3. **Write tests** in `backend/tests/unit/test_app_api_my_feature.py` (use FastAPI's
   `TestClient` and mock the DB session).

### Database Changes

Database models live in [`backend/packages/shared/models/`](backend/packages/shared/models/__init__.py). To make schema changes:

1. Update or add the model in `backend/packages/shared/models/`
2. Generate a migration: `cd backend && uv run alembic revision --autogenerate -m "description"`
3. Apply the migration: `uv run alembic upgrade head`
4. Update corresponding CRUD operations in [`backend/packages/shared/crud/`](backend/packages/shared/crud/__init__.py)
5. Write tests for the new model and CRUD operations

See [docs/migrations.md](docs/migrations.md) for the full migration workflow.

## Documentation

### Updating Documentation

When contributing:

1. Update relevant documentation files
2. Keep examples up to date
3. Add new sections if needed
4. Use clear and concise language
5. Include code examples

### Documentation Files

- `README.md` - Project overview
- `CONTRIBUTING.md` - This file
- `docs/backend.md` - Backend documentation
- `docs/frontend.md` - Frontend documentation
- `docs/deployment.md` - Deployment guide
- `docs/development.md` - Development guide
- `docs/api.md` - API reference

## Pull Request Guidelines

### PR Title

Use conventional commit style:

- `feat: add new ML model`
- `fix: resolve database connection issue`
- `docs: update API documentation`
- `refactor: improve code structure`

### PR Description

Include:

- Brief summary of changes
- Link to related issues
- Explanation of changes
- Any breaking changes
- Screenshots (if applicable)

### PR Checklist

Before submitting:

- [ ] Code follows coding standards
- [ ] Tests pass
- [ ] Documentation is updated
- [ ] Commit messages are clear
- [ ] No merge conflicts
- [ ] Branch is up to date with main

### Review Process

1. Reviewers will provide feedback
2. Address all comments
3. Make requested changes
4. Update PR description if needed
5. Request re-review

## Getting Help

If you need help:

1. Check the documentation
2. Search existing issues
3. Ask in the project's community
4. Open a new issue with details

## License

By contributing to WhatIsMyTip, you agree that your contributions will be licensed under the project's license.

## Acknowledgments

Thank you for contributing to WhatIsMyTip! Your contributions help make this project better for everyone.

## Questions?

If you have questions about contributing:

1. Check the [Development Guide](docs/development.md)
2. Review this file
3. Open an issue
4. Contact the maintainers

---

**Happy Contributing! 🚀**
