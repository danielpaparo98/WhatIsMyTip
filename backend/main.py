from fastapi import FastAPI, Request, HTTPException
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


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Middleware to add security headers to all responses."""
    
    async def dispatch(self, request: Request, call_next):
        response: Response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        response.headers["Content-Security-Policy"] = "default-src 'self'"
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
)


# Temporarily disabled startup event to allow server to start
# @app.on_event("startup")
# async def startup_event():
#     """Run pre-generation of backtest data on startup."""
#     db_gen = get_db()
#     db = await db_gen.__anext__()
#     try:
#         service = BacktestService()
#         await service.pre_generate_all_seasons(db)
#     except Exception as e:
#         # Log error but don't fail startup
#         logger.warning(f"Failed to pre-generate backtest data on startup: {e}")
#     finally:
#         await db_gen.aclose()

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


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host=settings.api_host, port=settings.api_port)
