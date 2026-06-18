/**
 * FX-13: console.log cleanup.
 *
 * All `console.*` calls in production source must be wrapped in an
 * `import.meta.dev` check so they never reach users' browser consoles
 * in production.  This is a "custom lint rule" enforced at test time.
 */
import { describe, it, expect } from 'vitest'
import { readFileSync, readdirSync, statSync } from 'node:fs'
import { join, dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const FRONTEND_ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '../..')

const SKIP_DIRS = new Set(['node_modules', '.nuxt', '.output', 'dist', 'tests'])

const CONSOLE_RE = /\bconsole\.(log|debug|info|warn|error|trace)\s*\(/
const DEV_GATE_RE = /import\.meta\.dev/

function walk(dir: string, out: string[] = []): string[] {
  for (const entry of readdirSync(dir)) {
    if (SKIP_DIRS.has(entry)) continue
    const full = join(dir, entry)
    const st = statSync(full)
    if (st.isDirectory()) {
      walk(full, out)
    } else if (/\.(vue|ts)$/.test(entry) && !entry.endsWith('.d.ts')) {
      out.push(full)
    }
  }
  return out
}

describe('FX-13: console.* gated by import.meta.dev', () => {
  const files = walk(FRONTEND_ROOT)

  for (const file of files) {
    const rel = file.replace(FRONTEND_ROOT + '\\', '').replace(/\\/g, '/')
    const source = readFileSync(file, 'utf8')

    it(`${rel}: every console.* call is gated by import.meta.dev`, () => {
      // Find all `console.*(` positions and check the enclosing
      // statement contains `import.meta.dev` somewhere on a
      // preceding or the same line.  We do a simple textual window
      // check: look at the 200 chars before each console call.
      const matches: number[] = []
      const re = new RegExp(CONSOLE_RE.source, 'g')
      let m
      while ((m = re.exec(source)) !== null) {
        matches.push(m.index)
      }
      if (matches.length === 0) return

      const violations: string[] = []
      for (const idx of matches) {
        const window = source.slice(Math.max(0, idx - 200), idx)
        if (!DEV_GATE_RE.test(window)) {
          violations.push(`offset ${idx}: ${source.slice(idx, idx + 80).replace(/\s+/g, ' ')}`)
        }
      }
      expect(
        violations,
        `${rel} has unguarded console.* calls: ${violations.join('; ')}`,
      ).toEqual([])
    })
  }
})
