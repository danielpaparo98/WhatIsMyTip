# Backend API Endpoint Inventory

> **DESIGN HISTORY — superseded by current implementation.**  The endpoint inventory in this
> file is a snapshot from the integration test sweep.  The live, authoritative endpoint list
> is always in [`docs/api.md`](../docs/api.md) and the live route table is printed by
> [`backend/list_routes.py`](../backend/list_routes.py:1) at runtime.

> Generated for the upcoming integration test sweep. All routes are declared in
> FastAPI routers under [`backend/app/api/`](backend/app/api/__init__.py:1) and
> mounted in [`backend/main.py`](backend/main.py:1). The helper script
> [`backend/list_routes.py`](backend/list_routes.py:1) prints the live route
> table at runtime — use it as a sanity check before curling.

---

## Application Wiring

| Aspect | Value | Source |
|---|---|---|
| App title / version | `WhatIsMyTip API` / `0.1.0` | [`backend/main.py`](backend/main.py:47) |
| Global URL prefix | **None** — routers declare their own (`/api/...`) | [`backend/main.py`](backend/main.py:170) |
| Lifespan | Acquires async SQLAlchemy engine + Redis client + APScheduler; never aborts startup on transient dep failure | [`backend/app/core/lifespan.py`](backend/app/core/lifespan.py:30) |
| CORS | `allow_origins` from `settings.cors_origins_list`; no credentials; `allow_methods=["*"]`; `max_age=600` | [`backend/main.py`](backend/main.py:66) |
| Custom middleware (outer → inner) | `RequestIDMiddleware` (UUID4 + `X-Request-ID`), `RequestSizeLimitMiddleware` (413 if body > `settings.max_request_body_bytes`), `SecurityHeadersMiddleware` (CSP/HSTS/X-Frame/etc.) | [`backend/main.py`](backend/main.py:83), [`backend/app/core/middleware.py`](backend/app/core/middleware.py:46) |
| Rate limiting | `slowapi` limiter on real client IP, default `${RATE_LIMIT_MAX_REQUESTS}/${RATE_LIMIT_WINDOW_SECONDS} seconds` (typically 60/min); exceeded → 429 via global handler | [`backend/app/core/rate_limit.py`](backend/app/core/rate_limit.py:19) |
| Exception handlers | `BackendServiceError` → mapped status; `RequestValidationError` → 422; generic `Exception` → 500 with `request_id` | [`backend/main.py`](backend/main.py:103) |
| Health probe deps | Reads `app.state.engine` and `app.state.redis` (set by `lifespan`) | [`backend/app/api/health.py`](backend/app/api/health.py:50) |

### Mount order (`include_router` calls in `main.py`)

| # | Router | Prefix | Source |
|---|---|---|---|
| 1 | `health_router` | *(none)* | [`backend/main.py`](backend/main.py:160) |
| 2 | `games_router` | `/api/games` | [`backend/main.py`](backend/main.py:170) |
| 3 | `tips_router` | `/api/tips` | [`backend/main.py`](backend/main.py:171) |
| 4 | `backtest_router` | `/api/backtest` | [`backend/main.py`](backend/main.py:172) |
| 5 | `admin_router` | `/api/admin` | [`backend/main.py`](backend/main.py:173) |

### Auto-mounted FastAPI routes (always present)

| Method | Path | Purpose | Source |
|---|---|---|---|
| `GET` | `/openapi.json` | OpenAPI 3 schema | [`backend/main.py`](backend/main.py:53) |
| `GET` | `/docs` | Swagger UI | [`backend/main.py`](backend/main.py:51) |
| `GET` | `/redoc` | ReDoc | [`backend/main.py`](backend/main.py:52) |

---

## Auth Model

