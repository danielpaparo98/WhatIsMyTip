# playhq-proxy — Cloudflare Worker in front of api.playhq.com

Token-gated reverse proxy that gives the WhatIsMyTip backend a
non-DigitalOcean egress for PlayHQ's discover GraphQL API.

**Why (2026-10-12 incident):** PlayHQ's CloudFront edge 403s
DigitalOcean's datacenter IP ranges outright — the generic CloudFront
`ERROR` page comes back regardless of browser headers (verified live:
prod's league-sync still failed after the browser-identity rework in
`backend/packages/shared/ingestion/playhq_provider.py`).  The provider
therefore supports `PLAYHQ_GRAPHQL_BASE` + `PLAYHQ_PROXY_TOKEN`; this
worker is the reference proxy.

## Deploy (one-time, ~5 min)

Prerequisites: a (free) Cloudflare account.  Node 18+ on this machine
(`npx` resolves `wrangler` on the fly).

```bash
cd infra/playhq-proxy

npx wrangler login            # opens the browser for OAuth

# Shared secret the backend must echo — generate one, e.g.:
#   python -c "import secrets; print(secrets.token_urlsafe(32))"
npx wrangler secret put PROXY_TOKEN
# → paste the token when prompted

npx wrangler deploy
# → note the URL: https://playhq-proxy.<account>.workers.dev
```

## Wire the backend to it (DigitalOcean App Platform)

Set two env vars on the `whatismytip-backend` component (dashboard or
`doctl apps update <app-id> --env ...`), same token as above:

```
PLAYHQ_GRAPHQL_BASE=https://playhq-proxy.<account>.workers.dev/graphql
PLAYHQ_PROXY_TOKEN=<the PROXY_TOKEN value>
```

The spec change triggers a health-gated redeploy automatically.  Then
trigger a sync and confirm:

```
POST https://whatismytip.com/api/admin/league-sync/trigger
X-API-Key: <prod key>   {"leagues": ["nwfl", "sfl"]}
```

## Rollback

Remove both env vars (or point `PLAYHQ_GRAPHQL_BASE` back to
`https://api.playhq.com/graphql` and drop the token) — the provider
falls back to direct PlayHQ traffic.

## Cost / load

The NWFL+SFL sync makes ~8 GraphQL requests per day (one 04:30 AWST
cron + manual triggers).  Free tier: 100k requests/day.  The token is
required so the URL is not an open PlayHQ relay.
