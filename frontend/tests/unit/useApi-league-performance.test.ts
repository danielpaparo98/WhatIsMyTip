/**
 * PERF-PER-LEAGUE (2026-10): league-aware backtest API client contract
 * (D4 + D5 of the performance-per-league feature).
 *
 * Exercises the live composable exactly like useApi-teams.test.ts:
 * `useRuntimeConfig` is a Nuxt auto-import (not a module import), so
 * stubbing it as a global is enough to run `useApi()` under plain vitest;
 * fetch is mocked per test.
 *
 * Contract pinned here:
 *  - getLeagueSeasons / getLeagueCurrentSeasonPerformance /
 *    getLeagueComparison hit GET /api/backtest/seasons | /current-season |
 *    /compare through the shared fetchWithTimeout path
 *  - `?league=` is appended ONLY for a non-AFL league — omitted league AND
 *    league='afl' must produce the byte-identical legacy AFL URLs (no
 *    empty league param, never a bare trailing '?')
 *  - season labels are STRINGS for state leagues (D4) — `season` fields in
 *    the current-season / comparison payloads type as number | string
 *  - non-OK INCLUDING 404 (unknown league → backend 404) THROWS like every
 *    other useApi method — the league page catches and degrades to the
 *    approved empty state
 *  - 4xx is a caller error: never retried
 */
import { describe, it, expect, vi, afterEach } from 'vitest'
import {
  useApi,
  type LeagueSeasonsResponse,
  type LeagueCurrentSeasonResponse,
  type LeagueComparisonResponse,
} from '~/composables/useApi'

const SEASONS_PAYLOAD: LeagueSeasonsResponse = {
  // State-league season labels are strings (D4) — '2026', not 2026.
  available_years: ['2024', '2025', '2026'],
  current_year: '2026',
}

const CURRENT_SEASON_PAYLOAD: LeagueCurrentSeasonResponse = {
  season: '2026',
  heuristics: [
    {
      heuristic: 'home_advantage',
      total_profit: 12.3,
      total_accuracy: 0.55,
      rounds_played: 4,
      avg_profit_per_round: 3.075,
      projected_annual_profit: 36.9,
      odds_coverage: 0,
    },
  ],
  rounds_completed: 4,
  total_rounds: 21,
}

const COMPARISON_PAYLOAD: LeagueComparisonResponse = {
  season: '2025',
  comparison: {
    home_advantage: {
      total_rounds: 20,
      total_tips: 110,
      total_correct: 66,
      overall_accuracy: 0.6,
      total_profit: 15.2,
      avg_profit_per_round: 0.76,
      best_round_accuracy: 0.9,
      worst_round_accuracy: 0.3,
      odds_coverage: 0,
    },
  },
  best_overall: {
    heuristic: 'home_advantage',
    accuracy: 0.6,
    profit: 15.2,
  },
}

