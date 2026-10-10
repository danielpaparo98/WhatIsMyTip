/**
 * PERF-VIEW-UNIFY (2026-10-08): /performance (AFL) and every
 * /{league}/performance page render THE SAME shared component —
 * components/PerformanceView.vue — so the presentation can never drift
 * between them. The pages are thin wrappers owning only route-level
 * concerns (route contract, active-league sync, per-route SEO).
 *
 * Covers:
 *  1. UNIFICATION — both wrappers render <PerformanceView>; the AFL
 *     wrapper passes league="afl"; neither wrapper contains presentation
 *     or data-fetch logic of its own.
 *  2. Component route-side contract (the [league] wrapper): validate()
 *     against the LEAGUES registry (unknown keys 404), the inline
 *     middleware canonicalising 'afl' to /performance (AFL owns the
 *     root-level page — there is NO /afl/performance route), the
 *     client-only setActiveLeague() sync.
 *  3. Component data wiring — one awaited useAsyncData per league key
 *     (reactive cache key + dedupe: 'cancel'), league-keyed API calls
 *     with AFL omitting the param (byte-identical legacy URLs), the
 *     past-season selection via lib/performanceSeasons, AFL-only model
 *     slots gated on isAfl, and per-slot degrade-quietly contracts.
 *  4. Presentation — ONE hero, the same two-season cards for every
 *     league (accuracy / profit / rounds), the model sections gated on
 *     isAfl, and the APPROVED graceful empty states.
 *  5. Per-league SEO — title/canonical/JSON-LD derived from
 *     getLeagueConfig(leagueKey).displayName and the
 *     ${siteUrl}/{league}/performance canonical URL.
 *  6. Prerender/sitemap trace — both call sites consume
 *     buildLeagueRoutes, which emits /{league}/performance for ALL
 *     LEAGUE_ROUTE_KEYS unconditionally (verified by source pins, not
 *     re-implemented).
 *
 * Matches the repo's static-analysis test style (SFCs cannot be
 * mounted in vitest — see league-home-page.test.ts): behaviour is
 * pinned by reading the SFC source; the pure season-selection helper
 * is unit-tested behaviourally.
 */
import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { resolve, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'
import { mostRecentPastSeasonYear } from '~/lib/performanceSeasons'
import { LEAGUES } from '~/composables/useSportConfig'
import { LEAGUE_ROUTE_KEYS } from '~/lib/leagueRoutes'

const FRONTEND_ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '../..')

const PAGE = readFileSync(
  resolve(FRONTEND_ROOT, 'pages/[league]/performance.vue'),
  'utf8',
)
const AFL_PAGE = readFileSync(
  resolve(FRONTEND_ROOT, 'pages/performance.vue'),
  'utf8',
)
const VIEW = readFileSync(
  resolve(FRONTEND_ROOT, 'components/PerformanceView.vue'),
  'utf8',
)
const VIEW_STYLES = VIEW.match(/<style[^>]*>([\s\S]*?)<\/style>/)?.[1] ?? ''
const HOME = readFileSync(resolve(FRONTEND_ROOT, 'pages/[league]/index.vue'), 'utf8')
const SEASON_LIB = readFileSync(resolve(FRONTEND_ROOT, 'lib/performanceSeasons.ts'), 'utf8')
const ROUTE_LIB = readFileSync(resolve(FRONTEND_ROOT, 'lib/leagueRoutes.ts'), 'utf8')
const NUXT_CONFIG = readFileSync(resolve(FRONTEND_ROOT, 'nuxt.config.ts'), 'utf8')
const SITEMAP = readFileSync(resolve(FRONTEND_ROOT, 'server/routes/sitemap.xml.ts'), 'utf8')

// ---------------------------------------------------------------------------
// Pure helper — most recent PAST season selection (the AFL/D2 rule:
// max(available_years < current_year), null when none)
// ---------------------------------------------------------------------------