| Dependency | Where applied | Behaviour | Source |
|---|---|---|---|
| `require_admin_key` (alias for `Depends(verify_api_key)`) | `APRouter(dependencies=[require_admin_key])` on the **admin** router; per-route on `POST /api/backtest/run` | Reads `X-API-Key` header, constant-time compares to `settings.admin_api_key`; raises `BackendServiceError(401, "invalid_api_key")` on missing/empty/wrong key | [`backend/app/core/security.py`](backend/app/core/security.py:19) |

> `POST /api/tips/generate` is **intentionally public** (R1 reverted): no `X-API-Key` is read or required. The endpoint calls OpenRouter (real cost) and writes to the DB, but the deliberate design is that any caller may trigger tip generation for a season/round that has no tips yet. The only protection is the per-IP rate limit of 10 requests/minute.

---

## Endpoint Inventory

**Total routes exposed by the app: 21** (3 auto-generated + 18 declared)
| Group | Count | Path prefix |
|---|---|---|
| Auto (docs/spec) | 3 | `/`, `/docs`, `/openapi.json` |
| Health | 1 | `/health` |
| Games | 3 | `/api/games/...` |
| Tips | 4 | `/api/tips/...` |
| Backtest | 7 | `/api/backtest/...` |
| Admin | 3 | `/api/admin/...` |

### 1. `GET /health` — Liveness probe

| Field | Value |
|---|---|
| **Source** | [`backend/app/api/health.py`](backend/app/api/health.py:50) — `@router.get("/health")` line 50 |
| **Auth** | None |
| **Rate limit** | Default global limit |
| **Request** | No params, no body |
| **Response 200** | `{ "status": "healthy"\|"degraded", "db": "ok"\|"error", "redis": "ok"\|"error", "version": "0.1.0", "request_id": "..." }` |
| **Example** | `curl http://localhost:8000/health` |
| **Notes** | Always returns 200; never takes the pod out of rotation. Depends on `app.state.engine` / `app.state.redis` being set by `lifespan`. Status is `"degraded"` if either dep probe fails. |

### 2. `GET /api/games/` — List games

| Field | Value |
|---|---|
| **Source** | [`backend/app/api/games.py`](backend/app/api/games.py:57) — `@router.get("/", response_model=None)` line 57 |
| **Auth** | None |
| **Rate limit** | Default global limit |
| **Request (query)** | `season: int? (ge=2000)`, `round: int? (ge=1, alias for round_id)`, `upcoming: bool=false`, `latest: bool=false`, `limit: int=50 (1..500)` |
| **Response 200** | Two shapes depending on `latest`:<br>• `latest=true` → `{ season, round_id, game_count, is_current_year, has_upcoming }` (round-locator)<br>• otherwise → `GameListResponse`: `{ games: GameResponse[], count }` |
| **Example** | `curl 'http://localhost:8000/api/games/?season=2025&round=1'` |
| **Notes** | Branching: `upcoming` → `GameCRUD.get_upcoming`; `season+round` → `get_by_round`; `season` only → `get_by_season`; default → `get_upcoming`. `latest` short-circuits all others and returns locator. |

### 3. `GET /api/games/{slug}` — Single game

| Field | Value |
|---|---|
| **Source** | [`backend/app/api/games.py`](backend/app/api/games.py:175) — `@router.get("/{slug}")` line 175 |
| **Auth** | None |
| **Rate limit** | Default global limit |
| **Request (path)** | `slug: str (1..128)` |
| **Response 200** | `GameResponse` (single game) |
| **Response 404** | `{ code: "not_found", message: "Game not found", ... }` |
| **Example** | `curl http://localhost:8000/api/games/richmond-v-carlton-r1-2025` |
| **Notes** | — |

### 4. `GET /api/games/{slug}/detail` — Full game detail

