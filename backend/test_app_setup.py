"""Test app setup progressively to find what's hanging."""
import asyncio
from fastapi import FastAPI, Request, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.api import api_router
from app.db import get_db
from app.services.backtest import BacktestService
from app.logger import get_logger

logger = get_logger(__name__)

print("[OK] All imports successful")

# Create app
app = FastAPI(
    title="WhatIsMyTip API",
    description="AI-powered footy tipping API",
    version="0.1.0",
)
print("[OK] FastAPI app created")

# Add CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
print("[OK] CORS middleware added")

# Add rate limiter
limiter = Limiter(key_func=get_remote_address)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
print("[OK] Rate limiter added")

# Include API routes
app.include_router(api_router)
print("[OK] API routes included")

print("\n[OK] App setup complete!")
