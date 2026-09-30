/**
 * League-redirect decision helper (league-first-class-urls).
 *
 * Covers the pure rule that decides whether a visitor landing on '/'
 * should be bounced to their persisted league: ONLY on the app's first
 * route resolution (initial hydration), ONLY from the root path, and
 * ONLY for a stored non-AFL league key.  Internal navigations to '/'
 * (logo link, league dropdown AFL choice, nav "Tips" link) are
 * intentional and must never redirect — that is the "go back to AFL"
 * escape hatch.
 */
import { readFileSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { describe, expect, it } from 'vitest'
import { resolveLeagueRedirect } from '../../composables/useLeagueRedirect'
import { LEAGUES } from '../../composables/useSportConfig'

const NON_AFL_KEYS = LEAGUES.map((l) => l.key).filter((key) => key !== 'afl')

describe('resolveLeagueRedirect', () => {
  it('redirects to the stored league on the first navigation from root', () => {
    expect(resolveLeagueRedirect('wafl', true, '/')).toBe('/wafl')
  })

  it('never redirects on internal navigations (escape hatch)', () => {
    expect(resolveLeagueRedirect('wafl', false, '/')).toBeNull()
  })

  it('never redirects when nothing is stored', () => {
    expect(resolveLeagueRedirect(null, true, '/')).toBeNull()
    expect(resolveLeagueRedirect('', true, '/')).toBeNull()
  })

  it('never redirects when the stored league is AFL (AFL lives at /)', () => {
    expect(resolveLeagueRedirect('afl', true, '/')).toBeNull()
  })

  it('never redirects for unknown stored keys', () => {
    expect(resolveLeagueRedirect('not-a-league', true, '/')).toBeNull()
  })

  it('never redirects away from a non-root path', () => {
    expect(resolveLeagueRedirect('wafl', true, '/about')).toBeNull()
    expect(resolveLeagueRedirect('wafl', true, '/wafl')).toBeNull()
    expect(resolveLeagueRedirect('wafl', true, '/game/some-slug')).toBeNull()
  })

  it('every non-AFL LEAGUES key resolves to its own /{league} URL', () => {
    expect(NON_AFL_KEYS.length).toBeGreaterThan(0)
    for (const key of NON_AFL_KEYS) {
      expect(resolveLeagueRedirect(key, true, '/')).toBe(`/${key}`)
    }
  })
})

// ---------------------------------------------------------------------------
// Global middleware composition contract (frontend/middleware/league-
// redirect.global.ts).
//
// Mounting a Nuxt *global* route middleware under plain vitest would
// require mocking Nuxt's #app/#imports runtime, so the wiring is pinned
// structurally instead — the same source-pattern convention the other
// page/layout tests in this suite use (grand-final-home, skip-link,
// aria-labels). The DECISION logic itself is fully covered by the pure
// resolveLeagueRedirect tests above; what needs pinning here is that
// the middleware wires it correctly.
// ---------------------------------------------------------------------------
const FRONTEND_ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '../..')
const MIDDLEWARE_PATH = 'middleware/league-redirect.global.ts'
const MIDDLEWARE = readFileSync(resolve(FRONTEND_ROOT, MIDDLEWARE_PATH), 'utf8')

describe('league-redirect.global middleware (composition contract)', () => {
  it('is registered as a GLOBAL middleware (auto-runs on every navigation)', () => {
    expect(MIDDLEWARE_PATH.endsWith('.global.ts')).toBe(true)
  })

  it('is client-only — prerender/SSR never redirect', () => {
    expect(MIDDLEWARE).toContain('import.meta.client')
  })

  it('restricts the redirect to the app\'s first navigation via the useState flag', () => {
    expect(MIDDLEWARE).toContain("useState('wimt-has-navigated'")
  })

  it('delegates the decision to resolveLeagueRedirect', () => {
    expect(MIDDLEWARE).toContain('resolveLeagueRedirect(')
    expect(MIDDLEWARE).toContain('useLeagueRedirect')
  })

  it('reads the persisted league via the shared storage helper', () => {
    expect(MIDDLEWARE).toContain('readStoredLeagueKey(')
  })

  it('replaces the history entry on redirect (back button returns to the entry URL)', () => {
    expect(MIDDLEWARE).toContain('{ replace: true }')
  })
})