| Field | Value |
|---|---|
| **Source** | [`backend/app/api/games.py`](backend/app/api/games.py:195) — `@router.get("/{slug}/detail")` line 195 |
| **Auth** | None |
| **Rate limit** | Default global limit |
| **Request (path)** | `slug: str (1..128)` |
| **Response 200** | `GameDetailResponse`: `{ game, tips, model_predictions, match_analysis, weather }` (any nested nullable) |
| **Response 404** | `{ code: "not_found", ... }` when slug does not match a game |
| **Example** | `curl http://localhost:8000/api/games/richmond-v-carlton-r1-2025/detail` |
| **Notes** | Composes 4 sub-queries (tips, model predictions, match analysis, weather) — heaviest read path. Slug has no pattern constraint, so any non-empty path segment works. |

### 5. `GET /api/tips/` — List tips

| Field | Value |
|---|---|
| **Source** | [`backend/app/api/tips.py`](backend/app/api/tips.py:58) — `@router.get("/")` line 58 |
| **Auth** | None |
| **Rate limit** | Default global limit |
| **Request (query)** | `season: int? (ge=2000)`, `round: int? (ge=1, alias round_id)`, `heuristic: str? (regex ^(best_bet\|high_risk_high_reward\|yolo)$)`, `limit: int=100 (1..500)` |
| **Response 200** | `TipListResponse`: `{ tips: TipResponse[], count }` |
| **Example** | `curl 'http://localhost:8000/api/tips/?heuristic=best_bet&limit=20'` |
| **Notes** | Branching: `season+round` → `get_by_round`; `heuristic` → `get_by_heuristic(limit)`; default → `get_by_heuristic("best_bet", limit=50)`. |

### 6. `GET /api/tips/games-with-tips` — Games-with-tips for a round

| Field | Value |
|---|---|
| **Source** | [`backend/app/api/tips.py`](backend/app/api/tips.py:103) — `@router.get("/games-with-tips")` line 103 |
| **Auth** | None |
| **Rate limit** | Default global limit |
| **Request (query)** | `season: int (REQUIRED, ge=2000)`, `round: int (REQUIRED, ge=1, alias round_id)`, `heuristic: str="best_bet" (regex)` |
| **Response 200** | `{ games: [{ id, slug, squiggle_id, round_id, season, home_team, away_team, home_score, away_score, venue, date, completed, tip?, model_predictions }], count }`. When round has no games → `{ games: [], count: 0 }`. |
| **Response 422** | FastAPI auto — missing `season` or `round`. |
| **Example** | `curl 'http://localhost:8000/api/tips/games-with-tips?season=2025&round=1'` |
| **Notes** | Uses `SELECT ... FOR UPDATE` inside a transaction to lock games for the round (prevents concurrent tip generation races). **Note:** `heuristic` is accepted as a query param but the docstring notes it gates which tips are attached; the default branch is "all tips for the round" because of the code's `if heuristic:` check on line 146. |

### 7. `GET /api/tips/{heuristic}` — Tips by heuristic

| Field | Value |
|---|---|
| **Source** | [`backend/app/api/tips.py`](backend/app/api/tips.py:219) — `@router.get("/{heuristic}")` line 219 |
| **Auth** | None |
| **Rate limit** | Default global limit |
| **Request (path)** | `heuristic: str (regex ^(best_bet\|high_risk_high_reward\|yolo)$)` |
| **Request (query)** | `limit: int=100 (1..500)` |
| **Response 200** | `TipListResponse`: `{ tips: TipResponse[], count }` |
| **Response 422** | Auto — heuristic not in the allow-list. |
| **Example** | `curl http://localhost:8000/api/tips/yolo?limit=50` |
| **Notes** | Ordering risk: this route is a catch-all `{heuristic}` and is registered *after* the static `/games-with-tips`. FastAPI prefers static segments, so `/games-with-tips` is still reachable, but the path conflict is worth knowing when reading OpenAPI output. |

### 8. `POST /api/tips/generate` — Generate tips for a round

