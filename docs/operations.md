# Production Operations Runbook

Quick-reference for operating the live deployment. For local development
see [`docs/development.md`](development.md); for the deployment
procedure itself see [`docs/deployment.md`](deployment.md).

## Health & Status

### Liveness probe

```bash
# Always 200 — body's status field signals health.
curl https://whatismytip.com/health
# {"status":"healthy","db":"ok","redis":"ok","version":"...","request_id":"..."}
```

The nginx proxy also exposes a self-contained `/healthz` that does
**not** depend on the FastAPI app (used by App Platform for routing).

| Status | Meaning | Action |
|--------|---------|--------|
| `healthy` | DB + Redis + app all OK | None |
| `degraded` (db down) | DB query failed | See [DB connection issues](#database-connection-issues) |
| `degraded` (redis down) | Redis call failed | See [Redis issues](#redis-issues) |

### Per-job execution metrics

```bash
curl -H "X-API-Key: $ADMIN_API_KEY" \
  https://whatismytip.com/api/admin/metrics
```

Returns per-job success rate, last-run timestamp, average duration, and
running status. Cached server-side for 30 s (TTL in
[`backend/app/api/admin.py`](../backend/app/api/admin.py:55)).

### Historic-refresh progress (long-running job)

```bash
curl -H "X-API-Key: $ADMIN_API_KEY" \
  https://whatismytip.com/api/admin/historic-refresh/progress
```

Returns the current season/round, items processed, and ETA for the
in-flight historic-refresh run.

## Manual Job Triggers

Each job is gated by the `X-API-Key` header. Replace `{job}` with one
of `daily-sync`, `match-completion`, `tip-generation`, `historic-refresh`.

```bash
curl -X POST -H "X-API-Key: $ADMIN_API_KEY" \
  https://whatismytip.com/api/admin/{job}/trigger
# 200 {"status":"started","job":"daily-sync","execution_id":42}
```

| Job | Purpose | Typical duration |
|-----|---------|------------------|
| `daily-sync` | Pull games from Squiggle API | seconds |
| `match-completion` | Detect completed matches, update tips | seconds |
| `tip-generation` | ML pipeline → tips for a round | 1-3 min |
| `historic-refresh` | Re-derive features for old seasons | up to 15 min |

## Deploy

Deploys are driven by [`backend/scripts/deploy.sh`](../backend/scripts/deploy.sh:1),
which:

1. Runs the unit-test suite
2. Runs `alembic upgrade head` (idempotent)
3. Builds the image from `backend/Dockerfile`, tags it `${DO_REGISTRY}/api:${git-sha}`
4. Pushes it to the DO Container Registry
5. Triggers `doctl apps create-deployment ${DO_APP_ID} --force-rebuild`
6. Polls `https://whatismytip.com/health` for up to 60 s

```bash
cd backend
DO_REGISTRY=registry.digitalocean.com/whatismytip \
DO_APP_ID=<your-app-id> \
./scripts/deploy.sh
# or preview-only:
./scripts/deploy.sh --dry-run
```

### Rollback a deploy

App Platform keeps the previous N images; rollback is one click in the
dashboard or:

```bash
# Find the previous deployment
doctl apps list-deployments ${DO_APP_ID}
# Roll back to deployment ID
doctl apps create-deployment ${DO_APP_ID} --deployment-id <previous-id>
```

Then watch the rollout: `doctl apps logs ${DO_APP_ID} -f`.

### Migrations are forward-only

Alembic migrations are append-only.  To roll back a bad migration,
write a **new** down-migration; do **not** `alembic downgrade` against
the live DB while a deploy is in progress.

## Scaling

| Component | How to scale | Notes |
|-----------|--------------|-------|
| App Platform `api` | Bump `instance_count` and/or `instance_size_slug` from `basic-xxs` in `.do/app.yaml` | In-process APScheduler is shared via Postgres advisory locks — safe to add instances. |
| App Platform `proxy` (nginx) | Same | Stateless |
| PostgreSQL | Upgrade plan via the DO dashboard | Watch `pg_stat_activity` for connection pressure |
| Redis | Upgrade plan via the DO dashboard | Memory-bound |
| Frontend (static) | Scales automatically | Static site, no state |

Multi-instance `api` deploys coordinate via **Postgres advisory locks**
— only one instance runs each scheduled job at a time.  Manual
`/api/admin/{job}/trigger` calls are **not** lock-coordinated; if you
need strict one-at-a-time semantics for a manual trigger, gate them
through the existing scheduler instead.

## Drain Traffic (Maintenance)

1. **Soft drain** (preferred): redeploy with `instance_count = 0` on the
   `api` component in `.do/app.yaml`.  Existing connections finish their
   in-flight requests, no new traffic is accepted.
2. **Hard stop** (emergency): `doctl apps delete-deployment ${DO_APP_ID} --force`.
   The proxy will return 502 until the next deploy.

For per-request blocking (e.g. a stuck cron), see
[How to clear a stuck job lock](#clear-a-stuck-job-lock).

## Common Alerts & Remediation

| Alert | Likely cause | Fix |
|-------|--------------|-----|
| `health` returns `degraded` with `db: error` | DB connection / auth / network | [DB connection issues](#database-connection-issues) |
| `health` returns `degraded` with `redis: error` | Redis pool exhausted or network | [Redis issues](#redis-issues) |
| Job `failed` 3× in a row | Crashing on a new round or upstream change | [Job failed 3×](#job-failed-3-times) |
| Job `skipped` for > 1 h | Stale lock from a crashed instance | [Clear a stuck job lock](#clear-a-stuck-job-lock) |
| 5xx rate > 1 % for 5 min | Bad deploy / external API outage | [Rollback](#rollback-a-deploy) |
| OpenRouter cost spike | Stuck retry loop or runaway tip-generation | Disable `OPENROUTER_API_KEY`, drain traffic, investigate. |
| `ALERT_WEBHOOK_URL` 4xx/5xx | Webhook URL misconfigured or down | Verify the URL, regenerate the token, redeploy. |

### Database connection issues

1. `curl https://whatismytip.com/health` → check `db` field.
2. Verify `?ssl=require` is in `DATABASE_URL` (managed DBs require it).
3. Confirm DB **Trusted Sources** in the DO dashboard allow the App
   Platform egress (`0.0.0.0/0` is the simplest setup).
4. Check pool exhaustion: `psql ... -c "SELECT count(*), state FROM pg_stat_activity GROUP BY state;"`.
5. If persistent, scale the DB plan or reduce `pool_size` in `db.py`.

### Redis issues

1. Check `redis` field of `/health`.
2. `redis-cli -u $REDIS_URL INFO clients` — too many connections?
3. `redis-cli -u $REDIS_URL INFO memory` — eviction pressure?
4. Scale Redis plan or shorten cache TTLs.

### Job failed 3 times

1. `GET /api/admin/metrics` — find the failing job and its `error_message`.
2. Inspect the most recent `job_executions` row: `SELECT * FROM job_executions WHERE job_name = '{job}' ORDER BY started_at DESC LIMIT 3;`
3. For tip-generation: check the upstream data (`/api/games?season=&round=`) and OpenRouter API health.
4. Trigger a fresh run after the root cause is fixed: `/api/admin/{job}/trigger`.

### Clear a stuck job lock

```sql
DELETE FROM job_locks
WHERE job_name = '{job}'
  AND lock_expires_at < NOW();
```

The next scheduled run will pick up cleanly.

## Logs

| Source | Command |
|--------|---------|
| App (combined) | `doctl apps logs ${DO_APP_ID}` |
| Single component | `doctl apps logs ${DO_APP_ID} --component api` (or `proxy`) |
| Structured JSON | Set `LOG_FORMAT=json` in the env, then `doctl apps logs ${DO_APP_ID} --format json` |
| Historical | DO dashboard → Apps → your-app → Logs → Run query |

Every log line includes a `request_id` for cross-correlation.

## See Also

- [`docs/deployment.md`](deployment.md) — Deployment procedure
- [`docs/development.md`](development.md) — Local development
- [`docs/security-model.md`](security-model.md) — Auth, rate limit, secret rotation
- [`docs/api.md`](api.md) — Full endpoint reference
- [`backend/app/api/admin.py`](../backend/app/api/admin.py:1) — Admin router
- [`backend/app/core/scheduler.py`](../backend/app/core/scheduler.py:1) — In-process scheduler
