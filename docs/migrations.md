# Database Migrations with Alembic

This document explains how to use Alembic for database migrations in the WhatIsMyTip project.

## Overview

Alembic is a database migration tool for SQLAlchemy that provides:
- **Version control** for database schema changes
- **Upgrade and downgrade** capabilities
- **Auto-generation** of migrations from model changes
- **Tracking** of migration history

The WhatIsMyTip project uses Alembic for all database schema changes, including the new cron-based data collection system.

## Setup

The Alembic configuration is located in the `backend/alembic/` directory:

- [`alembic.ini`](../backend/alembic.ini) - Main configuration file
- [`alembic/env.py`](../backend/alembic/env.py) - Environment configuration (imports models, sets up database URL)
- [`alembic/versions/`](../backend/alembic/versions/) - Migration scripts

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

## Migration Workflow

### Development Workflow

1. **Make model changes** in [`backend/app/models/`](../backend/app/models/)
2. **Generate migration**:
   ```bash
   cd backend
   uv run alembic revision --autogenerate -m "Add new column to games table"
   ```
3. **Review the generated migration** in `backend/alembic/versions/`
4. **Apply migration**:
   ```bash
   uv run alembic upgrade head
   ```
5. **Test your changes**

### Production Workflow

1. **Generate migration** during development (as above)
2. **Test migration** on a staging database
3. **Commit migration files** to version control
4. **Deploy** and run migration:
   ```bash
   cd backend
   uv run alembic upgrade head
   ```

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

## Best Practices

1. **Review auto-generated migrations** - Auto-generation isn't perfect. Always check:
   - Indexes are created/dropped correctly
   - Foreign keys are handled properly
   - Data migrations are included if needed

2. **Write descriptive commit messages** - Use clear, descriptive messages for migrations:
   ```bash
   uv run alembic revision --autogenerate -m "Add betting_odds column to games table"
   ```

3. **Test downgrades** - Ensure you can rollback migrations:
   ```bash
   uv run alembic downgrade -1
   uv run alembic upgrade head
   ```

4. **Don't modify existing migrations** - If you need to change something, create a new migration instead.

5. **Keep migrations reversible** - Always implement the `downgrade()` function.

6. **Handle data migrations separately** - For complex data changes, consider using a separate script or adding data migration steps in the migration.

## Troubleshooting

### Migration Already Applied

If you get an error that a migration is already applied:

```bash
# Check current version
uv run alembic current

# Stamp to the correct version
uv run alembic stamp <revision_id>
```

### Database Out of Sync

If your database schema doesn't match the models:

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

## Database URL Configuration

The database URL is configured in [`backend/app/config.py`](../backend/app/config.py) and automatically loaded by Alembic. For different environments, set the `DATABASE_URL` environment variable:

```bash
# Development (default)
export DATABASE_URL="sqlite+aiosqlite:///./whatismytip.db"

# Production
export DATABASE_URL="postgresql+asyncpg://user:pass@host/db"
```

## Cron System Migrations

The cron-based data collection system includes several database migrations. These migrations add new tables and columns to support job execution tracking, locking, and data synchronization.

### Migration Files

#### 1. Add Cron Job Tables ([`2026_04_02_1330-9a1b2c3d4e5f_add_cron_job_tables.py`](../backend/alembic/versions/2026_04_02_1330-9a1b2c3d4e5f_add_cron_job_tables.py))

**Purpose**: Create tables for tracking cron job executions and managing job locks.

**Tables Created**:
- `job_executions` - Tracks execution history of all cron jobs
- `job_locks` - Prevents concurrent execution of the same job
- `elo_cache` - Persists Elo ratings cache for faster initialization

**Key Operations**:
```python
# Create job_executions table
CREATE TABLE job_executions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_name VARCHAR(100) NOT NULL,
    status VARCHAR(20) NOT NULL,
    started_at DATETIME NOT NULL,
    completed_at DATETIME,
    duration_seconds FLOAT,
    items_processed INTEGER DEFAULT 0,
    items_succeeded INTEGER DEFAULT 0,
    items_failed INTEGER DEFAULT 0,
    error_message TEXT,
    error_details TEXT,
    metadata TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

# Create job_locks table
CREATE TABLE job_locks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_name VARCHAR(100) NOT NULL UNIQUE,
    locked_at DATETIME NOT NULL,
    locked_by VARCHAR(100),
    expires_at DATETIME NOT NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

# Create elo_cache table
CREATE TABLE elo_cache (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    team_name VARCHAR(100) NOT NULL UNIQUE,
    rating FLOAT NOT NULL,
    games_played INTEGER DEFAULT 0,
    last_updated DATETIME NOT NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);
```

**Indexes Created**:
- `idx_job_executions_job_name` on `job_executions(job_name)`
- `idx_job_executions_status` on `job_executions(status)`
- `idx_job_executions_started_at` on `job_executions(started_at)`
- `idx_job_executions_job_started` on `job_executions(job_name, started_at)`
- `idx_job_locks_expires_at` on `job_locks(expires_at)`
- `idx_elo_cache_team_name` on `elo_cache(team_name)`

#### 2. Add Sync Tracking to Games ([`2026_04_02_2143-ef5dc0ca76d2_add_sync_tracking_to_games.py`](../backend/alembic/versions/2026_04_02_2143-ef5dc0ca76d2_add_sync_tracking_to_games.py))