| Field | Value |
|---|---|
| **Source** | [`backend/app/api/tips.py`](backend/app/api/tips.py:261) — `@router.post("/generate")` + `@_post_generate_limiter.limit("10/minute")` lines 261-262 |
| **Auth** | **None** (intentionally public — no `X-API-Key` is read or required; any caller may trigger tip generation for a season/round that has no tips yet) |
| **Rate limit** | **10/minute per client IP** (slowapi) |
| **Request (body)** | `TipGenerateRequest`: `{ season: int (REQUIRED, ge=2000), round_id: int? (ge=1), heuristics: list[str]? (allow-list), regenerate: bool=false }` |
| **Example body** | ```json\n{ "season": 2025, "round_id": 1, "regenerate": false, "heuristics": ["best_bet", "yolo"] }\n``` |
| **Response 200** | `{ status: "success", season, round_id, games_processed, tips_created, tips_skipped, tips_updated, errors: [] }` |
| **Response 422** | `invalid_heuristics` if any heuristic not in `["best_bet","high_risk_high_reward","yolo"]`; also `validation_error` when `round_id` is `null` (re-validated inside the handler, line 287). |
| **Response 404** | `not_found` when no games exist for the round. |
| **Response 429** | Rate limit (10/min/IP) exceeded. |
| **Example** | `curl -X POST -H 'Content-Type: application/json' -d '{"season":2025,"round_id":1}' http://localhost:8000/api/tips/generate` |
| **Notes / risks** | **Intentionally public.**  The earlier "R1 follow-up" that added `require_admin_key` to this route was reverted: the deliberate design is that any user should be able to trigger tip generation when no tips exist for a period.  The only protection is the per-IP rate limit (10/min).  Pinned by `tests/unit/test_app_api_tips.py::TestGenerateTips` and `tests/integration/test_api_tips_integration.py::TestGenerateTipsPublic`.  Triggers `TipGenerationService.generate_for_round` — may invoke OpenRouter for AI explanations. |

### 9. `GET /api/backtest/` — Deprecated

| Field | Value |
|---|---|
| **Source** | [`backend/app/api/backtest.py`](backend/app/api/backtest.py:67) — `@router.get("/")` line 67 |
| **Auth** | None |
| **Rate limit** | Default global limit |
| **Request** | None |
| **Response 200** | Always `BacktestListResponse { results: [], count: 0 }` |
| **Example** | `curl http://localhost:8000/api/backtest/` |
| **Notes** | Marked DEPRECATED in source — kept for FaaS-parity. |

### 10. `GET /api/backtest/compare` — Compare heuristics for a season

| Field | Value |
|---|---|
| **Source** | [`backend/app/api/backtest.py`](backend/app/api/backtest.py:83) — `@router.get("/compare")` line 83 |
| **Auth** | None |
| **Rate limit** | Default global limit |
| **Request (query)** | `season: int (REQUIRED, ge=2000)` |
| **Response 200** | `{ season, comparison: { heuristic: {overall_accuracy, total_profit, ...} }, best_overall: { heuristic, accuracy, profit } }` |
| **Response 422** | Auto — missing/invalid `season`. |
| **Example** | `curl 'http://localhost:8000/api/backtest/compare?season=2024'` |
| **Notes** | `best_overall` is the heuristic with highest `overall_accuracy`; falls back to `null`/0.0 when no data. |

### 11. `GET /api/backtest/model-compare` — Compare ML models for a season

| Field | Value |
|---|---|
| **Source** | [`backend/app/api/backtest.py`](backend/app/api/backtest.py:125) — `@router.get("/model-compare")` line 125 |
| **Auth** | None |
| **Rate limit** | Default global limit |
| **Request (query)** | `season: int (REQUIRED, ge=2000)` |
| **Response 200** | `{ season, comparison: [ { model_name, overall_accuracy, total_profit, ... } ], best_overall: { model_name, accuracy, profit } }` |
| **Response 422** | Auto — missing/invalid `season`. |
| **Example** | `curl 'http://localhost:8000/api/backtest/model-compare?season=2024'` |
| **Notes** | Comparison list is sorted by `overall_accuracy` desc; `best_overall` = first element. |

