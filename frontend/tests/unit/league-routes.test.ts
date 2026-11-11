/**
 * LEAGUE-ROUTES (2026-09-30, user request): pins the build-time league
 * route enumeration shared by the nuxt.config.ts prerender hook and
 * server/routes/sitemap.xml.ts.
 *
 * The contract under test (lib/leagueRoutes.ts, deliberately Nuxt-free
 * so nuxt.config.ts can import it in plain Node):
 *  1. `/{league}` AND `/{league}/performance` are enumerated for EVERY
 *     non-AFL league key — even unsynced ones (the pages degrade to
 *     their "unavailable"/empty state; a stale 404 in the wild is
 *     worse than a graceful page).
 *  2. `/{league}/match/{slug}` is enumerated for each event of the
 *     DERIVED current round (same semantics as deriveCurrentRound:
 *     first round with live events wins, else latest round with
 *     results), for leagues whose season payload was fetched.
 *  3. 'afl' is never enumerated — AFL lives at '/' with the legacy
 *     /game prerender (and, from PERFORMANCE-ROUTES on, /performance).
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
  deriveCurrentRound,
  enumerateLeagueRoutes,
  isPlaceholderEvent,
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
    // NRL-EXPANSION (nrl-expansion-07): the three rugby-league
    // competitions join the ten state leagues.
    expect(LEAGUE_ROUTE_KEYS).toHaveLength(13)
  })

  it('includes the three rugby-league competitions (nrl-expansion-07)', () => {
    for (const key of ['nrl', 'nrlw', 'origin']) {
      expect(LEAGUE_ROUTE_KEYS, `missing rugby-league key: ${key}`).toContain(key)
    }
  })

  it('binds the rugby-league competition names to the backend NATIONAL_LEAGUES registry', () => {
    // The mapping is a contract with packages/shared/ingestion/
    // national_leagues.py — the names must match config.name exactly so
    // resolveCompetition can match the synced /api/sports payload.
    expect(LEAGUE_COMPETITION_NAMES['nrl']).toBe('National Rugby League')
    expect(LEAGUE_COMPETITION_NAMES['nrlw']).toBe("NRL Women's Premiership")
    expect(LEAGUE_COMPETITION_NAMES['origin']).toBe('State of Origin')
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
  it('enumerates /{league} and /{league}/performance for every key even when no events were fetched', () => {
    expect(buildLeagueRoutes(['wafl', 'vfl'], {}, NOW)).toEqual([
      '/wafl',
      '/wafl/performance',
      '/vfl',
      '/vfl/performance',
    ])
  })

  it('enumerates the rugby-league routes (nrl-expansion-07)', () => {
    expect(buildLeagueRoutes(['nrl', 'nrlw', 'origin'], {}, NOW)).toEqual([
      '/nrl',
      '/nrl/performance',
      '/nrlw',
      '/nrlw/performance',
      '/origin',
      '/origin/performance',
    ])
  })

  it('enumerates rugby-league match routes from a derived round like any other league', () => {
    expect(buildLeagueRoutes(['nrl'], { nrl: WAFL_SEASON }, NOW)).toEqual([
      '/nrl',
      '/nrl/performance',
      '/nrl/match/waf-r6-g1',
      '/nrl/match/waf-r6-g2',
    ])
  })

  it('never enumerates afl — not even when explicitly passed with events', () => {
    const routes = buildLeagueRoutes(
      ['afl', 'wafl'],
      { afl: FINISHED_SEASON, wafl: [] },
      NOW,
    )
    expect(routes).toEqual(['/wafl', '/wafl/performance'])
    expect(routes.some((r) => r.startsWith('/afl'))).toBe(false)
  })

  it('emits match routes for the derived current round only (live round wins)', () => {
    const routes = buildLeagueRoutes(['wafl'], { wafl: WAFL_SEASON }, NOW)
    // Round 6 is the first round with live events → its two games only.
    expect(routes).toEqual([
      '/wafl',
      '/wafl/performance',
      '/wafl/match/waf-r6-g1',
      '/wafl/match/waf-r6-g2',
    ])
  })

  it('falls back to the latest completed round once the season is over', () => {
    const routes = buildLeagueRoutes(['wafl'], { wafl: FINISHED_SEASON }, NOW)
    expect(routes).toEqual([
      '/wafl',
      '/wafl/performance',
      '/wafl/match/fin-r23-gf',
    ])
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

  it('gives leagues with an empty event payload their home and performance routes', () => {
    const routes = buildLeagueRoutes(['wafl', 'vfl'], { wafl: [], vfl: WAFL_SEASON }, NOW)
    expect(routes).toEqual([
      '/wafl',
      '/wafl/performance',
      '/vfl',
      '/vfl/performance',
      '/vfl/match/waf-r6-g1',
      '/vfl/match/waf-r6-g2',
    ])
  })

  it('skips events with an empty slug instead of emitting a broken URL', () => {
    const events = [
      makeEvent({ id: 3, slug: '', round_id: 6, starts_at: '2026-06-20T13:10:00' }),
    ]
    expect(buildLeagueRoutes(['wafl'], { wafl: events }, NOW)).toEqual([
      '/wafl',
      '/wafl/performance',
    ])
  })

  it('is deterministic and duplicate-free', () => {
    const first = buildLeagueRoutes(LEAGUE_ROUTE_KEYS, { wafl: WAFL_SEASON }, NOW)
    const second = buildLeagueRoutes(LEAGUE_ROUTE_KEYS, { wafl: WAFL_SEASON }, NOW)
    expect(first).toEqual(second)
    expect(new Set(first).size).toBe(first.length)
  })
})

// ---------------------------------------------------------------------------
// PERFORMANCE-ROUTES (2026-10-07): /{league}/performance for every league
// ---------------------------------------------------------------------------

describe('buildLeagueRoutes performance routes', () => {
  it('emits /{league}/performance for ALL non-AFL keys, ALWAYS — even unsynced', () => {
    // No events fetched at all: the graceful-degradation contract means
    // performance routes still ship (pages degrade to their empty state).
    const routes = buildLeagueRoutes(LEAGUE_ROUTE_KEYS, {}, NOW)
    for (const key of LEAGUE_ROUTE_KEYS) {
      expect(routes, `missing performance route for ${key}`).toContain(`/${key}/performance`)
    }
  })

  it('emits exactly one performance route per league, adjacent to its home route', () => {
    const routes = buildLeagueRoutes(LEAGUE_ROUTE_KEYS, {}, NOW)
    expect(routes.filter((r) => r.endsWith('/performance'))).toHaveLength(
      LEAGUE_ROUTE_KEYS.length,
    )
    for (const key of LEAGUE_ROUTE_KEYS) {
      expect(routes.indexOf(`/${key}/performance`)).toBe(routes.indexOf(`/${key}`) + 1)
    }
  })

  it('never emits an AFL performance route', () => {
    const routes = buildLeagueRoutes(['afl', ...LEAGUE_ROUTE_KEYS], {}, NOW)
    expect(routes).not.toContain('/afl/performance')
    // Exact-AFL routes only — '/aflw' legitimately starts with '/afl'.
    expect(routes.some((r) => r === '/afl' || r.startsWith('/afl/'))).toBe(false)
  })
})

// ---------------------------------------------------------------------------
// AFLW-2026 REGRESSION (2026-11-11, prod): finals bracket placeholders.
//
// The Sportix feed emits finals as PLACEHOLDER events — participants like
// "1st"/"4th"/"Loser of QF1", venue "To Be Confirmed" — and never closes
// them out when the real fixtures land. Before the fix,
// `deriveCurrentRound` ("first round with live events wins") pinned the
// league home to the FIRST finals round forever: /aflw showed R13's
// qualifying finals on 2026-11-11 while the R14 semi-final (with its
// generated tips) was unreachable, and the never-settling placeholders
// also blocked `season_complete` forever.
// ---------------------------------------------------------------------------

/** Replica of the live AFLW 2026 tail-end payload as of 2026-11-11. */
const AFLW_NOW = new Date('2026-11-11T00:00:00Z')

