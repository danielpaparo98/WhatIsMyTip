# Database Migrations with Alembic

This document explains how to use Alembic for database migrations in the WhatIsMyTip project.

## Overview

Alembic is a database migration tool for SQLAlchemy that provides:
- **Version control** for database schema changes
- **Upgrade and downgrade** capabilities
- **Auto-generation** of migrations from model changes
- **Tracking** of migration history

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

## Additional Resources

- [Alembic Documentation](https://alembic.sqlalchemy.org/)
- [SQLAlchemy Documentation](https://docs.sqlalchemy.org/)
- [Alembic Tutorial](https://alembic.sqlalchemy.org/en/latest/tutorial.html)