### 12. `GET /api/backtest/table` — Round-by-round table

| Field | Value |
|---|---|
| **Source** | [`backend/app/api/backtest.py`](backend/app/api/backtest.py:163) — `@router.get("/table")` line 163 |
| **Auth** | None |
| **Rate limit** | Default global limit |
| **Request (query)** | `season: int (REQUIRED, ge=2000)` |
| **Response 200** | `BacktestTableResponse`: `{ season, heuristics: [ { heuristic, season, rounds: [ { round_id, tips_made, tips_correct, accuracy, profit } ], total_profit, total_accuracy } ] }` |
| **Response 422** | Auto — missing/invalid `season`. |
| **Example** | `curl 'http://localhost:8000/api/backtest/table?season=2024'` |
| **Notes** | Iterates over `service.orchestrator.get_available_heuristics()`. |

### 13. `GET /api/backtest/seasons` — List available seasons

| Field | Value |
|---|---|
| **Source** | [`backend/app/api/backtest.py`](backend/app/api/backtest.py:216) — `@router.get("/seasons")` line 216 |
| **Auth** | None |
| **Rate limit** | Default global limit |
| **Request** | None |
| **Response 200** | `{ available_years: int[], current_year: int }` (current_year = `datetime.now().year`) |
| **Example** | `curl http://localhost:8000/api/backtest/seasons` |
| **Notes** | — |

### 14. `GET /api/backtest/current-season` — YTD performance

| Field | Value |
|---|---|
| **Source** | [`backend/app/api/backtest.py`](backend/app/api/backtest.py:240) — `@router.get("/current-season")` line 240 |
| **Auth** | None |
| **Rate limit** | Default global limit |
| **Request** | None |
| **Response 200** | Shape of `BacktestService.get_current_season_performance(...)` — year-to-date aggregates across heuristics. |
| **Example** | `curl http://localhost:8000/api/backtest/current-season` |
| **Notes** | — |

### 15. `POST /api/backtest/run` — Run model backtest (admin)

| Field | Value |
|---|---|
| **Source** | [`backend/app/api/backtest.py`](backend/app/api/backtest.py:258) — `@router.post("/run", dependencies=[require_admin_key])` lines 258-261 |
| **Auth** | **`X-API-Key` header** (router-level `require_admin_key`) |
| **Rate limit** | Default global limit |
| **Request (body)** | `BacktestRunRequest`: `{ season: int (REQUIRED, ge=2000), round: int? (ge=1, alias round_id, unused), heuristic: str? (unused) }` |
| **Example body** | ```json\n{ "season": 2024, "round": 5, "heuristic": "best_bet" }\n``` |
| **Response 200** | `{ season, round, heuristic, count, results: [...] }` |
| **Response 401** | `invalid_api_key` (missing/wrong header) |
| **Response 422** | Pydantic validation (e.g. missing `season`) |
| **Example** | `curl -X POST -H 'Content-Type: application/json' -H 'X-API-Key: $ADMIN_API_KEY' -d '{"season":2024}' http://localhost:8000/api/backtest/run` |
| **Notes** | `round` and `heuristic` fields are accepted for FaaS parity but currently unused by `BacktestService.run_model_backtest`. |

### 16. `POST /api/admin/{job_name}/trigger` — Trigger a cron job (admin)

