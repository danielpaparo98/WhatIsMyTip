# WhatIsMyTip API Documentation

## Overview
RESTful API for footy tipping predictions and backtesting.

## Base URL
```
http://localhost:8000
```

## Authentication
Public API with rate limiting.

## Endpoints

### Health Check
```
GET /health
```

### Games
```
GET /api/games
GET /api/games/{game_id}
```

### Tips
```
GET /api/tips
GET /api/tips/{heuristic}
POST /api/tips/generate
POST /api/tips/explanations/generate
```

**Generate Tips:**
```bash
POST /api/tips/generate?season=2025&round=1&heuristics=best_bet,yolo&generate_explanations=true
```

**Generate Explanations:**
```bash
POST /api/tips/explanations/generate?season=2025&round=1
```

### Backtesting
```
GET /api/backtest
GET /api/backtest/{heuristic}
```

## Rate Limiting
60 requests per minute per IP address.
