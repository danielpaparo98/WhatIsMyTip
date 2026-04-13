from fastapi import FastAPI, Request, HTTPException, Depends
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
from app.cron import init_cron_manager, get_cron_manager

logger = get_logger(__name__)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Middleware to add security headers to all responses."""
    
    async def dispatch(self, request: Request, call_next):
        response: Response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        csp = (
            "default-src 'self'; "
            "script-src 'self' https://analytics.whatismytip.com; "
            "img-src 'self' data: https:; "
            "style-src 'self' 'unsafe-inline'; "
            "connect-src 'self'; "
            "font-src 'self'; "
            "frame-ancestors 'none'"
        )
        response.headers["Content-Security-Policy"] = csp
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        response.headers["Cross-Origin-Opener-Policy"] = "same-origin"
        return response


class RequestSizeLimitMiddleware(BaseHTTPMiddleware):
    """Middleware to limit request size."""
    
    def __init__(self, app, max_size: int = 10 * 1024 * 1024):
        super().__init__(app)
        self.max_size = max_size
    
    async def dispatch(self, request: Request, call_next):
        content_length = request.headers.get("content-length")
        if content_length and int(content_length) > self.max_size:
            raise HTTPException(status_code=413, detail="Request too large")
        return await call_next(request)

app = FastAPI(
    title="WhatIsMyTip API",
    description="AI-powered footy tipping API",
    version="0.1.0",
    docs_url=None if settings.environment == "production" else "/docs",
    redoc_url=None if settings.environment == "production" else "/redoc",
    openapi_url=None if settings.environment == "production" else "/openapi.json",
)


# Initialize CronJobManager
cron_mgr = init_cron_manager(app)


@app.on_event("startup")
async def startup_event():
    """Initialize cron jobs on startup."""
    try:
        await cron_mgr.register_jobs()
        logger.info("Cron jobs registered successfully")
    except Exception as e:
        logger.error(f"Failed to register cron jobs: {e}")

# Security headers middleware
app.add_middleware(SecurityHeadersMiddleware)

# Request size limit middleware (5MB)
app.add_middleware(RequestSizeLimitMiddleware, max_size=5 * 1024 * 1024)

# CORS middleware - restrictive configuration
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=False,
    allow_methods=["GET", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization"],
)

# Rate limiting
limiter = Limiter(key_func=get_remote_address)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# Include API routes
app.include_router(api_router)


@app.get("/")
@limiter.limit("60/minute")
async def root(request: Request):
    return {
        "message": "WhatIsMyTip API",
        "version": "0.1.0",
        "docs": "/docs",
    }


@app.get("/health")
@limiter.limit("60/minute")
async def health(request: Request):
    """Health check endpoint with database connectivity check."""
    # Check database connectivity
    try:
        from sqlalchemy import text
        from app.db import AsyncSessionLocal
        
        async with AsyncSessionLocal() as session:
            await session.execute(text("SELECT 1"))
        db_status = "healthy"
    except Exception as e:
        logger.error(f"Health check failed: {e}")
        db_status = "unhealthy"
    
    return {
        "status": "healthy" if db_status == "healthy" else "degraded",
        "database": db_status,
        "version": "1.0.0"
    }


@app.get("/health/cron")
@limiter.limit("60/minute")
async def cron_health(request: Request, db: AsyncSession = Depends(get_db)):
    """Health check endpoint for cron jobs."""
    try:
        health_status = await cron_mgr.get_health(db)
        return health_status
    except Exception as e:
        logger.error(f"Cron health check failed: {e}")
        return {
            "status": "unhealthy",
            "timestamp": None,
            "jobs": [],
            "database": "unknown",
            "cron_enabled": settings.cron_enabled
        }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host=settings.api_host, port=settings.api_port)
