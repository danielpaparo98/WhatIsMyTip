from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase
from .config import settings

_engine = None
_async_session_factory = None


class Base(DeclarativeBase):
    pass


def _normalize_async_url(url: str) -> str:
    """Normalize database URL for async drivers.

    asyncpg does not support ``sslmode`` as a query parameter; it requires
    ``ssl=require`` instead.  This helper converts the URL transparently.
    """
    if "+asyncpg" in url and "sslmode=" in url:
        url = url.replace("sslmode=require", "ssl=require")
    return url


def get_engine():
    """Get or create the async engine (singleton pattern for FaaS cold starts).

    Uses conservative pool settings suitable for serverless/FaaS environments:
    - pool_size=1: Each invocation only needs 1 connection
    - max_overflow=1: Allow 1 extra during brief spikes
    - pool_pre_ping=True: Verify connections before use
    - pool_recycle=300: Recycle connections every 5 minutes

    With 8 functions × (pool_size=1 + max_overflow=1) = 16 max connections,
    well within the ~25 connection limit of a dev database.
    """
    global _engine
    if _engine is None:
        db_url = _normalize_async_url(settings.database_url)
        _engine = create_async_engine(
            db_url,
            echo=settings.environment == "development",
            pool_size=1,
            max_overflow=1,
            pool_pre_ping=True,
            pool_recycle=300,
        )
    return _engine


def _get_session_factory():
    """Get or create the async session factory."""
    global _async_session_factory
    if _async_session_factory is None:
        engine = get_engine()
        _async_session_factory = async_sessionmaker(
            engine,
            class_=AsyncSession,
            expire_on_commit=False,
        )
    return _async_session_factory


async def dispose_engine(force: bool = False) -> None:
    """Dispose of the engine and its connection pool.

    Only actually disposes when ``force=True`` (i.e. on error). On normal
    completion the engine is kept alive so warm starts can reuse the pool.

    Args:
        force: When True, dispose and reset the engine. Defaults to False.
    """
    global _engine, _async_session_factory
    if _engine is not None and force:
        await _engine.dispose()
        _engine = None
        _async_session_factory = None
