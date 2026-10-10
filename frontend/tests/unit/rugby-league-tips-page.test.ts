/**
 * NRL-TIPS (nrl-expansion-09, 2026-10-10): rugby-league tips pages under
 * first-class /{league} routes.
 *
 * The three rugby-league competitions (nrl, nrlw, origin) HAVE a reduced
 * tipping model set (elo, form, home_advantage, matchup — backend
 * nrl-expansion-08), unlike the state leagues whose pages are
 * fixtures-only by design (pinned by league-home-page.test.ts — that
 * file must keep rendering NO tipping UI). So subtask 09 ships:
 *
 *  1. Three THIN static pages — pages/nrl, pages/nrlw, pages/origin —
 *     which Nuxt serves INSTEAD of pages/[league]/index.vue for those
 *     URLs (static segments outrank the dynamic param), leaving every
 *     AFL/state-league page byte-identical.
 *  2. One shared view — components/RugbyLeagueTipsView.vue — carrying
 *     the whole tips surface: always-on hero, round strip, the reduced
 *     model strip, clickable cards with club badges, the pending-tips
 *     degradation on upcoming cards, and the end-of-season celebration.
 *  3. Pure helpers in composables/useRugbyLeagueTips.ts so vitest can
 *     exercise the label/wording contracts without mounting the SFC
 *     (the repo's static-analysis test style).
 *
 * Config flow (the core contract): every heuristic/model label, the
 * contest noun ("Match") and the stage noun ("Round") flow from
 * getLeagueConfig(key) → RUGBY_LEAGUE_CONFIG — no hardcoded strings in
 * components. Prerender + sitemap enumeration for the three URLs is
 * leagueRoutes-driven (committed); these tests pin that contract too.
 */
import { describe, it, expect } from 'vitest'
import { readFileSync, existsSync } from 'node:fs'
import { resolve, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'
import {
  formatRoundStrip,
  isRugbyLeagueLeague,
  tipModels,
} from '~/composables/useRugbyLeagueTips'
import { RUGBY_LEAGUE_CONFIG, getLeagueConfig } from '~/composables/useSportConfig'
import { LEAGUE_ROUTE_KEYS, buildLeagueRoutes } from '~/lib/leagueRoutes'
import type { SportEvent } from '~/composables/useApi'

const FRONTEND_ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '../..')

const PAGE_PATHS = ['nrl', 'nrlw', 'origin'].map(
  (key) => `pages/${key}/index.vue`,
)
const VIEW_PATH = 'components/RugbyLeagueTipsView.vue'
const HELPERS_PATH = 'composables/useRugbyLeagueTips.ts'

const readSource = (rel: string): string =>
  readFileSync(resolve(FRONTEND_ROOT, rel), 'utf8')

const PAGE_SOURCES: Record<string, string> = {}
for (const key of ['nrl', 'nrlw', 'origin']) {
  PAGE_SOURCES[key] = readSource(`pages/${key}/index.vue`)
}
const VIEW = readSource(VIEW_PATH)
const HELPERS = readSource(HELPERS_PATH)

const VIEW_STYLES = VIEW.match(/<style[^>]*>([\s\S]*?)<\/style>/)?.[1] ?? ''

// ---------------------------------------------------------------------------
// Fixtures (mirrors league-home-page.test.ts)
// ---------------------------------------------------------------------------

const ev = (overrides: Partial<SportEvent> & { id: number }): SportEvent => ({
  slug: `ev${overrides.id}`,
  round_id: 1,
  venue: null,
  starts_at: null,
  status: 'scheduled',
  completed: false,
  competition: 'NRL',
  season: '2026',
  participants: [],
  ...overrides,
})

// ---------------------------------------------------------------------------
// Files + route contract
// ---------------------------------------------------------------------------

describe('rugby-league tips page files', () => {
  for (const key of ['nrl', 'nrlw', 'origin']) {
    it(`pages/${key}/index.vue exists as a first-class static route`, () => {
      expect(existsSync(resolve(FRONTEND_ROOT, `pages/${key}/index.vue`))).toBe(true)
    })
  }

  it('ships the shared tips view and the pure helper module', () => {
    expect(existsSync(resolve(FRONTEND_ROOT, VIEW_PATH))).toBe(true)
    expect(existsSync(resolve(FRONTEND_ROOT, HELPERS_PATH))).toBe(true)
  })

  for (const key of ['nrl', 'nrlw', 'origin']) {
    it(`pages/${key} is a thin wrapper passing its league key to the shared view`, () => {
      const page = PAGE_SOURCES[key]
      expect(page).toMatch(/<RugbyLeagueTipsView/)
      // The page passes ITS OWN key — the route contract, not a label.
      expect(page).toContain(`league="${key}"`)
      // Thin by contract: no data wiring leaks into the wrapper pages —
      // all fetching/presentation lives in the shared view.
      expect(page).not.toMatch(/useLeagueEvents|useApi|getSports|getEvents/)
    })
  }

  it('carries the tagged rationale in the view', () => {
    expect(VIEW).toContain('// NRL-TIPS (nrl-expansion-09')
  })
})

