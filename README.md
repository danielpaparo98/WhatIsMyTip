# WhatIsMyTip.com

AI-powered AFL tipping with smart heuristics and data-driven predictions.

![License](https://img.shields.io/badge/license-MIT-blue.svg)
![Python](https://img.shields.io/badge/python-3.11+-green.svg)
![Node.js](https://img.shields.io/badge/node.js-18+-brightgreen.svg)
![Nuxt](https://img.shields.io/badge/nuxt-4.0.0-00DC82.svg)
![FastAPI](https://img.shields.io/badge/fastapi-0.115.0-009688.svg)

## Overview

WhatIsMyTip is a comprehensive AFL tipping application that combines machine learning models, heuristic strategies, and AI-powered explanations to provide accurate footy predictions. Built with modern technologies, it offers a robust backend with FastAPI and a sleek frontend with Nuxt 4.

## Features

### Core Features

- **Multiple Picking Heuristics**: Choose from different prediction strategies
  - **Best Bet**: Conservative picks with high confidence
  - **YOLO**: High-risk, high-reward selections
  - **High Risk High Reward**: Balanced approach for adventurous tippers

- **Margin Calculations**: Predicted winning margins for each game

- **AI-Powered Explanations**: Get insights on why certain picks were made using OpenRouter with gptoss-120b

- **Backtesting**: Analyze historical performance of different heuristics

- **Real-time Data**: Powered by the Squiggle API

### ML Models

- **Elo Model**: Track team strength over time using the Elo rating system
- **Form Model**: Predict based on recent team performance
- **Home Advantage Model**: Account for venue-specific advantages
- **Value Model**: Identify value bets based on odds

### Technical Features

- **Monochrome Bold Typographic Design**: Clean, modern UI with high contrast
- **Static Site Generation**: Fast, SEO-friendly frontend
- **Async Database Operations**: Efficient SQLite database with SQLAlchemy
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
- **FastAPI**: High-performance Python web framework
- **SQLite**: Lightweight database for local development
- **SQLAlchemy**: Async ORM for database operations
- **Pydantic**: Data validation and settings management
- **uv**: Python package manager
- **Bun**: JavaScript runtime and package manager

### AI & Data
- **OpenRouter**: AI-powered explanation generation with gptoss-120b
- **Squiggle API**: AFL data source

## Installation

### Prerequisites

- **Bun** (JavaScript runtime and package manager)
- **uv** (Python package manager)
- **Python 3.11+**
- **Node.js 18+** (for Nuxt 4)

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
uv sync
cp .env.example .env
# Edit .env with your configuration
```

## Development

### Frontend Development

```bash
cd frontend
bun run dev
```

The frontend will be available at `http://localhost:3000`

### Backend Development

```bash
cd backend
uv run uvicorn main:app --reload
```

The backend API will be available at `http://localhost:8000`

API documentation: `http://localhost:8000/docs`

### Running Both

Use terminal multiplexer or run in separate terminals:

```bash
# Terminal 1 - Frontend
cd frontend && bun run dev

# Terminal 2 - Backend
cd backend && uv run uvicorn main:app --reload
```

## Configuration

### Environment Variables

**Backend ([`.env`](backend/.env.example:1))**:

```bash
# Database
DATABASE_URL=sqlite+aiosqlite:///./whatismytip.db

# API Configuration
API_HOST=0.0.0.0
API_PORT=8000
CORS_ORIGINS=http://localhost:3000,https://whatismytip.com

# Rate Limiting
RATE_LIMIT_PER_MINUTE=60

# Squiggle API
SQUIGGLE_API_BASE=https://api.squiggle.com.au

# OpenRouter (for explanation generation)
OPENROUTER_API_KEY=your_openrouter_api_key_here
OPENROUTER_MODEL=gptoss-120b

# Environment
ENVIRONMENT=development
```

**Frontend**:

```bash
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
├── backend/               # FastAPI backend
│   ├── main.py
│   ├── pyproject.toml
│   ├── uv.lock
│   ├── .env.example
│   └── app/
│       ├── api/           # API endpoints
│       ├── crud/          # Database operations
│       ├── db/            # Database sessions
│       ├── models/        # Database models
│       ├── models_ml/     # ML models
│       ├── heuristics/    # Heuristic layers
│       ├── openrouter/    # AI client
│       ├── schemas/       # Pydantic schemas
│       └── services/      # Business logic
├── docs/                  # Documentation
│   ├── backend.md         # Backend documentation
│   ├── frontend.md        # Frontend documentation
│   ├── deployment.md      # Deployment guide
│   ├── development.md     # Development guide
│   └── api.md             # API reference
├── CONTRIBUTING.md        # Contributing guidelines
├── README.md
└── LICENSE
```

## Documentation

Comprehensive documentation is available in the [`docs/`](docs/) directory:

- [`docs/backend.md`](docs/backend.md) - Backend architecture, models, and API
- [`docs/frontend.md`](docs/frontend.md) - Frontend structure and design system
- [`docs/deployment.md`](docs/deployment.md) - Deployment to Digital Ocean
- [`docs/development.md`](docs/development.md) - Local development setup
- [`docs/api.md`](docs/api.md) - Complete API reference

## Deployment

See [docs/deployment.md](docs/deployment.md) for detailed deployment instructions to Digital Ocean App Platform.

### Quick Deployment

**Backend**:
```bash
cd backend
uv run uvicorn main:app --host 0.0.0.0 --port 8000
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

The backend uses OpenRouter with the `gptoss-120b` model to generate AI-powered explanations for tips.

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
- [ ] Database migrations (Alembic)
- [ ] Redis caching layer
- [ ] Email notifications
- [ ] User favorites/bookmarks
- [ ] Mobile app (React Native)
- [ ] More ML models
- [ ] Betting odds integration
- [ ] Real-time notifications
- [ ] Advanced analytics dashboard

## Support

- Check the [documentation](docs/)
- Open an [issue](https://github.com/danielpaparo98/WhatIsMyTip/issues)
- Contact the development team

## Acknowledgments

- [Squiggle API](https://api.squiggle.com.au/) for AFL data
- [OpenRouter](https://openrouter.ai/) for AI model access
- [Nuxt](https://nuxt.com/) for the frontend framework
- [FastAPI](https://fastapi.tiangolo.com/) for the backend framework

## Authors

- [Daniel Paparo](https://github.com/danielpaparo98/WhatIsMyTip) - Initial work

## License

See [LICENSE](LICENSE) file for details.