| Field | Value |
|---|---|
| **Source** | [`backend/app/api/admin.py`](backend/app/api/admin.py:71) — `@router.post("/{job_name}/trigger")` line 71 (router has `dependencies=[require_admin_key]` on line 63) |
| **Auth** | **`X-API-Key` header** (router-level) |
| **Rate limit** | Default global limit |
| **Request (path)** | `job_name: str` — one of `daily-sync`, `match-completion`, `tip-generation`, `historic-refresh` (allow-list `ALLOWED_JOB_NAMES` on line 53) |
| **Request (body)** | Optional `dict`, dispatched to a per-job Pydantic schema:<br>• `daily-sync` → `DailySyncTriggerRequest` (`{ season?: int }`)<br>• `match-completion` → `MatchCompletionTriggerRequest` (`{ buffer_minutes?: int }`)<br>• `tip-generation` → `TipGenerationTriggerRequest` (`{ season?: int, round_id?: int, regenerate?: bool }`)<br>• `historic-refresh` → `HistoricRefreshTriggerRequest` (`{ seasons?: str, round_id?: int, regenerate_tips?: bool }`) |
| **Response 200** | Per-job shape: e.g. daily-sync → `{ success, message, season, games_created, games_updated, games_skipped, games_failed, duration_seconds }`; tip-generation → `{ success, message, season, round_id, games_processed, tips_created, tips_skipped, tips_updated, model_predictions_created, model_predictions_updated, errors, duration_seconds }`; historic-refresh → `{ success, message, seasons_processed, games_synced, tips_generated, errors, duration_seconds, season_stats }`; match-completion → `{ success, message, games_checked, games_completed, games_already_completed, games_not_ready, games_failed, duration_seconds, elo_cache_updated }` |
| **Response 401** | `invalid_api_key` |
| **Response 422** | `invalid_job_name` for unknown name; Pydantic validation for malformed body |
| **Example** | `curl -X POST -H 'X-API-Key: $ADMIN_API_KEY' -H 'Content-Type: application/json' -d '{}' http://localhost:8000/api/admin/daily-sync/trigger` |
| **Notes** | Heavy operations: each may call the Squiggle API, regenerate tips, refresh Elo cache, etc. The `tip-generation` job can also invoke OpenRouter for explanations. |

### 17. `GET /api/admin/historic-refresh/progress` — Historic-refresh progress

| Field | Value |
|---|---|
| **Source** | [`backend/app/api/admin.py`](backend/app/api/admin.py:281) — `@router.get("/historic-refresh/progress")` line 281 |
| **Auth** | **`X-API-Key` header** (router-level) |
| **Rate limit** | Default global limit |
| **Request** | None |
| **Response 200** | Either the active progress payload `{ progress_id, operation_type, total_items, completed_items, status, started_at, completed_at, error_message, progress_percentage }`, or the null shape with `message: "No active historic refresh operation found"` when no op is in flight. |
| **Response 401** | `invalid_api_key` |
| **Example** | `curl -H 'X-API-Key: $ADMIN_API_KEY' http://localhost:8000/api/admin/historic-refresh/progress` |
| **Notes** | Sub-path `historic-refresh/progress` could theoretically collide with `POST /{job_name}/trigger`, but since the methods differ FastAPI routes them independently. |

### 18. `GET /api/admin/metrics` — Per-job execution metrics

| Field | Value |
|---|---|
| **Source** | [`backend/app/api/admin.py`](backend/app/api/admin.py:327) — `@router.get("/metrics")` line 327 |
| **Auth** | **`X-API-Key` header** (router-level) |
| **Rate limit** | Default global limit |
| **Request** | None |
| **Response 200** | `{ metrics: { "daily-sync": {...}, "match-completion": {...}, "tip-generation": {...}, "historic-refresh": {...} }, system: { python_version, platform }, alerting_enabled: bool }` |
| **Response 401** | `invalid_api_key` |
| **Example** | `curl -H 'X-API-Key: $ADMIN_API_KEY' http://localhost:8000/api/admin/metrics` |
| **Notes** | Per-job metrics come from `JobExecutionCRUD.get_job_metrics(job_name)`; alerting flag mirrors `settings.alert_enabled`. |

---

## Risks, Gotchas, and Things to Watch During the curl Sweep

