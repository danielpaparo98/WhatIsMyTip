import { describe, it, expect } from 'vitest'

/**
 * Smoke test — confirms the vitest test runner is wired up correctly.
 * Real component / composable tests are added per fix.
 */
describe('vitest infrastructure', () => {
  it('runs', () => {
    expect(1 + 1).toBe(2)
  })
})
