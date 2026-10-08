/**
 * PERF-PER-LEAGUE (2026-10): first-class /{league}/performance pages
 * (D5 of the performance-per-league feature).
 *
 * Covers:
 *  1. Route contract — copied from pages/[league]/index.vue EXACTLY:
 *     validate() against the LEAGUES registry (unknown keys 404), the
 *     inline middleware canonicalising 'afl' to /performance (AFL owns
 *     the root-level page — there is NO /afl/performance route), the
 *     client-only setActiveLeague() sync, and the leagueConfig resolved
 *     from the ROUTE param (never the global active league store — the
 *     prerender bakes every league page in one process).
 *  2. Data wiring — the subtask-09 useApi league methods
 *     (getLeagueCurrentSeasonPerformance / getLeagueSeasons /
 *     getLeagueComparison) fetched through an awaited useAsyncData with
 *     a reactive per-league cache key so the payload inlines into the
 *     prerendered HTML (the SEO-C1 contract).
 *  3. Presentation — the same two-season cards as the AFL page
 *     (accuracy / profit / rounds) WITHOUT the AFL-only model/weighted/
 *     boosted sections, plus the APPROVED graceful empty state when a
 *     league has no tips yet (unsynced leagues included).
 *  4. Per-league SEO — title/canonical/JSON-LD derived from
 *     getLeagueConfig(leagueKey).displayName and the
 *     ${siteUrl}/{league}/performance canonical URL.
 *  5. Prerender/sitemap trace — both call sites consume
 *     buildLeagueRoutes, which emits /{league}/performance for ALL
 *     LEAGUE_ROUTE_KEYS unconditionally (verified by source pins, not
 *     re-implemented).
 *
 * Matches the repo's static-analysis test style (SFCs cannot be
 * mounted in vitest — see league-home-page.test.ts): page behaviour is
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
const HOME = readFileSync(resolve(FRONTEND_ROOT, 'pages/[league]/index.vue'), 'utf8')
const SEASON_LIB = readFileSync(resolve(FRONTEND_ROOT, 'lib/performanceSeasons.ts'), 'utf8')
const ROUTE_LIB = readFileSync(resolve(FRONTEND_ROOT, 'lib/leagueRoutes.ts'), 'utf8')
const NUXT_CONFIG = readFileSync(resolve(FRONTEND_ROOT, 'nuxt.config.ts'), 'utf8')
const SITEMAP = readFileSync(resolve(FRONTEND_ROOT, 'server/routes/sitemap.xml.ts'), 'utf8')

const PAGE_STYLES = PAGE.match(/<style[^>]*>([\s\S]*?)<\/style>/)?.[1] ?? ''

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
// Route contract — copied from pages/[league]/index.vue
// ---------------------------------------------------------------------------

describe('league performance page route contract', () => {
  it('carries the tagged rationale comment', () => {
    expect(PAGE).toContain('// PERF-PER-LEAGUE (2026-10')
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

  it('resolves leagueConfig from the ROUTE param, never the global active league', () => {
    // Prerender bakes every league page in one process — a global-store
    // read would leak one league's labels into every static page.
    expect(PAGE).toMatch(/getLeagueConfig\(leagueKey\.value \?\? 'afl'\)/)
    expect(PAGE).not.toMatch(/activeConfig/)
  })
})

// ---------------------------------------------------------------------------
// Data wiring — subtask-09 useApi league methods through awaited useAsyncData
// ---------------------------------------------------------------------------

describe('league performance page data wiring', () => {
  it('fetches through an awaited useAsyncData so prerender inlines the payload', () => {
    // The SEO-C1 contract: generate-time fetch, payload inlined into the
    // static HTML (see [league]/index.vue's LEAGUE-ROUTES review note).
    expect(PAGE).toMatch(/await useAsyncData/)
  })

  it('uses a reactive per-league cache key with dedupe cancel (H-5 contract)', () => {
    expect(PAGE).toMatch(/league-performance-\$\{/)
    expect(PAGE).toMatch(/dedupe:\s*'cancel'/)
  })

  it('calls the three league backtest methods with the route league key', () => {
    expect(PAGE).toMatch(/getLeagueCurrentSeasonPerformance\(league\)/)
    expect(PAGE).toMatch(/getLeagueSeasons\(league\)/)
    expect(PAGE).toMatch(/getLeagueComparison\(league,\s*prevSeason\)/)
  })

  it('selects the past season with the shared max(year<current) helper', () => {
    expect(PAGE).toMatch(/mostRecentPastSeasonYear\(/)
    expect(PAGE).toMatch(/import \{ mostRecentPastSeasonYear \} from '~\/lib\/performanceSeasons'/)
  })

  it('degrades a failed current-season fetch to the empty state (404 unsynced included)', () => {
    // Unsynced league → the backend 404s. The page catches and renders
    // the approved empty state — a missing league must never 500 the
    // prerender.
    expect(PAGE).toMatch(/getLeagueCurrentSeasonPerformance\(league\)[\s\S]{0,80}\.catch\(\(\) => null\)/)
  })

  it('isolates the past-season fetch so it can never break the page', () => {
    // The seasons→compare chain is additive: wrapped in try/catch and
    // nulled on failure, hiding only the past-season section.
    expect(PAGE).toMatch(/catch[\s\S]{0,40}past = null/)
  })

  it('refreshes client-side after hydration (preserves post-hydration freshness)', () => {
    expect(PAGE).toMatch(/import\.meta\.client/)
    expect(PAGE).toMatch(/onNuxtReady/)
  })
})

// ---------------------------------------------------------------------------
// Presentation — two-season cards, no AFL-only sections, approved empty state
// ---------------------------------------------------------------------------

describe('league performance page presentation', () => {
  it('renders exactly ONE hero before any conditional state branch', () => {
    expect(PAGE.match(/class="hero"/g)?.length).toBe(1)
    const heroIdx = PAGE.indexOf('class="hero"')
    const firstConditional = PAGE.search(/v-if=/)
    expect(firstConditional).toBeGreaterThan(-1)
    expect(heroIdx).toBeLessThan(firstConditional)
  })

  it('renders the current-season heuristic cards with accuracy, profit and rounds', () => {
    expect(PAGE).toMatch(/class="season-cards"/)
    expect(PAGE).toMatch(/class="season-card"/)
    expect(PAGE).toMatch(/Current Season/)
    expect(PAGE).toContain('Accuracy')
    expect(PAGE).toContain('Rounds Played')
    expect(PAGE).toMatch(/formatProfit\(/)
  })

  it('renders the Most Recent Past Season section', () => {
    expect(PAGE).toContain('Most Recent Past Season')
    expect(PAGE).toMatch(/pastSeasonEntries/)
  })

  it('has NO AFL-only model/weighted/XGBoost sections', () => {
    // The model comparison, Weighted Tip and boosted (XGBoost) sections
    // are AFL-only — state leagues run heuristics exclusively.
    expect(PAGE).not.toMatch(/ModelCoefficientChart/)
    expect(PAGE).not.toMatch(/Weighted Tip Model/)
    expect(PAGE).not.toMatch(/XGBoost|boostedModel|getActiveBoostedModel/)
    expect(PAGE).not.toMatch(/activeModelData|getActiveModel|compareModels|model-mini/)
  })

  it('renders the APPROVED graceful empty state verbatim', () => {
    expect(PAGE).toContain(
      "Performance tracking isn't available for {{ leagueConfig.displayName }} yet.",
    )
    expect(PAGE).toContain(
      'Performance tracking begins once tipping models are live for this league.',
    )
    expect(PAGE).toMatch(/class="empty-hint"/)
  })

  it('announces loading and empty states (role="status" aria-live)', () => {
    expect(PAGE.match(/role="status"/g)?.length).toBeGreaterThanOrEqual(2)
    expect(PAGE.match(/aria-live="polite"/g)?.length).toBeGreaterThanOrEqual(2)
    expect(PAGE).toMatch(/class="spinner"/)
  })
})

// ---------------------------------------------------------------------------
// Per-league SEO
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

  it('covers exactly the 10 non-AFL registry keys, all validatable', () => {
    expect(LEAGUE_ROUTE_KEYS).toHaveLength(10)
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
// States, a11y & responsive design
// ---------------------------------------------------------------------------

describe('league performance page states & styling', () => {
  it('stays monochrome (design-system custom properties, no hex accents)', () => {
    expect(PAGE_STYLES).toMatch(/var\(--color-/)
    expect(PAGE_STYLES).not.toMatch(/#[0-9a-fA-F]{3,8}\b/)
  })

  it('is responsive at the league-page breakpoints (mobile / tablet / desktop)', () => {
    expect(PAGE_STYLES).toMatch(/@media \(max-width: 640px\)/)
    expect(PAGE_STYLES).toMatch(/@media \(min-width: 641px\) and \(max-width: 1024px\)/)
    expect(PAGE_STYLES).toMatch(/@media \(min-width: 1025px\)/)
  })

  it('renders every registry league name from the same config contract', () => {
    // Sanity: every validated key resolves to a displayName, so the
    // empty state and hero always have a label to show.
    for (const league of LEAGUES) {
      expect(league.displayName.length).toBeGreaterThan(0)
    }
  })
})
