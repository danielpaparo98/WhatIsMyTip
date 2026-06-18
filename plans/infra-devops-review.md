# Infrastructure / DevOps Code Review

Scope: Dockerfiles, docker-compose, nginx, deploy scripts, GitHub Actions, `.do/` App Platform specs, env templates, and the related unit tests. **Read-only review.**

## Critical (must fix before prod)

- **[CR-001] `.do/app.yaml` references a non-existent `functions` component sourced from `backend/project.yml` — App Platform will reject or recreate the FaaS architecture on the next deploy.**
  - File: `.do/app.yaml:84-235` (the `functions:` block), and `.do/app.yaml:88` (comment still says `backend/project.yml`).
  - Problem: Phase 4 retired DO Functions in favour of a single FastAPI container, but `.do/app.yaml` still declares a `functions:` component pointing at `source_dir: backend` and expects App Platform to discover `backend/project.yml`. That file was deleted (commit log confirms it: `🔥 chore(cleanup): delete FaaS-only project.yml and get-fn-urls.ps1`). The same spec still declares `whatismytip-proxy` (an nginx service for an OpenWhisk path-rewrite), `whatismytip-backend` as a functions component, and a static site for the frontend — all wired through an `ingress` that only routes `/api` to the proxy.
  - Why it matters in prod: If anyone runs `doctl apps create/update --spec .do/app.yaml` (this is exactly what `scripts/deploy.sh` does — see CR-003), App Platform will either error out at the functions step or, worse, succeed in recreating the FaaS layout, blowing away the working FastAPI component. The embedded `EV[...]` secrets inside the same file (lines 99, 103, 125, 135) would also be re-applied to whichever components DO creates from this spec — possibly to a brand-new functions namespace.
  - Fix: Delete `.do/app.yaml` outright (preferred — the deploy pipeline does not read it; it builds and pushes an image then calls `doctl apps create-deployment --force-rebuild`). If you want to keep a single source of truth, rewrite it from scratch against the current App Platform architecture (a single `service:` component named `fastapi`, image from the registry, optional second `service:` for the nginx proxy, plus a `static_sites:` entry for the frontend). Mark the file as deprecated at minimum with a header banner warning that running it will recreate the FaaS app.

- **[CR-002] Plaintext production secrets are committed inside `.do/app.yaml` (`EV[...]` values for `DATABASE_URL`, `REDIS_URL`, `ADMIN_API_KEY`, `OPENROUTER_API_KEY`).**
  - File: `.do/app.yaml:99`, `.do/app.yaml:103`, `.do/app.yaml:125`, `.do/app.yaml:135`.
  - Problem: These are supposed to be DO-encrypted secret references, but they are hard-coded as raw `EV[...]` ciphertext values inside a tracked file. Even if DO's "encrypted secret" wrapping hides the plaintext at rest in the App Platform control plane, the ciphertext itself is in git history and is treated as a credential by App Platform — leaking the ciphertext is functionally equivalent to leaking the secret (anyone with the ciphertext can replay it against the same DO project). They are also tied to the wrong (FaaS) component. The hardcoded `SQUIGGLE_CONTACT_EMAIL: paparodaniel98@gmail.com` (line 131) is also PII baked into a tracked file.
  - Why it matters in prod: Any cloned fork or CI artifact preserves these ciphertexts. If the DO project rotates, the old ciphertext becomes useless but the old data is recoverable from git. There is no expiry and no per-environment separation.
  - Fix: (a) Strip all `EV[...]` and PII from `.do/app.yaml`. (b) Rotate the four secrets immediately. (c) Store them only as GitHub Actions secrets (`DO_DATABASE_URL`, `DO_REDIS_URL`, `DO_ADMIN_API_KEY`, `DO_OPENROUTER_API_KEY`) and inject via `doctl apps update --spec <(envsubst ...)` at deploy time. (d) Delete this file's secret history with `git filter-repo` if any environment was using these values in the wild. (e) Broaden the scanner regex in `backend/tests/unit/test_no_hardcoded_secrets.py` to flag `EV[` values (see CR-006).

- **[CR-003] `scripts/deploy.sh` will recreate the deleted FaaS app on DigitalOcean.**
  - File: `scripts/deploy.sh:36`, `scripts/deploy.sh:41`.
  - Problem: This legacy script invokes `doctl apps create --spec .do/app.yaml || doctl apps update --spec .do/app.yaml` (and the same for `.do/frontend.yaml`). As noted in CR-001, `.do/app.yaml` is stale. Running this script is the single most reliable way to nuke the working FastAPI deployment and replace it with the FaaS layout. Note that `backend/scripts/deploy.sh` is the correct script (it pushes an image and calls `doctl apps create-deployment --force-rebuild`), and the GitHub Actions workflow `.github/workflows/deploy.yml:90` invokes that one.
  - Why it matters in prod: A panicked operator who runs `bash scripts/deploy.sh` instead of `backend/scripts/deploy.sh` would push the FaaS spec to DO and tear down production.
  - Fix: Delete `scripts/deploy.sh` outright. Add a top-of-repo `DEPLOY.md` that points unambiguously at `backend/scripts/deploy.sh` and the GitHub Actions workflow. If you must keep it as a reference, gate the `doctl apps create/update --spec ...` calls behind a `WIMT_ALLOW_LEGACY_DEPLOY=1` env guard and a banner saying "DO NOT RUN — recreates the retired FaaS app".

