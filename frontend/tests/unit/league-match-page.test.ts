/**
 * LEAGUE-ROUTES (2026-09-30, user request): the /{league}/match/{slug}
 * event detail pages.
 *
 *  1. Route validation: the league key must be a known LEAGUES entry and
 *     the slug must satisfy the backend's Path constraint (1–16 chars of
 *     [A-Za-z0-9_-]).  'afl' redirects home — AFL matches live on the
 *     legacy /game/{slug} surface, never under /afl/match/.
 *  2. Cross-league ownership: GET /api/events/{slug} resolves ANY league's
 *     event, so the page must verify the payload's competition name against
 *     LEAGUE_COMPETITION_NAMES[league] and 404 the mismatch — otherwise
 *     /wafl/match/<a-sanfl-slug> would render SANFL content under a WAFL URL.
 *
 * The pure rules live in composables/useLeagueMatchRoute.ts (mirroring the
 * useGameSlug.ts precedent) and are exercised behaviourally; page wiring is
 * asserted via source-grep, matching the repo's established static-analysis
 * test style (no Nuxt runtime in vitest — SFCs cannot be mounted here).
 */
import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { resolve, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'
import {
  EVENT_SLUG_REGEX,
  eventMatchesLeague,
  isValidEventSlug,
  validateLeagueMatchRoute,
} from '~/composables/useLeagueMatchRoute'
import { LEAGUE_COMPETITION_NAMES } from '~/composables/useLeagueEvents'
import { LEAGUES } from '~/composables/useSportConfig'

const FRONTEND_ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '../..')

const PAGE = readFileSync(
  resolve(FRONTEND_ROOT, 'pages/[league]/match/[slug].vue'),
  'utf8',
)

// ---------------------------------------------------------------------------
// EVENT_SLUG_REGEX / isValidEventSlug — backend Path constraint max_length=16
// ---------------------------------------------------------------------------

describe('EVENT_SLUG_REGEX', () => {
  it('accepts a typical league event slug', () => {
    expect(EVENT_SLUG_REGEX.test('r1-claremont')).toBe(true)
    expect(EVENT_SLUG_REGEX.test('gf_souths_na')).toBe(true)
  })

  it('accepts the length bounds (1..16 chars)', () => {
    expect(EVENT_SLUG_REGEX.test('x')).toBe(true)
    expect(EVENT_SLUG_REGEX.test('a'.repeat(16))).toBe(true)
  })

  it('rejects slugs longer than the backend Path constraint (16)', () => {
    expect(EVENT_SLUG_REGEX.test('a'.repeat(17))).toBe(false)
    expect(EVENT_SLUG_REGEX.test('r1-claremont-perth')).toBe(false) // 18 chars
  })

  it('rejects empty slugs and foreign characters', () => {
    expect(EVENT_SLUG_REGEX.test('')).toBe(false)
    expect(EVENT_SLUG_REGEX.test('abc.def')).toBe(false)
    expect(EVENT_SLUG_REGEX.test('abc/def')).toBe(false)
    expect(EVENT_SLUG_REGEX.test('abc def')).toBe(false)
  })
})

describe('isValidEventSlug', () => {
  it('returns true for syntactically-valid slugs', () => {
    expect(isValidEventSlug('r1-claremont')).toBe(true)
    expect(isValidEventSlug('abc_123')).toBe(true)
  })

  it('returns false for invalid or non-string inputs', () => {
    expect(isValidEventSlug('a'.repeat(17))).toBe(false)
    expect(isValidEventSlug('abc.def')).toBe(false)
    expect(isValidEventSlug(undefined)).toBe(false)
    expect(isValidEventSlug(null)).toBe(false)
    expect(isValidEventSlug(123)).toBe(false)
  })
})

// ---------------------------------------------------------------------------
// validateLeagueMatchRoute — the definePageMeta.validate decision table
// ---------------------------------------------------------------------------

