/**
 * FX-09: Skip-link must be present in the default layout, target the main
 * content element, and be visually hidden until focused.
 */
import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { resolve, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'

const FRONTEND_ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '../..')

const LAYOUT = 'layouts/default.vue'

describe('FX-09: skip-link in default layout', () => {
  const source = readFileSync(resolve(FRONTEND_ROOT, LAYOUT), 'utf8')

  it('contains a skip-link <a> with class "skip-link"', () => {
    expect(source).toMatch(/<a[^>]+class="skip-link"/)
  })

  it('skip-link href targets #main-content', () => {
    const m = source.match(/<a\s+[^>]*class="skip-link"[^>]*>/)
    expect(m, 'skip-link anchor not found').not.toBeNull()
    const hrefMatch = m![0].match(/href="([^"]+)"/)
    expect(hrefMatch, 'skip-link must have an href').not.toBeNull()
    expect(hrefMatch![1]).toBe('#main-content')
  })

  it('main element has matching id="main-content"', () => {
    expect(source).toMatch(/<main[^>]+id="main-content"/)
  })
})

describe('FX-09: skip-link CSS in default layout', () => {
  // The skip-link is scoped to the default layout, not the global stylesheet.
  const source = readFileSync(resolve(FRONTEND_ROOT, LAYOUT), 'utf8')

  it('hides the skip-link off-screen by default', () => {
    expect(source).toMatch(/\.skip-link\s*\{[^}]*position\s*:\s*absolute/)
  })

  it('shows the skip-link on :focus', () => {
    expect(source).toMatch(/\.skip-link:focus\s*\{/)
  })
})
