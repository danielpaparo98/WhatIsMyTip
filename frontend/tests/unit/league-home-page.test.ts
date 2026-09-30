/**
 * LEAGUE-ROUTES (2026-09-30, user request): first-class /{league} home
 * pages + the route-driven useLeagueEvents refactor.
 *
 * Covers:
 *  1. Route contract — unknown league keys 404 (validate), 'afl'
 *     canonicalises to '/' (AFL stays at the root URL), and the page
 *     syncs the header dropdown via setActiveLeague.
 *  2. Presentation parity with the AFL home — identical hero (always
 *     visible), GF/R round strip, clickable cards with badges, result
 *     rows, end-of-season celebration with confetti.
 *  3. The useLeagueEvents signature refactor (completed by subtask 07):
 *     the route param is the REQUIRED league source — the legacy
 *     no-arg global-selector path is gone and pages/index.vue is
 *     AFL-only again. The mid-flight stale-league guards survive.
 *
 * The pure helpers (`resolveCompetition`, `deriveCurrentRound`,
 * `sortRoundEvents`) keep their pinned behaviour in
 * league-aware-home.test.ts; here we assert the signatures survive the
 * refactor and that the page wiring is present, matching the repo's
 * static-analysis test style (SFCs cannot be mounted in vitest).
 */
import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { resolve, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'
import {
  LEAGUE_COMPETITION_NAMES,
  deriveCurrentRound,
  resolveCompetition,
  sortRoundEvents,
  type SportEvent,
} from '~/composables/useLeagueEvents'
import { LEAGUES } from '~/composables/useSportConfig'
import { derivePremier, deriveSeasonState } from '~/composables/useSeasonState'

const FRONTEND_ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '../..')

const PAGE = readFileSync(resolve(FRONTEND_ROOT, 'pages/[league]/index.vue'), 'utf8')
const COMPOSABLE = readFileSync(
  resolve(FRONTEND_ROOT, 'composables/useLeagueEvents.ts'),
  'utf8',
)
const INDEX = readFileSync(resolve(FRONTEND_ROOT, 'pages/index.vue'), 'utf8')
const CONFETTI = readFileSync(resolve(FRONTEND_ROOT, 'components/ConfettiEffect.vue'), 'utf8')
const CELEBRATION = readFileSync(
  resolve(FRONTEND_ROOT, 'components/OffSeasonCelebration.vue'),
  'utf8',
)

/** Collapse whitespace so hero markup compares indentation-agnostic. */
const norm = (s: string): string => s.replace(/\s+/g, ' ').trim()

const PAGE_STYLES = PAGE.match(/<style[^>]*>([\s\S]*?)<\/style>/)?.[1] ?? ''

// ---------------------------------------------------------------------------
// Fixtures (mirrors league-aware-home.test.ts)
// ---------------------------------------------------------------------------

const ev = (overrides: Partial<SportEvent> & { id: number }): SportEvent => ({
  slug: `ev${overrides.id}`,
  round_id: 1,
  venue: null,
  starts_at: null,
  status: 'scheduled',
  completed: false,
  competition: 'WAFL',
  season: '2026',
  participants: [],
  ...overrides,
})

// ---------------------------------------------------------------------------
// Route contract
// ---------------------------------------------------------------------------

