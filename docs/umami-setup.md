# Umami analytics — setup and operations

WhatIsMyTip tracking lives in the operator's **shared self-hosted Umami
v3.2** instance — the same instance that serves `analytics.resumeroo.com`
and `analytics.howsthespot.com` — reached as
**https://analytics.whatismytip.com**. WhatIsMyTip is a **website entry**
in that instance (distinct website ID), not a separate deployment. This
document is the operator runbook: domain wiring, the app's tracking
implementation, the auth model, the event coverage map, and
troubleshooting.

Recorded: 2026-11-13 · Owner: engineering · See also: `docs/deployment.md`
Full instance runbook: `ResumeRoo/docs/umami-setup.md` (droplet layout,
backups, upgrades — out of scope here).

## Service endpoints

| Env | URL | Notes |
|---|---|---|
| Dev / preview | (none) | `NUXT_PUBLIC_UMAMI_*` unset → the injection plugin emits no script and `track()` no-ops. Dev never tracks. |
| Prod | https://analytics.whatismytip.com | The SHARED Umami instance on the operator's DigitalOcean droplet. TLS + reverse proxy on the host. |

Image: `docker.io/umamisoftware/umami:3.2` (pinned in the droplet's compose file).

One instance, many websites: each project (howsthespot, resumeroo.com,
whatismytip.com, …) is a separate website entry with its own website ID.
Data is isolated per website in the dashboard; the database and the
instance are shared.

## Prod domain — analytics.whatismytip.com

The app's site domain is `whatismytip.com` (DO-hosted DNS zone), so the
analytics host rides the same zone as a subdomain. Three one-time steps,
all operator-side (done 2026-11-13; nothing in-repo):

1. **DNS** — DO zone `whatismytip.com`, **A record**:
   `analytics` → 134.199.170.234 (the shared Umami droplet's public IP;
   TTL 1800).
2. **Reverse proxy vhost** — `/opt/umami/Caddyfile` on the droplet gained
   the third site block (backups land as `/opt/umami/Caddyfile.bak-*`):

   ```
   analytics.whatismytip.com {
       encode zstd gzip
       reverse_proxy umami:3000
   }
   ```

   Applied with `docker exec umami-caddy-1 caddy reload --config
   /etc/caddy/Caddyfile`. Caddy provisions/renews the Let's Encrypt cert
   automatically once DNS resolves.
3. **Website entry** — already created in the Umami dashboard
   (Settings → Websites): Name `WhatIsMyTip`, Domain `whatismytip.com`,
   website ID `f9498b93-2df4-404b-903a-307de0874edf`. This ID is wired
   into the App Platform spec (both components). It is a plain value, not
   a secret — it ships in every page's `<script data-website-id>`. If you
   ever recreate the entry, update both spec keys and redeploy.

## Prod wiring (App Platform)

The tracking env vars live in the App Platform spec and are applied with
`doctl apps update` (spec changes never auto-deploy). The working copy
with the EV secrets must live OUTSIDE the repo:

```sh
doctl apps spec get ecc5a2a0-917b-4659-add6-e558c2d00902 --format yaml > ~/.do/whatismytip.app.yaml
# edit both components' UMAMI keys, then:
doctl apps update ecc5a2a0-917b-4659-add6-e558c2d00902 --spec ~/.do/whatismytip.app.yaml
```

- `whatismytip-frontend` (static_site, BUILD_TIME):
  `NUXT_PUBLIC_UMAMI_HOST=https://analytics.whatismytip.com`,
  `NUXT_PUBLIC_UMAMI_WEBSITE_ID=f9498b93-2df4-404b-903a-307de0874edf`.
  **BUILD_TIME is required** — `nuxt generate` bakes `runtimeConfig.public`
  into the static site; a RUN_TIME-only scope silently tracks nothing.
- `whatismytip-backend` (service, RUN_AND_BUILD_TIME): the same two keys
  (kept for consistency; the FastAPI backend itself does not send events).

The injection plugin (`frontend/plugins/umami.client.ts`) emits the script
tag only when BOTH values are non-empty, so dev builds and branch previews
stay untracked by default.

Post-deploy verification: open https://whatismytip.com, then the Umami
dashboard → WhatIsMyTip → Live; page views should appear within seconds.
If not, see Troubleshooting.

## Migration note (2026-11-13)

Tracking previously pointed at **cloud.umami.is**
(website ID `82e9b924-49cb-4d16-9e69-5682a1143ea6`). The migration to the
shared self-hosted instance started FRESH — Umami Cloud has no DB export
path, and the decision was to not import history. The retired cloud
dashboard remains readable at cloud.umami.is if historical data is ever
needed.

## Auth model — JWT only

Self-hosted Umami uses **JWT-only auth** (no static API key). Any scripted
integration must `POST /api/auth/login` with `username` + `password`, take
the `token` from the response, and send `Authorization: Bearer <jwt>`
thereafter (re-login when it expires, ~1 day TTL).

## Event coverage map

Client wrapper: `frontend/utils/analytics.ts` (`ANALYTICS_EVENTS` — the
typed vocabulary; add new events there FIRST, then wire call sites).
Page views on every route are automatic (Umami tracker, SPA navigations
included). Payloads are routing metadata only — never user content.

| Family | Events | Fires from |
|---|---|---|
| View anchors | `home_viewed`, `league_viewed`, `match_viewed`, `performance_viewed`, `about_viewed` | Page mounts (`pages/*.vue`), with a `league` payload. Typed anchors survive route-scheme changes (`/game/{slug}` vs `/{league}/match/{slug}`) and make league mix queryable. |
| Interactions | `heuristic_selected` (heuristic, league), `league_selector_used` (to) | Heuristic tablists on the home pages (user clicks only — the localStorage restore does NOT fire); header LeagueSelector |
| Conversion | `buy_me_a_coffee_clicked` | Footer supporter CTA |
| Errors | `error_page_shown` (status_code) | `error.vue` on mount (client-side only) |

### Funnels to create in the dashboard

1. **Content path**: `home_viewed` → `match_viewed` → `performance_viewed`
   → `buy_me_a_coffee_clicked`.
2. **League interest**: `league_selector_used` → `league_viewed` →
   `match_viewed` (per-league).
3. **Heuristic engagement**: `heuristic_selected` distribution per league —
   which tipping models users actually compare.

## Instance operations (shared droplet)

Backups, upgrades, DB access and the instance's first-run history are
covered by the fleet runbook (`ResumeRoo/docs/umami-setup.md`). Quick
reference: droplet `umami-analytics` (134.199.170.234, syd1), SSH key
`umami-vm-key` (DO key ID 60769287), stack at `/opt/umami/` (Caddy +
Umami + containerised Postgres).

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| No events in prod, no script tag in HTML | `NUXT_PUBLIC_UMAMI_*` unset or frontend BUILD_TIME scope missing | Set both keys on the static_site component in the App Platform spec, apply via `doctl apps update`, redeploy |
| Script loads but no page views | Website ID belongs to another website entry, or domain mismatch | Re-copy the ID from the `whatismytip.com` website entry in the dashboard |
| Script blocked in browser console (CSP) | CSP directives missing the analytics host | `https://analytics.whatismytip.com` must appear in BOTH `script-src` and `connect-src` — analytics silently fails otherwise (no CSP is currently shipped; if one lands, wire the host in) |
| `analytics.whatismytip.com` unreachable | DNS record or proxy vhost missing | See "Prod domain" above — A record, then Caddyfile vhost |
| Old cloud.umami.is stats needed | Historical data stayed behind | See Migration note — the retired cloud dashboard remains accessible |
