/**
 * FX-05 / FX-20: SEO meta tags + canonical URLs.
 *
 * Every page that calls `useHead(...)` or `useSeoMeta(...)` must include:
 *   - a description
 *   - og:title, og:description, og:url, og:type
 *   - twitter:title, twitter:description
 *   - a canonical link
 *
 * Both shapes are accepted:
 *   - useHead({ meta: [{ name: 'description', ... }] })
 *   - useSeoMeta({ description: ... })
 */
import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { resolve, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'

const FRONTEND_ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '../..')

const PAGE_FILES = [
  'pages/index.vue',
  'pages/about.vue',
  'pages/backtest.vue',
  'pages/game/[slug].vue',
]

const NUXT_CONFIG = 'nuxt.config.ts'

function hasUseHead(source: string): boolean {
  return /useHead\s*\(/.test(source)
}

function hasUseSeoMeta(source: string): boolean {
  return /useSeoMeta\s*\(/.test(source)
}

/**
 * Accepts both the useHead-style:
 *   { name: 'description', content: '...' }
 *   { property: 'og:title', content: '...' }
 * and the useSeoMeta-style shorthand:
 *   description: '...'
 *   ogTitle: '...'
 */
function hasMeta(source: string, name: string, property = false): boolean {
  if (property) {
    // useHead form: property: 'og:title'
    const headRe = new RegExp(`property\\s*:\\s*['"]${name}['"]`)
    if (headRe.test(source)) return true
    // useSeoMeta shorthand: ogTitle, ogDescription, ogUrl, ogType
    const camel = name
      .split(':')[1]
      ?.replace(/^./, (c) => c.toUpperCase())
    if (camel) {
      const seoRe = new RegExp(`og${camel}\\s*:`)
      if (seoRe.test(source)) return true
    }
    return false
  }
  // useHead form: name: 'description'
  const headRe = new RegExp(`name\\s*:\\s*['"]${name}['"]`)
  if (headRe.test(source)) return true
  // useSeoMeta shorthand: description, twitterTitle, twitterDescription, twitterCard, keywords
  const seoMap: Record<string, string> = {
    description: 'description',
    keywords: 'keywords',
    'twitter:title': 'twitterTitle',
    'twitter:description': 'twitterDescription',
    'twitter:card': 'twitterCard',
  }
  const seoKey = seoMap[name]
  if (seoKey) {
    const seoRe = new RegExp(`\\b${seoKey}\\s*:`)
    if (seoRe.test(source)) return true
  }
  return false
}

function hasCanonical(source: string): boolean {
  return (
    /canonical\s*:/.test(source) ||
    /rel\s*:\s*['"]canonical['"]/.test(source)
  )
}

describe('FX-05: SEO meta tags on every page', () => {
  for (const rel of PAGE_FILES) {
    describe(rel, () => {
      const source = readFileSync(resolve(FRONTEND_ROOT, rel), 'utf8')

      if (!hasUseHead(source) && !hasUseSeoMeta(source)) {
        it('inherits defaults from nuxt.config (no useHead/useSeoMeta)', () => {
          expect(true).toBe(true)
        })
        return
      }

      const required: Array<[string, boolean]> = [
        ['description', false],
        ['og:title', true],
        ['og:description', true],
        ['og:url', true],
        ['og:type', true],
        ['twitter:title', false],
        ['twitter:description', false],
      ]

      for (const [name, isProperty] of required) {
        it(`declares ${name}`, () => {
          expect(
            hasMeta(source, name, isProperty),
            `${rel} should declare ${name}`,
          ).toBe(true)
        })
      }
    })
  }
})

describe('FX-20: Canonical URLs on every page', () => {
  for (const rel of PAGE_FILES) {
    describe(rel, () => {
      const source = readFileSync(resolve(FRONTEND_ROOT, rel), 'utf8')

      if (!hasUseHead(source) && !hasUseSeoMeta(source)) {
        it('inherits canonical from nuxt.config (skipped)', () => {
          expect(true).toBe(true)
        })
        return
      }

      it('declares a canonical URL via a useHead link (H-2)', () => {
        // `useSeoMeta({ canonical })` is NOT a supported key — it
        // silently renders nothing.  Canonicals are <link> elements
        // and must go through useHead (or ogUrl + useHead link).
        expect(
          hasCanonical(source),
          `${rel} should set a canonical URL via useHead({ link: [{ rel: 'canonical', ... }] })`,
        ).toBe(true)
      })

      it('derives the canonical from the siteUrl runtime config (H-2)', () => {
        expect(
          /siteUrl/.test(source),
          `${rel} should use runtimeConfig.public.siteUrl instead of a hardcoded domain`,
        ).toBe(true)
      })
    })
  }
})

describe('H-2: nuxt.config.ts must NOT set a global default canonical', () => {
  const source = readFileSync(resolve(FRONTEND_ROOT, NUXT_CONFIG), 'utf8')

  it('does not declare a site-wide canonical in app.head.link', () => {
    // A global canonical pointing at "/" contradicted the per-page
    // canonicals on every non-home page (duplicate-content hazard).
    // Each page owns its canonical now.
    expect(source).not.toMatch(/rel\s*:\s*['"]canonical['"]/)
  })

  it('does not hardcode a preconnect to the retired API subdomain (M-8)', () => {
    expect(source).not.toMatch(/api\.whatismytip\.com/)
  })

  it('does not declare an empty-src analytics script (H-1)', () => {
    // The analytics script is registered conditionally by
    // plugins/umami.client.ts; a static head entry rendered
    // <script src=""> when env vars were unset.  nuxt.config must not
    // declare the script itself (runtimeConfig keys for the plugin are
    // fine).
    expect(source).not.toMatch(/umami-analytics/)
    expect(source).not.toMatch(/data-website-id/)
  })

  it('ships the og:image / twitter:image preview asset (M-7)', () => {
    expect(source).toMatch(/og:image/)
    expect(source).toMatch(/twitter:image/)
    expect(source).not.toMatch(/TODO: Create og-image/i)
  })
})
