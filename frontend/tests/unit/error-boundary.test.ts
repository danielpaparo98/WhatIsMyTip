/**
 * FX-10: Global error boundary.
 *
 * app.vue must declare an `onErrorCaptured` lifecycle hook so that
 * unhandled errors from descendant components are routed to Nuxt's
 * error page via `showError`.
 */
import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { resolve, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'

const FRONTEND_ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '../..')
const APP = 'app.vue'
const ERROR_PAGE = 'error.vue'

describe('FX-10: global error boundary in app.vue', () => {
  const source = readFileSync(resolve(FRONTEND_ROOT, APP), 'utf8')

  it('declares an onErrorCaptured hook', () => {
    expect(source).toMatch(/onErrorCaptured\s*\(/)
  })

  it('routes caught errors through Nuxt showError', () => {
    expect(source).toMatch(/showError\s*\(/)
  })

  it('gates console logging to dev mode (no prod console errors)', () => {
    // The console.error call should be inside an import.meta.dev check
    const block = source.match(/if\s*\(\s*import\.meta\.dev\s*\)\s*\{[\s\S]*?console\.[a-z]+\(/)
    expect(block, 'console.* should be wrapped in import.meta.dev').not.toBeNull()
  })
})

describe('FX-10: error page exists', () => {
  const source = readFileSync(resolve(FRONTEND_ROOT, ERROR_PAGE), 'utf8')

  it('defines an error page that displays a status code', () => {
    expect(source).toMatch(/statusCode/)
  })

  it('provides a link back to the home page', () => {
    expect(source).toMatch(/to="\/"/)
  })
})