**Purpose**: Add columns to track game synchronization status.

**Columns Added**:
- `last_synced_at` - Track when the game was last synced from Squiggle
- `sync_count` - Track number of syncs for this game
- `sync_version` - Version of sync data

**Operations**:
```python
ALTER TABLE games ADD COLUMN last_synced_at DATETIME;
ALTER TABLE games ADD COLUMN sync_count INTEGER DEFAULT 0;
ALTER TABLE games ADD COLUMN sync_version INTEGER DEFAULT 1;
```

#### 3. Add Job Execution Tracking to Generation Progress ([`2026_03_31_1831-ddf16b980603_add_generation_progress_table.py`](../backend/alembic/versions/2026_03_31_1831-ddf16b980603_add_generation_progress_table.py))

**Purpose**: Enhance generation progress tracking with job execution context.

**Columns Added**:
- `job_execution_id` - Link to job execution record
- `current_item` - Track current item being processed
- `estimated_remaining_seconds` - Estimated time remaining

**Operations**:
```python
ALTER TABLE generation_progress ADD COLUMN job_execution_id INTEGER;
ALTER TABLE generation_progress ADD COLUMN current_item INTEGER;
ALTER TABLE generation_progress ADD COLUMN estimated_remaining_seconds INTEGER;
```

### Migration Order and Dependencies

The migrations must be applied in the following order:

1. **First**: [`2026_03_31_1756-a1b2c3d4e5f_add_status_tracking_to_games.py`](../backend/alembic/versions/2026_03_31_1756-a1b2c3d4e5f_add_status_tracking_to_games.py)
   - Adds status tracking to games table

2. **Second**: [`2026_03_31_1831-ddf16b980603_add_generation_progress_table.py`](../backend/alembic/versions/2026_03_31_1831-ddf16b980603_add_generation_progress_table.py)
   - Creates generation_progress table

3. **Third**: [`2026_04_02_1330-9a1b2c3d4e5f_add_cron_job_tables.py`](../backend/alembic/versions/2026_04_02_1330-9a1b2c3d4e5f_add_cron_job_tables.py)
   - Creates cron job tables (job_executions, job_locks, elo_cache)

4. **Fourth**: [`2026_04_02_2143-ef5dc0ca76d2_add_sync_tracking_to_games.py`](../backend/alembic/versions/2026_04_02_2143-ef5dc0ca76d2_add_sync_tracking_to_games.py)
   - Adds sync tracking columns to games table

### Applying Migrations in Production

#### Step 1: Backup Database

```bash
cd backend
sqlite3 whatismytip.db ".backup whatismytip_backup_$(date +%Y%m%d_%H%M%S).db"
```

#### Step 2: Apply All Migrations

```bash
cd backend
uv run alembic upgrade head
```

#### Step 3: Verify Migration Status

```bash
# Check current version
uv run alembic current

# View migration history
uv run alembic history
```

#### Step 4: Test Application

```bash
# Start the application
uv run uvicorn main:app --host 0.0.0.0 --port 8000

# Test health check
curl http://localhost:8000/api/health/cron

# Verify cron jobs are running
curl http://localhost:8000/api/health/cron
```

#### Step 5: Monitor for Issues

After migration, monitor:
- Cron job execution logs
- Application logs for errors
- Database performance
- Job execution history

### Rollback Procedures

#### Rollback to Previous Migration

```bash
cd backend
uv run alembic downgrade -1
```

#### Rollback All Migrations

```bash
cd backend
uv run alembic downgrade base
```

#### Restore from Backup

```bash
cd backend
cp whatismytip_backup_YYYYMMDD_HHMMSS.db whatismytip.db
```

### Migration Best Practices

1. **Always Backup**: Create a database backup before applying migrations
2. **Test in Staging**: Apply migrations to a staging environment first
3. **Review Generated SQL**: Always review the generated migration files
4. **Test Downgrades**: Ensure downgrade scripts work correctly
5. **Monitor After Migration**: Watch for errors and performance issues
6. **Document Changes**: Keep track of what each migration does
7. **Use Descriptive Names**: Use clear, descriptive migration names

### Troubleshooting Migrations

#### Migration Already Applied

If you get an error that a migration is already applied:

```bash
# Check current version
uv run alembic current

# Stamp to the correct version
uv run alembic stamp <revision_id>
```

#### Database Schema Out of Sync

If your database schema doesn't match the models:

```bash
# Generate a migration from the current state
uv run alembic revision --autogenerate -m "Fix schema sync"

# Review and apply the migration
uv run alembic upgrade head
```

#### Migration Fails During Apply

1. Check the error message for specific details
2. Review the migration file to understand what's failing
3. Fix any issues in the migration
4. Try applying again

#### Downgrade Issues

If downgrade fails:

```bash
# Check the downgrade function in the migration file
# Ensure it properly reverses the upgrade operations
# Fix any issues and try again
```

## Additional Resources

- [Alembic Documentation](https://alembic.sqlalchemy.org/)
- [SQLAlchemy Documentation](https://docs.sqlalchemy.org/)
- [Alembic Tutorial](https://alembic.sqlalchemy.org/en/latest/tutorial.html)
- [Cron-Based Data Collection Architecture](../plans/cron-based-data-collection-architecture.md)