const AFLW_FINALS: SportEvent[] = [
  // Home-and-away done through round 12 (last game Nov 1).
  makeCompleted({ id: 41, slug: 'afla-r12-last', round_id: 12, starts_at: '2026-11-01T17:05:00' }),
  // R13: the stale bracket placeholder — scheduled Nov 2, STILL 'scheduled'.
  makeEvent({
    id: 42,
    slug: 'afla-8fff82c9',
    round_id: 13,
    venue: 'To Be Confirmed',
    starts_at: '2026-11-02T12:00:00',
    participants: [
      { side: 'home', participant_name: '1st', score: null, is_winner: null },
      { side: 'away', participant_name: '4th', score: null, is_winner: null },
    ],
  }),
  // R13: the REAL qualifying finals, played.
  makeCompleted({ id: 43, slug: 'afla-9274546b', round_id: 13, starts_at: '2026-11-07T13:05:00' }),
  makeCompleted({ id: 44, slug: 'afla-507a5784', round_id: 13, starts_at: '2026-11-08T13:05:00' }),
  makeCompleted({ id: 45, slug: 'afla-7c934cca', round_id: 13, starts_at: '2026-11-08T15:05:00' }),
  // R14: another stale placeholder + the REAL upcoming semi-final.
  makeEvent({
    id: 46,
    slug: 'afla-d37ff3e3',
    round_id: 14,
    venue: 'To Be Confirmed',
    starts_at: '2026-11-09T12:00:00',
    participants: [
      { side: 'home', participant_name: 'Loser of QF1', score: null, is_winner: null },
      { side: 'away', participant_name: 'Winner of EF1', score: null, is_winner: null },
    ],
  }),
  makeEvent({ id: 47, slug: 'afla-8bda6c91', round_id: 14, starts_at: '2026-11-14T16:05:00' }),
  // R15/R16: future placeholders (SF + GF).
  makeEvent({
    id: 48,
    slug: 'afla-af12368e',
    round_id: 15,
    venue: 'To Be Confirmed',
    starts_at: '2026-11-16T12:00:00',
    participants: [
      { side: 'home', participant_name: 'Winner of QF1', score: null, is_winner: null },
      { side: 'away', participant_name: 'Winner of SF2', score: null, is_winner: null },
    ],
  }),
  makeEvent({
    id: 49,
    slug: 'afla-04957198',
    round_id: 16,
    venue: 'To Be Confirmed',
    starts_at: '2026-11-23T12:00:00',
    participants: [
      { side: 'home', participant_name: 'Winner of PF1', score: null, is_winner: null },
      { side: 'away', participant_name: 'Winner of PF2', score: null, is_winner: null },
    ],
  }),
]