describe('league page route contract', () => {
  it('carries the tagged rationale comment', () => {
    expect(PAGE).toContain('// LEAGUE-ROUTES (2026-09-30, user request)')
  })

  it('validate() rejects keys that are not in the LEAGUES registry', () => {
    expect(PAGE).toMatch(/definePageMeta/)
    expect(PAGE).toMatch(/validate/)
    expect(PAGE).toMatch(/route\.params\.league/)
    // The whitelist check against the static registry is the 404 gate.
    expect(PAGE).toMatch(/LEAGUES\.some/)
  })

  it("redirects the 'afl' key to '/' so AFL stays at the root URL", () => {
    expect(PAGE).toContain("'afl'")
    expect(PAGE).toMatch(/navigateTo\('\/',\s*\{\s*replace:\s*true\s*\}\)/)
  })

  it('validates every non-AFL registry key as valid', () => {
    // Sanity for the whitelist source itself: every registry key except
    // 'afl' must have a competition name contract, so a valid URL always
    // has data to resolve against (or a clean "not synced" state).
    const selectable = LEAGUES.map((l) => l.key).filter((k) => k !== 'afl')
    for (const key of selectable) {
      expect(LEAGUE_COMPETITION_NAMES[key], `missing competition name for ${key}`).toBeTruthy()
    }
  })

  it('syncs the active league to the route (client-only)', () => {
    expect(PAGE).toMatch(/setActiveLeague\(/)
    expect(PAGE).toMatch(/import\.meta\.client/)
  })

  it('drives the composable with the route-derived league key', () => {
    expect(PAGE).toMatch(/useLeagueEvents\(leagueKey\)/)
  })
})

// ---------------------------------------------------------------------------
// Hero — identical to index.vue's and always visible
// ---------------------------------------------------------------------------

describe('league page hero', () => {
  it('uses the exact hero markup of the AFL home', () => {
    const indexHero = INDEX.match(/<section class="hero">[\s\S]*?<\/section>/)?.[0]
    expect(indexHero, 'index.vue hero not found').toBeTruthy()
    expect(norm(PAGE)).toContain(norm(indexHero as string))
  })

  it('renders exactly ONE hero, before any conditional state branch', () => {
    // ALWAYS-HERO convention: the hero must not live inside a v-if branch.
    expect(PAGE.match(/class="hero"/g)?.length).toBe(1)
    const heroIdx = PAGE.indexOf('class="hero"')
    const firstConditional = PAGE.search(/v-if=/)
    expect(firstConditional).toBeGreaterThan(-1)
    expect(heroIdx).toBeLessThan(firstConditional)
  })
})

// ---------------------------------------------------------------------------
// Round strip + game count
// ---------------------------------------------------------------------------

describe('league page round strip', () => {
  it("shows 'GF • {season}' for grand_final_upcoming/season_complete, 'R{n} • {season}' otherwise", () => {
    expect(PAGE).toMatch(/deriveSeasonState\(/)
    expect(PAGE).toContain('GF • ${season}')
    expect(PAGE).toContain('R${roundId.value} • ${season}')
  })

  it('counts games with the league config contest noun', () => {
    expect(PAGE).toMatch(/contestNoun/)
  })
})

// ---------------------------------------------------------------------------
// Fixture cards
// ---------------------------------------------------------------------------

describe('league page cards', () => {
  it('renders clickable cards linking to /{league}/match/{slug}', () => {
    expect(PAGE).toMatch(/<NuxtLink/)
    expect(PAGE).toMatch(/game-card-link/)
    expect(PAGE).toContain('/match/')
    expect(PAGE).toMatch(/\.slug/)
  })

  it('shows club badges via the extended logoFor with the league key', () => {
    expect(PAGE).toMatch(/logoFor\([^)]*leagueKey[^)]*\)/)
    expect(PAGE).toMatch(/:alt=/)
  })

  it('shows completed events as result rows with winner emphasis', () => {
    expect(PAGE).toMatch(/league-result/)
    expect(PAGE).toMatch(/winner:\s*\w+\(ev,\s*'home'\)/)
    expect(PAGE).toMatch(/winner:\s*\w+\(ev,\s*'away'\)/)
    expect(PAGE).toMatch(/result-score/)
  })

  it('shows a status label for upcoming events', () => {
    expect(PAGE).toMatch(/'Scheduled'/)
  })

  it('renders venue and a formatted date', () => {
    expect(PAGE).toMatch(/venue/)
    expect(PAGE).toMatch(/formatDate\(/)
  })
})

// ---------------------------------------------------------------------------
// End-of-season treatment
// ---------------------------------------------------------------------------

describe('league page season-complete celebration', () => {
  it('renders OffSeasonCelebration with the derived premier and season', () => {
    expect(PAGE).toMatch(/<OffSeasonCelebration/)
    expect(PAGE).toMatch(/:premier="premier"/)
    expect(PAGE).toMatch(/:season=/)
    expect(PAGE).toMatch(/derivePremier\(/)
  })

  it('fires ConfettiEffect in both finalists’ league colours', () => {
    expect(PAGE).toMatch(/<ConfettiEffect/)
    expect(PAGE).toMatch(/:colors=/)
    expect(PAGE).toMatch(/leagueColorFor\(/)
  })

  it('ConfettiEffect supports an explicit active opt-in (AFL gate untouched)', () => {
    // The league pages need the bursts without the AFL grand-final gate;
    // the default (no prop) must keep driving off latestRound.
    expect(CONFETTI).toMatch(/active\?:\s*boolean/)
    expect(CONFETTI).toMatch(/is_grand_final/)
    expect(INDEX).toMatch(/<ConfettiEffect\s+:colors="gfConfettiColors"\s*\/>/)
  })

  it('OffSeasonCelebration resolves the premier badge for the league', () => {
    expect(CELEBRATION).toMatch(/league\?:/)
    expect(CELEBRATION).toMatch(/logoFor\(/)
  })

  it('OffSeasonCelebration burst colours prefer the league palette (getTeamColors fallback)', () => {
    // LEAGUE-ROUTES (2026-09-30, code review): a state-league premier
    // must burst in their club colours, not the AFL map's generic
    // fallback (which clashed with the page's club-coloured confetti).
    expect(CELEBRATION).toMatch(/leagueColorFor\(props\.league, props\.premier\)/)
    // AFL callers pass no league → leagueColorFor yields null and this
    // fallback keeps their behaviour byte-identical.
    expect(CELEBRATION).toMatch(/getTeamColors\(props\.premier\)/)
  })
})

// ---------------------------------------------------------------------------
// States & accessibility
// ---------------------------------------------------------------------------

describe('league page states & a11y', () => {
  it('preserves the unavailable message', () => {
    expect(PAGE).toContain('fixtures have been synced yet.')
    expect(PAGE).toMatch(/No \{\{ \w[\w.]*\.displayName \}\}/)
  })

  it('announces loading, error and empty states (role="status" aria-live)', () => {
    expect(PAGE.match(/role="status"/g)?.length).toBeGreaterThanOrEqual(4)
    expect(PAGE.match(/aria-live="polite"/g)?.length).toBeGreaterThanOrEqual(4)
    expect(PAGE).toMatch(/class="spinner"/)
  })

  it('offers a retry button on error', () => {
    expect(PAGE).toMatch(/@click="\w*[rR]efresh\w*"/)
    expect(PAGE).toContain('Retry')
  })

  it('has NO tips/models/weather sections (no league data exists)', () => {
    // NOTE: the hero copy ("Smart heuristics.") is shared with the AFL
    // home and is fine — only the tipping UI must be absent.
    expect(PAGE).not.toMatch(/TipCard|WeatherCard|MatchAnalysisCard|ModelPrediction/)
    expect(PAGE).not.toMatch(/heuristic-selector|heuristic-btn|tips-grid|models-grid|model-card/)
  })

  it('stays monochrome (design-system custom properties, no hex accents)', () => {
    expect(PAGE_STYLES).toMatch(/var\(--color-/)
    expect(PAGE_STYLES).not.toMatch(/#[0-9a-fA-F]{3,8}\b/)
  })

  it('keeps 44px touch targets and focus-visible styles', () => {
    expect(PAGE_STYLES).toMatch(/min-height:\s*44px/)
    expect(PAGE_STYLES).toMatch(/focus-visible/)
  })
})

// ---------------------------------------------------------------------------
// useLeagueEvents refactor — route-driven, backward compatible
// ---------------------------------------------------------------------------

describe('useLeagueEvents refactor', () => {
  it('requires an explicit league source argument', () => {
    expect(COMPOSABLE).toMatch(/export type LeagueEventsSource/)
    expect(COMPOSABLE).toMatch(
      // LEAGUE-ROUTES (2026-09-30, code review): async — the fetch is an
      // awaited useAsyncData so the page's setup suspends on it.
      /export async function useLeagueEvents\(leagueSource: LeagueEventsSource\)/,
    )
  })

  it('is route-driven only — the legacy no-arg global-selector path is gone', () => {
    // LEAGUE-ROUTES (2026-09-30, user request): subtask 07 removed the
    // home page's league branch, so the composable has exactly one
    // caller — pages/[league]/index.vue with the route key — and no
    // dependency on the global league store.
    expect(COMPOSABLE).not.toMatch(/leagueSource === undefined/)
    expect(COMPOSABLE).not.toMatch(/useActiveLeague/)
    // pages/index.vue is AFL-only: it calls neither the composable nor
    // the league store.
    expect(INDEX).not.toMatch(/useLeagueEvents/)
    expect(INDEX).not.toMatch(/useActiveLeague/)
    // The league page is the only call site, and it passes the route key.
    expect(PAGE).toMatch(/useLeagueEvents\(leagueKey\)/)
  })

  it('fetches through an awaited useAsyncData so prerender inlines the payload', () => {
    // LEAGUE-ROUTES (2026-09-30, code review): the watch+refs wiring only
    // ran on the client, so every prerendered league home baked a
    // hero+spinner shell with zero fixtures content — the SEO-C1 failure
    // pages/index.vue documents as fixed for AFL via awaited useAsyncData.
    expect(COMPOSABLE).toMatch(/await useAsyncData/)
    // Reactive per-league cache key + dedupe cancel (the game/match
    // pages' H-5 contract): a league change refetches, a stale response
    // is cancelled — the replacement for the removed mid-flight guards.
    expect(COMPOSABLE).toMatch(/league-events-\$\{/)
    expect(COMPOSABLE).toMatch(/dedupe:\s*'cancel'/)
  })

  it('refreshes client-side after hydration (preserves the old immediate-watch freshness)', () => {
    // The payload renders the first paint; onNuxtReady schedules ONE
    // background refresh on hydrated visits, client-only.
    expect(COMPOSABLE).toMatch(/import\.meta\.client/)
    expect(COMPOSABLE).toMatch(/onNuxtReady/)
  })

  it('exposes the full season payload for season-state derivation', () => {
    expect(COMPOSABLE).toMatch(/seasonEvents/)
    expect(COMPOSABLE).toMatch(/interface LeagueEventsState[\s\S]*?seasonEvents/)
  })

  it('keeps the pure helper signatures stable', () => {
    // Behavioural pins live in league-aware-home.test.ts; here we make
    // sure the exports survived the refactor with their contracts.
    expect(typeof resolveCompetition).toBe('function')
    expect(typeof deriveCurrentRound).toBe('function')
    expect(typeof sortRoundEvents).toBe('function')
    expect(resolveCompetition(null, 'wafl')).toBeNull()
    expect(deriveCurrentRound([], new Date('2026-08-15T00:00:00Z'))).toBeNull()
    const sorted = sortRoundEvents([
      ev({ id: 2, starts_at: '2026-08-15T05:00:00Z' }),
      ev({ id: 1, starts_at: '2026-08-16T05:00:00Z' }),
    ])
    expect(sorted.map((e) => e.id)).toEqual([2, 1])
  })

  it('stays importable without the Nuxt runtime (type-only useApi import)', () => {
    expect(COMPOSABLE).toMatch(/import type \{ SportEvent \} from '~\/composables\/useApi'/)
    // LEAGUE-ROUTES (2026-09-30, user request): the pure helpers moved to
    // the Nuxt-free lib/leagueRoutes.ts — importable from nuxt.config.ts
    // in plain Node — and are re-exported here, so the prerender hook,
    // the sitemap, the composable, and these tests share one source of
    // truth for the league/competition mapping and round derivation.
    expect(COMPOSABLE).toMatch(/from '\.\.\/lib\/leagueRoutes'/)
  })
})

// ---------------------------------------------------------------------------
// Season-state helpers used by the page (re-export sanity)
// ---------------------------------------------------------------------------

describe('season-state helper integration', () => {
  it('the page imports both deriveSeasonState and derivePremier', () => {
    expect(PAGE).toMatch(/import \{[^}]*deriveSeasonState[^}]*\} from '~\/composables\/useSeasonState'/)
    expect(PAGE).toMatch(/derivePremier/)
  })

  it('deriveSeasonState/derivePremier behave as the page expects', () => {
    const settled = [
      ev({ id: 1, round_id: 1, starts_at: '2026-04-04T13:10:00', completed: true, status: 'completed' }),
      ev({
        id: 2,
        round_id: 23,
        starts_at: '2026-09-26T14:10:00',
        completed: true,
        status: 'completed',
        participants: [
          { side: 'home', participant_name: 'South Fremantle', score: 90, is_winner: true },
          { side: 'away', participant_name: 'Claremont', score: 70, is_winner: false },
        ],
      }),
    ]
    expect(deriveSeasonState(settled, 23)).toBe('season_complete')
    expect(derivePremier(settled)).toBe('South Fremantle')
    expect(deriveSeasonState([], null)).toBe('regular')
  })
})
