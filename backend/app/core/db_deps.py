"""Database session dependency for FastAPI routes.

Provides :func:`get_db`, a generator-based dependency that yields an
:class:`AsyncSession` and ensures it is closed after the request
completes (regardless of success or failure).  Mirrors the
``yield session`` pattern recommended by the SQLAlchemy 2.0 async docs.

The dependency is intentionally a thin wrapper around
:func:`packages.shared.db.get_session` so that all session lifecycle
behaviour (engine pooling, factory reuse) stays in one place.
"""

from __future__ import annotations

from typing import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession

from packages.shared.db import get_session


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """Yield a database session for the duration of a single request.

    Use as a FastAPI dependency:

        @router.get(...)
        async def handler(db: AsyncSession = Depends(get_db)):
            ...

    The session is closed when the request finishes; any uncommitted
    transaction is rolled back implicitly by :meth:`AsyncSession.close`
    on a session that was never committed.
    """
    session = get_session()
    try:
        yield session
    finally:
        await session.close()