describe('validateLeagueMatchRoute', () => {
  it("accepts every known non-AFL league key with a valid slug ('ok')", () => {
    const selectable = LEAGUES.map((l) => l.key).filter((k) => k !== 'afl')
    for (const key of selectable) {
      expect(validateLeagueMatchRoute(key, 'r1-claremont')).toBe('ok')
    }
  })

  it("sends 'afl' home — AFL matches live on /game/{slug}, never /afl/match/", () => {
    expect(validateLeagueMatchRoute('afl', 'r1-claremont')).toBe('redirect-home')
  })

  it("rejects unknown league keys ('not-found')", () => {
    expect(validateLeagueMatchRoute('nswl', 'r1-claremont')).toBe('not-found')
    expect(validateLeagueMatchRoute('tsl', 'r1-claremont')).toBe('not-found')
  })

  it('matches league keys exactly (no case tolerance — routes are lowercase)', () => {
    expect(validateLeagueMatchRoute('WAFL', 'r1-claremont')).toBe('not-found')
  })

  it("rejects malformed slugs even for known leagues ('not-found')", () => {
    expect(validateLeagueMatchRoute('wafl', 'a'.repeat(17))).toBe('not-found')
    expect(validateLeagueMatchRoute('wafl', 'abc.def')).toBe('not-found')
    expect(validateLeagueMatchRoute('wafl', '')).toBe('not-found')
  })

  it('rejects non-string params defensively', () => {
    expect(validateLeagueMatchRoute(undefined, 'r1-claremont')).toBe('not-found')
    expect(validateLeagueMatchRoute('wafl', undefined)).toBe('not-found')
    expect(validateLeagueMatchRoute(null, null)).toBe('not-found')
  })
})

// ---------------------------------------------------------------------------
// eventMatchesLeague — the cross-league slug-probing guard
// ---------------------------------------------------------------------------

describe('eventMatchesLeague', () => {
  it('accepts an event whose competition name matches the league', () => {
    expect(
      eventMatchesLeague(
        { competition: 'West Australian Football League' },
        'wafl',
      ),
    ).toBe(true)
  })

  it("rejects another league's event (cross-league slug probing)", () => {
    expect(
      eventMatchesLeague(
        { competition: 'South Australian National Football League' },
        'wafl',
      ),
    ).toBe(false)
  })

  it('rejects legacy AFL events under league URLs', () => {
    expect(
      eventMatchesLeague({ competition: 'Australian Football League' }, 'wafl'),
    ).toBe(false)
  })

  it('rejects an unknown league key and a missing event', () => {
    expect(eventMatchesLeague({ competition: 'WAFL' }, 'nswl')).toBe(false)
    expect(eventMatchesLeague(null, 'wafl')).toBe(false)
    expect(eventMatchesLeague(undefined, 'wafl')).toBe(false)
  })

  it('resolves a competition name for every non-AFL league key', () => {
    const selectable = LEAGUES.map((l) => l.key).filter((k) => k !== 'afl')
    for (const key of selectable) {
      const name = LEAGUE_COMPETITION_NAMES[key]
      expect(name, `LEAGUE_COMPETITION_NAMES missing ${key}`).toBeTruthy()
      expect(eventMatchesLeague({ competition: name }, key)).toBe(true)
    }
  })
})

// ---------------------------------------------------------------------------
// Page wiring — source-grep assertions
// ---------------------------------------------------------------------------

