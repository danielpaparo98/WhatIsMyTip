/**
 * LEAGUE-ROUTES (2026-09-30, user request): `getEvent(slug)` contract for
 * the new `/{league}/match/{slug}` pages (ADR 0001 read APIs).
 *
 * Exercises the live composable exactly like useApi-retry.test.ts does:
 * `useRuntimeConfig` is a Nuxt auto-import (not a module import), so
 * stubbing it as a global is enough to run `useApi()` under plain vitest;
 * fetch is mocked per test.
 *
 * Contract pinned here:
 *  - GET {apiBase}/api/events/{slug} through the shared fetchWithTimeout path
 *  - resolves the parsed `EventDetailResponse` (= `SportEvent`) payload on
 *    HTTP 200
 *  - 404 (unknown slug) THROWS like every other non-OK response — the
 *    getSports/getEvents convention in useApi.ts; the match page owns the
 *    not-found presentation
 *  - 4xx is a caller error: never retried
 */
import { describe, it, expect, vi, afterEach } from 'vitest'
import { useApi, type EventDetailResponse } from '~/composables/useApi'

const EVENT_PAYLOAD: EventDetailResponse = {
  id: 42,
  slug: 'r1-claremont-perth',
  round_id: 1,
  venue: 'Revo Fitness Stadium',
  starts_at: '2026-04-04T08:10:00Z',
  status: 'completed',
  completed: true,
  competition: 'West Australian Football League',
  season: '2026',
  participants: [
    { side: 'home', participant_name: 'Claremont', score: 87, is_winner: true },
    { side: 'away', participant_name: 'Perth', score: 64, is_winner: false },
  ],
}

describe('getEvent (league match pages, LEAGUE-ROUTES)', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  function stubRuntimeConfig() {
    vi.stubGlobal('useRuntimeConfig', () => ({ public: { apiBase: 'http://api.test' } }))
  }

  it('hits GET /api/events/{slug} on the configured API base and resolves the SportEvent payload', async () => {
    stubRuntimeConfig()
    const fetchMock = vi.fn(
      async (..._args: Parameters<typeof fetch>): Promise<Response> =>
        new Response(JSON.stringify(EVENT_PAYLOAD), { status: 200 }),
    )
    vi.stubGlobal('fetch', fetchMock)

    const { getEvent } = useApi()
    const event = await getEvent('r1-claremont-perth')

    expect(event.slug).toBe('r1-claremont-perth')
    expect(event.competition).toBe('West Australian Football League')
    expect(event.completed).toBe(true)
    expect(event.participants).toHaveLength(2)
    expect(event.participants[0]?.participant_name).toBe('Claremont')

    // Exactly one call — success path never retries.
    expect(fetchMock).toHaveBeenCalledTimes(1)
    expect(fetchMock.mock.calls[0][0]).toBe('http://api.test/api/events/r1-claremont-perth')
  })

  it('throws on unknown slug (HTTP 404) like every other non-OK response', async () => {
    stubRuntimeConfig()
    const fetchMock = vi.fn(
      async (..._args: Parameters<typeof fetch>): Promise<Response> =>
        new Response('{"detail": "Event not found"}', { status: 404 }),
    )
    vi.stubGlobal('fetch', fetchMock)

    const { getEvent } = useApi()
    await expect(getEvent('no-such-slug')).rejects.toThrow('Failed to fetch event')

    // 404 is a caller error — never retried (4xx path in fetchWithTimeout).
    expect(fetchMock).toHaveBeenCalledTimes(1)
  })

  it('does NOT map 404 to null (unlike getGameReport — the match page owns 404)', async () => {
    stubRuntimeConfig()
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => new Response('{"detail": "Event not found"}', { status: 404 })),
    )

    const { getEvent } = useApi()
    const outcome = await getEvent('no-such-slug').then(
      () => 'resolved',
      () => 'threw',
    )
    expect(outcome).toBe('threw')
  })

  it('throws on other non-OK statuses (e.g. 500, non-transient)', async () => {
    stubRuntimeConfig()
    const fetchMock = vi.fn(async () => new Response('boom', { status: 500 }))
    vi.stubGlobal('fetch', fetchMock)

    const { getEvent } = useApi()
    await expect(getEvent('r1-claremont-perth')).rejects.toThrow('Failed to fetch event')
    expect(fetchMock).toHaveBeenCalledTimes(1)
  })
})
