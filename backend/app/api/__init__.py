from fastapi import APIRouter
from .games import router as games_router
from .tips import router as tips_router
from .backtest import router as backtest_router
from .sync import router as sync_router
from .admin import jobs_router as admin_jobs_router

api_router = APIRouter(prefix="/api")

api_router.include_router(games_router, prefix="/games", tags=["games"])
api_router.include_router(tips_router, prefix="/tips", tags=["tips"])
api_router.include_router(backtest_router, prefix="/backtest", tags=["backtest"])
api_router.include_router(sync_router, prefix="/sync", tags=["sync"])
api_router.include_router(admin_jobs_router, prefix="/admin/jobs", tags=["admin-jobs"])
