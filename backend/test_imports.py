"""Test imports from main.py to find what's hanging."""
import asyncio
from fastapi import FastAPI, Request, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response

print("[OK] FastAPI imports successful")

# Test slowapi imports
try:
    from slowapi import Limiter, _rate_limit_exceeded_handler
    from slowapi.util import get_remote_address
    from slowapi.errors import RateLimitExceeded
    print("[OK] SlowAPI imports successful")
except Exception as e:
    print(f"[FAIL] SlowAPI imports failed: {e}")

# Test app config imports
from sqlalchemy.ext.asyncio import AsyncSession
print("[OK] SQLAlchemy imports successful")

from app.config import settings
print("[OK] Config import successful")

from app.api import api_router
print("[OK] API router import successful")

from app.db import get_db
print("[OK] DB import successful")

from app.services.backtest import BacktestService
print("[OK] BacktestService import successful")

from app.logger import get_logger
print("[OK] Logger import successful")

logger = get_logger(__name__)
print("[OK] Logger initialized")

print("\n[OK] All imports successful!")
