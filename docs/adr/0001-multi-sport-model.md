# ADR 0001: Multi-Sport Data Model

Status: **Accepted** · Date: 2026-09-22 · Supersedes: none
Decides: [plans/multi-sport-refactor-plan.md](../../plans/multi-sport-refactor-plan.md) §1 (D1–D4)

## Context

WhatIsMyTip is a single-competition AFL tipping app. Expansion targets are
local AFL competitions, other team sports, and individual sports (golf,
tennis). A full review (2026-09-22, see plan §2) found the binding
constraint is **stringly-typed identity**: no `Sport`/`Competition`/`Season`/
`Participant` entities exist; teams are `String(100)` columns validated
against a Python dict. This has already cost two data-repair migrations
(0004, 0007) and a production logo outage, and it structurally excludes
individual sports (`home_team`/`away_team`, `Tip.selected_team`,
`ModelPrediction.winner`).

## Decisions

### D1 — Single shared database, shared tables + scope columns

One Postgres database; every domain table carries `sport_id` /
`competition_id` (directly or via its `season_id` FK). Rejected:
schema-per-sport (heavy SQLAlchemy/Alembic multi-schema plumbing, painful
cross-sport queries) and DB-per-sport (N engines/migration chains, N× ops
cost on a small deployment).

### D2 — Consolidated baseline migration, staged cutover

The target schema arrives in **one** migration, `0010_consolidated_multisport`,
not as 10–15 incremental ALTERs of AFL-shaped tables (the migration history
shows mid-flight data migrations are where this codebase bleeds).

**Staging (strangler pattern), made explicit:**

1. `0010` creates the new sport-generic tables **and copies AFL data**
   (games → events/event_participants/participants/source_refs;
   elo_cache → rating_snapshots) in-migration.
2. Legacy tables (`games`, `tips`, …) remain the live read/write path and
   are **not dropped**. Every deploy stays shippable.
3. Later phases switch services onto the new tables domain-by-domain
   (Phase 2: predictions; Phase 3: ingestion + stats).
4. A final migration drops legacy tables once no code reads them.

Rationale for staging over a same-deploy table swap: the API/frontend
contract is a frozen FaaS surface; a wholesale swap would force rewriting
every CRUD/service/router in a single atomic release — an unbounded blast
radius on a live product.

### D3 — Participant abstraction over Team | Individual

The unit of prediction is an **event between participants within a
competition**. `Participant.kind ∈ {team, individual}` is the single
abstraction that serves rugby/soccer *and* golf/tennis without per-sport
prediction tables. Sides live in `event_participants` (`home`/`away`/`n/a`),
making n-side events (doubles, races) representable.

### D4 — Execution order

Phase 0 (stabilize) → Phase 1 (this ADR, schema, storage) → Phase 2
(prediction contracts) → Phase 3 (ingestion) → Phase 4 (API/frontend) →
Phase 5 expansion: local AFL → one other team sport → tennis/golf.

## Target Model (created by 0010)

```
sports (id TEXT PK, display_name)                    e.g. 'afl'
competitions (id PK, sport_id FK, name, tier, format, timezone)
  tier   ∈ {national, state, local}
  format ∈ {rounds, tournament}
seasons (id PK, competition_id FK, label, start_date?, end_date?, is_current)
  UQ (competition_id, label)
participants (id PK, sport_id FK, kind, name)
  kind ∈ {team, individual}; partial UQ (sport_id, name) WHERE kind='team'
teams (participant_id PK/FK)            — club metadata
individuals (participant_id PK/FK, dob?, height_cm?, weight_kg?)
team_aliases (id PK, team_participant_id FK, alias, source?)
rosters (id PK, team_participant_id FK, individual_participant_id FK,
         season_id FK, position?)       — UQ (team, individual, season)
events (id PK, season_id FK, event_type, round_id?, venue?, starts_at,
        status, completed, slug UQ, last_synced_at, sync_version)
  event_type ∈ {match, race, tournament_round}
  UQ (season_id, event_type, round_id, starts_at, venue)   — natural key
event_participants (id PK, event_id FK, participant_id FK,
                    side ∈ {home, away, n/a}, score?, is_winner?)
  UQ (event_id, participant_id); partial UQ (event_id, side) WHERE side<>'n/a'
event_source_refs (event_id FK, source, external_id)     — UQ (source, external_id)
participant_source_refs (participant_id FK, source, external_id) — same UQ
rating_snapshots (participant_id FK, season_id FK, rating)  — UQ (participant, season)
participant_match_stats (event_id FK, participant_id FK, stats JSONB,
                         stat_schema_version)            — UQ (event, participant)
```

Key integrity decisions:
- **Vendor IDs move out of columns** into `*_source_refs` — ends the
  DUP-GUARD class of bugs and makes any provider pluggable.
- **`rating_snapshots` is keyed (participant, season)** — fixes the
  `elo_cache` UQ-on-team-name-only defect structurally.
- **Stats are JSONB + schema version** — typed AFL stat columns do not
  survive sport #2; the codebase already trusts JSONB for reports and
  metrics.
- `starts_at` stays timezone-naive (venue-local), interpreted through
  `competitions.timezone` — matches current `games.date` semantics without
  inventing conversions during the copy.

## Consequences

- Legacy AFL tables and the new model coexist for Phases 2–3; sync
  responsibility is explicit (new tables are written by migration copy
  first, by services later).
- `naming_convention` lands on the declarative Base before `0010` so all
  new constraints have deterministic names.
- Players/stats/injuries are NOT copied by `0010`; they migrate with the
  ingestion rework (Phase 3), where per-sport stat schemas are defined.
- `GenerationProgress.job_execution_id` remains a documented soft
  reference (no FK) — pre-existing behaviour, revisit at legacy-table
  cleanup.
