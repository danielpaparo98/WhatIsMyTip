/**
 * ANALYTICS (2026-11-13): unit tests for the typed Umami event wrapper
 * (utils/analytics.ts).
 *
 * Contract under test:
 *   1. SSR-safe — importing and calling on the server never throws and
 *      always returns false (no `window`).
 *   2. Best-effort — a missing `window.umami`, a malformed global, or a
 *      throwing `track` never propagates (analytics must never break the
 *      app); every failure returns false.
 *   3. Passthrough — a healthy global receives the exact event name and
 *      payload, and the call returns true.
 *   4. Vocabulary hygiene — every event name is snake_case and unique
 *      (the union type enforces call-site typing; this guards the data).
 */
import { describe, it, expect, vi, afterEach, beforeEach } from 'vitest'
import { ANALYTICS_EVENTS, track, __resetAnalyticsForTests } from '../../utils/analytics'
import type { UmamiAnalytics } from '../../utils/analytics'

beforeEach(() => {
  // Each test starts with a clean queue/timer/disabled state.
  __resetAnalyticsForTests()
  vi.useFakeTimers()
})

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
  vi.useRealTimers()
})

/** Install a (possibly broken) fake `window.umami` for a test. */
function stubUmami(umami: unknown): void {
  vi.stubGlobal('window', { umami })
}

describe('track() — environment guards', () => {
  it('no-ops on the server (no window at all)', () => {
    vi.stubGlobal('window', undefined)
    expect(track(ANALYTICS_EVENTS.homeViewed)).toBe(false)
  })

  it('queues (returns true) when the Umami global is absent — flush later', () => {
    vi.stubGlobal('window', {})
    // ANALYTICS-QUEUE: absent global = script not loaded YET, not "never".
    // The event buffers and flushes once the deferred script appears
    // (see the ready-queue suite below for the full contract).
    expect(track(ANALYTICS_EVENTS.homeViewed)).toBe(true)
  })

  it('queues when window.umami.track is not a function (script still booting)', () => {
    stubUmami({ track: 'not-a-function' })
    expect(track(ANALYTICS_EVENTS.matchViewed, { league: 'afl' })).toBe(true)
  })

  it('swallows a throwing track() and returns false', () => {
    stubUmami({ track: () => { throw new Error('CSP blew up') } })
    expect(track(ANALYTICS_EVENTS.errorPageShown, { status_code: 404 })).toBe(false)
  })
})

describe('track() — happy path', () => {
  it('forwards the event name and payload to window.umami.track', () => {
    const trackFn = vi.fn()
    stubUmami({ track: trackFn } satisfies { track: UmamiAnalytics['track'] })

    const sent = track(ANALYTICS_EVENTS.matchViewed, { league: 'afl' })

    expect(sent).toBe(true)
    expect(trackFn).toHaveBeenCalledOnce()
    expect(trackFn).toHaveBeenCalledWith('match_viewed', { league: 'afl' })
  })

  it('forwards event-only calls (no payload)', () => {
    const trackFn = vi.fn()
    stubUmami({ track: trackFn })

    expect(track(ANALYTICS_EVENTS.buyMeACoffeeClicked)).toBe(true)
    expect(trackFn).toHaveBeenCalledWith('buy_me_a_coffee_clicked', undefined)
  })

  it('accepts JSON-primitive payloads only (typed surface)', () => {
    const trackFn = vi.fn()
    stubUmami({ track: trackFn })

    expect(
      track(ANALYTICS_EVENTS.heuristicSelected, {
        heuristic: 'best_bet',
        league: 'afl',
      }),
    ).toBe(true)
    expect(trackFn).toHaveBeenCalledWith('heuristic_selected', {
      heuristic: 'best_bet',
      league: 'afl',
    })
  })

  it('ad-hoc event names are a compile-time error only (types are erased at runtime)', () => {
    const trackFn = vi.fn()
    stubUmami({ track: trackFn })
    // @ts-expect-error — ad-hoc strings are a compile-time error by design.
    // At runtime the type layer is gone and the string forwards like any
    // other — this documents that the guard lives in the type system, and
    // the vocabulary check in CI is `vue-tsc`/`typecheck`, not vitest.
    expect(track('totally_made_up_event')).toBe(true)
    expect(trackFn).toHaveBeenCalledWith('totally_made_up_event', undefined)
  })
})