describe('mostRecentPastSeasonYear (lib/performanceSeasons)', () => {
  it('picks max(year < current_year) from string season labels', () => {
    expect(mostRecentPastSeasonYear(['2024', '2025', '2026'], 2026)).toBe(2025)
  })

  it('accepts numeric years too', () => {
    expect(mostRecentPastSeasonYear([2024, 2025], 2026)).toBe(2025)
  })

  it('handles mixed numeric and string labels', () => {
    expect(mostRecentPastSeasonYear(['2023', 2025, '2026'], 2026)).toBe(2025)
  })

  it('returns null when every available season is the current year', () => {
    expect(mostRecentPastSeasonYear(['2026'], 2026)).toBeNull()
    expect(mostRecentPastSeasonYear([], 2026)).toBeNull()
  })

  it('ignores non-numeric labels instead of throwing', () => {
    expect(mostRecentPastSeasonYear(['n/a', '2025'], 2026)).toBe(2025)
    expect(mostRecentPastSeasonYear(['n/a'], 2026)).toBeNull()
  })

  it('ignores future seasons (they are not PAST seasons)', () => {
    expect(mostRecentPastSeasonYear(['2027', '2026'], 2026)).toBeNull()
  })

  it('stays dependency-free so build-time and tests can import it', () => {
    // lib/ modules are imported by plain-Node callers and the vitest
    // suite — they must not pull the Vue/Nuxt runtime.
    expect(SEASON_LIB).not.toMatch(/from 'vue'|from 'nuxt'|#app|auto-import/)
    expect(SEASON_LIB).toMatch(/export function mostRecentPastSeasonYear/)
  })
})

// ---------------------------------------------------------------------------
// UNIFICATION — one shared view, two thin wrappers (the entire point)
// ---------------------------------------------------------------------------

describe('performance view unification', () => {
  it('both wrappers render the SAME PerformanceView component', () => {
    expect(PAGE).toMatch(/<PerformanceView\b/)
    expect(AFL_PAGE).toMatch(/<PerformanceView\b/)
  })

  it('the AFL wrapper passes league="afl" (its canonical URL stays /performance)', () => {
    expect(AFL_PAGE).toMatch(/<PerformanceView\s+league="afl"\s*\/>/)
  })

  it('the league wrapper passes the ROUTE league key (never global state)', () => {
    expect(PAGE).toMatch(/:league="leagueKey"/)
  })

  it('neither wrapper contains presentation or data-fetch logic', () => {
    for (const [name, src] of [['[league] wrapper', PAGE], ['afl wrapper', AFL_PAGE]] as const) {
      expect(src, name).not.toMatch(/useAsyncData|getCurrentSeasonPerformance|getLeagueCurrentSeasonPerformance/)
      expect(src, name).not.toMatch(/ModelCoefficientChart|getActiveModel|getActiveBoostedModel|compareModels/)
      expect(src, name).not.toMatch(/class="current-season-card"|class="season-card"/)
    }
  })

  it('the component owns the shared card + model presentation', () => {
    expect(VIEW).toMatch(/class="current-season-card"/)
    expect(VIEW).toMatch(/ModelCoefficientChart/)
    expect(VIEW).toMatch(/Weighted Tip Model/)
    expect(VIEW).toMatch(/Active Boosted Model \(XGBoost\)/)
  })

  it('model sections are gated on isAfl (leagues render them the moment data exists)', () => {
    // The weighted/boosted sections are AFL-only because the model API
    // is AFL-only — the gate is the data source, not a different
    // presentation. When league model endpoints land, dropping the
    // isAfl fetch gate lights the sections up for every league.
    expect(VIEW).toMatch(/v-if="isAfl"/)
    expect(VIEW).toMatch(/const isAfl = computed\(\(\) => props\.league === 'afl'\)/)
  })
})

// ---------------------------------------------------------------------------
// Route contract — copied from pages/[league]/index.vue ([league] wrapper)
// ---------------------------------------------------------------------------

describe('league performance page route contract', () => {
  it('carries the tagged rationale comment', () => {
    expect(PAGE).toContain('PERF-VIEW-UNIFY (2026-10-08')
  })

  it('validate() rejects keys that are not in the LEAGUES registry (404 gate)', () => {
    expect(PAGE).toMatch(/definePageMeta/)
    expect(PAGE).toMatch(/validate/)
    expect(PAGE).toMatch(/route\.params\.league/)
    expect(PAGE).toMatch(/LEAGUES\.some\(\(l\) => l\.key === key\)/)
  })

  it("redirects the 'afl' key to /performance so AFL keeps the root-level page", () => {
    // NOT '/' like the league home — AFL's canonical performance URL is
    // /performance (there is deliberately no /afl/performance route).
    expect(PAGE).toMatch(/navigateTo\('\/performance',\s*\{\s*replace:\s*true\s*\}\)/)
    expect(PAGE).not.toMatch(/navigateTo\('\/',\s*\{\s*replace:\s*true\s*\}\)/)
  })

  it('copies the league home route contract shape (parity)', () => {
    // Both pages must agree on the three contract pieces — validate,
    // inline middleware, route-derived key — so /{league}/… behaves
    // consistently across the league route family.
    expect(HOME).toMatch(/LEAGUES\.some/)
    expect(HOME).toMatch(/navigateTo\('\/',\s*\{\s*replace:\s*true\s*\}\)/)
    expect(PAGE).toMatch(/middleware:\s*\[/)
    expect(PAGE).toMatch(/const key = Array\.isArray\(raw\) \? raw\[0\] : raw/)
  })

  it('syncs the active league to the route (client-only) and watches for param changes', () => {
    expect(PAGE).toMatch(/setActiveLeague\(/)
    expect(PAGE).toMatch(/import\.meta\.client/)
    expect(PAGE).toMatch(/watch\(leagueKey/)
  })
})

// ---------------------------------------------------------------------------
// Data wiring — one awaited useAsyncData inside the shared component
// ---------------------------------------------------------------------------

describe('performance view data wiring', () => {
  it('fetches through an awaited useAsyncData so prerender inlines the payload', () => {
    // The SEO-C1 contract: generate-time fetch, payload inlined into the
    // static HTML (see [league]/index.vue's LEAGUE-ROUTES review note).
    expect(VIEW).toMatch(/await useAsyncData/)
  })

  it('uses a reactive per-league cache key with dedupe cancel (H-5 contract)', () => {
    expect(VIEW).toMatch(/performance-\$\{props\.league\}/)
    expect(VIEW).toMatch(/dedupe:\s*'cancel'/)
  })

  it('routes AFL to the legacy endpoints by omitting the league param', () => {
    // Byte-identical legacy URLs for AFL: the league arg is undefined,
    // so useApi builds /api/backtest/* with no query (pinned in
    // useApi-league-performance.test.ts). The league path passes the key.
    expect(VIEW).toMatch(/const league = isAfl\.value \? undefined : props\.league/)
    expect(VIEW).toMatch(/isAfl\.value\s*\n?\s*\?\s*api\.getCurrentSeasonPerformance\(\)/)
    expect(VIEW).toMatch(/api\.getLeagueCurrentSeasonPerformance\(props\.league\)/)
    expect(VIEW).toMatch(/api\.getLeagueSeasons\(league\)/)
    expect(VIEW).toMatch(/api\.getLeagueComparison\(\s*league,\s*prevSeason,?\s*\)/)
  })

  it('selects the past season with the shared max(year<current) helper', () => {
    expect(VIEW).toMatch(/mostRecentPastSeasonYear\(/)
    expect(VIEW).toMatch(/import \{ mostRecentPastSeasonYear \} from '~\/lib\/performanceSeasons'/)
  })

  it('degrades a failed current-season fetch to the empty state (404 unsynced included)', () => {
    // Unsynced league → zero payload (or 404). The view catches and
    // renders the approved empty state — a missing league must never
    // 500 the prerender.
    expect(VIEW).toMatch(/\.catch\(\(\) => null\)/)
  })

  it('isolates the past-season fetch so it can never break the page', () => {
    // The seasons→compare chain is additive: wrapped in try/catch and
    // nulled on failure, hiding only the past-season section.
    expect(VIEW).toMatch(/catch \{\s*\n\s*return null\s*\n\s*\}/)
  })

  it('fetches independent slots in parallel waves (no serialized waterfall)', () => {
    // Review #3: current / past / weighted / boosted are independent —
    // one Promise.all wave; only model-compare waits (it needs
    // current's season).
    expect(VIEW).toMatch(/await Promise\.all\(\[\s*\n?\s*fetchCurrent\(\),\s*\n?\s*fetchPast\(\),\s*\n?\s*fetchActiveModel\(\),\s*\n?\s*fetchBoostedModel\(\),/)
    expect(VIEW).toMatch(/fetchModels\(current\)/)
  })

  it('degrades slots INDEPENDENTLY (a current failure cannot blank the others)', () => {
    // Review #1: the empty state only renders when even the past-season
    // slot is empty, and the current-season section guards its OWN slot.
    expect(VIEW).toMatch(/v-else-if="!currentSeason && !pastSeason"/)
    expect(VIEW).toMatch(/<section v-if="currentSeason" class="current-season-section">/)
  })

  it('fetches the AFL-only model slots only for AFL, each failure-isolated', () => {
    expect(VIEW).toMatch(/if \(!isAfl\.value \|\| !current\) return \{ models: null, modelsError: null \}/)
    // Every slot is wrapped so no single failure can break the page.
    expect(VIEW).toMatch(/api\.compareModels\(season\)/)
    expect(VIEW).toMatch(/api\.getActiveModel\(\)/)
    expect(VIEW).toMatch(/api\.getActiveBoostedModel\(\)/)
  })

  it('renders the weighted-model states as ONE mutually-exclusive chain', () => {
    // Review #2: error → empty → content; a fetch failure must never
    // render the error AND the "no trained model" copy together.
    expect(VIEW).toMatch(/v-if="activeModelError"[\s\S]{0,200}v-else-if="!activeModelData\?\.active"/)
    expect(VIEW).toMatch(/v-else-if="activeModelData\.model" class="model-content"/)
  })

  it('refreshes client-side after hydration (preserves post-hydration freshness)', () => {
    expect(VIEW).toMatch(/import\.meta\.client/)
    expect(VIEW).toMatch(/onNuxtReady/)
  })
})

// ---------------------------------------------------------------------------
// Presentation — the SAME two-season cards for every league
// ---------------------------------------------------------------------------

describe('performance view presentation', () => {
  it('renders exactly ONE hero before any conditional state branch', () => {
    expect(VIEW.match(/class="hero"/g)?.length).toBe(1)
    const heroIdx = VIEW.indexOf('class="hero"')
    const firstConditional = VIEW.search(/v-if=/)
    expect(firstConditional).toBeGreaterThan(-1)
    expect(heroIdx).toBeLessThan(firstConditional)
  })

  it('leads the hero with the league name (identical shape for AFL and leagues)', () => {
    expect(VIEW).toMatch(/\{\{ leagueConfig\.displayName \}\}<br>Performance</)
  })

  it('renders the current-season heuristic cards with accuracy, profit and rounds', () => {
    expect(VIEW).toMatch(/class="current-season-cards"/)
    expect(VIEW).toMatch(/class="current-season-card"/)
    expect(VIEW).toContain('Current Season')
    expect(VIEW).toContain('Accuracy')
    expect(VIEW).toContain('Rounds Played')
    expect(VIEW).toContain('Year-to-Date Profit')
  })

  it('renders the Most Recent Past Season section', () => {
    expect(VIEW).toContain('Most Recent Past Season')
    expect(VIEW).toMatch(/v-for="row in pastSeason\.rows"/)
  })

  it('gates cards on graded rounds (no "$0.00" cards for an unplayed season)', () => {
    // Mirrors the AFL hasSeasonResults contract — a payload whose
    // rounds are all unplayed renders the season-not-started state.
    expect(VIEW).toMatch(/hasSeasonResults/)
    expect(VIEW).toMatch(/rounds_completed > 0 \\?\|\|\s*\n?\s*view\.heuristics\.some/)
  })

  it('renders the APPROVED graceful empty states verbatim', () => {
    expect(VIEW).toContain(
      "Performance tracking isn't available for {{ leagueConfig.displayName }} yet.",
    )
    expect(VIEW).toContain(
      'Performance tracking begins once tipping models are live for this league.',
    )
    expect(VIEW).toMatch(/class="empty-hint"/)
    expect(VIEW).toContain("The {{ currentSeason.season }} season hasn't started yet.")
  })

  it('orders league heuristics deterministically (review-flagged NaN edge retired)', () => {
    // League heuristics are unlisted in the AFL heuristicOrder — the
    // view sorts them with an explicit league order instead of relying
    // on NaN-comparison stability.
    expect(VIEW).toMatch(/LEAGUE_HEURISTIC_ORDER = \['home_advantage', 'form', 'ladder'\]/)
    expect(VIEW).toMatch(/byLeagueHeuristicOrder/)
  })

  it('announces loading and empty states (role="status" aria-live)', () => {
    expect(VIEW.match(/role="status"/g)?.length).toBeGreaterThanOrEqual(2)
    expect(VIEW.match(/aria-live="polite"/g)?.length).toBeGreaterThanOrEqual(2)
    expect(VIEW).toMatch(/class="spinner"/)
  })
})

// ---------------------------------------------------------------------------
// Per-league SEO ([league] wrapper)
// ---------------------------------------------------------------------------

describe('league performance page SEO', () => {
  it('derives title/description/og/twitter from the league displayName via getters', () => {
    expect(PAGE).toMatch(/useSeoMeta\(/)
    expect(PAGE).toMatch(/title: \(\) =>/)
    expect(PAGE).toMatch(/leagueName\.value/)
    for (const key of ['ogTitle', 'ogDescription', 'twitterTitle', 'twitterDescription']) {
      expect(PAGE).toMatch(new RegExp(`${key}: \\(\\) =>`))
    }
  })

  it('sets the per-league canonical ${siteUrl}/{league}/performance', () => {
    expect(PAGE).toMatch(/rel: 'canonical'/)
    expect(PAGE).toMatch(/\/\$\{leagueKey\.value \?\? ''\}\/performance/)
  })

  it('ships a JSON-LD WebPage with the same canonical URL', () => {
    expect(PAGE).toMatch(/application\/ld\+json/)
    expect(PAGE).toMatch(/'@type': 'WebPage'/)
    expect(PAGE).toMatch(/\/\$\{leagueKey\.value \?\? ''\}\/performance/)
  })

  it('the AFL wrapper keeps its own /performance canonical + SEO', () => {
    expect(AFL_PAGE).toMatch(/useSeoMeta\(/)
    expect(AFL_PAGE).toMatch(/rel: 'canonical'/)
    expect(AFL_PAGE).toMatch(/\$\{siteUrl\}\/performance/)
  })
})

// ---------------------------------------------------------------------------
// Prerender + sitemap trace (VERIFY, not re-implement)
// ---------------------------------------------------------------------------

describe('league performance prerender + sitemap coverage', () => {
  it('buildLeagueRoutes emits /{league}/performance for every non-AFL key, unconditionally', () => {
    // One source of truth: the pure builder pushes the performance
    // route next to the home route BEFORE any events fetch, so
    // unsynced leagues still ship their performance page.
    expect(ROUTE_LIB).toMatch(/routes\.push\(`\/\$\{league\}\/performance`\)/)
    // Unconditional = pushed before the events-dependent match routes.
    const pushIdx = ROUTE_LIB.indexOf('routes.push(`/${league}/performance`)')
    const eventsIdx = ROUTE_LIB.indexOf('const events = eventsByLeague[league]')
    expect(pushIdx).toBeGreaterThan(-1)
    expect(eventsIdx).toBeGreaterThan(pushIdx)
  })

  it('covers exactly the 13 non-AFL registry keys, all validatable', () => {
    // NRL-EXPANSION (nrl-expansion-07): ten state leagues + three
    // rugby-league competitions (nrl, nrlw, origin).
    expect(LEAGUE_ROUTE_KEYS).toHaveLength(13)
    expect(LEAGUE_ROUTE_KEYS).not.toContain('afl')
    // Every emitted route key passes the page's validate() gate, so a
    // prerendered /{league}/performance can never 404.
    for (const key of LEAGUE_ROUTE_KEYS) {
      expect(LEAGUES.some((l) => l.key === key)).toBe(true)
    }
  })

  it('the prerender hook consumes enumerateLeagueRoutes (no parallel implementation)', () => {
    expect(NUXT_CONFIG).toMatch(/enumerateLeagueRoutes\(/)
    // And the config documents the performance-route contract.
    expect(NUXT_CONFIG).toMatch(/PERFORMANCE-ROUTES/)
  })

  it('the sitemap consumes the same enumerer (cannot drift from prerender)', () => {
    expect(SITEMAP).toMatch(/enumerateLeagueRoutes\(/)
    expect(SITEMAP).toMatch(/PERFORMANCE-ROUTES/)
  })

  it('keeps AFL off the league route family (no /afl/performance)', () => {
    // The middleware redirect is the ONLY afl handling; the enumerer
    // filters 'afl' defensively.
    expect(ROUTE_LIB).toMatch(/if \(league === 'afl'\) continue/)
    expect(ROUTE_LIB).not.toMatch(/`\/afl\/performance`/)
  })
})

// ---------------------------------------------------------------------------
// States, a11y & responsive design (shared component styles)
// ---------------------------------------------------------------------------

describe('performance view states & styling', () => {
  it('styles the shared view through the design-system custom properties', () => {
    // PERF-VIEW-UNIFY: one style block for every league — the AFL
    // accent colours (#15803d/#b91c1c profit accents) are part of the
    // shared language now, so the league page's old monochrome-only
    // pin is deliberately retired with the unification.
    expect(VIEW_STYLES).toMatch(/var\(--color-/)
  })

  it('is responsive at the shared breakpoints (mobile / tablet / desktop)', () => {
    expect(VIEW_STYLES).toMatch(/@media \(max-width: 640px\)/)
    expect(VIEW_STYLES).toMatch(/@media \(min-width: 641px\) and \(max-width: 1024px\)/)
    expect(VIEW_STYLES).toMatch(/@media \(min-width: 1025px\)/)
  })

  it('renders every registry league name from the same config contract', () => {
    // Sanity: every validated key resolves to a displayName, so the
    // empty state and hero always have a label to show.
    for (const league of LEAGUES) {
      expect(league.displayName.length).toBeGreaterThan(0)
    }
  })
})
