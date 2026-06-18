/**
 * FX-01: Image optimization — all <img> tags that render team logos
 * must have `loading="lazy"`, `decoding="async"`, and explicit `width`/`height`.
 *
 * This test walks the .vue source files and asserts the three attributes
 * are present on every <img ...> tag.  Using a regex source-grep is fine
 * here because the entire site is a small static project.
 */
import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { resolve, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'

// Resolve to the frontend/ directory from this test file
// (frontend/tests/unit/lazy-images.test.ts -> frontend/)
const FRONTEND_ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '../..')

// Files that render <img> elements for team logos
const IMG_SOURCE_FILES = [
  'components/GameCard.vue',
  'pages/index.vue',
  'pages/game/[slug].vue',
]

const IMG_TAG_RE = /<img\b[^>]*\/?>/g

function extractImgTags(source: string): string[] {
  return source.match(IMG_TAG_RE) ?? []
}

function getAttr(tag: string, name: string): string | null {
  // Match the attribute in either single/double quotes, with optional whitespace
  const re = new RegExp(`\\b${name}\\s*=\\s*(['"])(.*?)\\1`, 'i')
  const m = tag.match(re)
  return m && m[2] !== undefined ? m[2] : null
}

describe('FX-01: team logo <img> tags', () => {
  for (const rel of IMG_SOURCE_FILES) {
    describe(rel, () => {
      const source = readFileSync(resolve(FRONTEND_ROOT, rel), 'utf8')
      const tags = extractImgTags(source)

      // Skip files that have no <img> elements
      if (tags.length === 0) {
        it('has no <img> tags (skipped)', () => {
          expect(tags).toEqual([])
        })
        return
      }

      it('contains at least one <img> tag', () => {
        expect(tags.length).toBeGreaterThan(0)
      })

      for (const tag of tags) {
        it(`<img> tag has loading="lazy"`, () => {
          expect(getAttr(tag, 'loading')).toBe('lazy')
        })

        it(`<img> tag has decoding="async"`, () => {
          expect(getAttr(tag, 'decoding')).toBe('async')
        })

        it(`<img> tag has explicit width`, () => {
          const w = getAttr(tag, 'width')
          expect(w).not.toBeNull()
          expect(Number(w)).toBeGreaterThan(0)
        })

        it(`<img> tag has explicit height`, () => {
          const h = getAttr(tag, 'height')
          expect(h).not.toBeNull()
          expect(Number(h)).toBeGreaterThan(0)
        })

        it(`<img> tag has non-empty alt`, () => {
          const alt = getAttr(tag, 'alt')
          expect(alt).not.toBeNull()
          expect(alt!.length).toBeGreaterThan(0)
        })
      }
    })
  }
})
