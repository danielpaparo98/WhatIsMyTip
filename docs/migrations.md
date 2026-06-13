# Database Migrations with Alembic

This document explains how to use Alembic for database migrations in the WhatIsMyTip project.

## Overview

Alembic is a database migration tool for SQLAlchemy that provides:
- **Version control** for database schema changes
- **Upgrade and downgrade** capabilities
- **Auto-generation** of migrations from model changes
- **Tracking** of migration history

The WhatIsMyTip backend uses **PostgreSQL** (via the asyncpg driver) with Alembic for all schema management.

## Setup

The Alembic configuration is located in the `backend/` directory:

- [`alembic.ini`](../backend/alembic.ini) — Main configuration file
- [`alembic/env.py`](../backend/alembic/env.py) — Environment configuration (imports models, sets up database URL)
- [`alembic/versions/`](../backend/alembic/versions/) — Migration scripts

### Database Models

Database models are defined in [`backend/packages/shared/models/`](../backend/packages/shared/models/__init__.py). Alembic's autogenerate feature compares these models against the current database schema to detect changes.

## Common Commands

All Alembic commands should be run from the `backend/` directory:

```bash
cd backend
uv run alembic <command>
```

### Viewing Migration Status

```bash
# Show current migration status
uv run alembic current

# Show migration history
uv run alembic history

# Show migration history with more detail
uv run alembic history --verbose
```

### Creating Migrations

#### Auto-generate from Model Changes

When you modify SQLAlchemy models, auto-generate a migration:

```bash
uv run alembic revision --autogenerate -m "Description of changes"
```

This will:
1. Compare current models against the database schema
2. Generate a new migration script in `alembic/versions/`
3. Name the file with a timestamp and your message

**Important:** Always review the generated migration script before applying it!

#### Manual Migration

For complex changes that auto-generation can't handle:

```bash
uv run alembic revision -m "Description of changes"
```

This creates a blank migration file that you can manually edit with SQL operations.

### Applying Migrations

#### Upgrade to Latest

```bash
# Apply all pending migrations
uv run alembic upgrade head
```

#### Upgrade to Specific Version

```bash
# Upgrade to a specific revision
uv run alembic upgrade <revision_id>
```

#### Downgrade

```bash
# Rollback one migration
uv run alembic downgrade -1

# Rollback to a specific revision
uv run alembic downgrade <revision_id>

# Rollback to base (remove all migrations)
uv run alembic downgrade base
```

### Stamping

If your database already has tables (like in production), use stamping to mark migrations as applied without running them:

```bash
# Mark current state as latest migration
uv run alembic stamp head

# Mark current state as specific revision
uv run alembic stamp <revision_id>
```

---

## Migration History

The project uses a **consolidated baseline** rather than individual historical migrations. The old SQLite-era incremental migrations have been replaced by a single comprehensive PostgreSQL baseline.

### Current Migrations

| Order | Revision | File | Description |
|-------|----------|------|-------------|
| 1 | `0001` | [`0001_consolidated_postgresql_schema.py`](../backend/alembic/versions/2026_05_28_1613-0001_consolidated_postgresql_schema.py) | Full PostgreSQL schema baseline (all tables, indexes, constraints) |
| 2 | `0002` | [`0002_weather_players_injuries.py`](../backend/alembic/versions/2026_06_10_0600-0002_weather_players_injuries.py) | Weather, player, and injury tracking tables |

### Migration 0001: Consolidated PostgreSQL Schema

This baseline migration creates the complete schema for the FaaS backend, including:

- **games** — AFL match data (teams, round, venue, scores, status, sync tracking)
- **tips** — Generated tips (team, confidence, margin, heuristic)
- **model_predictions** — Individual model predictions per game
- **match_analysis** — Detailed match analysis (weather, injuries, player data)
- **job_executions** — Cron job execution records (status, timing, error details)
- **job_locks** — Advisory locks for preventing concurrent job runs
- **elo_cache** — Cached Elo ratings for teams
- **generation_progress** — Tip generation progress tracking
- **backtest_results** — Backtesting performance metrics

All indexes and constraints are included in the consolidated migration.

### Migration 0002: Weather, Players & Injuries

Adds tables for:
- **weather_data** — Match-day weather conditions
- **players** — Player roster information
- **player_stats** — Individual player performance metrics
- **injuries** — Team injury lists and player availability

---

## Migration Workflow

### Development Workflow

1. **Start local PostgreSQL** via Docker:
   ```bash
   cd backend
   ./scripts/dev.sh
   ```