describe('finals bracket placeholders (AFLW-2026 regression)', () => {
  it('isPlaceholderEvent detects bracket placeholders and keeps real fixtures', () => {
    const placeholder = (home: string, away: string): SportEvent =>
      makeEvent({
        id: 1,
        slug: 'ph',
        participants: [
          { side: 'home', participant_name: home, score: null, is_winner: null },
          { side: 'away', participant_name: away, score: null, is_winner: null },
        ],
      })
    expect(isPlaceholderEvent(placeholder('1st', '4th'))).toBe(true)
    expect(isPlaceholderEvent(placeholder('Loser of QF1', 'Winner of EF1'))).toBe(true)
    expect(isPlaceholderEvent(placeholder('Winner of PF1', 'Winner of PF2'))).toBe(true)
    expect(isPlaceholderEvent(placeholder('TBC', 'TBD'))).toBe(true)
    // Real fixtures (lowercase/casing-insensitive) are never placeholders.
    expect(isPlaceholderEvent(placeholder('Geelong Cats', 'Sydney'))).toBe(false)
    // Mixed placeholder/real participants: not decidable → treat as real.
    expect(isPlaceholderEvent(placeholder('1st', 'Geelong Cats'))).toBe(false)
    // No participants at all: not decidable → treat as real.
    expect(isPlaceholderEvent(makeEvent({ id: 2, slug: 'ph2' }))).toBe(false)
  })

  it('derives the round of the REAL upcoming fixture, not the first stale finals round', () => {
    // Before the fix this returned 13 (the immortal "1st vs 4th"
    // placeholder pinned the derivation) and /aflw never reached the
    // R14 semi-final.
    expect(deriveCurrentRound(AFLW_FINALS, AFLW_NOW)).toBe(14)
  })

  it('excludes placeholder events from the enumerated match routes', () => {
    const routes = buildLeagueRoutes(['aflw'], { aflw: AFLW_FINALS }, AFLW_NOW)
    expect(routes).toContain('/aflw/match/afla-8bda6c91') // the real semi
    expect(routes).not.toContain('/aflw/match/afla-d37ff3e3') // placeholder
    expect(routes).not.toContain('/aflw/match/afla-8fff82c9')
  })

  it('stale-scheduled REAL fixtures also stop pinning the round', () => {
    // A real-named fixture whose start passed >48h ago but was never
    // marked completed is stale feed data, not a live game.
    const events = [
      makeCompleted({ id: 1, slug: 'done-r5', round_id: 5, starts_at: '2026-06-01T13:10:00' }),
      makeEvent({
        id: 2,
        slug: 'stale-r6',
        round_id: 6,
        starts_at: '2026-06-10T13:10:00', // 5 days before NOW
        participants: [
          { side: 'home', participant_name: 'Geelong Cats', score: null, is_winner: null },
          { side: 'away', participant_name: 'Sydney', score: null, is_winner: null },
        ],
      }),
      makeEvent({ id: 3, slug: 'live-r7', round_id: 7, starts_at: '2026-06-20T13:10:00' }),
    ]
    expect(deriveCurrentRound(events, NOW)).toBe(7)
  })

  it('keeps in-progress matches live (staleness threshold is 48h, not "any past date")', () => {
    const events = [
      makeEvent({ id: 1, slug: 'live-now', round_id: 6, starts_at: '2026-06-14T21:00:00' }),
      makeEvent({ id: 2, slug: 'next-week', round_id: 7, starts_at: '2026-06-20T13:10:00' }),
    ]
    // Started 3h before NOW, still not completed → the live round wins.
    expect(deriveCurrentRound(events, NOW)).toBe(6)
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

    // AFLW-2026 REGRESSION: the build clock is injectable — pin it to
    // the fixture season so the June fixtures are live, not stale.
    const routes = await enumerateLeagueRoutes('http://api.test', undefined, NOW)

    expect(routes).toContain('/wafl')
    expect(routes).toContain('/wafl/performance')
    expect(routes).toContain('/wafl/match/waf-r6-g1')
    expect(routes).toContain('/wafl/match/waf-r6-g2')
    // VFL finished its season → its grand-final round is enumerated.
    expect(routes).toContain('/vfl/match/fin-r23-gf')
    // Unsynced leagues still get their home + performance routes.
    expect(routes).toContain('/sanfl')
    expect(routes).toContain('/sanfl/performance')
    expect(routes).toContain('/aflw')
    expect(routes).toContain('/aflw/performance')

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

  it('returns home + performance routes only and never throws when the API is unreachable', async () => {
    const onError = vi.fn()
    stubFetch(() => {
      throw new Error('connection refused')
    })

    const routes = await enumerateLeagueRoutes('http://api.test', onError)

    expect(routes).toEqual(
      LEAGUE_ROUTE_KEYS.flatMap((key) => [`/${key}`, `/${key}/performance`]),
    )
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

    // WAFL degrades to its home + performance routes only…
    expect(routes).toContain('/wafl')
    expect(routes).toContain('/wafl/performance')
    expect(routes.some((r) => r.startsWith('/wafl/match/'))).toBe(false)
    // …while VFL still gets its match routes.
    expect(routes).toContain('/vfl/match/fin-r23-gf')
    // And the failure is attributed to the right league.
    expect(onError).toHaveBeenCalledWith('wafl', expect.any(Error))
    expect(onError).not.toHaveBeenCalledWith('vfl', expect.anything())
  })

  it('treats unsynced leagues as home + performance only without reporting an error', async () => {
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

    // All 10 homes + 10 performance routes present; match routes only
    // for the two synced leagues.
    for (const key of LEAGUE_ROUTE_KEYS) {
      expect(routes).toContain(`/${key}`)
      expect(routes, `missing performance route for ${key}`).toContain(`/${key}/performance`)
    }
    expect(routes.filter((r) => r.includes('/match/')).every((r) =>
      r.startsWith('/wafl/') || r.startsWith('/vfl/'),
    )).toBe(true)
    expect(onError).not.toHaveBeenCalled()
  })
})