describe('getLeagueSeasons (league backtest, PERF-PER-LEAGUE)', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  function stubRuntimeConfig() {
    vi.stubGlobal('useRuntimeConfig', () => ({ public: { apiBase: 'http://api.test' } }))
  }

  it('hits GET /api/backtest/seasons?league={league} and resolves string season labels', async () => {
    stubRuntimeConfig()
    const fetchMock = vi.fn(
      async (..._args: Parameters<typeof fetch>): Promise<Response> =>
        new Response(JSON.stringify(SEASONS_PAYLOAD), { status: 200 }),
    )
    vi.stubGlobal('fetch', fetchMock)

    const { getLeagueSeasons } = useApi()
    const payload = await getLeagueSeasons('wafl')

    expect(payload.available_years).toEqual(['2024', '2025', '2026'])
    expect(payload.current_year).toBe('2026')

    expect(fetchMock).toHaveBeenCalledTimes(1)
    expect(fetchMock.mock.calls[0]?.[0]).toBe('http://api.test/api/backtest/seasons?league=wafl')
  })

  it('omits the league param entirely when no league is given (byte-identical legacy URL)', async () => {
    stubRuntimeConfig()
    const fetchMock = vi.fn(
      async (..._args: Parameters<typeof fetch>): Promise<Response> =>
        new Response(JSON.stringify({ available_years: [2024, 2025], current_year: 2026 }), {
          status: 200,
        }),
    )
    vi.stubGlobal('fetch', fetchMock)

    const { getLeagueSeasons } = useApi()
    await getLeagueSeasons()

    expect(fetchMock).toHaveBeenCalledTimes(1)
    // Same bytes as the legacy getAvailableSeasons URL — no bare '?'.
    expect(fetchMock.mock.calls[0]?.[0]).toBe('http://api.test/api/backtest/seasons')
  })

  it("treats league='afl' as the legacy path (byte-identical URL, no league param)", async () => {
    stubRuntimeConfig()
    const fetchMock = vi.fn(
      async (..._args: Parameters<typeof fetch>): Promise<Response> =>
        new Response(JSON.stringify(SEASONS_PAYLOAD), { status: 200 }),
    )
    vi.stubGlobal('fetch', fetchMock)

    const { getLeagueSeasons } = useApi()
    await getLeagueSeasons('afl')

    expect(fetchMock).toHaveBeenCalledTimes(1)
    expect(fetchMock.mock.calls[0]?.[0]).toBe('http://api.test/api/backtest/seasons')
  })

  it('throws on unknown league (HTTP 404) like every other non-OK response', async () => {
    stubRuntimeConfig()
    const fetchMock = vi.fn(
      async (..._args: Parameters<typeof fetch>): Promise<Response> =>
        new Response('{"detail": "Unknown league"}', { status: 404 }),
    )
    vi.stubGlobal('fetch', fetchMock)

    const { getLeagueSeasons } = useApi()
    await expect(getLeagueSeasons('no-such-league')).rejects.toThrow(
      'Failed to fetch available seasons',
    )

    // 404 is a caller error — never retried (4xx path in fetchWithTimeout).
    expect(fetchMock).toHaveBeenCalledTimes(1)
  })
})

describe('getLeagueCurrentSeasonPerformance (league backtest, PERF-PER-LEAGUE)', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  function stubRuntimeConfig() {
    vi.stubGlobal('useRuntimeConfig', () => ({ public: { apiBase: 'http://api.test' } }))
  }

  it('hits GET /api/backtest/current-season?league={league} and resolves the payload', async () => {
    stubRuntimeConfig()
    const fetchMock = vi.fn(
      async (..._args: Parameters<typeof fetch>): Promise<Response> =>
        new Response(JSON.stringify(CURRENT_SEASON_PAYLOAD), { status: 200 }),
    )
    vi.stubGlobal('fetch', fetchMock)

    const { getLeagueCurrentSeasonPerformance } = useApi()
    const payload = await getLeagueCurrentSeasonPerformance('wafl')

    // D4: season label is stringified for state leagues.
    expect(payload.season).toBe('2026')
    expect(payload.heuristics).toHaveLength(1)
    expect(payload.heuristics[0]?.heuristic).toBe('home_advantage')
    expect(payload.rounds_completed).toBe(4)

    expect(fetchMock).toHaveBeenCalledTimes(1)
    expect(fetchMock.mock.calls[0]?.[0]).toBe(
      'http://api.test/api/backtest/current-season?league=wafl',
    )
  })

  it('omits the league param entirely when no league is given (byte-identical legacy URL)', async () => {
    stubRuntimeConfig()
    const fetchMock = vi.fn(
      async (..._args: Parameters<typeof fetch>): Promise<Response> =>
        new Response(JSON.stringify(CURRENT_SEASON_PAYLOAD), { status: 200 }),
    )
    vi.stubGlobal('fetch', fetchMock)

    const { getLeagueCurrentSeasonPerformance } = useApi()
    await getLeagueCurrentSeasonPerformance()

    expect(fetchMock).toHaveBeenCalledTimes(1)
    // Same bytes as the legacy getCurrentSeasonPerformance URL.
    expect(fetchMock.mock.calls[0]?.[0]).toBe('http://api.test/api/backtest/current-season')
  })

  it('throws on 404 (league not synced yet) — the page catches and degrades', async () => {
    stubRuntimeConfig()
    const fetchMock = vi.fn(
      async (..._args: Parameters<typeof fetch>): Promise<Response> =>
        new Response('{"detail": "Unknown league"}', { status: 404 }),
    )
    vi.stubGlobal('fetch', fetchMock)

    const { getLeagueCurrentSeasonPerformance } = useApi()
    await expect(getLeagueCurrentSeasonPerformance('vfl')).rejects.toThrow(
      'Failed to fetch current season performance',
    )
    expect(fetchMock).toHaveBeenCalledTimes(1)
  })
})