| # | Category | Observation | Source |
|---|---|---|---|
| R1 | **Resolution:** Endpoint kept public | `POST /api/tips/generate` is intentionally public (no `X-API-Key` is read or required). Per-IP rate limit of 10/min is the only protection (see [`backend/app/api/tips.py`](backend/app/api/tips.py:261)). Rationale: any user should be able to trigger tip generation when no tips exist for a period. Pinned by `tests/unit/test_app_api_tips.py::TestGenerateTips::test_generate_tips_ignores_invalid_x_api_key_header` and `tests/integration/test_api_tips_integration.py::TestGenerateTipsPublic::test_generate_tips_does_not_require_auth`. | [`backend/app/api/tips.py:261`](backend/app/api/tips.py:261), [`backend/tests/unit/test_app_api_tips.py`](backend/tests/unit/test_app_api_tips.py), [`docs/api.md:163`](docs/api.md:163) |
| R2 | **Destructive POST** | `POST /api/admin/{job_name}/trigger` and `POST /api/backtest/run` mutate state (DB writes, external API calls). They are correctly gated by `require_admin_key`. **Curl them only with a real `X-API-Key`.** | [`backend/app/api/admin.py:71`](backend/app/api/admin.py:71), [`backend/app/api/backtest.py:258`](backend/app/api/backtest.py:258) |
| R3 | **Path-parameter shadowing** | `GET /api/tips/{heuristic}` (`tips.py:219`) is a catch-all that is registered after `GET /api/tips/games-with-tips` (`tips.py:103`). FastAPI's static-first matcher makes the live behaviour correct, but the OpenAPI ordering is a maintenance hazard — renaming `games-with-tips` could silently break it. | [`backend/app/api/tips.py:103`](backend/app/api/tips.py:103) |
| R4 | **Health always 200** | `/health` never returns a non-2xx status; check the `status` field (`"healthy"` / `"degraded"`) instead. | [`backend/app/api/health.py:50`](backend/app/api/health.py:50) |
| R5 | **Payload size cap** | `RequestSizeLimitMiddleware` rejects bodies above `settings.max_request_body_bytes` with 413 (Content-Length) or 422 (chunked). POSTs with very large bodies will be rejected before the handler runs. | [`backend/app/core/middleware.py:103`](backend/app/core/middleware.py:103) |
| R6 | **External dependencies** | The app talks to **Squiggle** (game sync), **OpenRouter** (AI tip explanations) and **PostgreSQL + Redis**. Endpoints that touch external services (e.g. `POST /api/admin/daily-sync/trigger`) will be slow or fail when those upstreams are flaky. | [`backend/app/api/admin.py:122`](backend/app/api/admin.py:122), [`backend/app/api/tips.py:309`](backend/app/api/tips.py:309) |
| R7 | **Required env vars** | `ADMIN_API_KEY`, `DATABASE_URL`, `REDIS_URL`, `CORS_ORIGINS`, `RATE_LIMIT_MAX_REQUESTS`, `RATE_LIMIT_WINDOW_SECONDS`, `MAX_REQUEST_BODY_BYTES`, `OPENROUTER_*`, `CURRENT_SEASON`, `HISTORIC_REFRESH_SEASONS`, `MATCH_COMPLETION_BUFFER_MINUTES`. If `ADMIN_API_KEY` is unset, **all admin endpoints return 401**. | [`backend/app/core/security.py:39`](backend/app/core/security.py:39), [`backend/.env.example`](backend/.env.example) |
| R8 | **Rate-limit headers** | slowapi adds `X-RateLimit-*` headers (per docs/limit). Tests should assert 429 on the 11th call to `POST /api/tips/generate` from a single IP. | [`backend/app/core/rate_limit.py:19`](backend/app/core/rate_limit.py:19) |
| R9 | **Locks `FOR UPDATE`** | `GET /api/tips/games-with-tips` opens a transaction with row locks on the round's games. Calling it concurrently with `POST /api/tips/generate` may block briefly. | [`backend/app/api/tips.py:128`](backend/app/api/tips.py:128) |
| R10 | **Response shape variability** | `list_games` returns different shapes based on `latest`. `games_with_tips` returns `{games, count}` with an empty list (not 404) when no games exist — a non-2xx test would fail. | [`backend/app/api/games.py:96`](backend/app/api/games.py:96), [`backend/app/api/tips.py:141`](backend/app/api/tips.py:141) |
| R11 | **Path param aliases** | Both `tips.py` and `games.py` accept `?round=` as the public-facing name, but the Python parameter is `round_id`. Don't confuse them when scripting curls. | [`backend/app/api/games.py:64`](backend/app/api/games.py:64), [`backend/app/api/tips.py:66`](backend/app/api/tips.py:66) |
| R12 | **Deprecated stub** | `GET /api/backtest/` always returns `{results:[], count:0}`. Don't assert on real data. | [`backend/app/api/backtest.py:67`](backend/app/api/backtest.py:67) |