describe('route + prerender contract (leagueRoutes-driven)', () => {
  it('enumerates the three tips URLs for prerender + sitemap', () => {
    // No nuxt.config change needed: the committed LEAGUE_ROUTE_KEYS drive
    // both the prerender hook and the sitemap. Pinned so the pages and
    // the enumeration cannot drift apart silently.
    for (const key of ['nrl', 'nrlw', 'origin']) {
      expect(LEAGUE_ROUTE_KEYS).toContain(key)
    }
  })

  it('buildLeagueRoutes emits /{league} for all three keys even before sync', () => {
    const routes = buildLeagueRoutes(['nrl', 'nrlw', 'origin'], {})
    expect(routes).toContain('/nrl')
    expect(routes).toContain('/nrlw')
    expect(routes).toContain('/origin')
  })

  it('every rugby-league key resolves through the rugby-league SportConfig', () => {
    for (const key of ['nrl', 'nrlw', 'origin']) {
      expect(getLeagueConfig(key).sportId).toBe(RUGBY_LEAGUE_CONFIG.sportId)
    }
    // The view guards against misuse with the same predicate.
    expect(isRugbyLeagueLeague('nrl')).toBe(true)
    expect(isRugbyLeagueLeague('nrlw')).toBe(true)
    expect(isRugbyLeagueLeague('origin')).toBe(true)
    expect(isRugbyLeagueLeague('wafl')).toBe(false)
    expect(isRugbyLeagueLeague('afl')).toBe(false)
    expect(isRugbyLeagueLeague(null)).toBe(false)
  })
})

// ---------------------------------------------------------------------------
// Config-driven wording — the core "no hardcoded strings" contract
// ---------------------------------------------------------------------------

