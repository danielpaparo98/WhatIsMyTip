# WhatIsMyTip Operations Guide

> **Status:** Living document — populated incrementally.  Currently cross-links to existing runbooks; future Phase 7+ work will fill in the full runbook content.

This is the operations runbook for the WhatIsMyTip production stack (single FastAPI container + nginx reverse proxy + managed PostgreSQL + managed Redis on DigitalOcean App Platform).  See [`docs/deployment.md`](deployment.md) for the deploy pipeline; the material here focuses on what to do **after** it's live.

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
