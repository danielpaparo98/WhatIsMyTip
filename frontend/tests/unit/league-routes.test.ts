/**
 * LEAGUE-ROUTES (2026-09-30, user request): pins the build-time league
 * route enumeration shared by the nuxt.config.ts prerender hook and
 * server/routes/sitemap.xml.ts.
 *
 * The contract under test (lib/leagueRoutes.ts, deliberately Nuxt-free
 * so nuxt.config.ts can import it in plain Node):
 *  1. `/{league}` is enumerated for EVERY non-AFL league key — even
 *     unsynced ones (the pages degrade to their "unavailable" state).
 *  2. `/{league}/match/{slug}` is enumerated for each event of the
 *     DERIVED current round (same semantics as deriveCurrentRound:
 *     first round with live events wins, else latest round with
 *     results), for leagues whose season payload was fetched.
 *  3. 'afl' is never enumerated — AFL lives at '/' with the legacy
 *     /game prerender.
 *  4. enumerateLeagueRoutes degrades gracefully: an unreachable API or
 *     a single league's failed events fetch costs match routes only —
 *     it must never throw.
 */
import { afterEach, describe, expect, it, vi } from 'vitest'
import {
  LEAGUE_COMPETITION_NAMES,
  LEAGUE_ROUTE_KEYS,
  SEASON_EVENT_LIMIT,
  buildLeagueRoutes,
  enumerateLeagueRoutes,
} from '~/lib/leagueRoutes'
import { LEAGUES } from '~/composables/useSportConfig'
import type { SportEvent } from '~/composables/useApi'

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

/** A point in the middle of the (fixture) season — rounds 5/6 straddle it. */
const NOW = new Date('2026-06-15T00:00:00Z')

/** Build a fully-typed SportEvent with sensible defaults. */
function makeEvent(overrides: Partial<SportEvent> & { id: number; slug: string }): SportEvent {
  return {
    round_id: 1,
    venue: 'Test Oval',
    starts_at: '2026-04-01T13:10:00',
    status: 'scheduled',
    completed: false,
    competition: 'West Australian Football League',
    season: '2026',
    participants: [],
    ...overrides,
  }
}

/** A finished event: completed with a result. Dates are far enough in
 *  the past/future relative to any realistic build time to stay stable. */
const makeCompleted = (overrides: Partial<SportEvent> & { id: number; slug: string }): SportEvent =>
  makeEvent({ status: 'completed', completed: true, ...overrides })

/** Round 5: played and in the books. */
const ROUND_5 = [
  makeCompleted({ id: 1, slug: 'waf-r5-g1', round_id: 5, starts_at: '2026-04-04T13:10:00' }),
  makeCompleted({ id: 2, slug: 'waf-r5-g2', round_id: 5, starts_at: '2026-04-04T15:10:00' }),
]

/** Round 6: the live round (two dated, unsettled events). */
const ROUND_6 = [
  makeEvent({ id: 3, slug: 'waf-r6-g1', round_id: 6, starts_at: '2026-06-20T13:10:00' }),
  makeEvent({ id: 4, slug: 'waf-r6-g2', round_id: 6, starts_at: '2026-06-21T13:10:00' }),
]

/** Round 23: scheduled fixture far ahead. */
const ROUND_23 = [
  makeEvent({ id: 5, slug: 'waf-r23-g1', round_id: 23, starts_at: '2026-08-29T13:10:00' }),
]

const WAFL_SEASON = [...ROUND_5, ...ROUND_6, ...ROUND_23]

/** A season where everything has been played (grand final included). */
const FINISHED_SEASON = [
  makeCompleted({ id: 11, slug: 'fin-r22', round_id: 22, starts_at: '2026-08-15T13:10:00' }),
  makeCompleted({ id: 12, slug: 'fin-r23-gf', round_id: 23, starts_at: '2026-09-26T14:10:00' }),
]

/** Minimal `/api/sports` payload: WAFL + VFL synced, everything else not. */
const SPORTS_PAYLOAD = {
  sports: [
    {
      id: 'afs',
      display_name: 'Australian Football',
      competitions: [
        {
          id: 7,
          sport_id: 'afs',
          name: LEAGUE_COMPETITION_NAMES['wafl'],
          tier: 'state',
          format: 'league',
          timezone: 'Australia/Perth',
          seasons: [
            { id: 71, label: '2026', start_date: null, end_date: null, is_current: true },
          ],
        },
        {
          id: 9,
          sport_id: 'afs',
          name: LEAGUE_COMPETITION_NAMES['vfl'],
          tier: 'state',
          format: 'league',
          timezone: 'Australia/Melbourne',
          seasons: [
            { id: 91, label: '2026', start_date: null, end_date: null, is_current: false },
          ],
        },
      ],
    },
  ],
}

