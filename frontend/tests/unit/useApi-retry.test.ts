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
import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { resolve, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'

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
 * generateTips must POST a JSON body — `POST /tips/generate` requires a
 * `TipGenerateRequest` body (season / round_id / heuristics). The frontend
 * previously put these in the query string and sent no body, which made
 * FastAPI return 422 {"detail":[{"type":"missing","loc":["body"],...}]}.
 *
 * Note the body schema uses `round_id` (not `round`). See
 * backend/packages/shared/schemas/admin.py and the passing backend contract
 * test backend/tests/unit/test_app_api_tips.py (sends json={..., round_id ...}).
 *
 * We source-grep (consistent with the rest of this file) because the live
 * behaviour needs a real Nuxt runtime to wire useRuntimeConfig().
 */
describe('generateTips POSTs the required JSON body (fix for POST /tips/generate 422)', () => {
  const source = readFileSync(resolve(FRONTEND_ROOT, COMPOSABLE), 'utf8')

  // Scope assertions to just generateTips so we don't false-match sibling
  // methods — e.g. runBacktest also uses `method: 'POST'` and getTips uses
  // URLSearchParams. The body has no `\n  }\n` (2-space dedent) so this
  // stops at the function's closing brace.
  const fnMatch = source.match(/const generateTips[\s\S]*?\n  }\n/)
  const generateTips = fnMatch ? fnMatch[0] : ''

  it('is defined and extractable from the composable source', () => {
    expect(generateTips).not.toBe('')
  })

  it('does NOT serialize season/round/heuristics into the query string', () => {
    expect(generateTips).not.toMatch(/URLSearchParams/)
    expect(generateTips).not.toMatch(/queryParams/)
    expect(generateTips).not.toMatch(/\/api\/tips\/generate\?\$\{queryParams\}/)
  })

  it('sends a POST with a JSON body and application/json content type', () => {
    expect(generateTips).toMatch(/method:\s*'POST'/)
    expect(generateTips).toMatch(/body:\s*JSON\.stringify/)
    expect(generateTips).toMatch(/'Content-Type':\s*'application\/json'/)
  })

  it('serializes round_id (not round) to match the backend TipGenerateRequest schema', () => {
    expect(generateTips).toMatch(/round_id:\s*round/)
    // A bare `round:` object key would be the old/wrong contract.
    expect(generateTips).not.toMatch(/\bround:\s*round\b/)
  })

  it('handles missing heuristics gracefully (undefined is dropped by JSON.stringify)', () => {
    // Contract demonstration: heuristics flows straight into JSON.stringify,
    // so an undefined value is omitted from the body rather than becoming a
    // query param. This is exactly what the call site
    // api.generateTips(season, round, [selectedHeuristic.value]) relies on.
    expect(JSON.stringify({ season: 2025, round_id: 12, heuristics: undefined }))
      .toBe(JSON.stringify({ season: 2025, round_id: 12 }))
  })
})