2. **Make model changes** in [`backend/packages/shared/models/`](../backend/packages/shared/models/__init__.py)

3. **Generate migration**:
   ```bash
   cd backend
   uv run alembic revision --autogenerate -m "Add new column to games table"
   ```

4. **Review the generated migration** in `backend/alembic/versions/`

5. **Apply migration**:
   ```bash
   uv run alembic upgrade head
   ```

6. **Test your changes**:
   ```bash
   uv run pytest tests/unit/ -v
   ```

### Production Workflow

1. **Generate migration** during development (as above)
2. **Test migration** against a staging database
3. **Commit migration files** to version control
4. **Run migration before deploying functions**:
   ```bash
   cd backend
   export DATABASE_URL="postgresql+asyncpg://doadmin:<password>@host:25060/defaultdb?ssl=require"
   uv run alembic upgrade head
   ```
5. **Deploy functions** via `./scripts/deploy.sh`

> **Important**: Always run migrations **before** deploying updated functions that depend on the new schema.

---

## Database URL Configuration

The database URL is configured via environment variables and loaded by [`config.py`](../backend/packages/shared/config.py:1). Alembic reads the `DATABASE_URL` environment variable (or `.env` file).

### Local Development

```bash
# Using Docker (dev.sh sets up PostgreSQL on localhost:5432)
export DATABASE_URL="postgresql+asyncpg://whatismytip:whatismytip@localhost:5432/whatismytip"
```

### Production (Managed PostgreSQL)

```bash
# Managed PostgreSQL requires SSL
export DATABASE_URL="postgresql+asyncpg://doadmin:<password>@db-postgresql-xxx.db.ondigitalocean.com:25060/defaultdb?ssl=require"
```

> **Note**: The `?ssl=require` parameter is **required** for DigitalOcean managed PostgreSQL.

---

## Migration File Structure

Each migration file has two main functions:

```python
def upgrade() -> None:
    """Apply the migration."""
    # Operations to apply the changes

def downgrade() -> None:
    """Rollback the migration."""
    # Operations to undo the changes
```

**Always ensure the `downgrade()` function properly reverses the `upgrade()` function!**

---

## Best Practices

1. **Review auto-generated migrations** — Auto-generation isn't perfect. Always check:
   - Indexes are created/dropped correctly
   - Foreign keys are handled properly
   - Data migrations are included if needed

2. **Write descriptive messages** — Use clear, descriptive messages:
   ```bash
   uv run alembic revision --autogenerate -m "Add betting_odds column to games table"
   ```

3. **Test downgrades** — Ensure you can rollback migrations:
   ```bash
   uv run alembic downgrade -1
   uv run alembic upgrade head
   ```

4. **Don't modify existing migrations** — If you need to change something, create a new migration instead.

5. **Keep migrations reversible** — Always implement the `downgrade()` function.

6. **Handle data migrations separately** — For complex data changes, consider using a separate script or adding data migration steps in the migration.

7. **Run migrations before deploying** — The `deploy.sh` script runs migrations automatically, but you can run them manually first.

---

## Troubleshooting

### Migration Already Applied

```bash
# Check current version
uv run alembic current

# Stamp to the correct version
uv run alembic stamp <revision_id>
```

### Database Out of Sync

```bash
# Generate a migration from the current state
uv run alembic revision --autogenerate -m "Fix schema sync"

# Review and apply the migration
```

### Conflicting Migrations

If you have merge conflicts in migration files:

1. Resolve the conflicts in the migration file
2. Ensure the `down_revision` points to the correct parent
3. Test the migration

### Connection Errors

If migrations fail with connection errors:

```bash
# Verify DATABASE_URL is set
echo $DATABASE_URL

# Ensure local PostgreSQL is running
cd backend && ./scripts/dev.sh

# For production, verify ?ssl=require is in the URL
```

### Rollback Procedures

```bash
# Rollback one migration
uv run alembic downgrade -1

# Rollback all migrations
uv run alembic downgrade base
```

For managed PostgreSQL, use the DigitalOcean dashboard to restore from a backup point if needed.

---

## Additional Resources

- [Alembic Documentation](https://alembic.sqlalchemy.org/)
- [SQLAlchemy Documentation](https://docs.sqlalchemy.org/)
- [Alembic Tutorial](https://alembic.sqlalchemy.org/en/latest/tutorial.html)
- [Backend Architecture](backend.md)
- [Deployment Guide](deployment.md)