---

## Suggested Test Ordering

Run curls in this order so dependencies and auth state are validated early, and rate-limit budgets aren't blown.

1. **Liveness (no auth, no DB dependency)**
   - `GET /openapi.json`
   - `GET /docs` *(HTML, just check 200)*
   - `GET /health` — expect 200 with `status: "healthy"` (or `degraded`)

2. **Public reads (no auth, DB required)**
   - `GET /api/backtest/seasons`
   - `GET /api/backtest/current-season`
   - `GET /api/backtest/compare?season=2024`
   - `GET /api/backtest/model-compare?season=2024`
   - `GET /api/backtest/table?season=2024`
   - `GET /api/backtest/` *(expect empty stub)*
   - `GET /api/games/?latest=true`
   - `GET /api/games/?upcoming=true`
   - `GET /api/games/?season=2025`
   - `GET /api/games/?season=2025&round=1`
   - `GET /api/games/{slug}` *(pick a real slug from the previous response)*
   - `GET /api/games/{slug}/detail`
   - `GET /api/tips/?heuristic=best_bet`
   - `GET /api/tips/?season=2025&round=1`
   - `GET /api/tips/games-with-tips?season=2025&round=1`
   - `GET /api/tips/best_bet?limit=10`
   - `GET /api/tips/yolo?limit=10`
   - `GET /api/tips/high_risk_high_reward?limit=10`

3. **Validation negatives (no auth)**
   - `GET /api/games/{slug}` with a non-existent slug → 404 `not_found`
   - `GET /api/games/{slug}/detail` with a non-existent slug → 404
   - `GET /api/backtest/compare` without `season` → 422
   - `GET /api/tips/games-with-tips` without `season` or `round` → 422
   - `GET /api/tips/bogus_heuristic` → 422 (path-pattern rejection)
   - `POST /api/tips/generate` with invalid body → 422

4. **Public, rate-limited write (intentionally no auth)**
   - `POST /api/tips/generate` *(rate-limited 10/min — fire at most ~9 times)*
   - Verify 429 on the 11th call within a minute
   - No `X-API-Key` is required: the endpoint is intentionally open (see R1)

5. **Admin (require `X-API-Key`)**
   - **Without key** (expect 401 on each):
     - `POST /api/admin/daily-sync/trigger`
     - `GET /api/admin/historic-refresh/progress`
     - `GET /api/admin/metrics`
     - `POST /api/backtest/run`
   - **With wrong key** → 401
   - **With correct key** (proceed only after the 401 sweep passes):
     - `GET /api/admin/metrics` *(cheap read — run first)*
     - `GET /api/admin/historic-refresh/progress` *(cheap read)*
     - `POST /api/admin/daily-sync/trigger` *(mutates; run last among reads)*
     - `POST /api/admin/tip-generation/trigger` *(heaviest — last)*
     - `POST /api/backtest/run` with `{ "season": 2024 }` *(heavy — last)*

6. **Final sanity**
   - Re-run `GET /health` to confirm the app is still up after the destructive POSTs.
   - Compare `list_routes.py` output to the table above to catch any drift.