/** Stub fetch Response carrying a JSON body (tests may use loose casts). */
function jsonResponse(body: unknown, ok = true, status = 200): Response {
  return { ok, status, json: async () => body } as unknown as Response
}

/** Install a global fetch mock routed by URL substring. */
function stubFetch(route: (url: string) => Response) {
  const fetchMock = vi.fn(async (input: string | URL | RequestInfo) =>
    route(String(input)),
  )
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

afterEach(() => {
  vi.unstubAllGlobals()
})

// ---------------------------------------------------------------------------
// LEAGUE_ROUTE_KEYS — the enumerated registry
// ---------------------------------------------------------------------------

describe('LEAGUE_ROUTE_KEYS', () => {
  it('is exactly the LEAGUES registry minus afl, in registry order', () => {
    expect(LEAGUE_ROUTE_KEYS).toEqual(
      LEAGUES.map((l) => l.key).filter((key) => key !== 'afl'),
    )
  })

  it('covers every non-AFL league with a competition-name contract entry', () => {
    for (const key of LEAGUE_ROUTE_KEYS) {
      expect(LEAGUE_COMPETITION_NAMES[key], `missing competition name for ${key}`).toBeTruthy()
    }
    expect(LEAGUE_ROUTE_KEYS).not.toContain('afl')
    expect(LEAGUE_ROUTE_KEYS).toHaveLength(10)
  })
})

describe('SEASON_EVENT_LIMIT', () => {
  it('is high enough to hold a full state-league season in one fetch', () => {
    // A state-league season is ~20 rounds x ~9 games plus finals; the
    // limit must comfortably exceed that so round derivation sees the
    // whole season (deriveCurrentRound needs future rounds to exist).
    // LEAGUE-ROUTES (2026-09-30, code review): renamed from
    // PRERENDER_EVENT_LIMIT — the composable's runtime season fetch now
    // shares this limit (a truncated season falsely celebrated
    // "Premiers" mid-season), so it is no longer prerender-only.
    expect(SEASON_EVENT_LIMIT).toBeGreaterThanOrEqual(300)
  })
})

// ---------------------------------------------------------------------------
// buildLeagueRoutes — the pure helper
// ---------------------------------------------------------------------------

describe('buildLeagueRoutes', () => {
  it('enumerates /{league} for every key even when no events were fetched', () => {
    expect(buildLeagueRoutes(['wafl', 'vfl'], {}, NOW)).toEqual(['/wafl', '/vfl'])
  })

  it('never enumerates afl — not even when explicitly passed with events', () => {
    const routes = buildLeagueRoutes(
      ['afl', 'wafl'],
      { afl: FINISHED_SEASON, wafl: [] },
      NOW,
    )
    expect(routes).toEqual(['/wafl'])
    expect(routes.some((r) => r.startsWith('/afl'))).toBe(false)
  })

  it('emits match routes for the derived current round only (live round wins)', () => {
    const routes = buildLeagueRoutes(['wafl'], { wafl: WAFL_SEASON }, NOW)
    // Round 6 is the first round with live events → its two games only.
    expect(routes).toEqual([
      '/wafl',
      '/wafl/match/waf-r6-g1',
      '/wafl/match/waf-r6-g2',
    ])
  })

  it('falls back to the latest completed round once the season is over', () => {
    const routes = buildLeagueRoutes(['wafl'], { wafl: FINISHED_SEASON }, NOW)
    expect(routes).toEqual(['/wafl', '/wafl/match/fin-r23-gf'])
  })

  it('ignores null-round (tournament-style) events when deriving the round', () => {
    const events = [
      ...WAFL_SEASON,
      makeEvent({ id: 99, slug: 'expo-match', round_id: null, starts_at: '2026-06-25T13:00:00' }),
    ]
    const routes = buildLeagueRoutes(['wafl'], { wafl: events }, NOW)
    expect(routes).toContain('/wafl/match/waf-r6-g1')
    expect(routes).not.toContain('/wafl/match/expo-match')
  })

  it('gives leagues with an empty event payload their home route only', () => {
    const routes = buildLeagueRoutes(['wafl', 'vfl'], { wafl: [], vfl: WAFL_SEASON }, NOW)
    expect(routes).toEqual([
      '/wafl',
      '/vfl',
      '/vfl/match/waf-r6-g1',
      '/vfl/match/waf-r6-g2',
    ])
  })

  it('skips events with an empty slug instead of emitting a broken URL', () => {
    const events = [
      makeEvent({ id: 3, slug: '', round_id: 6, starts_at: '2026-06-20T13:10:00' }),
    ]
    expect(buildLeagueRoutes(['wafl'], { wafl: events }, NOW)).toEqual(['/wafl'])
  })

  it('is deterministic and duplicate-free', () => {
    const first = buildLeagueRoutes(LEAGUE_ROUTE_KEYS, { wafl: WAFL_SEASON }, NOW)
    const second = buildLeagueRoutes(LEAGUE_ROUTE_KEYS, { wafl: WAFL_SEASON }, NOW)
    expect(first).toEqual(second)
    expect(new Set(first).size).toBe(first.length)
  })
})

// ---------------------------------------------------------------------------
// enumerateLeagueRoutes — the fetch pipeline (graceful degradation)
// ---------------------------------------------------------------------------

describe('enumerateLeagueRoutes', () => {
  it('fetches each synced competition with competition/season/limit params and enumerates its routes', async () => {
    const fetchMock = stubFetch((url) => {
      if (url.includes('/api/sports')) return jsonResponse(SPORTS_PAYLOAD)
      if (url.includes('competition=7')) {
        return jsonResponse({ events: WAFL_SEASON, count: WAFL_SEASON.length })
      }
      if (url.includes('competition=9')) {
        return jsonResponse({ events: FINISHED_SEASON, count: FINISHED_SEASON.length })
      }
      throw new Error(`unexpected fetch: ${url}`)
    })

    const routes = await enumerateLeagueRoutes('http://api.test')

    expect(routes).toContain('/wafl')
    expect(routes).toContain('/wafl/match/waf-r6-g1')
    expect(routes).toContain('/wafl/match/waf-r6-g2')
    // VFL finished its season → its grand-final round is enumerated.
    expect(routes).toContain('/vfl/match/fin-r23-gf')
    // Unsynced leagues still get their home routes.
    expect(routes).toContain('/sanfl')
    expect(routes).toContain('/aflw')

    // Every competition fetch asks for the derived season with the
    // full-season limit. LEAGUE-ROUTES (2026-09-30, code review): each
    // fetch is also bounded by an abort signal, so a hung backend can
    // never stall `nuxt generate` beyond the 15s timeout.
    expect(fetchMock).toHaveBeenCalledWith(
      expect.stringContaining('season=2026'),
      expect.objectContaining({ signal: expect.any(AbortSignal) }),
    )
    expect(fetchMock).toHaveBeenCalledWith(
      expect.stringContaining(`limit=${SEASON_EVENT_LIMIT}`),
      expect.objectContaining({ signal: expect.any(AbortSignal) }),
    )
  })

  it('returns home routes only and never throws when the API is unreachable', async () => {
    const onError = vi.fn()
    stubFetch(() => {
      throw new Error('connection refused')
    })

    const routes = await enumerateLeagueRoutes('http://api.test', onError)

    expect(routes).toEqual(LEAGUE_ROUTE_KEYS.map((key) => `/${key}`))
    // The discovery failure is surfaced so the caller can logger.warn it.
    expect(onError).toHaveBeenCalledWith('sports', expect.any(Error))
  })

  it('keeps the failed league\u2019s home route and the other leagues\u2019 match routes when one events fetch fails', async () => {
    const onError = vi.fn()
    stubFetch((url) => {
      if (url.includes('/api/sports')) return jsonResponse(SPORTS_PAYLOAD)
      if (url.includes('competition=7')) return jsonResponse({}, false, 500)
      if (url.includes('competition=9')) {
        return jsonResponse({ events: FINISHED_SEASON, count: FINISHED_SEASON.length })
      }
      throw new Error(`unexpected fetch: ${url}`)
    })

    const routes = await enumerateLeagueRoutes('http://api.test', onError)

    // WAFL degrades to its home route only…
    expect(routes).toContain('/wafl')
    expect(routes.some((r) => r.startsWith('/wafl/match/'))).toBe(false)
    // …while VFL still gets its match routes.
    expect(routes).toContain('/vfl/match/fin-r23-gf')
    // And the failure is attributed to the right league.
    expect(onError).toHaveBeenCalledWith('wafl', expect.any(Error))
    expect(onError).not.toHaveBeenCalledWith('vfl', expect.anything())
  })

  it('treats unsynced leagues as home-only without reporting an error', async () => {
    const onError = vi.fn()
    stubFetch((url) => {
      if (url.includes('/api/sports')) return jsonResponse(SPORTS_PAYLOAD)
      if (url.includes('competition=7')) {
        return jsonResponse({ events: WAFL_SEASON, count: WAFL_SEASON.length })
      }
      if (url.includes('competition=9')) {
        return jsonResponse({ events: FINISHED_SEASON, count: FINISHED_SEASON.length })
      }
      throw new Error(`unexpected fetch: ${url}`)
    })

    const routes = await enumerateLeagueRoutes('http://api.test', onError)

    // All 10 homes present; match routes only for the two synced leagues.
    for (const key of LEAGUE_ROUTE_KEYS) {
      expect(routes).toContain(`/${key}`)
    }
    expect(routes.filter((r) => r.includes('/match/')).every((r) =>
      r.startsWith('/wafl/') || r.startsWith('/vfl/'),
    )).toBe(true)
    expect(onError).not.toHaveBeenCalled()
  })
})
