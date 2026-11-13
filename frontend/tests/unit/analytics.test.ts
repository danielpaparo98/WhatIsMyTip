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
import { describe, it, expect, vi, afterEach } from 'vitest'
import { ANALYTICS_EVENTS, track } from '../../utils/analytics'
import type { UmamiAnalytics } from '../../utils/analytics'

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
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

  it('no-ops when the Umami global is absent (script blocked, disabled env)', () => {
    vi.stubGlobal('window', {})
    expect(track(ANALYTICS_EVENTS.homeViewed)).toBe(false)
  })

  it('no-ops when window.umami.track is not a function', () => {
    stubUmami({ track: 'not-a-function' })
    expect(track(ANALYTICS_EVENTS.matchViewed, { league: 'afl' })).toBe(false)
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
