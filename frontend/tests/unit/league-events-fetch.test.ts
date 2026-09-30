/**
 * LEAGUE-ROUTES (2026-09-30, code review): behavioural pins for the
 * useLeagueEvents fetch pipeline. The source-grep suites can only
 * assert wiring is present — these exercise the live composable and
 * assert what it actually SENDS to the read API and what it derives.
 *
 * FIX 1 (BLOCKER): the season fetch must carry the full-season limit.
 * The old call omitted `limit`, so the backend default (first 100
 * events of the season, starts_at ASC) truncated long seasons —
 * deriveCurrentRound pinned a stale round AND deriveSeasonState's
 * every-event-settled check fired 'season_complete' MID-SEASON →
 * false "Premiers" celebration on every league home.
 *
 * Nuxt auto-imports are stubbed as globals (same pattern as
 * useApi-retry.test.ts's useRuntimeConfig stub): useApi returns mock
 * endpoints, useAsyncData runs its handler immediately, onNuxtReady is
 * a no-op (the composable registers a client-side refresh on it).
 */
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ref } from 'vue'
import { useLeagueEvents } from '../../composables/useLeagueEvents'
import { SEASON_EVENT_LIMIT } from '../../lib/leagueRoutes'
import type { SportEvent } from '../../composables/useApi'

/** Build a fully-typed SportEvent with sensible defaults. */
function makeEvent(overrides: Partial<SportEvent> & { id: number }): SportEvent {
  return {
    slug: `ev${overrides.id}`,
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

/** Round 5 played, round 6 live, round 23 scheduled — current round 6. */
const SEASON: SportEvent[] = [
  makeEvent({ id: 1, round_id: 5, starts_at: '2026-04-04T13:10:00', status: 'completed', completed: true }),
  makeEvent({ id: 3, round_id: 6, starts_at: '2026-06-21T13:10:00' }),
  makeEvent({ id: 4, round_id: 6, starts_at: '2026-06-20T13:10:00' }),
  makeEvent({ id: 5, round_id: 23, starts_at: '2026-08-29T13:10:00' }),
]

/** Minimal `/api/sports` payload: WAFL synced on competition id 7. */
const SPORTS_PAYLOAD = {
  sports: [
    {
      id: 'afs',
      display_name: 'Australian Football',
      competitions: [
        {
          id: 7,
          sport_id: 'afs',
          name: 'West Australian Football League',
          tier: 'state',
          format: 'league',
          timezone: 'Australia/Perth',
          seasons: [
            { id: 71, label: '2026', start_date: null, end_date: null, is_current: true },
          ],
        },
      ],
    },
  ],
}

/**
 * Stub the Nuxt auto-imports the composable consumes and run the
 * awaited useAsyncData handler eagerly, so one `await
 * useLeagueEvents(...)` leaves the state fully resolved.
 */
function stubNuxt() {
  const getEvents = vi.fn(async () => ({ events: SEASON, count: SEASON.length }))
  const getSports = vi.fn(async () => SPORTS_PAYLOAD)
  vi.stubGlobal('useApi', () => ({ getSports, getEvents }))
  vi.stubGlobal(
    'useAsyncData',
    async (_key: unknown, handler: () => Promise<unknown>) => {
      const data = ref<unknown>(null)
      const pending = ref(true)
      const error = ref<unknown>(null)
      try {
        data.value = await handler()
      } catch (err) {
        error.value = err
      } finally {
        pending.value = false
      }
      return { data, pending, error, refresh: async () => {} }
    },
  )
  vi.stubGlobal('onNuxtReady', (_cb: () => void) => {})
  return { getEvents, getSports }
}

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('useLeagueEvents season fetch (FIX 1: full-season limit)', () => {
  it('sends competition + season + the full-season limit to /api/events', async () => {
    const { getEvents } = stubNuxt()

    await useLeagueEvents('wafl')

    // THE fix: without `limit`, the backend default truncates a long
    // season to its first 100 events → false mid-season "Premiers".
    expect(getEvents).toHaveBeenCalledTimes(1)
    expect(getEvents).toHaveBeenCalledWith({
      competition: 7,
      season: '2026',
      limit: SEASON_EVENT_LIMIT,
    })
    // The limit must cover a full state-league season, mirroring the
    // enumeration pin in league-routes.test.ts.
    expect(SEASON_EVENT_LIMIT).toBeGreaterThanOrEqual(300)
  })

  it('derives the current round, label and ordered round events from the payload', async () => {
    stubNuxt()

    const { roundId, seasonLabel, seasonEvents, roundEvents, unavailable, error } =
      await useLeagueEvents('wafl')

    expect(roundId.value).toBe(6) // first round with live events
    expect(seasonLabel.value).toBe('2026')
    expect(seasonEvents.value).toHaveLength(SEASON.length)
    expect(roundEvents.value.map((e) => e.id)).toEqual([4, 3]) // start-time order
    expect(unavailable.value).toBe(false)
    expect(error.value).toBeNull()
  })

  it('degrades to unavailable without calling getEvents when the competition is not synced', async () => {
    const { getEvents, getSports } = stubNuxt()
    getSports.mockResolvedValue({ sports: [] })

    const { unavailable, roundId } = await useLeagueEvents('wafl')

    expect(getEvents).not.toHaveBeenCalled()
    expect(unavailable.value).toBe(true)
    expect(roundId.value).toBeNull()
  })

  it('surfaces a failed events fetch as the error state', async () => {
    const { getEvents } = stubNuxt()
    getEvents.mockRejectedValue(new Error('boom'))

    const { error, unavailable } = await useLeagueEvents('wafl')

    expect(error.value).toBe('Failed to load fixtures')
    expect(unavailable.value).toBe(false)
  })

  it('never fetches for the afl key (AFL lives at /)', async () => {
    const { getEvents } = stubNuxt()

    const { roundId, unavailable, error } = await useLeagueEvents('afl')

    expect(getEvents).not.toHaveBeenCalled()
    expect(roundId.value).toBeNull()
    expect(unavailable.value).toBe(false)
    expect(error.value).toBeNull()
  })
})
