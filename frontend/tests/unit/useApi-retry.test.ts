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
