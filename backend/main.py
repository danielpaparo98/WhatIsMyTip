from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.api import api_router
from app.db import get_db
from app.services.backtest import BacktestService

app = FastAPI(
    title="WhatIsMyTip API",
    description="AI-powered footy tipping API",
    version="0.1.0",
)


@app.on_event("startup")
async def startup_event():
    """Run pre-generation of backtest data on startup."""
    db_gen = get_db()
    db = await db_gen.__anext__()
    try:
        service = BacktestService()
        await service.pre_generate_all_seasons(db)
    except Exception as e:
        # Log error but don't fail startup
        print(f"Warning: Failed to pre-generate backtest data on startup: {e}")
    finally:
        await db_gen.aclose()

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
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
async def health():
    return {"status": "healthy"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host=settings.api_host, port=settings.api_port)
