# Multi-Sport Refactor Plan

Status: **Active** · Owner: @danielpaparo98 · Created: 2026-09-22

Objective: uplift WhatIsMyTip from a single-competition AFL tipping app into a
sport-generic platform, so that adding local AFL competitions, other team sports,
and individual sports (golf, tennis) becomes a **data-entry + provider task**, not a
migration campaign. Full review findings are summarized inline; this plan is the
source of truth for sequencing.

---

## 1. Approved Decisions

| # | Decision | Choice |
|---|----------|--------|
| D1 | Database strategy | **Single shared DB**, shared tables + `sport_id` / `competition_id` scope columns (rejected: schema-per-sport, DB-per-sport) |
| D2 | Migration approach | **Consolidated baseline migration** (precedent: `0001_consolidated`) rather than 10–15 incremental ALTERs |
| D3 | Expansion order | (1) local AFL competition → (2) one other team sport → (3) tennis/golf |
| D4 | Execution | Phase 0 first on `feature/` branch, TDD, gitmoji + DCO commits |

---

## 2. Why (Review Summary)

Root cause of all sport coupling: **identity is stringly-typed**. There is no
`Sport`/`Competition`/`Season`/`Participant` entity; teams are `String(100)` columns
validated against a Python dict (`packages/shared/teams.py`). This already cost
2 of 9 migrations (0004, 0007) + a production logo outage, and the canonical team
map is duplicated in 4 places (`teams.py`, `scripts/load_csv_to_db.py`,
`frontend/composables/useTeamLogos.ts`, `scripts/download_logos.py`).

Key structural findings (verified 2026-09-22):

- **No sport scoping anywhere**: `BacktestResult` UQ `(season, round, heuristic)`,
  `ModelVersion` UQ `(model_name, version)`, `JobLock` UQ `(job_name)`, and every
  CRUD lookup silently mix rows once a second sport/competition exists.
  `EloCache` UQ is `team_name` alone — broken for multi-season *today*.
- **Game shape is team-vs-team**: `home_team`/`away_team` structural; golf/tennis
  break the `Game`, `Tip.selected_team`, and `ModelPrediction.winner` concepts.
- **Vendor dialect inside CRUD**: `crud/games.py` maps Squiggle fields
  (`hteam/ateam/hscore/complete`); provider IDs are columns (`squiggle_id`,
  `afltables_match_id`, `afltables_id`, `footywire_id`); `squiggle_id` is
  non-nullable in the public API contract. No `FeedProvider` abstraction exists.
- **ML interface is a facade**: `predict(game, db) -> (winner, conf, margin)` bakes
  AFL semantics; each model invents its own margin scale. Win probability is the
  only universal signal. Abstention has 3 competing mechanisms; 4 models silently
  vote home-team on crash.
- **Correctness bugs** (Phase 0 scope): missing `return game`, double prediction
  runs, no-op SQL WHERE in Elo, draws counted as losses / awarded to away team,
  stale season windows, unscheduled FootyWire/weather scraping (documented
  decision instead — see P0-6).
- **Docs drift**: `migrations.md` (2 vs 9 migrations), `backend.md` (nonexistent
  `factory` symbol, 9/17 models listed), README (4 vs 5 cron jobs),
  `pyproject.toml` (legacy `whatismytip-faas` name).

What survives intact: `BaseJob` framework, orchestrator session-per-model pattern,
point-in-time discipline, weighted-tip pure functions, weather entity, health
router, cache tiers, OpenRouter pipeline.

---

## 3. Target Conceptual Model (Phases 1–2 end state)

```
Sport (afl, rugby, soccer, golf, tennis, …)
  └─ Competition (sport_id, name, tier: national|state|local, format: rounds|tournament)
       └─ Season (competition_id, label, start_date, end_date)
            └─ Event (season_id, event_type: match|race|tournament_round,
                    starts_at timestamptz, venue_id?, status)
                 ├─ EventParticipant (event_id, participant_id, side: home|away|n/a,
                 │                     score, result)        ← replaces home/away columns
                 ├─ Prediction (event_id, model/heuristic, predicted_participant_id,
                 │              probability, score_projection?, payload JSONB)
                 │                                            ← merges Tip + ModelPrediction
                 └─ EventCondition (event_id, …, raw JSONB)   ← MatchWeather, genericized

Participant (kind: team|individual, sport_id, name)
  ├─ Team (club metadata) ── TeamAlias (alias, source)        ← teams.py becomes data
  └─ Individual (dob, height_cm, weight_kg)
       └─ Roster (team_participant_id, individual_id, season_id, position)

ParticipantMatchStats (event_id, participant_id, stats JSONB, stat_schema_version)
  ← PlayerMatchStats + PlayerAdvancedStats; shapes validated by versioned Pydantic schemas

SourceRef (source, external_id) UQ per entity                  ← replaces vendor-ID columns
RatingSnapshot (participant_id, season_id) UQ                  ← replaces EloCache
ModelVersion (model_name, competition_id, version) UQ
JobLock (job_name, competition_id) UQ
```

