#!/usr/bin/env python3
"""
Run database migrations and optionally seed data from CSV files.

Uses a connection string provided via CLI argument or DATABASE_URL env var.
Migrations are run via Alembic's Python API; seed data is loaded from the
``backend-faas/seed_data/`` directory.

Usage:
    # Run migrations only
    uv run python scripts/migrate_and_seed.py \\
        --database-url "postgresql://user:pass@localhost:5432/whatismytip"

    # Run migrations + seed CSV data
    uv run python scripts/migrate_and_seed.py \\
        --database-url "postgresql://user:pass@localhost:5432/whatismytip" \\
        --seed

    # Run migrations + seed with clear (wipe existing data first)
    uv run python scripts/migrate_and_seed.py \\
        --database-url "postgresql://user:pass@localhost:5432/whatismytip" \\
        --seed --clear

    # Use DATABASE_URL from env / .env (no --database-url flag needed)
    uv run python scripts/migrate_and_seed.py --seed
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Sequence

# ---------------------------------------------------------------------------
# Ensure ``packages.shared`` is importable when running from repo root or
# from the ``backend-faas/`` directory.
# ---------------------------------------------------------------------------
_BACKEND_FAAS_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_BACKEND_FAAS_DIR))

from alembic import command
from alembic.config import Config as AlembicConfig
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from packages.shared.db import Base
from packages.shared.models import (  # noqa: F401 – ensure models registered on Base
    BacktestResult,
    EloCache,
    Game,
    GenerationProgress,
    JobExecution,
    MatchAnalysis,
    ModelPrediction,
    Tip,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# CSV file → ORM class mapping, in foreign-key-safe insertion order.
_SEED_TABLES: List[Dict[str, object]] = [
    {"csv": "games.csv", "model": Game},
    {"csv": "model_predictions.csv", "model": ModelPrediction},
    {"csv": "tips.csv", "model": Tip},
    {"csv": "elo_cache.csv", "model": EloCache},
    {"csv": "backtest_results.csv", "model": BacktestResult},
    {"csv": "match_analyses.csv", "model": MatchAnalysis},
    {"csv": "generation_progress.csv", "model": GenerationProgress},
    {"csv": "job_executions.csv", "model": JobExecution},
]

# Tables to clear (reverse FK order).
_CLEAR_TABLES: List[str] = [
    "match_analyses",
    "tips",
    "model_predictions",
    "backtest_results",
    "generation_progress",
    "job_executions",
    "job_locks",
    "elo_cache",
    "games",
]

# Default seed-data directory.
_DEFAULT_SEED_DIR = _BACKEND_FAAS_DIR / "seed_data"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _to_async_url(url: str) -> str:
    """Ensure a PostgreSQL URL uses the ``asyncpg`` driver.

    Accepts both ``postgresql://...`` and ``postgresql+asyncpg://...`` forms.
    """
    if "+asyncpg" in url:
        return url
    return url.replace("postgresql://", "postgresql+asyncpg://", 1)


def _to_sync_url(url: str) -> str:
    """Strip the async driver so Alembic (sync) can use the URL."""
    return url.replace("+asyncpg", "")


def _build_alembic_config(sync_url: str) -> AlembicConfig:
    """Create an Alembic ``Config`` pointing at *sync_url*."""
    ini_path = _BACKEND_FAAS_DIR / "alembic.ini"
    cfg = AlembicConfig(str(ini_path))
    cfg.set_main_option("sqlalchemy.url", sync_url)
    # Ensure the script_location resolves correctly regardless of cwd.
    cfg.set_main_option(
        "script_location", str(_BACKEND_FAAS_DIR / "alembic")
    )
    return cfg


def _parse_value(col_type: object, raw: str):
    """Convert a CSV string value to the appropriate Python type."""
    if raw == "" or raw is None:
        return None

    # Import column types locally to avoid circular issues.
    from sqlalchemy import Boolean, Float, Integer

    if isinstance(col_type, Integer):
        return int(raw)
    if isinstance(col_type, Float):
        return float(raw)
    if isinstance(col_type, Boolean):
        return raw.lower() in ("true", "1", "yes")
    # String / Text / DateTime — return as-is (let SQLAlchemy handle).
    return raw


def _row_to_orm(model_cls: type, row: Dict[str, str]) -> object:
    """Convert a CSV row dict (all strings) into an ORM instance."""
    table = model_cls.__table__
    kwargs: Dict[str, object] = {}
    for col in table.columns:
        if col.key not in row:
            continue
        raw = row[col.key]
        if raw == "":
            # Let server defaults handle empty values.
            continue
        kwargs[col.key] = _parse_value(col.type, raw)
    return model_cls(**kwargs)


# ---------------------------------------------------------------------------
# Migration
# ---------------------------------------------------------------------------


def run_migrations(database_url: str, verbose: bool = False) -> None:
    """Run Alembic ``upgrade head`` against *database_url*.

    *database_url* should be a **sync** PostgreSQL URL
    (``postgresql://...``).
    """
    cfg = _build_alembic_config(database_url)
    if verbose:
        print(f"🔄 Running Alembic upgrade head on {database_url.split('@')[-1]} ...")
    command.upgrade(cfg, "head")
    if verbose:
        print("✅ Migrations complete.")


# ---------------------------------------------------------------------------
# Seeding
# ---------------------------------------------------------------------------


async def clear_database(async_url: str, verbose: bool = False) -> None:
    """Delete all rows from known tables (FK-safe order)."""
    engine = create_async_engine(async_url, pool_pre_ping=True)
    try:
        async with engine.begin() as conn:
            for table in _CLEAR_TABLES:
                await conn.execute(text(f"DELETE FROM {table}"))
        if verbose:
            print("🗑️  Existing data cleared.")
    finally:
        await engine.dispose()


async def seed_from_csv(
    async_url: str,
    seed_dir: Path,
    clear: bool = False,
    verbose: bool = False,
) -> Dict[str, int]:
    """Load CSV seed data into the database.

    Returns a dict mapping table names to the number of records inserted.
    """
    if clear:
        await clear_database(async_url, verbose=verbose)

    engine = create_async_engine(async_url, pool_pre_ping=True)
    session_factory = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )

    counts: Dict[str, int] = {}

    try:
        async with session_factory() as session:
            for entry in _SEED_TABLES:
                csv_name: str = entry["csv"]  # type: ignore[assignment]
                model_cls: type = entry["model"]  # type: ignore[assignment]
                csv_path = seed_dir / csv_name

                if not csv_path.exists():
                    if verbose:
                        print(f"⚠️  Skipping {csv_name} (file not found)")
                    continue

                rows: List[Dict[str, str]] = []
                with open(csv_path, newline="", encoding="utf-8") as fh:
                    reader = csv.DictReader(fh)
                    for row in reader:
                        # Filter out keys that aren't actual table columns.
                        col_keys = {c.key for c in model_cls.__table__.columns}
                        filtered = {k: v for k, v in row.items() if k in col_keys}
                        rows.append(filtered)

                if not rows:
                    if verbose:
                        print(f"⚠️  {csv_name} is empty — skipping")
                    continue

                objects = [_row_to_orm(model_cls, r) for r in rows]
                session.add_all(objects)
                await session.flush()

                table_name = model_cls.__tablename__
                counts[table_name] = len(objects)
                if verbose:
                    print(f"  ✅ {table_name}: {len(objects)} records from {csv_name}")

            await session.commit()

        if verbose:
            print("\n🌱 Seed complete! Summary:")
            for table, count in counts.items():
                print(f"   {table}: {count} records")

        return counts
    except Exception:
        await session.rollback()
        raise
    finally:
        await session.close()
        await engine.dispose()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _resolve_database_url(args: argparse.Namespace) -> str:
    """Determine the database URL from args or environment."""
    url: Optional[str] = args.database_url
    if url:
        return url

    # Try environment variable.
    url = os.environ.get("DATABASE_URL")
    if url:
        return url

    # Try loading from .env file (lightweight — no python-dotenv dependency).
    env_file = _BACKEND_FAAS_DIR / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("DATABASE_URL="):
                url = line.split("=", 1)[1].strip().strip("\"'")
                return url

    print(
        "❌ No database URL provided. Use --database-url or set DATABASE_URL.",
        file=sys.stderr,
    )
    sys.exit(1)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run database migrations and optionally seed CSV data.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            '  uv run python scripts/migrate_and_seed.py -d "postgresql://user:pass@localhost/db"\n'
            '  uv run python scripts/migrate_and_seed.py -d "postgresql://user:pass@localhost/db" --seed\n'
            '  uv run python scripts/migrate_and_seed.py -d "postgresql://user:pass@localhost/db" --seed --clear\n'
        ),
    )
    parser.add_argument(
        "-d",
        "--database-url",
        type=str,
        default=None,
        help=(
            "PostgreSQL connection string "
            '(e.g. "postgresql://user:pass@localhost:5432/whatismytip"). '
            "Falls back to DATABASE_URL env var or .env file."
        ),
    )
    parser.add_argument(
        "--seed",
        action="store_true",
        help="Load seed data from CSV files after running migrations.",
    )
    parser.add_argument(
        "--seed-dir",
        type=str,
        default=None,
        help=f"Directory containing CSV seed files (default: {_DEFAULT_SEED_DIR})",
    )
    parser.add_argument(
        "--clear",
        action="store_true",
        help="Clear existing data before seeding (only applies when --seed is used).",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Print progress to stdout.",
    )
    parser.add_argument(
        "--migrations-only",
        action="store_true",
        help="Run only migrations (equivalent to omitting --seed).",
    )
    args = parser.parse_args()

    raw_url = _resolve_database_url(args)
    sync_url = _to_sync_url(raw_url)
    async_url = _to_async_url(raw_url)
    seed_dir = Path(args.seed_dir) if args.seed_dir else _DEFAULT_SEED_DIR

    # --- Migrations ---
    run_migrations(sync_url, verbose=args.verbose)

    # --- Seeding (optional) ---
    if args.seed:
        if args.verbose:
            print(f"\n🌱 Loading seed data from {seed_dir} ...")
        asyncio.run(
            seed_from_csv(
                async_url,
                seed_dir=seed_dir,
                clear=args.clear,
                verbose=args.verbose,
            )
        )


if __name__ == "__main__":
    main()