describe('SportConfig-driven labels (no hardcoded strings in the view)', () => {
  it('tipModels() maps the reduced model set through the config labels, in order', () => {
    expect(
      tipModels(RUGBY_LEAGUE_CONFIG.heuristicOrder, RUGBY_LEAGUE_CONFIG.heuristicLabels),
    ).toEqual([
      { key: 'elo', label: 'Elo Rating' },
      { key: 'form', label: 'Form' },
      { key: 'home_advantage', label: 'Home Advantage' },
      { key: 'matchup', label: 'Matchup' },
    ])
  })

  it('tipModels() falls back to the raw key when a label is missing', () => {
    expect(tipModels(['elo', 'mystery_model'], { elo: 'Elo Rating' })).toEqual([
      { key: 'elo', label: 'Elo Rating' },
      { key: 'mystery_model', label: 'mystery_model' },
    ])
  })

  it('the view resolves chips via heuristicOrder + heuristicLabels', () => {
    expect(VIEW).toMatch(/tipModels\(/)
    expect(VIEW).toMatch(/heuristicOrder/)
    expect(VIEW).toMatch(/heuristicLabels/)
  })

  it('the view hardcodes NO heuristic/model label strings', () => {
    expect(VIEW).not.toMatch(/['"]Elo Rating['"]/)
    expect(VIEW).not.toMatch(/['"]Home Advantage['"]/)
    expect(VIEW).not.toMatch(/['"]Matchup['"]/)
    expect(VIEW).not.toMatch(/['"]Form['"]/)
  })

  it('the view uses the config nouns (contestNoun, stageNoun) — never literals', () => {
    expect(VIEW).toMatch(/contestNoun/)
    expect(VIEW).toMatch(/stageNoun/)
    expect(VIEW).not.toMatch(/['"]Match['"]/)
    expect(VIEW).not.toMatch(/['"]Round['"]/)
  })

  it('the round strip wording flows through formatRoundStrip with the config stageNoun', () => {
    expect(VIEW).toMatch(/formatRoundStrip\(/)
    expect(formatRoundStrip(8, '2026', 'regular', 'Round')).toBe('Round 8 • 2026')
    expect(formatRoundStrip(3, '2026', 'grand_final_upcoming', 'Round')).toBe('GF • 2026')
    expect(formatRoundStrip(3, '2026', 'season_complete', 'Round')).toBe('GF • 2026')
    expect(formatRoundStrip(null, '2026', 'regular', 'Round')).toBe('')
    expect(formatRoundStrip(8, null, 'regular', 'Round')).toBe('Round 8 • ')
  })
})

// ---------------------------------------------------------------------------
// Tips surface — fixtures + models + graceful degradation
// ---------------------------------------------------------------------------

describe('tips surface presentation', () => {
  it('drives data through the route-driven useLeagueEvents composable', () => {
    expect(VIEW).toMatch(/useLeagueEvents\(/)
  })

  it('renders the reduced model strip on the page', () => {
    expect(VIEW).toMatch(/class="model-strip"/)
    expect(VIEW).toMatch(/class="model-chip"/)
  })

  it('renders a model-tips block on upcoming cards with the pending degradation', () => {
    expect(VIEW).toMatch(/class="tips-block"/)
    expect(VIEW).toMatch(/class="tips-pending"/)
    // Graceful empty state: no crash on missing tips — the copy explains
    // the generation cadence instead (same tone as the AFL home).
    expect(VIEW).toMatch(/generated automatically/)
  })

  it('shows completed events as result rows with winner emphasis', () => {
    expect(VIEW).toMatch(/league-result/)
    expect(VIEW).toMatch(/result-score/)
    expect(VIEW).toMatch(/is_winner/)
  })

  it('renders clickable cards linking to /{league}/match/{slug}', () => {
    expect(VIEW).toMatch(/<NuxtLink/)
    expect(VIEW).toMatch(/game-card-link/)
    expect(VIEW).toContain('/match/')
    expect(VIEW).toMatch(/\.slug/)
  })

  it('shows club badges best-effort via logoFor with the league key (colour fallback)', () => {
    expect(VIEW).toMatch(/logoFor\([^)]*league[^)]*\)/)
    expect(VIEW).toMatch(/:alt=/)
  })

  it('resolves the presentation config from the league key via getLeagueConfig', () => {
    expect(VIEW).toMatch(/getLeagueConfig\(/)
  })
})

// ---------------------------------------------------------------------------
// End-of-season treatment (parity with the league-page pattern)
// ---------------------------------------------------------------------------

describe('end-of-season treatment', () => {
  it('celebrates the premier via OffSeasonCelebration with the league prop', () => {
    expect(VIEW).toMatch(/<OffSeasonCelebration/)
    expect(VIEW).toMatch(/:premier="premier"/)
    expect(VIEW).toMatch(/:season=/)
    expect(VIEW).toMatch(/:league=/)
    expect(VIEW).toMatch(/derivePremier\(/)
    expect(VIEW).toMatch(/deriveSeasonState\(/)
  })

  it('fires ConfettiEffect in the finalists’ league colours', () => {
    expect(VIEW).toMatch(/<ConfettiEffect/)
    expect(VIEW).toMatch(/:colors=/)
    expect(VIEW).toMatch(/leagueColorFor\(/)
  })
})

// ---------------------------------------------------------------------------
// States, SEO, a11y, responsiveness
// ---------------------------------------------------------------------------

describe('states & accessibility', () => {
  it('preserves the unavailable message for the pre-first-sync state', () => {
    expect(VIEW).toContain('fixtures have been synced yet.')
    expect(VIEW).toMatch(/No \{\{ \w[\w.]*\.displayName \}\}/)
  })

  it('announces loading, error and empty states (role="status" aria-live)', () => {
    expect(VIEW.match(/role="status"/g)?.length).toBeGreaterThanOrEqual(4)
    expect(VIEW.match(/aria-live="polite"/g)?.length).toBeGreaterThanOrEqual(4)
    expect(VIEW).toMatch(/class="spinner"/)
  })

  it('offers a retry button on error', () => {
    // The match page's `retry` wrapper convention (void-typed handler).
    expect(VIEW).toMatch(/@click="retry"/)
    expect(VIEW).toMatch(/const retry = \(\) => refresh\(\)/)
    expect(VIEW).toContain('Retry')
  })

  it('syncs the header league dropdown client-only', () => {
    expect(VIEW).toMatch(/setActiveLeague\(/)
    expect(VIEW).toMatch(/import\.meta\.client/)
  })
})

describe('SEO', () => {
  it('declares state-aware meta and a canonical link', () => {
    expect(VIEW).toMatch(/useSeoMeta\(/)
    expect(VIEW).toMatch(/useHead\(/)
    expect(VIEW).toMatch(/rel: 'canonical'/)
    expect(VIEW).toMatch(/siteUrl/)
  })
})

describe('design-system compliance (monochrome, touch, responsive)', () => {
  it('stays monochrome (design-system custom properties, no hex accents)', () => {
    expect(VIEW_STYLES).toMatch(/var\(--color-/)
    expect(VIEW_STYLES).not.toMatch(/#[0-9a-fA-F]{3,8}\b/)
  })

  it('keeps 44px touch targets and focus-visible styles', () => {
    expect(VIEW_STYLES).toMatch(/min-height:\s*44px/)
    expect(VIEW_STYLES).toMatch(/focus-visible/)
  })

  it('is responsive at the mobile, tablet and desktop breakpoints', () => {
    expect(VIEW_STYLES).toMatch(/@media \(max-width: 640px\)/)
    expect(VIEW_STYLES).toMatch(/@media \(min-width: 641px\) and \(max-width: 1024px\)/)
    expect(VIEW_STYLES).toMatch(/@media \(min-width: 1025px\)/)
  })

  it('keeps card hover micro-interactions under 400ms on transform only', () => {
    expect(VIEW_STYLES).toMatch(/transition:[^;]*0\.2s/)
    expect(VIEW_STYLES).toMatch(/translateY\(-2px\)/)
  })
})
