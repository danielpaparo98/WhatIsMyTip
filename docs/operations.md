# WhatIsMyTip Operations Guide

> **Status:** Living document — populated incrementally.  Currently cross-links to existing runbooks and carries the runbook for the scheduled jobs (in-process cron); future Phase 7+ work will fill in the rest.

This is the operations runbook for the WhatIsMyTip production stack (single FastAPI container + managed PostgreSQL + managed Redis on DigitalOcean App Platform).  See [`docs/deployment.md`](deployment.md) for the deploy pipeline; the material here focuses on what to do **after** it's live.

## Scheduled jobs (in-process cron)

Every job below is registered by the in-process APScheduler (`backend/app/core/scheduler.py`) from the cron expressions in `backend/packages/shared/config.py`, interpreted in `settings.cron_timezone` (default `Australia/Perth`).  Per-job implementation details live in [`docs/backend.md`](backend.md#scheduled-jobs); this section is the operations runbook for the **daily sync** pass — what it runs, how often, how to trigger it by hand, and what to do when it fails.

| Job id | Default cadence (app-tz) | On/off flag | What it refreshes |
|--------|--------------------------|-------------|-------------------|
| `daily-sync` | `*/15 * * * *` (every 15 min) | `daily_sync_enabled` | AFL games (Squiggle) + rugby-league fixtures (FixtureDownload) |
| `match-completion` | `5,20,35,50 * * * *` | `match_completion_check_enabled` | completed AFL games |
| `tip-generation` | `0 3 * * *` | `tip_generation_enabled` | nightly tips |
| `historic-refresh` | `0 4 * * 0` (Sundays) | `historic_refresh_enabled` | 16-season AFL history |
| `model-retrain` | `0 5 * * 1` (Mondays) | `model_retrain_enabled` | weighted + boosted models |
| `supplementary-sync` | `45 5 * * *` | `supplementary_sync_enabled` | injuries + weather |
| `league-sync` | `30 4 * * *` | `league_sync_enabled` | state-league fixtures (WAFL, SANFL, …) |
| `odds-sync` | `15 6 * * *` | `odds_sync_enabled` | AFL bookmaker odds snapshot |

### The daily-sync pass — one job, two sections

Since Phase 5.2 the `daily-sync` job carries two independent sections:

1. **AFL section** — Squiggle game sync + Elo cache refresh.  Skipped during the AFL off-season (Oct–Feb, app timezone) outside the reduced 02:00–04:00 window.
2. **Rugby-league section** — sweeps every "live" competition in the national registry (`backend/packages/shared/ingestion/national_leagues.py`): **`nrl`**, **`nrlw`**, **`origin`** (NRL, NRL Women's Premiership, State of Origin).

#### Rugby-league cadence & politeness

- The section rides the daily-sync schedule; there is **no separate cron entry**.  Toggle it with `RUGBY_LEAGUE_SYNC_ENABLED=false` — the AFL section is unaffected.
- The in-season gate is evaluated in **`Australia/Brisbane`** — the rugby-league `SportContext` cron timezone, **not** the app timezone — with the rugby-league off-season months **Nov–Feb** (`off_season_months` in `backend/packages/shared/sport_context.py`).  During the off-season the sweep only runs inside the same shared reduced-window hours as the AFL section (`DAILY_SYNC_OFF_SEASON_START_HOUR` / `DAILY_SYNC_OFF_SEASON_END_HOUR`, default 02:00–04:00 local).  The two gates are independent: in October the AFL section skips (AFL off-season) while the rugby-league section keeps running (finals month).
- Politeness: the sweep drives every league through `run_league_sync`, whose FixtureDownload-backed provider (`NrlProvider`) carries a **shared 24h TTL cache — at most one fetch per slug per day** reaches fixturedownload.com no matter how many 15-minute passes fire.  The DB-side work is an idempotent upsert.

#### Failure handling & isolation

- **Per-league isolation:** each of `nrl` / `nrlw` / `origin` syncs inside its own try/except.  One league failing (feed unreachable, DB fault) never aborts the other two and never fails the AFL section or the job; the failed league's transaction is rolled back so the shared session stays usable.
- **Aggregate result:** the section reports `status` (`success`/`partial`), `leagues_synced`, `leagues_failed`, `fixtures_synced` and per-league `errors`.  The AFL `status` of the pass is never downgraded by a rugby-league failure, and the execution summary appends one `Rugby league (<status>): N synced, M failed` line when the section ran.
- **Alerting:** job-level alerting is unchanged — `ALERT_WEBHOOK_URL` pages only on a whole-job failure (via `BaseJob`, which the rugby-league section can never trigger on its own).  Per-league failures are **log-detected**: grep the backend logs for `rugby-league sync failed:` (one error + traceback per league), or `rugby-league daily sync section failed` for a whole-section crash.

#### Manual trigger

- **Whole job (AFL section only):** `POST /api/admin/daily-sync/trigger` — the admin trigger predates the shared service and runs the Squiggle sync inline ([`docs/api.md`](api.md#admin)); it does **not** run the rugby-league section.
- **Rugby-league section (one or all leagues):**

  ```bash
  cd backend && uv run python -c "
  import asyncio
  from sqlalchemy.ext.asyncio import async_sessionmaker
  from packages.shared.db import get_engine
  from packages.shared.ingestion.national_leagues import run_league_sync

  async def main():
      engine = get_engine()
      factory = async_sessionmaker(engine, expire_on_commit=False)
      async with factory() as session:
          for league in ('nrl', 'nrlw', 'origin'):
              stats = await run_league_sync(session, league, 2026)
              print(league, stats['fixtures_synced'], 'fixtures,',
                    len(stats['errors']), 'errors')
      await engine.dispose()

  asyncio.run(main())
  "
  ```

  Substitute the season as needed (feeds exist 2017+).  A re-run is an idempotent upsert, and the provider cache keeps it to one fetch per slug per day.  Restrict the loop to a single league for targeted retries of a `leagues_failed` entry.
- **State leagues** keep their own admin trigger: `POST /api/admin/league-sync/trigger`.

> **Known gap (Phase 5.2 follow-up):** the admin `daily-sync` trigger does not exercise the rugby-league section yet (it predates the shared `run_daily_sync` service).  Wiring it through the shared service is a candidate follow-up — it touches `app/api/admin.py`, which was out of scope for the expansion's ops subtask.

## Current cross-links

| Topic | Reference |
|-------|-----------|
| Cron job schedules, triggers, env-var overrides | [`docs/backend.md`](backend.md#scheduled-jobs) |
| Health check contract | [`docs/api.md`](api.md#health-check) |
| Auth + rate-limit model | [`docs/security-model.md`](security-model.md) |
| Manual job trigger via admin API | [`docs/api.md`](api.md#admin) |
| Deploy script (`deploy.sh`) | [`docs/deployment.md`](deployment.md#step-6-deploy-the-backend) |
| Database migrations on prod | [`docs/migrations.md`](docs/migrations.md) |
| Deployment troubleshooting | [`docs/deployment.md`](deployment.md#troubleshooting) |
| Stale `.do/app.yaml` rewrite TODO | [`docs/deployment.md`](deployment.md#whatismytip-deployment-guide) (⚠️ header) |
