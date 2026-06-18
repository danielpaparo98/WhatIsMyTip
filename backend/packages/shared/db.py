import os
import ssl as _ssl

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from .config import settings


# Truthy parser for the DB_SSL_VERIFY env var.  We need to read the
# env var at call time (not import time) so that tests can ``monkeypatch.setenv``
# to flip the value, and so that the engine picks up changes made
# after the ``Settings`` singleton was constructed.
_TRUTHY = frozenset({"1", "true", "yes", "on"})
_FALSY = frozenset({"0", "false", "no", "off", ""})


def _resolve_db_ssl_verify() -> bool:
    """Return the effective value of the ``DB_SSL_VERIFY`` env var.

    Reads the env var at call time so tests can ``monkeypatch.setenv``
    and have the change picked up.  Defaults to ``True`` (production
    behaviour) when the variable is unset.
    """
    raw = os.environ.get("DB_SSL_VERIFY")
    if raw is None:
        return True
    normalized = raw.strip().lower()
    if normalized in _TRUTHY:
        return True
    if normalized in _FALSY:
        return False
    # Unknown values fall back to safe (verify ON) to avoid silently
    # disabling TLS verification in production.
    return True

_engine = None
_async_session_factory = None


class Base(DeclarativeBase):
    pass


def _normalize_async_url(url: str) -> tuple[str, dict]:
    """Normalize database URL for async drivers.

    asyncpg does not support ``sslmode`` or ``ssl`` as query parameters in the
    DSN string in some versions.  This helper strips SSL params from the URL
    and returns them as ``connect_args`` for ``create_async_engine`` instead.

    SSL verification is governed by ``settings.db_ssl_verify`` (env var
    ``DB_SSL_VERIFY``, default ``True``).  When the flag is on — the
    production default — the returned ``SSLContext`` is built with the
    system trust store, ``verify_mode=CERT_REQUIRED`` and
    ``check_hostname=True``, so the engine refuses any peer whose
    certificate is not signed by a trusted CA.  Setting
    ``DB_SSL_VERIFY=false`` is an explicit opt-out for local
    development against a Postgres container with a self-signed cert.

    Returns:
        Tuple of (clean_url, connect_args) where connect_args includes ssl ctx
        if SSL was specified in the URL.
    """
    connect_args: dict = {}

    if "+asyncpg" not in url:
        return url, connect_args

    # Detect if SSL is requested
    needs_ssl = "sslmode=require" in url or "ssl=require" in url

    if needs_ssl:
        # Strip ssl/sslmode params from URL
        clean_url = url
        if "?" in clean_url:
            base_url, query = clean_url.split("?", 1)
            params = [
                p for p in query.split("&")
                if not p.startswith("sslmode=") and not p.startswith("ssl=")
            ]
            clean_url = base_url + ("?" + "&".join(params) if params else "")

        # Build the SSL context.  ``create_default_context`` loads the
        # system trust store, which is the correct baseline for managed
        # Postgres (DigitalOcean, RDS, etc.).  Verification is on by
        # default; DB_SSL_VERIFY=false is the explicit local-dev opt-out.
        #
        # Note: ``check_hostname`` MUST be cleared before
        # ``verify_mode=CERT_NONE`` — CPython raises ``ValueError`` if
        # you set the verify mode to NONE while hostname checking is
        # still on.  We therefore set ``check_hostname`` first.
        ssl_ctx = _ssl.create_default_context()
        if _resolve_db_ssl_verify():
            ssl_ctx.verify_mode = _ssl.CERT_REQUIRED
            ssl_ctx.check_hostname = True
        else:
            ssl_ctx.check_hostname = False
            ssl_ctx.verify_mode = _ssl.CERT_NONE
        connect_args["ssl"] = ssl_ctx

        return clean_url, connect_args

    return url, connect_args


def get_engine():
    """Get or create the async engine (singleton pattern for FaaS cold starts).

    Pool settings (ME-005) are read from ``Settings`` so they can be
    tuned per environment without code changes:

    - ``db_pool_size``  : persistent connections kept in the pool
      (default 5)
    - ``db_max_overflow``: extra connections allowed during spikes
      (default 10)
    - ``db_pool_timeout``: seconds to wait for a free connection
      before raising ``TimeoutError`` (default 30)
    - pool_pre_ping=True: Verify connections before use
    - pool_recycle=300: Recycle connections every 5 minutes

    The defaults are conservative for a single-tenant FaaS workload
    but can be raised via ``DB_POOL_SIZE`` / ``DB_MAX_OVERFLOW`` /
    ``DB_POOL_TIMEOUT`` env vars in heavier deployments.
    """
    global _engine
    if _engine is None:
        db_url, connect_args = _normalize_async_url(settings.database_url)
        _engine = create_async_engine(
            db_url,
            connect_args=connect_args,
            echo=settings.environment == "development",
            pool_size=settings.db_pool_size,
            max_overflow=settings.db_max_overflow,
            pool_timeout=settings.db_pool_timeout,
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


def get_session() -> AsyncSession:
    """Return a new :class:`AsyncSession` from the shared factory.

    Used as a FastAPI dependency via :mod:`app.core.db_deps` so that route
    handlers can write ``db: AsyncSession = Depends(get_session)`` and have
    the session lifecycle managed by FastAPI.

    The caller is responsible for committing/rolling back and closing the
    session (use :func:`get_db` in :mod:`app.core.db_deps` for the standard
    FastAPI generator-based pattern).
    """
    return _get_session_factory()()


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