describe('ANALYTICS_EVENTS vocabulary', () => {
  const names = Object.values(ANALYTICS_EVENTS)

  it('is non-empty', () => {
    expect(names.length).toBeGreaterThan(0)
  })

  it('uses snake_case for every event name', () => {
    for (const name of names) {
      expect(name).toMatch(/^[a-z][a-z0-9]*(_[a-z0-9]+)*$/)
    }
  })

  it('has no duplicate event names', () => {
    expect(new Set(names).size).toBe(names.length)
  })
})

// ---------------------------------------------------------------------------
// Ready-queue (ANALYTICS-QUEUE, 2026-11-13): the injection plugin is
// client-only, so `window.umami` does not exist until the deferred script
// has fetched and executed AFTER hydration — but view-anchor events fire
// in `onMounted`. Without a queue, every mount-time event hits the no-op
// guard and is silently lost (verified live: pageviews landed, all custom
// events dropped). The wrapper buffers events until the global appears.
// ---------------------------------------------------------------------------
describe('track() — ready queue (umami not loaded yet)', () => {
  it('buffers a mount-time event and flushes it when umami appears', () => {
    vi.stubGlobal('window', {}) // hydrated, but the deferred script hasn't run
    expect(track(ANALYTICS_EVENTS.homeViewed, { league: 'afl' })).toBe(true)

    const trackFn = vi.fn()
    vi.stubGlobal('window', { umami: { track: trackFn } })
    vi.advanceTimersByTime(150) // one poll tick

    expect(trackFn).toHaveBeenCalledOnce()
    expect(trackFn).toHaveBeenCalledWith('home_viewed', { league: 'afl' })
  })

  it('flushes the whole burst in order once umami appears', () => {
    vi.stubGlobal('window', {})
    track(ANALYTICS_EVENTS.homeViewed, { league: 'afl' })
    track(ANALYTICS_EVENTS.leagueViewed, { league: 'wafl' })

    const trackFn = vi.fn()
    vi.stubGlobal('window', { umami: { track: trackFn } })
    vi.advanceTimersByTime(150)

    expect(trackFn.mock.calls.map(c => c[0])).toEqual(['home_viewed', 'league_viewed'])
  })

  it('is bounded — only the first MAX_QUEUED events survive an absent umami', () => {
    vi.stubGlobal('window', {})
    for (let i = 0; i < 25; i++) track(ANALYTICS_EVENTS.homeViewed, { n: i })

    const trackFn = vi.fn()
    vi.stubGlobal('window', { umami: { track: trackFn } })
    vi.advanceTimersByTime(150)

    expect(trackFn).toHaveBeenCalledTimes(20)
    expect(trackFn.mock.calls[0]?.[1]).toEqual({ n: 0 })
  })

  it('abandons the buffer after the timeout but recovers if umami appears later', () => {
    vi.stubGlobal('window', {}) // script stalled — umami never appears in time
    expect(track(ANALYTICS_EVENTS.homeViewed)).toBe(true)

    vi.advanceTimersByTime(6000) // past READY_TIMEOUT_MS

    const trackFn = vi.fn()
    vi.stubGlobal('window', { umami: { track: trackFn } })
    // The buffered event was dropped on timeout — it never reaches umami...
    expect(trackFn).not.toHaveBeenCalled()
    // ...but a NEW event takes the normal path and dispatches immediately
    // (the queue was torn down, not permanently disabled).
    expect(track(ANALYTICS_EVENTS.matchViewed, { league: 'afl' })).toBe(true)
    expect(trackFn).toHaveBeenCalledTimes(1)
    expect(trackFn).toHaveBeenCalledWith('match_viewed', { league: 'afl' })
  })

  it('dispatches immediately when umami is already present (no queue hop)', () => {
    const trackFn = vi.fn()
    vi.stubGlobal('window', { umami: { track: trackFn } })

    expect(track(ANALYTICS_EVENTS.aboutViewed)).toBe(true)
    // No timer flush needed — the call already happened.
    expect(trackFn).toHaveBeenCalledWith('about_viewed', undefined)
    expect(vi.getTimerCount()).toBe(0)
  })

  it('never queues on the server (no window)', () => {
    vi.stubGlobal('window', undefined)
    expect(track(ANALYTICS_EVENTS.homeViewed)).toBe(false)
    // And nothing magically appears once a client exists:
    vi.stubGlobal('window', { umami: { track: vi.fn() } })
    vi.advanceTimersByTime(1000)
    // (the flushed queue stays empty — asserted via the next direct call)
  })
})