Cross-cutting runtime concepts:

- **`SportContext`**: `participant_model (team|individual)`, `has_draws`,
  `has_home_advantage`, `scoring_unit`, `season_calendar`, `venue_registry`,
  `participant_registry`, `feed_provider`, `cache_namespace`. One object replaces
  `_OFF_SEASON_MONTHS`, `settings.current_season`, `total_rounds=24`,
  `VENUE_COORDS`, `teams.py`, and global Redis key prefixes.
- **`Prediction` contract**: `{pick, probability, score_projection?, rationale}` +
  explicit `Abstained` — margin becomes sport-optional; abstention is in the ABC.
- **`FeedProvider` protocol**: `get_fixtures / get_results / get_participant_stats`;
  provider owns the vendor dialect (moved out of CRUD). `SquiggleProvider` first.
- **Repositories** between ML models and storage; point-in-time filtering enforced
  in one place. Models stop receiving raw `AsyncSession`.
- **Registries**: orchestrator takes `register_model(sport, model)`; weighted-tip
  feature names derive from the registry, not a hardcoded list.

---

## 4. Phases & Subtasks

### Phase 0 — Stabilize (branch: `feature/phase-0-stabilization`)

Goal: fix correctness bugs and drift so the refactor doesn't inherit landmines.
Every fix is TDD (failing test first), one logical commit per subtask.

| ID | Subtask | Files | Acceptance |
|----|---------|-------|------------|
| P0-1 | `update_game_completion` success path returns `None` — add `return game` | `packages/shared/crud/games.py` (~:649) | Unit test asserts completed game object is returned on all paths |
| P0-2 | Models run twice per game: orchestrator discards predictions, tip-generation re-runs all 8. Thread predictions through | `services/tip_generation.py` (~:377–407), `models_ml/orchestrator.py` | Predictions persisted from orchestrator output; behavior tests assert single model invocation |
| P0-3 | 4 models silently vote home-team on internal error — re-raise so orchestrator abstains (keep legitimate cold-start fallbacks) | `models_ml/weather_impact.py`, `injury_impact.py`, `matchup.py`, `player_form.py` | Tests: exception inside model ⇒ orchestrator records abstention, not a home-team pick |
| P0-4 | No-op WHERE: `Game.home_team is not None` is a Python identity check | `models_ml/elo.py` (~:249,253) | Test asserts `IS NOT NULL` in emitted SQL / filter functions |
| P0-5 | Draws: Form model counts draw as loss; backtest awards draw to away team (4 sites) | `models_ml/form.py` (~:47–56), `services/backtest.py` (~:99,150,389,467) | Draw tests pass for both form stats and backtest correctness accounting |
| P0-6 | Season-window drift: `ALL_SEASONS` ends 2025; 4 overlapping window mechanisms. *Decision: scheduled FootyWire/weather scraping is deferred to Phase 3 (documented here), not fixed now* | `services/historic_refresh.py` (~:31), `services/historic_data_refresh.py`, `config.py` | Single derived window (latest−N) used everywhere; current season included; test pins it |
| P0-7 | Dead code: `_check_cache` ×4; `generation_service.close()` no-op call; stray root `test_*.py` in `backend/`. *Scope revision: the deprecated `GET /api/backtest` endpoint STAYS — its no-trailing-slash alias is load-bearing for the ingress path-trim quirk; removal moves to P4-1 with a deprecation window* | 4 model files, `app/cron/tip_generation.py` | Removed; suite green; no references remain (grep-verified) |
| P0-8 | Docs drift: migrations count/head, `factory` symbol, model list, cron-job count, `pyproject` name; unify `is_grand_final` (3 copies) → one helper | `docs/*`, `README.md`, `backend/pyproject.toml`, `models_ml/neutral.py` + 2 dupes | Docs match disk truth; `is_grand_final` imported from one module |

**Exit criteria**: full unit suite green; integration suite green (Docker stack);
each subtask = 1 commit (`🐛 fix: …` / `🔧 chore:` / `📝 docs:`), DCO signed.

