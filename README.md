# WhatIsMyTip.com

AI-powered AFL tipping with smart heuristics and data-driven predictions.

![License](https://img.shields.io/badge/license-MIT-blue.svg)
![Python](https://img.shields.io/badge/python-3.12+-green.svg)
![Node.js](https://img.shields.io/badge/node.js-18+-brightgreen.svg)
![Nuxt](https://img.shields.io/badge/nuxt-4.0.0-00DC82.svg)
![FastAPI](https://img.shields.io/badge/FastAPI-Container-009688.svg)
![DO App Platform](https://img.shields.io/badge/DO_App_Platform-Container-0080FF.svg)

## Overview

WhatIsMyTip is a comprehensive AFL tipping application that combines machine learning models, heuristic strategies, and AI-powered explanations to provide accurate footy predictions. Built with modern technologies, it offers a single FastAPI container backend on DigitalOcean App Platform (with an in-process APScheduler for cron jobs) and a sleek frontend with Nuxt 4.

## Features

### Core Features

- **Multiple Picking Heuristics**: Choose from different prediction strategies
  - **Best Bet**: Conservative picks with high confidence
  - **YOLO**: High-risk, high-reward selections
  - **High Risk High Reward**: Balanced approach for adventurous tippers

- **Margin Calculations**: Predicted winning margins for each game

- **AI-Powered Explanations**: Get insights on why certain picks were made using OpenRouter (model configurable via `OPENROUTER_MODEL`)

- **Backtesting**: Analyze historical performance of different heuristics

- **Real-time Data**: Powered by the Squiggle API

### ML Models

- **Elo Model**: Track team strength over time using the Elo rating system
- **Form Model**: Predict based on recent team performance
- **Home Advantage Model**: Account for venue-specific advantages
- **Value Model**: Identify value bets based on odds
- **Weather Impact Model**: Adjust predictions based on match-day weather conditions
- **Injury Impact Model**: Factor in team injury lists and player availability
- **Matchup Model**: Leverage head-to-head historical performance between teams
- **Player Form Model**: Incorporate individual player form metrics

### Technical Features

- **Monochrome Bold Typographic Design**: Clean, modern UI with high contrast
- **Static Site Generation**: Fast, SEO-friendly frontend
- **Async Database Operations**: PostgreSQL with SQLAlchemy (asyncpg driver)
- **Redis Caching**: 3-tier TTL cache (60s / 300s / 3600s) shared across all routes
- **Container-based**: Single FastAPI process per container, deployed to DigitalOcean App Platform
- **In-process APScheduler**: 4 scheduled jobs (daily-sync, match-completion, tip-generation, historic-refresh) running inside the API process
- **Rate Limiting**: 60 requests per minute per IP
- **CORS Support**: Configurable cross-origin requests
- **No GPU Required**: Cost-efficient AI explanations using CPU

## Tech Stack

### Frontend
- **Nuxt 4**: Modern Vue.js framework with static site generation
- **Tailwind CSS**: Utility-first CSS framework
- **TypeScript**: Type-safe development
- **Bun**: JavaScript runtime and package manager

### Backend
- **FastAPI**: Single Python web framework process (the API server)
- **APScheduler**: In-process cron scheduler for the 4 background jobs
- **PostgreSQL**: Managed relational database (asyncpg driver)
- **Redis**: Managed cache with 3-tier TTL strategy
- **Pydantic Settings**: Configuration and environment management
- **Pydantic**: Data validation
- **Alembic**: Database migrations
- **SQLAlchemy**: Async ORM for database operations
- **uv**: Python package manager
- **Multi-stage Docker**: `backend/Dockerfile` produces a slim runtime image

### AI & Data
- **OpenRouter**: AI-powered explanation generation
- **Squiggle API**: AFL data source
- **AFLTables / FootyWire**: Historical AFL data (injury, player, matchup data)
- **Open-Meteo**: Match-day weather data

## Quick Start (Docker — full stack)

The fastest way to run the entire stack (Postgres + Redis + FastAPI +
Nuxt) on your machine is `docker compose`.  This works on Windows +
PowerShell, macOS, and Linux (Docker Desktop or Podman).

```bash
# macOS / Linux / WSL
git clone https://github.com/danielpaparo98/WhatIsMyTip.git
cd WhatIsMyTip
git checkout feature/local-docker-stack
./scripts/dev.sh up --logs
# Open http://localhost:3000  (frontend)
# Open http://localhost:8000  (API)
# Open http://localhost:8000/docs  (Swagger UI)
```

```powershell
# Windows + PowerShell
git clone https://github.com/danielpaparo98/WhatIsMyTip.git
cd WhatIsMyTip
git checkout feature/local-docker-stack
.\scripts\dev.ps1 up -Up -Logs
```

That's it — the dev script auto-detects Docker (or Podman), builds the
API image, runs Alembic migrations + a one-shot CSV loader, and brings
up Postgres, Redis, the API, and the frontend.

Useful commands:

| Command | Effect |
|---------|--------|
| `./scripts/dev.sh down` | Stop the stack (volumes preserved) |
| `./scripts/dev.sh reset` | Stop AND delete the database volume (full reset) |
| `./scripts/dev.sh logs api` | Tail the API container logs |
| `./scripts/dev.sh shell api` | Bash into the API container |
| `./scripts/dev.sh psql` | Open a psql shell against the dev DB |
| `./scripts/dev.sh config` | Validate `docker-compose.yml` |

For PowerShell, use the same subcommand names with `.\scripts\dev.ps1`
(`.\scripts\dev.ps1 reset`, etc.).

See [`docs/development.md`](docs/development.md:1) for the full local
development guide (loading CSV data, hot-reload, disabling the in-process
cron jobs, troubleshooting).

## Installation

### Prerequisites

- **Bun** (JavaScript runtime and package manager)
- **uv** (Python package manager)
- **Python 3.12+**
- **Node.js 18+** (for Nuxt 4)
- **Docker** (for local PostgreSQL + Redis)
- **PostgreSQL 16** and **Redis 7** (or use the `scripts/dev.sh` Docker setup)

### Clone Repository

```bash
git clone https://github.com/danielpaparo98/WhatIsMyTip.git
cd whatismytip
```

### Frontend Setup

```bash
cd frontend
bun install
cp .env.example .env
# Edit .env with your configuration
```

### Backend Setup

```bash
cd backend
cp .env.example .env
# Edit .env with your configuration (DATABASE_URL, REDIS_URL, etc.)
uv sync

# Start local PostgreSQL + Redis via Docker
./scripts/dev.sh

# Apply database migrations
uv run alembic upgrade head
```

## Development

### Frontend Development

```bash
cd frontend
bun run dev
```

The frontend will be available at `http://localhost:3000`

### Backend Development

The FastAPI backend runs as a single Python process. For local development, use Docker for
PostgreSQL + Redis, then run the unit tests:

```bash
cd backend
./scripts/dev.sh                  # Starts local PostgreSQL + Redis via Docker
uv run alembic upgrade head       # Apply migrations
uv run pytest tests/unit/ -v      # Run unit tests
uv run uvicorn main:app --reload  # Run the API on http://localhost:8000
```

The app exposes Swagger UI at `http://localhost:8000/docs` and ReDoc at
`http://localhost:8000/redoc`. The OpenAPI 3 spec is at `http://localhost:8000/openapi.json`.
See [`docs/api.md`](docs/api.md) for the full endpoint reference.

### Running Both

Use terminal multiplexer or run in separate terminals:

```bash
# Terminal 1 - Frontend
cd frontend && bun run dev

# Terminal 2 - Backend (local services + tests)
cd backend && ./scripts/dev.sh && uv run pytest tests/unit/ -v
```

## Configuration

### Environment Variables

**Backend ([`.env`](backend/.env.example:1))**:

```bash
# Database (PostgreSQL via asyncpg)
DATABASE_URL=postgresql+asyncpg://user:password@localhost:5432/whatismytip

# Redis
REDIS_URL=redis://localhost:6379/0

# Squiggle API
SQUIGGLE_API_BASE=https://api.squiggle.com.au
SQUIGGLE_CONTACT_EMAIL=contact@whatismytip.com

# OpenRouter (for explanation generation)
OPENROUTER_API_KEY=your_openrouter_api_key_here
OPENROUTER_MODEL=google/gemma-4-26b-a4b-it:free
OPENROUTER_BASE_URL=https://openrouter.ai/api/v1

# Admin
ADMIN_API_KEY=your_admin_api_key_here

# Environment
ENVIRONMENT=development
```

**Frontend**:

```bash
# Production points at the FastAPI service on the same hostname via App Platform:
# API_BASE_URL=https://whatismytip.com/api
# Local development:
API_BASE_URL=http://localhost:8000
```

## API Documentation

See [docs/api.md](docs/api.md) for detailed API documentation including:

- All endpoints and their parameters
- Request/response examples
- Rate limiting details
- Error codes
- Integration examples

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
├── backend/               # FastAPI backend (single Python process)
│   ├── main.py            # FastAPI app entry point
│   ├── Dockerfile         # Multi-stage container image
│   ├── docker-compose.yml # Local dev (PostgreSQL + Redis)
│   ├── pyproject.toml     # Python project configuration and dependencies
│   ├── app/               # FastAPI app (routers, middleware, scheduler)
│   │   ├── api/           # HTTP routers (games, tips, backtest, admin, health)
│   │   ├── core/          # Lifespan, middleware, scheduler, security, rate limit
│   │   └── cron/          # Job classes bound to the in-process APScheduler
│   ├── packages/
│   │   └── shared/        # Shared code (crud, services, models, models_ml,
│   │                      #   heuristics, schemas, squiggle, weather,
│   │                      #   openrouter, cache, config, db, alerting, etc.)
│   ├── alembic/           # Database migrations (env.py + versions/)
│   ├── tests/             # Unit + integration tests
│   └── scripts/           # Deployment and utility scripts (deploy.sh, dev.sh)
├── docs/                  # Documentation
│   ├── index.md           # Doc-of-docs landing page
│   ├── backend.md         # Backend architecture (FastAPI monolith)
│   ├── frontend.md        # Frontend documentation
│   ├── deployment.md      # Deployment guide
│   ├── development.md     # Development guide
│   ├── api.md             # API reference
│   ├── operations.md      # Runtime/deployment operations
│   ├── security-model.md  # Auth + rate limiting model
│   ├── data-loading.md    # CSV data loading + scraper
│   └── migrations.md      # Alembic migrations
├── CONTRIBUTING.md        # Contributing guidelines
├── README.md
└── LICENSE
```

## Documentation

Comprehensive documentation is available in the [`docs/`](docs/) directory (start at [`docs/index.md`](docs/index.md)):

- [`docs/index.md`](docs/index.md) - Doc-of-docs landing page
- [`docs/backend.md`](docs/backend.md) - Backend architecture (FastAPI monolith)
- [`docs/frontend.md`](docs/frontend.md) - Frontend structure and design system
- [`docs/deployment.md`](docs/deployment.md) - Container deployment to DigitalOcean App Platform
- [`docs/development.md`](docs/development.md) - Local development setup
- [`docs/api.md`](docs/api.md) - Complete API reference
- [`docs/operations.md`](docs/operations.md) - Runtime/deployment operations
- [`docs/security-model.md`](docs/security-model.md) - Auth + rate limiting model
- [`docs/data-loading.md`](docs/data-loading.md) - CSV data loading + scraper
- [`docs/migrations.md`](docs/migrations.md) - Alembic migrations

## Deployment

See [docs/deployment.md](docs/deployment.md) for detailed deployment instructions to DigitalOcean App Platform + Managed PostgreSQL + Managed Redis.

### Quick Deployment

**Backend** (builds the Docker image, pushes to DO Container Registry, triggers an App Platform deploy):
```bash
cd backend
./scripts/deploy.sh
```

**Frontend**:
```bash
cd frontend
bun run build
# Deploy .output/public to hosting provider
```

## Data Source

This project uses the [Squiggle API](https://api.squiggle.com.au/) for AFL data including fixtures, results, and team information. Special thanks to the Squiggle team for providing this valuable resource.

## AI Explanations

The backend uses OpenRouter to generate AI-powered explanations for tips (default model configured via `OPENROUTER_MODEL`).

**Cost**: ~$0.15 per 1M tokens
**Estimated Monthly Cost**: $5-20 depending on usage

## License

This project is licensed under the terms of the LICENSE file in the root directory.

## Contributing

Contributions are welcome! Please read [CONTRIBUTING.md](CONTRIBUTING.md) for details on:

- How to report bugs
- How to suggest enhancements
- How to submit code
- Code review process
- Development workflow

## Roadmap

- [ ] User authentication and accounts
- [ ] Email notifications
- [ ] User favorites/bookmarks
- [ ] Mobile app (React Native)
- [ ] Betting odds integration
- [ ] Real-time notifications
- [ ] Advanced analytics dashboard
- [x] OpenAPI spec generation — `/openapi.json` and Swagger UI at `/docs`

## Support

- Check the [documentation](docs/)
- Open an [issue](https://github.com/danielpaparo98/WhatIsMyTip/issues)
- Contact the development team

## Acknowledgments

- [Squiggle API](https://api.squiggle.com.au/) for AFL data
- [OpenRouter](https://openrouter.ai/) for AI model access
- [Nuxt](https://nuxt.com/) for the frontend framework
- [FastAPI](https://fastapi.tiangolo.com/) for the backend web framework
- [DigitalOcean App Platform](https://www.digitalocean.com/products/app-platform) for the container hosting

## Authors

- [Daniel Paparo](https://github.com/danielpaparo98/WhatIsMyTip) - Initial work

## License

See [LICENSE](LICENSE) file for details.