describe('getLeagueComparison (league backtest, PERF-PER-LEAGUE)', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  function stubRuntimeConfig() {
    vi.stubGlobal('useRuntimeConfig', () => ({ public: { apiBase: 'http://api.test' } }))
  }

  it('hits GET /api/backtest/compare?season={season}&league={league} and resolves the payload', async () => {
    stubRuntimeConfig()
    const fetchMock = vi.fn(
      async (..._args: Parameters<typeof fetch>): Promise<Response> =>
        new Response(JSON.stringify(COMPARISON_PAYLOAD), { status: 200 }),
    )
    vi.stubGlobal('fetch', fetchMock)

    const { getLeagueComparison } = useApi()
    const payload = await getLeagueComparison('wafl', 2025)

    expect(payload.season).toBe('2025')
    expect(payload.comparison['home_advantage']?.overall_accuracy).toBe(0.6)
    expect(payload.best_overall.heuristic).toBe('home_advantage')

    expect(fetchMock).toHaveBeenCalledTimes(1)
    expect(fetchMock.mock.calls[0]?.[0]).toBe(
      'http://api.test/api/backtest/compare?season=2025&league=wafl',
    )
  })

  it('omits the league param when no league is given (byte-identical to compareHeuristics)', async () => {
    stubRuntimeConfig()
    const fetchMock = vi.fn(
      async (..._args: Parameters<typeof fetch>): Promise<Response> =>
        new Response(JSON.stringify(COMPARISON_PAYLOAD), { status: 200 }),
    )
    vi.stubGlobal('fetch', fetchMock)

    const { getLeagueComparison } = useApi()
    await getLeagueComparison(undefined, 2025)

    expect(fetchMock).toHaveBeenCalledTimes(1)
    // Same bytes as the legacy compareHeuristics(2025) URL.
    expect(fetchMock.mock.calls[0]?.[0]).toBe('http://api.test/api/backtest/compare?season=2025')
  })

  it("treats league='afl' as the legacy path (byte-identical URL, no league param)", async () => {
    stubRuntimeConfig()
    const fetchMock = vi.fn(
      async (..._args: Parameters<typeof fetch>): Promise<Response> =>
        new Response(JSON.stringify(COMPARISON_PAYLOAD), { status: 200 }),
    )
    vi.stubGlobal('fetch', fetchMock)

    const { getLeagueComparison } = useApi()
    await getLeagueComparison('afl', 2025)

    expect(fetchMock).toHaveBeenCalledTimes(1)
    expect(fetchMock.mock.calls[0]?.[0]).toBe('http://api.test/api/backtest/compare?season=2025')
  })

  it('appends ?league= alone when no season is given — never a bare trailing ?', async () => {
    stubRuntimeConfig()
    const fetchMock = vi.fn(
      async (..._args: Parameters<typeof fetch>): Promise<Response> =>
        new Response(JSON.stringify(COMPARISON_PAYLOAD), { status: 200 }),
    )
    vi.stubGlobal('fetch', fetchMock)

    const { getLeagueComparison } = useApi()
    await getLeagueComparison('wafl')

    expect(fetchMock).toHaveBeenCalledTimes(1)
    expect(fetchMock.mock.calls[0]?.[0]).toBe('http://api.test/api/backtest/compare?league=wafl')
  })

  it('throws on 404 (unknown league) and never retries a 4xx', async () => {
    stubRuntimeConfig()
    const fetchMock = vi.fn(
      async (..._args: Parameters<typeof fetch>): Promise<Response> =>
        new Response('{"detail": "Unknown league"}', { status: 404 }),
    )
    vi.stubGlobal('fetch', fetchMock)

    const { getLeagueComparison } = useApi()
    await expect(getLeagueComparison('no-such-league', 2025)).rejects.toThrow(
      'Failed to compare heuristics',
    )
    expect(fetchMock).toHaveBeenCalledTimes(1)
  })

  it('throws on other non-OK statuses (e.g. 500, non-transient)', async () => {
    stubRuntimeConfig()
    const fetchMock = vi.fn(async () => new Response('boom', { status: 500 }))
    vi.stubGlobal('fetch', fetchMock)

    const { getLeagueComparison } = useApi()
    await expect(getLeagueComparison('wafl', 2025)).rejects.toThrow('Failed to compare heuristics')
    expect(fetchMock).toHaveBeenCalledTimes(1)
  })
})
