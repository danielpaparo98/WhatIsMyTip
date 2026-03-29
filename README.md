# WhatIsMyTip.com

AI-powered footy tipping with smart heuristics and data-driven predictions.

## Features

- **Multiple Picking Heuristics**: Choose from different prediction strategies
  - Best Bet: Conservative picks with high confidence
  - YOLO: High-risk, high-reward selections
  - High Risk High Reward: Balanced approach for adventurous tippers
- **Margin Calculations**: Predicted winning margins for each game
- **AI-Powered Explanations**: Get insights on why certain picks were made
- **Backtesting**: Analyze historical performance of different heuristics
- **Real-time Data**: Powered by the Squiggle API

## Tech Stack

### Frontend
- **Nuxt 4**: Modern Vue.js framework with static site generation
- **Tailwind CSS**: Utility-first CSS framework
- **TypeScript**: Type-safe development

### Backend
- **FastAPI**: High-performance Python web framework
- **SQLite**: Lightweight database for local development
- **SQLAlchemy**: Async ORM for database operations
- **Pydantic**: Data validation and settings management
- **OpenAI**: AI-powered explanation generation

## Installation

### Prerequisites
- **Bun** (JavaScript runtime and package manager)
- **UV** (Python package manager)
- **Python 3.11+**

### Frontend Setup

```bash
cd frontend
bun install
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

## Deployment

### Frontend Deployment
Generate static files:
```bash
cd frontend
bun run generate
```

Deploy the `.output/public` directory to your hosting provider.

### Backend Deployment
Run with uvicorn:
```bash
cd backend
uv run uvicorn main:app --host 0.0.0.0 --port 8000
```

## API Documentation

See [docs/api.md](docs/api.md) for detailed API documentation.

## Data Source

This project uses the [Squiggle API](https://api.squiggle.com.au/) for AFL data including fixtures, results, and team information. Special thanks to the Squiggle team for providing this valuable resource.

## License

This project is licensed under the terms of the LICENSE file in the root directory.

## Contributing

Contributions are welcome! Please feel free to submit a Pull Request.