- **[CR-004] No rollback / rollback-automation path exists; the deploy script is a one-way valve.**
  - File: `.github/workflows/deploy.yml:59-90`, `backend/scripts/deploy.sh:149-175`.
  - Problem: `backend/scripts/deploy.sh` runs `doctl apps create-deployment --force-rebuild` — `--force-rebuild` forces a brand-new container build and does not preserve the previous image. There is no companion `doctl apps rollback` step, no capturing of the previous deployment ID, and no automatic rollback on health-check failure. The post-deploy `/health` poll (line 161-170 of the script) only prints "did not become healthy" and exits 0 — a broken deploy is silently "successful". The deploy workflow's own smoke step (lines 97-106) likewise `exit 0` on failure and only `echo` a warning.
  - Why it matters in prod: A bad migration, a regression, or an unhealthy new revision will leave the broken revision serving traffic. The operator has to manually run `doctl apps list-deployments`, identify the previous healthy one, and `doctl apps rollback` — and there is no runbook documenting this. With cron-driven database migrations inside the container, a broken revision can also leave the schema ahead of the app.
  - Fix: (a) Drop `--force-rebuild` from the deploy call (let DO reuse the previous image's cache where appropriate, and use a new image tag for each SHA — already done via `IMAGE_TAG=${{ github.sha }}`). (b) Capture `doctl apps create-deployment` output (`-o json`) into a variable `NEW_DEPLOYMENT_ID`. (c) After `/health` fails for >2 min, auto-run `doctl apps rollback <app-id>`. (d) Make the smoke step in `.github/workflows/deploy.yml` fail the workflow on health-check miss, and chain to a rollback job.

- **[CR-005] nginx security headers are missing — the proxy terminates nothing (TLS is offloaded at App Platform), but it does inject `X-Forwarded-Proto` and never sets `X-Content-Type-Options`, `X-Frame-Options`, `Referrer-Policy`, `Strict-Transport-Security`, or `Content-Security-Policy`.**
  - File: `backend/proxy/nginx.conf:22-63` (entire `server {}` block).
  - Problem: The proxy passes through everything; the FastAPI app is responsible for adding security headers (this is confirmed by the proxy config test `test_nginx_conf_keeps_proxy_headers`). If the FastAPI middleware that adds them regresses, the production site serves HTML without the protection. Also, even with TLS offloaded, the response is plaintext HTTP between proxy and client if App Platform's edge is misconfigured — the proxy should set HSTS as a defence-in-depth. There is also no rate-limiting at the edge (see HI-007).
  - Why it matters in prod: A single regression in the FastAPI security-headers middleware removes clickjacking / MIME-sniffing / referrer-leak protection from every public endpoint. There is no test asserting these headers are returned at the edge, only that nginx does not strip them.
  - Fix: Add `add_header` directives in `nginx.conf` inside the `server {}` block: `X-Content-Type-Options "nosniff"`, `X-Frame-Options "SAMEORIGIN"`, `Referrer-Policy "strict-origin-when-cross-origin"`, and (if you front the public domain) `Strict-Transport-Security "max-age=31536000; includeSubDomains"`. Add a test in `test_proxy_config.py` asserting each header is present. Keep them on `always` so even error responses carry them.

- **[CR-006] The hardcoded-secret scanner only checks for `dop_v1_` PATs and misses the four `EV[...]` ciphertexts in `.do/app.yaml`.**
  - File: `backend/tests/unit/test_no_hardcoded_secrets.py:36`, `.do/app.yaml:99, 103, 125, 135`.
  - Problem: The regex `r"dop_v1_[a-f0-9]{56}"` is intentionally narrow. It does not match DO-encrypted-secret ciphertexts (`EV[1:...:...]`) or AWS keys, GitHub PATs, Slack tokens, OpenRouter keys (`sk-or-...`), or any other format. The four `EV[...]` values in `.do/app.yaml` would pass this scanner today.
  - Why it matters in prod: A second incident similar to the previous DO PAT leak (referenced in the test docstring) involving a different credential shape would not be caught. The scanner gives false confidence that "no secrets in tracked files" — but only for one specific shape.
  - Fix: Broaden `DO_PAT_PATTERN` (rename to `SECRET_PATTERNS` and make it a list) to include: `EV\[[0-9]+:[A-Za-z0-9+/=]+:[A-Za-z0-9+/=]+\]` (DO encrypted secret), `sk-or-[A-Za-z0-9_-]{20,}` (OpenRouter), `ghp_[A-Za-z0-9]{36}` (GitHub PAT), `github_pat_[A-Za-z0-9_]{82}`, `xox[baprs]-[A-Za-z0-9-]{10,}` (Slack), `AKIA[0-9A-Z]{16}` (AWS access key). `.do/app.yaml` is already in scope (scanner walks the whole repo).

## High (should fix before prod)

- **[HI-001] Backend Dockerfile has no `.dockerignore` for `backend/`; the build context copies the entire repo into the image (including `.venv/`, `__pycache__/`, `tests/`, debug scripts, `.git/`, logs, etc.).**
  - File: `backend/Dockerfile:33-37` (`COPY pyproject.toml uv.lock ./`, `COPY . ./`); no `backend/.dockerignore` exists.
  - Problem: `COPY . ./` ships the entire repo into the image. The runtime stage then `COPY --from=builder /app /app` (line 56) and inherits everything from the builder — including `tests/`, debug scripts (`_fix_tests2.py`, `list_routes.py`), `.env`, `__pycache__/`, `*.log`, and the `.venv` itself. Anyone with `docker exec` access sees internal test code in production. Image size is larger than necessary, increasing push/pull time and App Platform cold-start.
  - Why it matters in prod: Larger attack surface, larger image, slower deploys, and visible test code in prod.
  - Fix: Create `backend/.dockerignore` containing at minimum: `.venv/`, `venv/`, `__pycache__/`, `*.pyc`, `.pytest_cache/`, `.mypy_cache/`, `.ruff_cache/`, `tests/`, `scripts/_*.py`, `*.log`, `.env`, `.env.*`, `!.env.example`, `data/`, `seed_data/`, `logs/`, `*.db`, `*.sqlite*`, `.git/`, `.gitignore`, `.gitattributes`, `docs/`, `README.md`, `LICENSE`, `Dockerfile`, `proxy/`, `list_routes.py`, `_fix_tests*.py`. Add a unit test that asserts `.dockerignore` exists and contains `tests/` (regression guard).

- **[HI-002] nginx `proxy_buffering off;` is unconditional and will degrade throughput for any non-streaming endpoint.**
  - File: `backend/proxy/nginx.conf:61`.
  - Problem: `proxy_buffering off` disables response buffering for every request, forcing nginx to send upstream data immediately as it arrives. For the bulk of the API (game lists, tip lists, chart payloads) this is worse than the default — it forces small TCP writes, disables any hope of compression-on-the-fly, and prevents nginx from serving the response from disk cache.
  - Why it matters in prod: Higher latency under load and pointless CPU usage. The comment says "Pass through WebSockets / SSE if we ever need them" — but WebSockets/SSE are not used today and would be better served by per-location overrides.
  - Fix: Remove the global `proxy_buffering off;`. If/when you add SSE or WebSockets, scope it to the specific `location` block (e.g. `location /api/stream { proxy_buffering off; proxy_cache off; }`). Optionally add `proxy_buffer_size 8k; proxy_buffers 16 8k;` for the API responses.

- **[HI-003] `scripts/deploy.sh` (legacy) is still wired into the docs as a deploy path and is referenced by older contributors.**
  - File: `scripts/deploy.sh:1-48`, `docs/deployment.md`, `docs/digital-ocean-setup.md`.
  - Problem: Even though the GitHub Actions workflow uses the correct `backend/scripts/deploy.sh`, the legacy root-level `scripts/deploy.sh` exists, is executable, and the docs likely still direct operators to it.
  - Why it matters in prod: Manual operator confusion → manual operator error (CR-003).
  - Fix: As in CR-003, delete `scripts/deploy.sh`. Audit `docs/*.md` for any references and either delete or repoint them at `backend/scripts/deploy.sh`.

- **[HI-004] `backend/scripts/deploy.sh` never validates that the target DigitalOcean registry is reachable before pushing the image.**
  - File: `backend/scripts/deploy.sh:83-97`, `backend/scripts/deploy.sh:139-152`.
  - Problem: The script checks `doctl account get` succeeds (line 86), which proves auth works. But if the user invokes the script in a CI context where `.env` exists but contains a stale or rotated `DO_REGISTRY`, the script will silently build a 700 MB image, push it to the wrong registry, then trigger a deployment that pulls a previous image. There is no assertion that `doctl registry login` succeeded for the target registry.
  - Why it matters in prod: Wasted CI minutes, deploys against stale images, and a confusing post-mortem (the push "succeeded" but the deployed image is from three deploys ago).
  - Fix: After sourcing `.env`, add `doctl registry login --registry "${DO_REGISTRY%%/*}"` (or `doctl compute registry list`) and `die` on failure. Optionally run `docker manifest inspect "${FULL_IMAGE}"` after push to confirm the push landed before triggering the deployment.

- **[HI-005] `docker-compose.yml` exposes Postgres and Redis ports to the host (5432 and 6379) with `restart: unless-stopped`, and the `api` service has no `security_opt: [no-new-privileges:true]`, no `read_only` FS, and no `cap_drop`.**
  - File: `docker-compose.yml:55-78` (postgres/redis port mappings), `docker-compose.yml:121-157` (api service), `docker-compose.yml:90-119` (init-data service).
  - Problem: Postgres and Redis are bound to host ports — fine for local dev, but combined with the `wimt_dev_password` default and no auth requirements beyond username/password, anyone on the host LAN can connect (or, on Windows/macOS with Docker Desktop, anyone on the same Wi-Fi can hit the port via the host's IP). There is no `[no-new-privileges, cap_drop=[ALL], read_only]` hardening on any of the four application services, so a RCE in FastAPI gives full container root.
  - Why it matters in prod: Local-dev footgun, but more importantly, this compose file is the canonical reference for "how the app is supposed to run", and is also used by some operators as a quasi-production pattern via `docker compose up -d`. A copy-paste into a VPS exposes the database.
  - Fix: Remove `ports:` from `postgres`/`redis` for the dev path (compose-internal DNS is enough); add an opt-in `docker-compose.override.yml.example` for users who want port mapping. Add `security_opt: ["no-new-privileges:true"]` to `api`, `init-data`, and `frontend`. For `api`, mount a writable tmpfs at `/tmp` and set `read_only: true` with explicit writable volumes for any cache dirs (`/app/.cache`, `/tmp`).

- **[HI-006] `docker-compose.yml` `api` service bind-mounts `./backend:/app/backend` for hot reload — but the entrypoint runs `uvicorn main:app --reload` from `/app`, not `/app/backend`. The bind mount does not overlay the project root, so `--reload` will silently watch a partially-correct directory.**
  - File: `docker-compose.yml:140-156` (`volumes: - ./backend:/app/backend`, `command: uvicorn main:app --reload`).
  - Problem: The Dockerfile's `WORKDIR` is `/app` and `uv sync --frozen --no-dev` installs the project (so `main.py` is at `/app/main.py`). The bind mount `./backend:/app/backend` overlays the host's `backend/` onto `/app/backend/` inside the container, leaving `/app/main.py`, `/app/app/`, `/app/packages/`, `/app/alembic/`, etc. from the *image* — but the bind mount shadows nothing because they are under `/app/`, not `/app/backend/`. So when uvicorn auto-reloads, it watches `/app/backend/` (because that is the CWD due to `uv run`), and changes to `main.py` or `app/` will not trigger a reload.
  - Why it matters in prod: Dev experience only, but consistently broken — operators will conclude "hot reload is broken" and edit production code without verification. Also, since `/app/packages` is from the image (not bind-mounted), edits to `packages/shared/*` will not reload either.
  - Fix: Either change the bind mount to `./backend:/app` (covers the whole project) and keep `WORKDIR=/app`, or remove the bind mount entirely and rely on `docker compose watch` for live reload. If you keep the bind mount, document the limitation.

- **[HI-007] No rate-limiting at the proxy or app level for the public endpoints — `RATE_LIMIT_PER_MINUTE=60` in `.do/app.yaml` is set but it lives in the FaaS-era functions block and the FastAPI app's actual rate-limit values are not pinned in compose or env.**
  - File: `backend/proxy/nginx.conf` (no `limit_req`/`limit_req_zone`), `docker-compose.yml:129-138` (no rate-limit env), `.do/app.yaml:110-118` (rate-limit envs only inside the stale functions block).
  - Problem: Rate-limit middleware exists in the FastAPI app (`app/core/rate_limit.py`), but it is not configured in any deployable spec. If it defaults to "off" or to a permissive limit, an attacker can DoS the single FastAPI container.
  - Why it matters in prod: No defence-in-depth at the edge; one runaway client can starve all other users.
  - Fix: Add `limit_req_zone $binary_remote_addr zone=wimt:10m rate=30r/s;` and `limit_req zone=wimt burst=60 nodelay;` to nginx.conf inside the `location /` block. Pin `RATE_LIMIT_PER_MINUTE`/`RATE_LIMIT_WINDOW_SECONDS`/`RATE_LIMIT_MAX_REQUESTS` in both the App Platform service spec and `docker-compose.yml`.

- **[HI-008] The `frontend` compose service uses `oven/bun:1` (a floating tag) instead of `oven/bun:1.3.6` (or whatever matches `bun.lockb` version).**
  - File: `docker-compose.yml:163` (`image: oven/bun:1`).
  - Problem: The comment claims they pinned away from `bun:1.1` because the lockfile moved to 1.3+, but `oven/bun:1` still floats (today it is 1.3.x; tomorrow it might be 2.0). `bun install --no-save` (line 183) explicitly tolerates lockfile drift.
  - Why it matters in prod: Local-dev only today, but copy-paste into a VPS gets you non-reproducible builds.
  - Fix: Pin to `oven/bun:1.3.6` (or whatever the lockfile demands). Keep `bun.lockb` parsed. Remove `--no-save` once pinned.

- **[HI-009] `doctl apps create-deployment --force-rebuild` is unconditionally applied on every CI deploy — even when only `backend/scripts/*.sh` or `docs/` changed.**
  - File: `.github/workflows/deploy.yml:5-7` (the `paths` filter restricts to `backend/**`, which is good), `backend/scripts/deploy.sh:151` (`--force-rebuild`).
  - Problem: The CI path filter is correct, so docs-only changes will not trigger a deploy. But once in the deploy job, `--force-rebuild` always builds a fresh image even if only `tests/conftest.py` changed. With App Platform, a new image push and `create-deployment` (without `--force-rebuild`) will trigger a re-deploy because App Platform detects the new image tag. `--force-rebuild` is also incompatible with the App Platform build cache, increasing deploy time and cost.
  - Why it matters in prod: Slower deploys, higher App Platform build-minute cost, no functional benefit over the default behaviour.
  - Fix: Remove `--force-rebuild`. Rely on the unique `IMAGE_TAG=${{ github.sha }}` to force a redeploy. Optionally add `--wait` so `create-deployment` blocks until the new revision is live.

- **[HI-010] The App Platform deploy workflow has no concurrency guard — pushing two commits to `main` within seconds will race two `doctl apps create-deployment` invocations.**
  - File: `.github/workflows/deploy.yml` (no `concurrency:` block).
  - Problem: Without `concurrency:` GitHub Actions will run two deploy jobs in parallel. Two concurrent `doctl apps create-deployment` against the same `DO_APP_ID` will both succeed; whichever finishes second wins on the live revision. With `--force-rebuild` this becomes two parallel image builds against the same registry tag (race condition: both push, last writer wins).
  - Why it matters in prod: Non-deterministic deploys; on rollback (CR-004) the operator does not know which revision is "previous".
  - Fix: Add `concurrency: { group: deploy-${{ github.ref }}, cancel-in-progress: false }` to the workflow. With `cancel-in-progress: false` the second push waits for the first to finish.## Medium (should fix soon)

- **[ME-001] `backend/scripts/dev.sh` is dead/legacy code that creates containers named `whatismytip-postgres`/`whatismytip-redis` using a different password (`whatismytip`/`whatismytip`) than the canonical `docker-compose.yml` (`wimt`/`wimt_dev_password`).**
  - File: `backend/scripts/dev.sh:39-54`.
  - Problem: Running this script creates DB containers that will not match the credentials in `docker-compose.yml` or the `.env.example` file. The "Test a function: `doctl serverless functions invoke api/games ...`" guidance is FaaS-era and should not be in any 2026-era dev script.
  - Fix: Delete `backend/scripts/dev.sh`, or rewrite it to delegate to `docker compose up -d postgres redis`. Remove the FaaS-era guidance.

- **[ME-002] `backend/scripts/setup-db.sh` parses `DATABASE_URL` with `sed` regexes that are fragile and do not handle URL-encoded passwords, IPv6 hosts, or query-string parameters.**
  - File: `backend/scripts/setup-db.sh:34-38`, `backend/scripts/setup-db.sh:47`, `backend/scripts/setup-db.sh:51`, `backend/scripts/setup-db.sh:75`.
  - Problem: A password containing `:`, `@`, `/`, or `?` (unlikely but allowed by RFC 3986) would break the parsing. The shell-quoted `psql` URI also exposes the password to `psql`'s argv (visible in `ps`/`/proc`). The script then prints the DB URL components to stdout (line 40) — a minor secret-leak risk in shared terminals.
  - Fix: Use Python (`urllib.parse.urlparse`) or rely on `psql` reading `PGPASSWORD` and `PGDATABASE`/`PGHOST`/`PGUSER`/`PGPORT` env vars. Do not echo the parsed credentials.

- **[ME-003] `backend/Dockerfile` has no explicit `apt-get install` step — it inherits the `python:3.12-slim` base which is fine, but the runtime stage does not `apt-get update && apt-get install -y --no-install-recommends` anything for `curl`/`wget` (used by HEALTHCHECK) because urllib is stdlib.**
  - File: `backend/Dockerfile:42-72`.
  - Problem: The HEALTHCHECK uses `python -c "import urllib.request; ..."` — good, no `curl` needed. But if anyone later switches to `curl`, they will add it without `--no-install-recommends`. There is also no `apt-get clean && rm -rf /var/lib/apt/lists/*` (not strictly needed because `python:3.12-slim` is already slim, but worth pinning for defence in depth).
  - Fix: If/when you add `apt-get install`, always pair with `--no-install-recommends` and `rm -rf /var/lib/apt/lists/*`. Add a comment in the Dockerfile stating the policy.

- **[ME-004] `backend/Dockerfile` does not set `LABEL` (owner, version, source). `EXPOSE 8000` is present but does not match the port App Platform actually binds (8080 for the proxy, 8000 for FastAPI).**
  - File: `backend/Dockerfile:60-66`.
  - Problem: Minor — `EXPOSE` is documentation only. But adding `LABEL org.opencontainers.image.source="..."` and `LABEL org.opencontainers.image.revision=$IMAGE_TAG` makes the image self-identifying in `docker inspect`, App Platform's UI, and any registry audit log.
  - Fix: Add the OCI labels. The `IMAGE_TAG` arg can be passed via `--build-arg IMAGE_TAG=$(git rev-parse HEAD)`.

- **[ME-005] `backend/Dockerfile` `CMD` uses `["uvicorn", ...]` (exec form) — good — but the `--workers 2` is hardcoded and not justified by `instance_size_slug` on App Platform.**
  - File: `backend/Dockerfile:72`.
  - Problem: On App Platform `basic-xxs` (1 vCPU / 512 MB RAM), 2 workers is borderline — each uvicorn worker is a separate Python process. With 2 workers the container needs ~300 MB just for Python, leaving little headroom for the DB pool + Redis. A larger instance (`apps-s-1vcpu-1gb`) could justify 2-4 workers, but the magic number `2` is not documented anywhere.
  - Fix: Drive worker count via env (`WORKERS=${WORKERS:-2}`) or App Platform spec. Document the relationship to `instance_size_slug` in `docs/deployment.md`. Consider `--workers 1` for `basic-xxs`.

- **[ME-006] `backend/proxy/Dockerfile` uses `wget` for the HEALTHCHECK — `wget` is not in `nginx:1.27-alpine` by default. The `apk add wget` step is missing.**
  - File: `backend/proxy/Dockerfile:21-22`.
  - Problem: `wget --quiet --tries=1 --spider http://127.0.0.1:8080/healthz` will fail with "wget: not found" on every healthcheck interval, which means the proxy container will be perpetually reported "unhealthy" by anything inspecting Docker HEALTHCHECK state. App Platform may or may not act on this (the App Platform HTTP healthcheck is the one it uses), but anyone using `docker ps` will see `(unhealthy)` perpetually.
  - Fix: Either `RUN apk add --no-cache wget` in the Dockerfile, or switch the healthcheck to a different mechanism (e.g. `nginx -t` to validate config, or hit `/healthz` via `nc -z`). Cleanest: `apk add --no-cache wget curl`.

- **[ME-007] `backend/proxy/Dockerfile` does not explicitly run as a non-root user. The default `nginx:alpine` image does drop to the `nginx` user for workers, but the running nginx master stays as root unless you override.**
  - File: `backend/proxy/Dockerfile:7-24`.
  - Problem: The Dockerfile does not `USER nginx` or `USER 1000`. nginx master process retains root to bind port 8080 (fine — port ≥1024 does not need root), but the worker processes inherit the master's UID. On `nginx:1.27-alpine` the default config does set `user nginx;` so workers run as `nginx`, but the master still runs as root.
  - Fix: Add `USER nginx` after the `COPY` line (or use `nginx:1.27-alpine-slim` if available). Verify with `docker exec <id> ps aux`.

- **[ME-008] `backend/scripts/deploy.sh` does not handle CRLF line endings on Windows despite the project's `.gitattributes` mandating LF — if a developer clones on Windows with `core.autocrlf=true`, the deploy script's `#!/usr/bin/env bash` shebang gets `\r\n` and bash on Linux CI will choke.**
  - File: `.gitattributes:15`, `backend/scripts/deploy.sh:1`.
  - Problem: `.gitattributes` does enforce `*.sh text eol=lf`, so this *should* work — but the script itself has no `sed -i 's/\r$//'` defence and no `file` check at the top. If a Windows user commits the file with `core.autocrlf=true` and then someone clones with `core.autocrlf=false`, the file lands with CRLF and `bash deploy.sh` fails with `'$'\r': command not found`.
  - Fix: Add at the top of `backend/scripts/deploy.sh` (after the shebang) a `dos2unix` self-check: `case "$(head -c1 "$0" | od -An -c | tr -d ' ')" in *\\r*) echo "Refusing to run: file has CRLF endings"; exit 1;; esac`. Alternatively, rely on the test that already verifies this in CI.

- **[ME-009] `backend/scripts/deploy.sh`'s post-deploy health check polls `APP_URL/health` for 60 seconds, but App Platform's image build + container start routinely takes 2-5 minutes.**
  - File: `backend/scripts/deploy.sh:161-170`.
  - Problem: 60 seconds is too short. The poll will always time out, and the script will always print "Service did not become healthy within the timeout" — making it useless for humans and CI alike.
  - Fix: Increase to at least 300 seconds (50 retries × 6s, or 30 × 10s). Better: use `doctl apps create-deployment --wait` and only poll `/health` for the last 30 seconds of that wait.

- **[ME-010] `.do/frontend.yaml` references `repo: your-username/WhatIsMyTip` (line 19) — placeholder text that would cause App Platform to fail or, worse, create a fork.**
  - File: `.do/frontend.yaml:19`.
  - Problem: This is a template placeholder. If anyone runs this spec (via the legacy `scripts/deploy.sh`), App Platform will look for `your-username/WhatIsMyTip` and fail. If GH happens to have a user with that name, it might succeed against the wrong repo.
  - Fix: Either delete the file (CR-003) or replace with the real `repo: danielpaparo98/WhatIsMyTip`.

- **[ME-011] `.do/frontend.yaml` is a single-region (`sfo3`) spec while the current live app is in `syd` (per `.do/app.yaml:2`). Mixing the two would cause App Platform to create a new app in a new region.**
  - File: `.do/frontend.yaml:14`, `.do/app.yaml:2`.
  - Fix: Standardise on one region (`syd`) across both files, or delete (CR-003).

- **[ME-012] `.do/backend.Dockerfile` and `.do/frontend.Dockerfile` are FaaS-era artefacts that reference a path layout (`COPY backend/ ./`) incompatible with the current Dockerfile layout.**
  - File: `.do/backend.Dockerfile:9`, `.do/backend.Dockerfile:15`, `.do/frontend.Dockerfile:7`, `.do/frontend.Dockerfile:13`.
  - Problem: `.do/backend.Dockerfile` does `COPY backend/pyproject.toml backend/uv.lock ./` and `COPY backend/ ./`, but the real `backend/Dockerfile` uses `./` as the build context (since `docker build -f backend/Dockerfile backend/` sets the context to `backend/`). If App Platform ever reads these, it will fail.
  - Fix: Delete `.do/backend.Dockerfile` and `.do/frontend.Dockerfile` — the canonical Dockerfiles live in `backend/Dockerfile` and the frontend (no frontend Dockerfile today; the static site builds via `bun run generate`).

- **[ME-013] `.github/workflows/deploy.yml` references `DATABASE_URL` and `REDIS_URL` as `secrets` for the deploy job — but `backend/scripts/deploy.sh` only uses `DO_REGISTRY`, `DO_APP_ID`, and the URL-based ones for the post-deploy health check. The DATABASE_URL/REDIS_URL values never reach the App Platform runtime; they are dead variables.**
  - File: `.github/workflows/deploy.yml:85-90`, `backend/scripts/deploy.sh:90-100`.
  - Problem: Those secrets are passed in but `deploy.sh` only sources `.env` (which is gitignored and absent in CI). The App Platform component reads its own env from the App Platform spec, not from the deploy script.
  - Fix: Remove the unused `DATABASE_URL` and `REDIS_URL` from the deploy job's env. Either configure those envs on the App Platform service spec (via the App Platform UI or `doctl apps update --spec <spec>`), or pass them via `doctl apps update --env DATABASE_URL=<value> --env REDIS_URL=<value>` after the deploy.

- **[ME-014] `.github/workflows/cron.yml` `workflow_dispatch` has no `environment:` gate, no required-reviewer check, and no rate-limiting — any GitHub user with `workflow:write` permission can fire any of the four jobs at will.**
  - File: `.github/workflows/cron.yml:20-32`, `cron.yml:39-50`.
  - Problem: The workflow POSTs to a public endpoint protected only by the `X-API-Key` header. There is no log of which GitHub user triggered which job. A malicious actor who gains a single maintainer's PAT can trigger a tip-generation storm or a historic-refresh on the production DB. There is also no idempotency — repeated triggers create duplicate cron runs.
  - Fix: Add `environment: production` to the workflow (GitHub will require manual approval). Add an `inputs.user_note` and log it via a step that records the actor to the FastAPI request. Document the rate-limit / dedup behaviour in `app/cron/base.py`.

- **[ME-015] `.env.example` files omit several env vars actually used by the app and App Platform spec.**
  - File: `backend/.env.example:1-20`, `frontend/.env.example:1-22`.
  - Problem: `backend/.env.example` is missing: `ENVIRONMENT`, `CORS_ORIGINS`, `LOG_FORMAT`, `RATE_LIMIT_PER_MINUTE`, `RATE_LIMIT_WINDOW_SECONDS`, `RATE_LIMIT_MAX_REQUESTS`, `MAX_REQUEST_BODY_BYTES`, `CRON_ENABLED`, `CRON_TIMEZONE`, `CURRENT_SEASON`, all `CRON_*`, `JOB_*`, `ALERT_*`, `METRICS_*` settings. Anyone copying `.env.example` → `.env` for a fresh setup will silently get all defaults and may be surprised by cron firing on local dev. `frontend/.env.example` is missing `NODE_ENV` and several NUXT public vars the frontend actually reads.
  - Fix: Mirror the full env-var inventory from `.do/app.yaml` (minus the FaaS-only ones) into `backend/.env.example`. Add comments explaining each. For `frontend/.env.example`, mirror the frontend build-time vars from `.do/app.yaml:240-258`.

- **[ME-016] Hardcoded dev credentials `wimt_dev_password` and `dev_admin_key_change_me` appear in the repo's tracked dev config (docker-compose.yml) and the gitignored `.env`.**
  - File: `docker-compose.yml:53`, `docker-compose.yml:108`, `docker-compose.yml:130`, `docker-compose.yml:133`, `backend/.env:8`, `backend/.env:25` (the `.env` is gitignored but the `.env.example` shows the same).
  - Problem: The compose file uses `wimt:wimt_dev_password` — a weak default that any operator copying this compose file as a quasi-production setup will leave in place. The `ADMIN_API_KEY=dev_admin_key_change_me` in `docker-compose.yml:133` is even worse because it is silently usable in production if anyone mirrors the compose.
  - Fix: Generate `wimt_dev_password` and `dev_admin_key_change_me` from `openssl rand -hex 32` on first `docker compose up` if they are missing (using an entrypoint script), or document loudly in the compose header that these are dev-only defaults and must be rotated.

- **[ME-017] `docker-compose.yml` does not set a `network:` for segmentation — all five services share the default bridge, and `postgres`/`redis` are reachable from `frontend`.**
  - File: `docker-compose.yml:47-187`.
  - Problem: In production-like patterns where someone runs `docker compose up` on a VPS, the `frontend` container can talk directly to `postgres:5432` over the bridge network. There is no reason for the frontend to ever talk to the DB.
  - Fix: Define two networks: `backend_net` (api, init-data, postgres, redis) and `frontend_net` (frontend, api). Attach `api` and `frontend` to both; attach DBs only to `backend_net`.

- **[ME-018] `backend/proxy/nginx.conf` `send_timeout 600s;` matches `proxy_read_timeout 600s;` but `proxy_connect_timeout 10s;` is much shorter — an upstream that takes >10s to TCP-accept will hit a 504 even though `proxy_read_timeout` would have allowed 600s.**
  - File: `backend/proxy/nginx.conf:33-36`.
  - Problem: Inconsistent timeouts can cause 504s during transient slowness. If a FastAPI worker is mid-restart, the new connection sits in the SYN queue, but nginx gives up at 10s.
  - Fix: Either raise `proxy_connect_timeout` to 60s, or document that 10s is intentional (the FastAPI container should be reachable within 10s via App Platform's internal DNS).

- **[ME-019] `backend/proxy/nginx.conf` has no `proxy_buffers`, `proxy_buffer_size`, `gzip`, or `gzip_types` configured — defaults are 8 × `proxy_buffer_size` = 8 × 4k = 32k, which is borderline for chart payloads that can be 50-200 kB JSON.**
  - File: `backend/proxy/nginx.conf:57-62`.
  - Problem: A 200 kB JSON response will be partially buffered and partially written to disk (nginx will spill to disk when response > `proxy_buffer_size`). This adds disk I/O on hot paths.
  - Fix: `proxy_buffer_size 16k; proxy_buffers 16 16k;` and `gzip on; gzip_types application/json text/plain; gzip_min_length 256;`.

## Low / nits

- **[LO-001] `backend/Dockerfile` line 56 does `COPY --from=builder --chown=appuser:appuser /app /app` — this brings the builder's `.venv` AND any test/utility files into the runtime image.**
  - Fix: See HI-001 — fix via `.dockerignore` rather than Dockerfile surgery.

- **[LO-002] `docker-compose.yml` line 130 hardcodes `wimt:wimt_dev_password@postgres:5432/whatismytip` in the api service even though it is a templated default — if someone overrides `.env` they expect the DB URL to come from `.env`.**
  - Fix: Read `DATABASE_URL` from `.env` via `${DATABASE_URL:-postgresql+asyncpg://wimt:wimt_dev_password@postgres:5432/whatismytip}`.

- **[LO-003] `docker-compose.yml` `init-data` uses `restart: "no"` but `depends_on` is `service_healthy` for postgres/redis — if postgres never becomes healthy, init-data is never started and api hangs forever.**
  - Fix: Add a `condition: service_started` fallback with an explicit wait loop, or document the recovery procedure.

- **[LO-004] `docker-compose.yml` `frontend` service mounts `./frontend:/app` AND `wimt_bun_cache:/root/.bun` AND `wimt_nuxt_cache:/app/.nuxt` AND `wimt_nuxt_output:/app/.output` — the last two are inside `./frontend`, so the named volumes shadow the bind mount for those paths. This is intentional for caching, but the order matters and is undocumented.**
  - Fix: Add a comment explaining the named-volume shadowing.

- **[LO-005] `backend/scripts/deploy.sh` line 73 `die()` echoes with emoji and ANSI; some CI runners strip ANSI.**
  - Fix: Use `printf '%s\n' "..." >&2` instead of `echo -e`.

- **[LO-006] `backend/scripts/deploy.sh` line 119 `run uv run pytest tests/unit/ -v --tb=short -q` mixes `-v` and `-q` (verbose and quiet). pytest treats `-q` as a synonym of `--quiet` and the output will be either verbose or quiet, not both.**
  - Fix: Drop one of `-v` or `-q`.

- **[LO-007] `backend/scripts/test.sh` is described as "FaaS backend tests" in its echo (line 12) — outdated label.**
  - Fix: Update to "FastAPI unit tests".

- **[LO-008] `backend/scripts/test_dockerfile.sh` does not push the smoke image to the registry; it only builds and runs locally. If the registry is the failure point (e.g. an expired doctl token), this won't catch it.**
  - Fix: Add an optional `--push` flag for CI usage.

- **[LO-009] `docker-compose.yml` `init-data` command is a long inline `sh -c` (lines 97-106) using `$$WIMT_INIT_MODE` for shell-escape inside Docker Compose. This is correct but fragile.**
  - Fix: Move the toggle logic into a shell script in `backend/scripts/` and `command: ["sh", "/app/scripts/init-data.sh"]`.

- **[LO-010] `.gitattributes` enforces LF for `Dockerfile` (single file name) but not `*.Dockerfile` — `.do/backend.Dockerfile` is not covered.**
  - Fix: Add `*.Dockerfile` to the `.gitattributes` rules.

- **[LO-011] `.gitignore` line 28 `.env.*` plus line 29 `!.env.example` plus line 30 `*.env` — three overlapping patterns. `*.env` will exclude `*.env.example` too unless the negation order is correct (it is, but the pattern is redundant).**
  - Fix: Clean up to just `.env` and `.env.*` with `!.env.example`.

- **[LO-012] `backend/scripts/deploy.sh` line 163 `curl -s -o /dev/null -w '%{http_code}' "${APP_URL}/health"` does not follow redirects and does not check the body — if the proxy returns a redirect or a `200` from a static 404 page, the check passes falsely.**
  - Fix: `curl -fsSL -o /dev/null -w '%{http_code}' "${APP_URL}/health"` (fail on non-2xx, follow redirects).

- **[LO-013] `backend/scripts/deploy.sh` does not `cleanup` partial images on failure — failed builds leave dangling `<none>` images that fill the local Docker cache.**
  - Fix: Add `trap 'docker rmi -f "${FULL_IMAGE}" 2>/dev/null || true' ERR` near the top.

- **[LO-014] `backend/scripts/run-migrations.sh` does not check whether `DATABASE_URL` is set before running alembic — alembic will fail with a less helpful error.**
  - Fix: `: "${DATABASE_URL:?DATABASE_URL must be set}"` after sourcing `.env`.

- **[LO-015] `.do/app.yaml` line 11 `- buildpack-stack=ubuntu-22` — App Platform buildpacks are deprecated for non-FaaS apps; this feature flag has no effect on the FastAPI container and is misleading.**
  - Fix: Delete the `features:` block (or remove this line) when rewriting the spec.

- **[LO-016] `backend/Dockerfile` does not pin the `python:3.12-slim` digest — a future `python:3.12-slim` rebuild (e.g. security patches) will produce a different image even with the same tag.**
  - Fix: Use `python:3.12-slim@sha256:...` for fully reproducible builds. Same for `python:3.12-slim` in the runtime stage.

- **[LO-017] `backend/proxy/Dockerfile` does not pin nginx digest either.**
  - Fix: `FROM nginx:1.27-alpine@sha256:...`.

- **[LO-018] `backend/Dockerfile` does not pass `--mount=type=cache` to `RUN uv sync` — uv rebuilds the entire wheel cache on every layer cache miss.**
  - Fix: `# syntax=docker/dockerfile:1.7` and `RUN --mount=type=cache,target=/root/.cache/uv uv sync ...`.

- **[LO-019] `docker-compose.yml` line 173-175 mounts `wimt_nuxt_cache` and `wimt_nuxt_output` as named volumes, but those paths are also covered by the `./frontend:/app` bind mount. With bind mount precedence, the named volumes are shadowed (bind mounts win on Linux). The named volumes are effectively useless.**
  - Fix: Either remove the bind mount and use named volumes only, or remove the redundant named volumes.

- **[LO-020] `backend/scripts/deploy.sh` line 95 uses `source <(grep -v '^#' .env | grep -v '^$')` — bash process substitution works, but if `.env` contains values with spaces or special chars without quoting, this breaks. The `grep -v '^#'` also strips inline comments after values.**
  - Fix: Use `set -a; source .env; set +a` (with `.env` written with proper quoting) and a dedicated `.env` loader.

## Strengths

- **Backend Dockerfile is well-structured**: multi-stage, non-root `appuser`, `HEALTHCHECK` present, `PYTHONDONTWRITEBYTECODE`, `PYTHONUNBUFFERED`, `uv` pinned to `ghcr.io/astral-sh/uv:0.5.11`, `uv sync --frozen --no-dev` in production — good hygiene.
- **nginx config has a self-contained `/healthz`** that does not depend on the upstream being reachable — orchestrator probes will succeed even if FastAPI is broken (defensive separation).
- **`.gitattributes` enforces LF on `*.sh`**, which prevents the most common Windows cross-platform deploy bug.
- **`.gitignore` excludes `.env`, `.env.*`, `bun.lockb`, `__pycache__/`, `node_modules/`, `data/`, `seed_data/`, etc.** — broad coverage.
- **`backend/tests/unit/test_no_hardcoded_secrets.py` exists** and validates scanner logic with both positive and negative cases — this is a quality safety net (just needs broader coverage per CR-006).
- **`backend/tests/unit/test_docker_compose_config.py` and `test_proxy_config.py` exist** and validate structural invariants of `docker-compose.yml` and `nginx.conf` — configuration drift is caught at PR time.
- **`backend/scripts/deploy.sh` supports `--dry-run`** and has a reasonable pre-flight check (docker, doctl, uv, auth).
- **`docker-compose.yml` uses healthchecks** on postgres and redis and `service_completed_successfully` on `init-data` — startup ordering is correct.
- **GitHub Actions deploy workflow runs `test → docker-smoke → deploy` sequentially** with proper `needs:` chaining.
- **`pyproject.toml` excludes integration tests by default** (`-m "not integration"`) so CI does not accidentally require a live DB.
- **TLS is correctly offloaded to App Platform** — the nginx proxy listens on plain HTTP 8080, which is right for the App Platform internal network.
- **`scripts/deploy.sh` and `backend/scripts/deploy.sh` are clearly separated** with the legacy file at the root and the modern file under `backend/scripts/`.

## Stale files that need rewriting or deletion

- **`.do/app.yaml`** — FaaS-era spec referencing deleted `backend/project.yml`. Contains embedded `EV[...]` secrets. **Action: delete and replace if a single source of truth is desired.**
- **`.do/frontend.yaml`** — Frontend-only legacy spec with placeholder repo name. **Action: delete.**
- **`.do/backend.Dockerfile`** — FaaS-era Dockerfile referencing `backend/...` paths inconsistent with current build context. **Action: delete.**
- **`.do/frontend.Dockerfile`** — Multi-stage bun Dockerfile that is unused (the canonical frontend builds via `bun run generate` as a static site). **Action: delete.**
- **`scripts/deploy.sh`** — Legacy FaaS deploy that calls `doctl apps create/update --spec .do/app.yaml`. **Action: delete.**
- **`backend/scripts/dev.sh`** — Creates `whatismytip-postgres`/`whatismytip-redis` containers with mismatched credentials and references FaaS-era `doctl serverless` commands. **Action: delete or rewrite to delegate to `docker compose up -d`.**

## Specific things to verify by running

- [ ] `docker build -t whatismytip-api -f backend/Dockerfile backend/` — confirm the image builds in <2 minutes and the final image is <400 MB.
- [ ] `docker run --rm -p 8000:8000 whatismytip-api` — confirm `curl http://localhost:8000/health` returns 200 within 10 seconds.
- [ ] `docker exec <id> ps aux` — confirm the `uvicorn` worker runs as `appuser` (UID not 0).
- [ ] `docker exec <id> ls /app/tests/` — confirm that `tests/` is NOT present in the runtime image (regression guard for HI-001).
- [ ] `docker buildx imagetools inspect whatismytip-api:latest` — confirm OCI labels (source, revision) are present (ME-004).
- [ ] `docker compose up -d` then `curl -fsS http://localhost:8000/health` — confirm the stack starts end-to-end with `CRON_ENABLED=false`.
- [ ] `docker compose exec postgres pg_isready -U wimt -d whatismytip` — confirm Postgres is reachable from inside the compose network but NOT from the host LAN if `ports:` are removed (HI-005).
- [ ] `bash backend/scripts/deploy.sh --dry-run` — confirm all destructive steps (docker push, doctl create-deployment) are echoed and skipped.
- [ ] `bash backend/scripts/deploy.sh` against a staging `DO_APP_ID` — confirm the build, push, deploy, and `/health` poll work end-to-end.
- [ ] `doctl apps list-deployments <DO_APP_ID>` after a deploy — confirm `IMAGE_TAG` shows the expected git SHA.
- [ ] `curl -fsS https://whatismytip.com/health` from an external host — confirm 200, no redirect, and the response body matches the `/health` endpoint contract.
- [ ] `curl -fsSI https://whatismytip.com/` — confirm `X-Content-Type-Options`, `X-Frame-Options`, `Referrer-Policy`, `Strict-Transport-Security` headers are present (regression guard for CR-005).
- [ ] `git ls-files | grep -E '(\.env|secret|credential)'` — confirm no secrets are tracked.
- [ ] `uv run pytest backend/tests/unit/test_no_hardcoded_secrets.py backend/tests/unit/test_docker_compose_config.py backend/tests/unit/test_proxy_config.py backend/tests/unit/test_deploy_script.py -v` — confirm the configuration-contract tests all pass.
- [ ] `bash -n backend/scripts/deploy.sh && shellcheck backend/scripts/deploy.sh` — confirm shell syntax and shellcheck cleanliness.

## Production-deployment pre-flight checklist

- [ ] CR-001 — `.do/app.yaml` deleted or rewritten; legacy FaaS layout cannot be recreated.
- [ ] CR-002 — All `EV[...]` secrets rotated; new secrets live only in GitHub Actions secrets; `.do/app.yaml` scrubbed.
- [ ] CR-003 — `scripts/deploy.sh` deleted; `DEPLOY.md` points operators at `backend/scripts/deploy.sh`.
- [ ] CR-004 — Rollback path documented and/or automated in `backend/scripts/deploy.sh`; smoke step fails the workflow on miss.
- [ ] CR-005 — nginx security headers (`X-Content-Type-Options`, `X-Frame-Options`, `Referrer-Policy`, HSTS) added with `always`; test asserts presence.
- [ ] CR-006 — `test_no_hardcoded_secrets.py` regex broadened to include `EV[...]`, `sk-or-...`, `ghp_...`, `AKIA...`.
- [ ] HI-001 — `backend/.dockerignore` created; runtime image no longer contains `tests/`, debug scripts, `.env`, `__pycache__/`.
- [ ] HI-002 — Global `proxy_buffering off` removed.
- [ ] HI-003 — `scripts/deploy.sh` deleted; docs updated.
- [ ] HI-004 — `doctl registry login` check added to deploy script.
- [ ] HI-005 — `no-new-privileges`, `read_only`, `cap_drop` on compose services; Postgres/Redis host-port mappings removed by default.
- [ ] HI-006 — Compose `api` bind mount corrected to `./backend:/app` or documented as a known limitation.
- [ ] HI-007 — nginx `limit_req_zone` added; rate-limit envs pinned in compose and App Platform spec.
- [ ] HI-008 — `oven/bun:1` pinned to `oven/bun:1.3.6`.
- [ ] HI-009 — `--force-rebuild` removed from `doctl apps create-deployment`.
- [ ] HI-010 — `concurrency:` block added to `.github/workflows/deploy.yml`.
- [ ] ME-001 through ME-019 reviewed and resolved or explicitly deferred.
- [ ] LO-001 through LO-020 reviewed and resolved or explicitly deferred.
- [ ] All `backend/tests/unit/test_*.py` pass locally and in CI.
- [ ] `docker buildx build --platform linux/amd64 -f backend/Dockerfile backend/` produces a working image on amd64 (App Platform's target).
- [ ] App Platform environment variables (`DATABASE_URL`, `REDIS_URL`, `ADMIN_API_KEY`, `OPENROUTER_API_KEY`, `CORS_ORIGINS`, `CRON_ENABLED`, `LOG_FORMAT`, all `CRON_*`, `JOB_*`, `ALERT_*`, `METRICS_*`) are configured on the production service via the App Platform UI or `doctl apps update --spec`.
- [ ] `DIGITALOCEAN_ACCESS_TOKEN`, `DO_REGISTRY`, `DO_APP_ID` GitHub Actions secrets are scoped to the `production` environment.
- [ ] A runbook exists for: rolling back a bad deploy, rotating any leaked secret, scaling workers, draining traffic during a DB migration.
- [ ] Log drain / alerting is configured (e.g. `ALERT_WEBHOOK_URL` points at a real Slack/PagerDuty hook).
- [ ] Backup/restore procedure for the Postgres volume is documented and tested.
- [ ] Cron jobs (`daily-sync`, `match-completion`, `tip-generation`, `historic-refresh`) have been exercised at least once in staging.
- [ ] `umami.is` analytics and any third-party integrations are confirmed working with the new container URLs.
- [ ] TLS certificate is valid for `whatismytip.com` and `www.whatismytip.com`; HSTS preload submission considered.
- [ ] DNS A/CNAME for `whatismytip.com` points at the App Platform edge; CAA records restrict CAs to `letsencrypt.org` and `digicert.com`.
