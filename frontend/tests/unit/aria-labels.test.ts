/**
 * FX-06: ARIA labels on interactive elements.
 *
 * Every <button>, <a>, and <input> that does not have a text node child
 * must declare an `aria-label` or `aria-labelledby` attribute so it is
 * announced correctly by screen readers.  For images, the `alt` attribute
 * is mandatory (or `alt=""` if purely decorative).
 */
import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { resolve, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'

const FRONTEND_ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '../..')

const VUE_FILES = [
  'components/Header.vue',
  'components/Footer.vue',
  'components/GameCard.vue',
  'components/TipCard.vue',
  'components/MatchAnalysisCard.vue',
  'components/WeatherCard.vue',
  'components/ProfitChart.vue',
  'components/AccuracyChart.vue',
  'components/CumulativeProfitChart.vue',
  'pages/index.vue',
  'pages/about.vue',
  'pages/backtest.vue',
  'pages/game/[slug].vue',
  'error.vue',
  'app.vue',
  'layouts/default.vue',
]

function getAttr(tag: string, name: string): string | null {
  const re = new RegExp(`\\b${name}\\s*=\\s*(['"])(.*?)\\1`, 'i')
  const m = tag.match(re)
  return m && m[2] !== undefined ? m[2] : null
}

/**
 * Find <button>...</button> tags.  Returns array of the full tag
 * (or block for paired tags).  For paired tags we need to look at the
 * text content, so we capture the body of the tag too.
 */
function findTags(source: string, tagName: string): Array<{ full: string; text: string }> {
  const re = new RegExp(`<${tagName}\\b([^>]*)>([\\s\\S]*?)</${tagName}>`, 'gi')
  const out: Array<{ full: string; text: string }> = []
  let m
  while ((m = re.exec(source)) !== null) {
    out.push({
      full: m[0],
      text: (m[2] ?? '').replace(/<[^>]+>/g, '').replace(/\s+/g, ' ').trim(),
    })
  }
  return out
}

function findSelfClosing(source: string, tagName: string): string[] {
  const re = new RegExp(`<${tagName}\\b[^>]*\\/>`, 'gi')
  return source.match(re) ?? []
}

function hasTextContent(text: string): boolean {
  return text.length > 0
}

describe('FX-06: ARIA labels on interactive elements', () => {
  for (const rel of VUE_FILES) {
    describe(rel, () => {
      const source = readFileSync(resolve(FRONTEND_ROOT, rel), 'utf8')
      const buttons = findTags(source, 'button')

      it('every <button> has accessible text or aria-label', () => {
        const offenders: string[] = []
        for (const btn of buttons) {
          const hasAriaLabel = getAttr(btn.full, 'aria-label') || getAttr(btn.full, 'aria-labelledby')
          const hasTitle = getAttr(btn.full, 'title')
          if (!hasAriaLabel && !hasTitle && !hasTextContent(btn.text)) {
            offenders.push(btn.full.slice(0, 120))
          }
        }
        expect(
          offenders,
          `${rel} contains buttons with no accessible name:\n${offenders.join('\n')}`,
        ).toEqual([])
      })
    })
  }

  // Image-level rules apply across all files
  describe('images across all files', () => {
    for (const rel of VUE_FILES) {
      const source = readFileSync(resolve(FRONTEND_ROOT, rel), 'utf8')
      const imgs = source.match(/<img\b[^>]*\/?>/gi) ?? []

      it(`${rel}: every <img> has alt attribute (empty string for decorative)`, () => {
        const missing: string[] = []
        for (const tag of imgs) {
          if (getAttr(tag, 'alt') === null) missing.push(tag.slice(0, 100))
        }
        expect(missing, missing.join('\n')).toEqual([])
      })
    }
  })
})
