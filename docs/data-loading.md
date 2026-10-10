# Loading real AFL data

> Phase 7 of the local Docker stack — populating the database with real AFL data scraped from public sources.

## TL;DR

```bash
# 1. Generate CSVs into ./data/  (10-30 min depending on scope)
./scripts/make-data.sh

# 2. Bring up the full stack — the init-data container auto-loads the CSVs
./scripts/dev.sh up --logs
```

The `init-data` Docker service bind-mounts `./data/` and runs [`scripts/migrate_and_seed.py --from-csv`](../backend/scripts/migrate_and_seed.py:1) on first start, so the API never boots against an empty database.

## Scope decision (2020 – current season)

The default scope is **5 most recent completed seasons** (2020 – current). To change the scope:

```bash
# Bash — fastest (2 seasons, ~3 min)
SEASONS="2024 2025" ./scripts/make-data.sh

# PowerShell
$env:SEASONS = "2024 2025"; .\scripts\make-data.ps1
```

The 5-season default balances model stability (≥ 3 seasons for Elo / Form / HomeAdvantage / Value / Matchup), bounded scrape time (~30 min), and bounded CSVs (~150k player-stat rows).

## Data sources

| Source | Data | CSV file(s) |
|--------|------|-------------|
| [AFL Tables](https://afltables.com/afl) | Historical matches, per-match player stats, advanced metadata | `season_games.csv`, `match_details.csv`, `players.csv`, `player_match_stats.csv` |
| [FootyWire](https://www.footywire.com) | Current injury list (1 req/run) | `injuries.csv` |
| [Open-Meteo](https://open-meteo.com) | Historical + forecast weather per game (10k req/day) | `match_weather.csv` |

The scraper is rate-limited: AFL Tables runs at ~1 req/sec (no formal limit but throttled); Open-Meteo uses the Redis sliding-window pattern in [`packages/shared/squiggle/client.py`](../backend/packages/shared/squiggle/client.py:1).

## Feed providers (multi-sport ingestion)

Beyond the AFL CSV pipeline, fixtures for other sports load through the
[`FeedProvider`](../backend/packages/shared/ingestion/base.py:1) protocol: a provider
owns its vendor dialect and yields canonical
[`FixtureDTO`](../backend/packages/shared/ingestion/dto.py:1) objects — nothing
downstream sees vendor field names.

### Rugby league — `NrlProvider` (FixtureDownload)

[`packages/shared/ingestion/nrl_provider.py`](../backend/packages/shared/ingestion/nrl_provider.py:1)
covers the three owner-approved competitions (Phase 5.2), sourced from the free
[FixtureDownload](https://fixturedownload.com/) JSON feed (no auth, no key):

| Competition key | Feed slug | Notes |
|-----------------|-----------|-------|
| `nrl` | `nrl-{year}` | Men's premiership, seasons 2017+ verified |
| `nrlw` | `nrlw-{year}` | Women's premiership |
| `origin` | `state-of-origin-{year}` | 3-match mid-year series |

Mapping (verified against the live feed schema 2026-10-09): `MatchNumber` →
`external_id`, `RoundNumber` → `round_id`, `DateUtc` → tz-aware UTC `starts_at`,
`HomeTeam`/`AwayTeam` → participant names, `Location` → venue. Completion is
driven by score presence (null scores pre-match); `Winner` is deliberately
ignored because rugby league has draws.

Politeness: FixtureDownload updates once a day, so the provider keeps an
in-process TTL cache (default 24 h) **shared by all instances**, giving at most
one fetch per feed slug per day. Single-fixture lookups never trigger a fetch —
they scan already-fetched season payloads.

### Timezone boundary decision (ADR 0001)

`FixtureDTO.starts_at` is **tz-aware UTC** at the provider boundary (a naive
feed value is assumed UTC). The **storage boundary** converts it to the
`events.starts_at` convention — venue-local **naive** — and the conversion
target is chosen per fixture:

1. the **venue's own IANA zone**, from
   [`league_seeding.VENUE_TIMEZONES`](../backend/packages/shared/ingestion/league_seeding.py:1)
   (keyed on the canonical ground), when the venue is listed — Mount Smart
   Stadium is `Pacific/Auckland` (the Warriors) and Perth's Optus Stadium is
   `Australia/Perth` (Origin host), which no single per-competition timezone
   can express;
2. otherwise the **competition timezone** (`Australia/Brisbane` for all three
   rugby-league competitions) — the fallback for unlisted grounds (e.g. the
   Las Vegas round-1 opener) and for every AFL/state competition, whose
   conversion behaviour is unchanged.

Display and backtest windows must therefore interpret a stored
`events.starts_at` through the **same venue zone table**
(`league_seeding.venue_timezone(venue)`), falling back to
`competitions.timezone` for unlisted venues. A per-competition timezone alone
is insufficient: roughly half the Warriors' fixtures would sit an hour off
during New Zealand daylight time.

### State of Origin — 3-match series semantics

The Origin feed is a 3-match mid-year series, mapped as follows:

| Feed field | Meaning for origin | Storage |
|------------|--------------------|---------|
| `RoundNumber` | the series **game number** (1–3) | `events.round_id` unchanged — "Game N of the series" is queryable through the same round-scoped surfaces a rounds competition uses |
| `Group` | the constant series label (`"State of Origin"`) | deliberately **not mapped** — it carries no per-match information beyond the competition identity the rows already carry, and the events schema has no group column |
| `competitions.format` | a series, not a round-robin | `'tournament'` (the schema CHECK admits only `'rounds'`/`'tournament'`; `national_leagues` and `league_seeding` agree so a real sync cannot violate the constraint) |

For `nrl`/`nrlw`, `RoundNumber` maps through unchanged as usual — the
recorded 2026 payload shows the grand final as round 31.

### The national sync registry

[`packages/shared/ingestion/national_leagues.py`](../backend/packages/shared/ingestion/national_leagues.py:1)
is the AFL `state_leagues.py` equivalent for the three rugby-league
competitions. `run_league_sync(session, league, season)` is the one entry
point for `league=nrl|nrlw|origin` and is **self-sufficient**: it registers
the `rugby-league` sport row and the 19 canonical team participants
(get-or-create, so feed nicknames always resolve at the exact-name step and
the transitional AFL canonical-team fallback can never hijack a club), then
drives the shared sync service — competition/season registration, participant
resolution, the timezone boundary above, and idempotent event upserts
(re-runs update in place; no duplicate events; results backfill flips
`completed` from score presence).

Failure contract: an unknown league key raises `BackendServiceError`
(400 `unknown_league`); a failed pass (feed unreachable, DB fault) raises
`BackendServiceError` (502 `league_sync_failed`) with the repo-standard
`status_code`/`code`/`message`/`details` shape. Per-fixture failures never
abort a pass — they are logged and returned on `stats["errors"]`. The
end-to-end path is proven against recorded feed payloads + in-memory SQLite
in `backend/tests/unit/test_national_league_sync.py` — no live HTTP, no
Postgres.

### Venue alias table

Sponsor branding on NRL venues drifts across seasons (PointsBet Stadium → Ocean
Protect Stadium; Mt Smart Stadium → Go Media/One NZ/Hnry Stadium), which would
fragment backtest history. [`venue_aliases.py`](../backend/packages/shared/ingestion/venue_aliases.py:1)
maps every observed sponsor variant to one canonical ground; unknown venues pass
through verbatim and are logged once as backfill candidates — extend `_ALIASES`
when new sponsor names appear. The canonical grounds each carry an IANA timezone
in `league_seeding.VENUE_TIMEZONES` (kept in lockstep with the alias table and
validated at import) — that pairing is what the timezone boundary above and the
display/backtest windows read.

### Historical backfill (rugby-league, 2017+)

[`packages/shared/services/nrl_historic_load.py`](../backend/packages/shared/services/nrl_historic_load.py:1)
backfills full history for the three rugby-league competitions. One entry point:

```python
from packages.shared.services.nrl_historic_load import run_nrl_historic_load

# nrl + nrlw + origin, seasons 2017 … current season:
stats = await run_nrl_historic_load(session)

# Or narrower: one competition, explicit seasons
stats = await run_nrl_historic_load(
    session, competitions=["nrl"], seasons=[2017, 2022]
)
```

| Behaviour | Contract |
|-----------|----------|
| Scope | seasons **2017+** (`league_seeding.MIN_SEASON`, the first FixtureDownload serves) for `nrl`, `nrlw`, `origin` |
| Path | the **same path as live sync** — `run_league_sync` → `NrlProvider` → canonical `FixtureDTO` → the generic multisport tables. No parallel schema and **no CSV parser**: the JSON feed serves history, so backtest rows and live rows are one shape |
| Venue normalization | applied **AT LOAD** — the provider resolves every sponsor-branded `Location` through `venue_aliases` before storage, so the Sharks' Cronulla ground lands as `Shark Park` whether the source season says Southern Cross Group Stadium (2017), PointsBet Stadium (2022) or Ocean Protect Stadium (2026). Backtest grouping is venue-stable across sponsor drift |
| Idempotency | re-running upserts in place (`EventCRUD.upsert_fixture` source-ref fast path) — no duplicate events, sides or source refs |
| Current-season flag | backfilled seasons are **never** marked current (`mark_current=False` default) — only the live sync marks the live season |
| Failure tolerance | one failed (competition, season) pass never aborts the sweep — it is rolled back, logged, and returned on `stats["errors"]` with `status="partial"` (`"failed"` when nothing syncs, `"success"` when all passes land) |

Loud refusals (all raise `ValueError` **before** the first pass runs, so an
out-of-scope request never touches the database):

* **Unknown source** — anything but `source="fixturedownload"` (the only
  sanctioned feed). Kaggle datasets (1990+) are explicitly out of scope.
* **Pre-2017 seasons** — `seasons=[1990]` or `through_year=2016`; the loader
  will not import deep history.
* **Unknown competition / empty request** — anything outside `nrl|nrlw|origin`,
  or an empty competitions/seasons list.

> **Not built (deliberately):** the nrl.com fallback (undocumented endpoints,
> Akamai bot-protected) is recorded in the source notes as a NOT-BUILT
> contingency only (see `.tmp/external-context/nrl-feed/`); this loader is
> FixtureDownload-only.

Tests: `backend/tests/unit/test_nrl_historic_load.py` drives the full backfill
against recorded season payloads — 2017 + 2022 + 2026 per competition,
in-memory SQLite, no live HTTP, no Postgres — including the cross-era venue
normalization proof (three sponsor names, one canonical ground) and the
idempotent re-load check.

## Where the CSVs go

`./data/` at the **project root**, per-season:

```
data/
├── .gitkeep
├── scrape.log
├── injuries.csv                       # top-level (not per-season)
├── 2024/
│   ├── season_games.csv
│   ├── match_details.csv
│   ├── players.csv
│   ├── player_match_stats.csv
│   └── match_weather.csv
└── 2025/                              # same layout
```

`./data/` is **gitignored**; CSVs are regenerated by `make-data.sh`, not checked in.

## How the loader finds the data

`scripts/migrate_and_seed.py --from-csv` calls [`find_csv_seed_dir()`](../backend/scripts/migrate_and_seed.py:148) which walks these candidate locations in order, taking the first that contains at least one `.csv` file:

1. `./data/` (what `make-data.sh` produces — always wins when present)
2. `./backend/seed_data/` (synthetic seed CSVs)
3. `./backend/data/` (legacy)
4. `./scripts/data/` (dev last-resort)

Override with `--seed-dir PATH` (added Phase 6a) or `--csv-dir PATH` (used with `--from-csv`).

### `migrate_and_seed.py` flags

| Flag | Purpose |
|------|---------|
| `--from-csv` | Load CSVs from `--csv-dir` (or auto-discovered) instead of running the synthetic seeder |
| `--csv-dir PATH` | Explicit CSV directory (overrides auto-discovery) |
| `--seed-dir PATH` | Explicit synthetic-seed directory (overrides default) |
| `--skip-migrations` | Skip `alembic upgrade head` (when schema is already up to date) |
| `--no-seed` | Skip the synthetic `seed_data.py` run |
| `--clear` | Clear existing data before seeding |
| `--verbose`, `-v` | Print progress to stdout |
| `--migrations-only` | Run only migrations (equivalent to omitting `--seed`) |

## Verifying the CSVs before loading

```bash
cd backend
uv run python scripts/load_csv_to_db.py --input-dir ../data --dry-run --season 2024 2025
```

Sample output (after a full 2024 + 2025 scrape):

```
DRY RUN - no database changes will be made
  input_dir: .../data
  seasons:   [2024, 2025]
  match_details        432 rows
  players              1314 rows
  player_match_stats   19872 rows
  match_weather        432 rows
  injuries             167 rows
```

## When to re-scrape

| Trigger | Action |
|---------|--------|
| New season starts (March) | Add the new season to `SEASONS` and re-run. |
| Bug in upstream HTML | Re-run; the scraper overwrites the per-season CSVs. |
| `injuries.csv` is stale | Re-run; injuries are regenerated each pass. |

CSVs are idempotent — re-running overwrites the per-season files. The Postgres loader uses `ON CONFLICT DO NOTHING` for players and stats, so re-running is safe.

## Known limitations

* **No player advanced stats** — the `player_form` model also wants `player_advanced_stats` (TOG%, metres gained, etc.). Available from FootyWire but requires per-player scraping. Tracked for Phase 8.
* **No team selections** — `get_team_selections` from FootyWire is not wired in. Only injuries are pulled in Phase 7.
* **No live game results** — the scraper only pulls historical data. Live game results come from the [`daily-sync`](../backend/app/cron/daily_sync.py:1) cron job via the Squiggle API.
* **Sequential scraper** — a single AFL Tables HTTP call per game (~1 req/sec, 20–30 min for 5 seasons). Parallelising is tracked for Phase 8.

## FAQ

**Q: Do I have to use the Docker stack to load CSV data?**
No. `migrate_and_seed.py` runs directly: `cd backend && uv run python scripts/migrate_and_seed.py --from-csv --csv-dir=../data --no-seed`.

**Q: How do I add 2026 data?**
Edit `scripts/make-data.sh` (or pass `SEASONS="... 2026"`) and re-run. The CSVs are versioned by season directory, so old and new data coexist. Reload with `./scripts/dev.sh reset && ./scripts/dev.sh up`.

**Q: Where's the synthetic seed data?**
`backend/seed_data/`. Generated by `scripts/seed_data.py` when `--from-csv` is **not** passed.

**Q: Tests for the loader?**
`backend/tests/unit/test_load_csv_dry_run.py` and `backend/tests/unit/test_load_csv_to_db.py` cover the loader end-to-end against synthetic CSVs.
