/**
 * FX-11: useApi retry/backoff for transient failures.
 *
 * Asserts that the fetchWithTimeout helper inside useApi:
 *  - has a retry loop (maxAttempts > 1)
 *  - treats 502/503/504 as transient
 *  - applies backoff with jitter
 *  - does NOT retry 4xx (caller error)
 *
 * We source-grep the composable because the live behaviour requires
 * a real Nuxt runtime to wire useRuntimeConfig() — better to test the
 * source invariants than to mock the world.
 */
import { describe, it, expect, vi, afterEach } from 'vitest'
import { readFileSync } from 'node:fs'
import { resolve, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'
import { useApi } from '~/composables/useApi'

const FRONTEND_ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '../..')
const COMPOSABLE = 'composables/useApi.ts'

describe('FX-11: useApi retry/backoff', () => {
  const source = readFileSync(resolve(FRONTEND_ROOT, COMPOSABLE), 'utf8')

  it('defines a retry attempt counter with a maxAttempts constant', () => {
    expect(source).toMatch(/maxAttempts\s*:\s*\d+/)
    // Loop must iterate at least twice for a retry to be meaningful
    expect(source).toMatch(/for\s*\([^)]*attempt\s*<\s*DEFAULT_RETRY_OPTIONS\.maxAttempts/)
  })

  it('treats 502/503/504 as transient', () => {
    expect(source).toMatch(/TRANSIENT_STATUSES\s*=\s*new Set\(\[502,\s*503,\s*504\]\)/)
  })

  it('exposes an isTransient helper that checks the response status', () => {
    expect(source).toMatch(/isTransient\s*=\s*\(/)
  })

  it('applies backoff with jitter between attempts', () => {
    expect(source).toMatch(/backoffMs/)
    expect(source).toMatch(/Math\.random\(\)/)
  })

  it('does not retry on non-transient status codes (4xx)', () => {
    // The early return: `if (!isTransient(response, null)) return response`
    expect(source).toMatch(/if\s*\(\s*!isTransient\(response,\s*null\)\)\s*return\s+response/)
  })

  it('does not sleep after the final attempt', () => {
    // After the loop, the helper either returns lastResponse or throws
    expect(source).toMatch(/if\s*\(attempt\s*<\s*DEFAULT_RETRY_OPTIONS\.maxAttempts\s*-\s*1\)/)
  })
})

describe('FX-11: useApi default retry policy', () => {
  const source = readFileSync(resolve(FRONTEND_ROOT, COMPOSABLE), 'utf8')

  it('exports a small bounded retry policy (max 3 attempts)', () => {
    // Sanity check — anything beyond 5 would harm UX.
    const m = source.match(/maxAttempts\s*:\s*(\d+)/)
    expect(m).not.toBeNull()
    const n = Number(m![1])
    expect(n).toBeGreaterThanOrEqual(2)
    expect(n).toBeLessThanOrEqual(5)
  })

  it('caps the backoff delay', () => {
    expect(source).toMatch(/maxDelayMs\s*:\s*\d+/)
  })
})

/**
 * TIPS-GEN-H4 (2026-09): `generateTips` has been REMOVED from the
 * composable.  `POST /api/tips/generate` requires the admin X-API-Key —
 * a browser must never hold that key, and the nightly tip-generation
 * cron is the generation path.  This test pins the removal so the
 * endpoint trigger can't quietly reappear in the public UI.
 */
describe('generateTips is removed from the public UI surface (TIPS-GEN-H4)', () => {
  const source = readFileSync(resolve(FRONTEND_ROOT, COMPOSABLE), 'utf8')

  it('no longer defines generateTips', () => {
    expect(source).not.toMatch(/const\s+generateTips/)
  })

  it('never posts to /api/tips/generate', () => {
    expect(source).not.toMatch(/\/api\/tips\/generate/)
  })
})

/**
 * Grand-final uplift (2026-09): `getGameReport` contract.
 *
 * Unlike the source-grep suites above, these exercise the live
 * composable: `useRuntimeConfig` is a Nuxt auto-import (not a module
 * import), so stubbing it as a global is enough to run `useApi()` under
 * plain vitest; fetch is mocked per test.  404 maps to null because a
 * missing report is an expected state (non-GF game, or the pre-match
 * report hasn't been generated yet).
 */
describe('getGameReport (grand-final uplift)', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  function stubRuntimeConfig() {
    vi.stubGlobal('useRuntimeConfig', () => ({ public: { apiBase: 'http://api.test' } }))
  }

  it('maps HTTP 404 to null', async () => {
    stubRuntimeConfig()
    const fetchMock = vi.fn(
      async (..._args: Parameters<typeof fetch>): Promise<Response> =>
        new Response('{"detail": "Match report not yet generated"}', { status: 404 }),
    )
    vi.stubGlobal('fetch', fetchMock)

    const { getGameReport } = useApi()
    await expect(getGameReport('abc12345')).resolves.toBeNull()

    // Hits the report endpoint on the configured API base exactly once
    // (404 is a caller error — never retried).
    expect(fetchMock).toHaveBeenCalledTimes(1)
    expect(fetchMock.mock.calls[0][0]).toBe('http://api.test/api/games/abc12345/report')
  })

  it('parses the MatchReportResponse payload on HTTP 200', async () => {
    stubRuntimeConfig()
    const payload = {
      id: 1,
      game_id: 2,
      report_type: 'grand_final_pre_match',
      report: { headline: 'Deck chairs on the Titanic' },
      created_at: '2026-09-19T00:00:00Z',
    }
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => new Response(JSON.stringify(payload), { status: 200 })),
    )

    const { getGameReport } = useApi()
    const result = await getGameReport('abc12345')
    expect(result).not.toBeNull()
    expect(result?.report_type).toBe('grand_final_pre_match')
    expect(result?.report.headline).toBe('Deck chairs on the Titanic')
  })

  it('throws on other non-OK statuses (non-transient 4xx)', async () => {
    stubRuntimeConfig()
    const fetchMock = vi.fn(async () => new Response('nope', { status: 400 }))
    vi.stubGlobal('fetch', fetchMock)

    const { getGameReport } = useApi()
    await expect(getGameReport('abc12345')).rejects.toThrow('Failed to fetch match report')
    expect(fetchMock).toHaveBeenCalledTimes(1)
  })
})