describe('match page wiring (source-grep)', () => {
  it('validates the route via definePageMeta and the shared verdict helper', () => {
    expect(PAGE).toMatch(/definePageMeta\(\{[\s\S]*?validate:/)
    expect(PAGE).toMatch(/validateLeagueMatchRoute\(/)
  })

  it("404s anything that is not a renderable league-match URL ('not-found')", () => {
    expect(PAGE).toMatch(/!== 'not-found'/)
  })

  it("redirects the 'afl' verdict home via inline route middleware", () => {
    // Nuxt's validate() can only return boolean|NuxtError — redirects
    // belong in (inline) route middleware.
    expect(PAGE).toMatch(/redirect-home/)
    expect(PAGE).toMatch(/navigateTo\(['"]\/['"]\)/)
    expect(PAGE).toMatch(/middleware:\s*\[/)
  })

  it('fetches the event through getEvent inside a reactive-key useAsyncData', () => {
    expect(PAGE).toMatch(/getEvent\(slug\.value\)/)
    expect(PAGE).toMatch(/league-match-\$\{league\.value\}-\$\{slug\.value\}/)
    expect(PAGE).toMatch(/dedupe:\s*'cancel'/)
  })

  it('404s cross-league slugs by checking the competition name', () => {
    expect(PAGE).toMatch(/eventMatchesLeague\(/)
    expect(PAGE).toMatch(/showError\(/)
    expect(PAGE).toMatch(/statusCode:\s*404/)
  })

  it('mirrors the game-page header: back link, round/season/status, meta grid', () => {
    expect(PAGE).toMatch(/:to="`\/\$\{league\}`"/)
    expect(PAGE).toMatch(/class="round"/)
    expect(PAGE).toMatch(/class="season"/)
    expect(PAGE).toMatch(/class="status"/)
    expect(PAGE).toMatch(/Venue:/)
    expect(PAGE).toMatch(/Date:/)
    expect(PAGE).toMatch(/Time:/)
    expect(PAGE).toMatch(/formatDate\(/)
    expect(PAGE).toMatch(/formatTime\(/)
  })

  it('renders both teams with league badges, display names and scores', () => {
    expect(PAGE).toMatch(/logoFor\(/)
    expect(PAGE).toMatch(/getTeamDisplayName\(/)
    expect(PAGE).toMatch(/homeScore !== null/)
    expect(PAGE).toMatch(/awayScore !== null/)
  })

  it('emphasises the winner like the league home result rows', () => {
    expect(PAGE).toMatch(/\{ winner: homeIsWinner \}/)
    expect(PAGE).toMatch(/\{ winner: awayIsWinner \}/)
    expect(PAGE).toMatch(/\.team-name\.winner/)
    expect(PAGE).toMatch(/font-weight:\s*800/)
  })

  it('has NO tips/model/weather/analysis sections (no league data exists)', () => {
    expect(PAGE).not.toMatch(/TipCard/)
    expect(PAGE).not.toMatch(/tips-section/)
    expect(PAGE).not.toMatch(/models-section/)
    expect(PAGE).not.toMatch(/WeatherCard/)
    expect(PAGE).not.toMatch(/weather-section/)
    expect(PAGE).not.toMatch(/match-analysis/)
    expect(PAGE).not.toMatch(/model_predictions/)
  })

  it('keeps the header league selector in sync with the route (client only)', () => {
    expect(PAGE).toMatch(/setActiveLeague\(/)
    expect(PAGE).toMatch(/import\.meta\.client/)
  })
})

describe('match page accessibility (source-grep)', () => {
  it('announces loading and error states via role="status" + aria-live', () => {
    expect(PAGE.match(/role="status"/g)?.length).toBeGreaterThanOrEqual(2)
    expect(PAGE).toMatch(/aria-live="polite"/)
  })

  it('labels team badges with alt text', () => {
    expect(PAGE.match(/:alt="/g)?.length).toBeGreaterThanOrEqual(2)
  })

  it('keeps interactive targets at 44px and styles focus-visible', () => {
    expect(PAGE).toMatch(/min-height:\s*44px/)
    expect(PAGE).toMatch(/focus-visible/)
  })

  it('stays monochrome: the style block uses CSS vars, no hex colours', () => {
    const style = PAGE.match(/<style[^>]*>([\s\S]*?)<\/style>/)?.[1] ?? ''
    expect(style.length).toBeGreaterThan(0)
    expect(style).not.toMatch(/#[0-9A-Fa-f]{3,8}\b/)
  })
})

describe('match page SEO (FX-05 / FX-20 conventions)', () => {
  it('declares the full useSeoMeta set', () => {
    expect(PAGE).toMatch(/useSeoMeta\s*\(/)
    for (const key of [
      'description',
      'ogTitle',
      'ogDescription',
      'ogUrl',
      'ogType',
      'twitterTitle',
      'twitterDescription',
      'twitterCard',
    ]) {
      expect(
        PAGE,
        `match page should declare ${key} in useSeoMeta`,
      ).toMatch(new RegExp(`\\b${key}\\s*:`))
    }
  })

  it('titles the match as "{Home} vs {Away} — {league displayName}"', () => {
    expect(PAGE).toMatch(/vs \$\{awayName\.value\}/)
    expect(PAGE).toMatch(/— \$\{leagueConfig\.value\.displayName\}/)
  })

  it('declares a canonical URL via useHead, derived from siteUrl', () => {
    expect(PAGE).toMatch(/rel:\s*'canonical'/)
    expect(PAGE).toMatch(/siteUrl/)
    expect(PAGE).toMatch(/\/match\//)
    // No hardcoded domain — the canonical base comes from runtime config.
    expect(PAGE).not.toMatch(/whatismytip\.com/)
  })

  it('resolves the league display name via the shared registry', () => {
    expect(PAGE).toMatch(/getLeagueConfig\(/)
  })

  it('carries the tagged rationale comment', () => {
    expect(PAGE).toMatch(/LEAGUE-ROUTES \(2026-09-30, user request\)/)
  })
})
