# WhatIsMyTip Documentation

Landing page for the [`docs/`](../README.md) directory. Start here.

## Current

| Doc | Purpose |
|-----|---------|
| [`operations.md`](operations.md) | Production ops: cron schedule, health checks, log queries, alert routing, on-call runbook |
| [`security-model.md`](security-model.md) | Auth + rate-limit model, `X-API-Key` flow, request-size and security headers, secret handling |
| [`api.md`](api.md) | Full HTTP API reference — routes, request/response shapes, error codes, integration examples |
| [`backend.md`](backend.md) | FastAPI backend architecture: routers, cron jobs, DB layer, caching, ML models, heuristics |
| [`frontend.md`](frontend.md) | Nuxt 4 frontend structure, design system, components, composables, build pipeline |
| [`deployment.md`](deployment.md) | Production deploy to DigitalOcean App Platform + Managed PG/Redis (⚠️ `.do/app.yaml` is stale — see TODO) |
| [`development.md`](development.md) | Local dev setup (Docker stack + manual), TDD workflow, common-tasks cheat sheet |
| [`data-loading.md`](data-loading.md) | CSV data loading + scraper (`make-data.sh`, `migrate_and_seed.py --from-csv`) |
| [`migrations.md`](migrations.md) | Alembic workflow + current migration history (2 revisions on disk) |

## Historical (read-only, kept for context)

| Doc | Why historical |
|-----|----------------|
| [`digital-ocean-setup.md`](digital-ocean-setup.md) | One-time DO setup walkthrough. Superseded by `deployment.md`. |

## Project meta

- [`../README.md`](../README.md) — project overview
- [`../CONTRIBUTING.md`](../CONTRIBUTING.md) — how to contribute (TDD + gitmoji + `feature/` branching + bun/uv toolchain)
- [`../backend/README.md`](../backend/README.md) — backend-specific quick start
