/**
 * TEAM-IDENTITY (2026-09-30, user request): `getTeams(sport?)` contract
 * for the club-crest population (frozen `GET /api/teams` contract —
 * the parallel backend half implements the exact same shape).
 *
 * Exercises the live composable exactly like useApi-event.test.ts:
 * `useRuntimeConfig` is a Nuxt auto-import (not a module import), so
 * stubbing it as a global is enough to run `useApi()` under plain vitest;
 * fetch is mocked per test.
 *
 * Contract pinned here:
 *  - GET {apiBase}/api/teams, with `?sport=` appended ONLY when a filter
 *    is given (sport is OPTIONAL — omit = all sports, per the frozen
 *    contract; never a bare trailing '?')
 *  - resolves the parsed `TeamsIdentityResponse` payload on HTTP 200
 *  - non-OK THROWS like every other useApi method (getSports convention) —
 *    syncTeamIdentity owns the catch-all degradation
 *  - 4xx is a caller error: never retried
 */
import { describe, it, expect, vi, afterEach } from 'vitest'
import { useApi, type TeamsIdentityResponse } from '~/composables/useApi'

const TEAMS_PAYLOAD: TeamsIdentityResponse = {
  teams: [
    {
      name: 'Peel Thunder',
      abbreviation: null,
      logo_url: 'https://cdn.example/peel.png',
      primary_color: '#000066',
      secondary_color: '#FFFFFF',
    },
    {
      // LEFT-JOIN with null identity columns — the team is still listed
      // (the frontend's populate step skips it).
      name: 'Claremont',
      abbreviation: 'CLA',
      logo_url: null,
      primary_color: null,
      secondary_color: null,
    },
  ],
}

describe('getTeams (team identity, TEAM-IDENTITY)', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  function stubRuntimeConfig() {
    vi.stubGlobal('useRuntimeConfig', () => ({ public: { apiBase: 'http://api.test' } }))
  }

  it('hits GET /api/teams with NO query string when no sport filter is given', async () => {
    stubRuntimeConfig()
    const fetchMock = vi.fn(
      async (..._args: Parameters<typeof fetch>): Promise<Response> =>
        new Response(JSON.stringify(TEAMS_PAYLOAD), { status: 200 }),
    )
    vi.stubGlobal('fetch', fetchMock)

    const { getTeams } = useApi()
    const payload = await getTeams()

    expect(payload.teams).toHaveLength(2)
    expect(payload.teams[0]?.name).toBe('Peel Thunder')
    expect(payload.teams[0]?.logo_url).toBe('https://cdn.example/peel.png')
    expect(payload.teams[1]?.primary_color).toBeNull()

    // Exactly one call — success path never retries.
    expect(fetchMock).toHaveBeenCalledTimes(1)
    // Omitting the filter must NOT append a bare '?' (frozen contract:
    // sport is OPTIONAL — omit = all sports).
    expect(fetchMock.mock.calls[0]?.[0]).toBe('http://api.test/api/teams')
  })

  it('appends ?sport= only when a sport filter is given', async () => {
    stubRuntimeConfig()
    const fetchMock = vi.fn(
      async (..._args: Parameters<typeof fetch>): Promise<Response> =>
        new Response(JSON.stringify({ teams: [] }), { status: 200 }),
    )
    vi.stubGlobal('fetch', fetchMock)

    const { getTeams } = useApi()
    const payload = await getTeams('afl')

    expect(payload.teams).toEqual([])
    expect(fetchMock).toHaveBeenCalledTimes(1)
    expect(fetchMock.mock.calls[0]?.[0]).toBe('http://api.test/api/teams?sport=afl')
  })

  it('throws on non-OK responses (getSports convention)', async () => {
    stubRuntimeConfig()
    const fetchMock = vi.fn(async () => new Response('boom', { status: 500 }))
    vi.stubGlobal('fetch', fetchMock)

    const { getTeams } = useApi()
    await expect(getTeams()).rejects.toThrow('Failed to fetch teams')
    expect(fetchMock).toHaveBeenCalledTimes(1)
  })

  it('never retries a 4xx (caller error)', async () => {
    stubRuntimeConfig()
    const fetchMock = vi.fn(async () => new Response('{"detail": "nope"}', { status: 404 }))
    vi.stubGlobal('fetch', fetchMock)

    const { getTeams } = useApi()
    await expect(getTeams('afl')).rejects.toThrow('Failed to fetch teams')
    expect(fetchMock).toHaveBeenCalledTimes(1)
  })
})
