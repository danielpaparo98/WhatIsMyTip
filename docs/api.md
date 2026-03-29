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
```

### Backtesting
```
GET /api/backtest
GET /api/backtest/{heuristic}
```

## Rate Limiting
60 requests per minute per IP address.
