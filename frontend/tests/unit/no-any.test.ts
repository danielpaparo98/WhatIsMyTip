/**
 * FX-12: Type safety — production code must not use the `any` type.
 *
 * The frontend's static-generated pages are easy to type safely because
 * the API is well-defined.  `any` defeats that benefit and bypasses the
 * typechecker, so we assert it never appears in production source.
 *
 * Exceptions:
 *   - This file and other tests under tests/ (where `any` is fine for
 *     mocks and edge cases).
 *   - Catch-all error handler parameters in lifecycle hooks.
 */
import { describe, it, expect } from 'vitest'
import { readFileSync, readdirSync, statSync } from 'node:fs'
import { resolve, join, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'

const FRONTEND_ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '../..')

// Files we DO NOT want to lint for `any` (tests, build artifacts, node_modules).
const SKIP_DIRS = new Set(['node_modules', '.nuxt', '.output', 'dist', 'tests'])

// Patterns we treat as the `any` type.  We use word boundaries so
// identifiers like `anything` or `Many` don't trip us up.
const ANY_PATTERNS: RegExp[] = [
  /(?<![A-Za-z_$0-9])any(?![A-Za-z_$0-9])/g, // bare `any` identifier
  /:\s*any(?:\[\s*\])?(?!\w)/g, // `: any`, `: any[]`, `: any |`
  /\bas\s+any\b/g, // `as any`
  /<any>/g, // `<any>` generic
  /Array<any>/g, // `Array<any>`
  /:\s*any\s*\|/g, // union with any
  /\|\s*any(?!\w)/g, // union trailing any
]

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

describe('FX-12: no `any` types in production source', () => {
  const files = walk(FRONTEND_ROOT)

  for (const file of files) {
    const rel = file.replace(FRONTEND_ROOT + '\\', '').replace(/\\/g, '/')
    it(`${rel} has no \`any\` types`, () => {
      const source = readFileSync(file, 'utf8')
      // Strip comments and strings to reduce false positives.
      const stripped = source
        .replace(/\/\*[\s\S]*?\*\//g, '') // block comments
        .replace(/\/\/.*$/gm, '') // line comments
        .replace(/(['"`])(?:\\.|(?!\1).)*?\1/g, '""') // string literals

      const offenders: string[] = []
      for (const re of ANY_PATTERNS) {
        re.lastIndex = 0
        let m
        while ((m = re.exec(stripped)) !== null) {
          offenders.push(m[0])
        }
      }
      expect(
        offenders,
        `${rel} contains \`any\` type usages: ${offenders.join(', ')}`,
      ).toEqual([])
    })
  }
})