### Phase 1 — Domain foundations (schema + storage)

| ID | Subtask | Notes |
|----|---------|-------|
| P1-1 | ADR: record D1–D4 + target model (this doc §3) in `docs/adr/0001-multi-sport-model.md` | New ADR convention |
| P1-2 | Add `naming_convention` to `Base`; resolve ORM↔migration FK drift | Prerequisite for clean autogen |
| P1-3 | Migration `0010_consolidated_multisport`: create Sport/Competition/Season/Event/EventParticipant/Participant/TeamAlias/Roster/SourceRef/RatingSnapshot/ParticipantMatchStats; copy AFL rows in-migration | Startup-safe (no long locks); `stamp`-able cutover |
| P1-4 | Composite integrity: natural key on events; `RatingSnapshot` UQ; CHECK `event_type='match' ⇒ 2 participants` | DB-level, not app-level |
| P1-5 | CRUD: make `season_id`/`competition_id` required args on all lookups (compile-time break over silent mixing) | Kills implicit single-sport queries |
| P1-6 | Move `canonical_team` out of write paths (tips/predictions CRUD); alias resolution becomes `ParticipantResolver` over `TeamAlias` | Ends silent no-op normalization |
| P1-7 | Backfill + verification scripts; docs updated in-lockstep | |

### Phase 2 — Prediction contract uplift

| ID | Subtask | Notes |
|----|---------|-------|
| P2-1 | `Prediction` dataclass + `Prediction \| Abstained` in model/heuristic ABCs | Probability is the universal signal |
| P2-2 | `SportContext` threaded through orchestrator, services, cron | Replaces scattered AFL calendar/config |
| P2-3 | Registry-driven model/heuristic sets per sport; weighted-tip features derive from registry | Retrain follows automatically |
| P2-4 | Repositories between models and storage (point-in-time in one place) | Models drop raw `AsyncSession` |
| P2-5 | Per-competition `ModelVersion` scoping; fix Elo `_LEARNED_HOME_ADVANTAGE` global + Redis namespace | |

### Phase 3 — Ingestion abstraction

| ID | Subtask | Notes |
|----|---------|-------|
| P3-1 | `FeedProvider` protocol; `SquiggleProvider` as first implementation; dialect mapping leaves CRUD | |
| P3-2 | `SourceRef` adoption; drop `squiggle_id` from public API schemas (deprecation window) | |
| P3-3 | Schedule FootyWire/AFLTables/weather refresh (carried from P0-6) or formally retire | Alerting on stale data |
| P3-4 | Unify 4 season-window mechanisms into `SportContext.season_calendar`; per-competition timezones | |
| P3-5 | CSV loader schemas per sport; de-duplicate the 4 canonical-team map copies | |

### Phase 4 — API + frontend generalization

| ID | Subtask | Notes |
|----|---------|-------|
| P4-1 | `sport`/`competition` route params; participant-aware response schemas | |
| P4-2 | Frontend types/copy/logos/colors externalized per sport; heuristic/model label maps data-driven | |
| P4-3 | Home page: competition-lifecycle state machine replacing grand-final/premiers lifecycle | |
| P4-4 | Update AFL-pinning tests (`useTeamLogos.test.ts`, `grand-final-home.test.ts`) in lockstep | These are guardrails |
| P4-5 | Backtest semantics: sport-aware staking; retire fake even-money profit until odds exist | Rename `ValueModel` → `HistoricalWinRateModel` |

### Phase 5 — Expansion (per D3)

1. **Local AFL competition** — proves Competition/tier scoping; zero new ingestion.
2. **One other team sport** — proves `FeedProvider` + team participants end-to-end.
3. **Tennis or golf** — proves `Participant(kind=individual)`; uses DB-only models
   (Elo, Form, H2H, Weather) first.

---

## 5. Risks & Guardrails

- **Startup migrations**: deploys run `alembic upgrade head` in-process — `0010`
  must avoid long table locks; copy-then-swap pattern.
- **Public API contract freeze**: routes are documented as a frozen FaaS contract;
  Phase 4 changes need a deprecation window and `docs/api.md` updates.
- **Tests pin AFL-ness**: treat as guardrails — update in the same commit as the
  generalization they protect.
- **Docs in-lockstep**: every phase updates `docs/` + `README.md` in the same PR
  (Phase 0's drift shows the cost of deferring this).
- **Baseline green**: 1485 unit tests passing as of 2026-09-22 — any phase landing
  red is a stop-and-report, not a fix-forward.
