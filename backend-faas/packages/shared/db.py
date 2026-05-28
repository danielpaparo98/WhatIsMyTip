from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import declarative_base
from .config import settings

_engine = None
_async_session_factory = None

Base = declarative_base()


def get_engine():
    """Get or create the async engine (singleton pattern for FaaS cold starts).
    
    Uses conservative pool settings suitable for serverless/FaaS environments:
    - pool_size=2: Small base pool for limited FaaS memory
    - max_overflow=3: Allow brief spikes above base pool
    - pool_pre_ping=True: Verify connections before use
    - pool_recycle=300: Recycle connections every 5 minutes
    """
    global _engine
    if _engine is None:
        _engine = create_async_engine(
            settings.database_url,
            echo=settings.environment == "development",
            pool_size=2,
            max_overflow=3,
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


async def get_db():
    """Async generator that yields a database session.
    
    Use as a dependency injector for function handlers:
        async with get_db() as session:
            ...
    """
    factory = _get_session_factory()
    async with factory() as session:
        try:
            yield session
        finally:
            await session.close()


async def dispose_engine():
    """Dispose of the engine and its connection pool.
    
    Call this during FaaS runtime shutdown to cleanly release connections.
    """
    global _engine, _async_session_factory
    if _engine is not None:
        await _engine.dispose()
        _engine = None
        _async_session_factory = None
