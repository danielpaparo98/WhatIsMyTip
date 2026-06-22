from logging.config import fileConfig

from sqlalchemy import engine_from_config
from sqlalchemy import pool
from sqlalchemy import text

from alembic import context

# Import models and settings from the shared package.
# Run alembic from the `backend/` directory so that `packages` is on sys.path.
from packages.shared.config import settings
from packages.shared.db import Base
from packages.shared.models import (  # noqa: F401
    Game,
    Tip,
    ModelPrediction,
    BacktestResult,
    GenerationProgress,
    JobExecution,
    JobLock,
    EloCache,
    MatchAnalysis,
)

# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config

# Set the database URL from settings.
# Convert async URL to sync URL for migrations:
#   postgresql+asyncpg://... → postgresql://...
#
# LO-006: the ``+asyncpg`` driver suffix is stripped from the URL
# passed to Alembic.  Alembic uses the synchronous psycopg2 driver so
# the asyncpg URL form would be rejected.  The strip is intentional
# and required.
database_url = settings.database_url.replace("+asyncpg", "")
config.set_main_option("sqlalchemy.url", database_url)

# Interpret the config file for Python logging.
# This line sets up loggers basically.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# add your model's MetaData object here
# for 'autogenerate' support
target_metadata = Base.metadata

# other values from the config, defined by the needs of env.py,
# can be acquired:
# my_important_option = config.get_main_option("my_important_option")
# ... etc.


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode.

    This configures the context with just a URL
    and not an Engine, though an Engine is acceptable
    here as well.  By skipping the Engine creation
    we don't even need a DBAPI to be available.

    Calls to context.execute() here emit the given string to the
    script output.

    """
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def _ensure_alembic_version_table_width(connectable) -> None:
    """Ensure ``alembic_version.version_num`` can hold long revision ids.

    Alembic creates its bookkeeping table with ``version_num VARCHAR(32)``,
    but this repo's revision ids are longer than 32 characters (e.g.
    ``0003_job_executions_metrics_index`` is 34 chars,
    ``0005_model_versions_coefficients`` is 33).  On a from-scratch
    ``alembic upgrade head`` alembic would raise
    ``value too long for type character varying(32)`` the moment a long
    revision id is stamped -- which happens *before* migration 0006 (which
    widens the column) can ever run.

    This guard runs *before* ``context.run_migrations()`` and pre-empts
    that failure:

    * If the table is **missing** (the very first run), ``CREATE TABLE IF
      NOT EXISTS`` builds it with the wide column, so alembic's subsequent
      ``checkfirst=True`` reuses this table instead of recreating it as
      ``VARCHAR(32)``.  This is what actually fixes from-scratch upgrades.
    * If the table already exists, ``ALTER ... TYPE VARCHAR(128)`` widens
      it.

    Both statements are idempotent, so the guard is safe on every
    migration invocation -- a fresh DB, an already-widened prod DB, or a
    narrow dev DB.  No new dependencies; uses the project's ``text()``
    style throughout.
    """
    # The guard MUST run on its own connection and commit independently.
    # If it executed on Alembic's migration connection it would leave an
    # open transaction that Alembic's ``begin_transaction()`` treats as
    # externally-managed -- Alembic would then skip its own commit and
    # every migration would be silently rolled back on connection close.
    #
    # CREATE first (a no-op when the table already exists), then widen
    # (a no-op when the column is already VARCHAR(128)).  Idempotent.
    with connectable.connect() as connection:
        connection.execute(
            text(
                "CREATE TABLE IF NOT EXISTS alembic_version "
                "(version_num VARCHAR(128) NOT NULL)"
            )
        )
        connection.execute(
            text(
                "ALTER TABLE alembic_version "
                "ALTER COLUMN version_num TYPE VARCHAR(128)"
            )
        )
        connection.commit()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode.

    In this scenario we need to create an Engine
    and associate a connection with the context.

    """
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    # Ensure alembic_version.version_num is wide enough for this repo's
    # revision ids before any migration runs.  This is the fix for
    # from-scratch upgrades (migration 0006 alone cannot help because long
    # ids are stamped before 0006 executes).  The guard commits on its own
    # connection so it never disturbs Alembic's migration transaction.
    _ensure_alembic_version_table_width(connectable)

    with connectable.connect() as connection:
        context.configure(
            connection=connection, target_metadata=target_metadata
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
